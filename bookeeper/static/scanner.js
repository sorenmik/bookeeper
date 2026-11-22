"use strict";

const controls = document.querySelector("#scanner-controls");
const begin = document.querySelector("#scan-start");
const end = document.querySelector("#scan-stop");
const readText = document.querySelector("#scan-text");
const previewOptions = document.querySelector("#scan-preview-options");
const mirror = document.querySelector("#scan-mirror");
const message = document.querySelector("#scan-status");
const view = document.querySelector("#scan-view");
const frame = view.parentElement;
let scanner;
let load;
let loadOcr;
let stopping = false;
let scanHint;

function showStatus(text) {
    clearTimeout(scanHint);
    message.textContent = text;
}

function isbn13(value) {
    if (!/^97[89]\d{10}$/.test(value)) return false;
    return [...value].reduce((sum, digit, index) => sum + Number(digit) * (index % 2 ? 3 : 1), 0) % 10 === 0;
}

function printedIsbn(text) {
    const found = new Set();
    for (const match of text.matchAll(/\bISBN(?:\s*-?\s*(?:10|13)(?=[\s:]))?\s*:?\s*((?:\d[.\s-]*){9,12}[\dX])\b/gi)) {
        const value = match[1].replace(/[.\s-]/g, "").toUpperCase();
        if (isbn13(value)) {
            found.add(value);
        } else if (/^\d{9}[\dX]$/.test(value)) {
            const checksum = [...value].reduce((sum, digit, i) => sum + (digit === "X" ? 10 : Number(digit)) * (10 - i), 0);
            if (checksum % 11 === 0) {
                const body = "978" + value.slice(0, 9);
                const total = [...body].reduce((sum, digit, i) => sum + Number(digit) * (i % 2 ? 3 : 1), 0);
                found.add(`${body}${(10 - total % 10) % 10}`);
            }
        }
    }
    return found.size === 1 ? [...found][0] : null;
}

function loadScript(src) {
    const script = document.createElement("script");
    script.src = src;
    return new Promise((resolve, reject) => {
        script.onload = resolve;
        script.onerror = reject;
        document.head.append(script);
    }).catch((error) => {
        script.remove();
        throw error;
    });
}

function acceptIsbn(value, source) {
    document.querySelector("#isbn").value = value;
    showStatus(`${source}. Choose Look up ISBN to fill in the book details.`);
    stopCamera();
}

async function stopCamera(status) {
    if (!scanner || stopping) return;
    clearTimeout(scanHint);
    stopping = true;
    try {
        await scanner.stop();
        scanner.clear();
        scanner = null;
        end.hidden = view.hidden = readText.hidden = previewOptions.hidden = true;
        readText.disabled = false;
        frame.removeAttribute("data-text-guide");
        begin.disabled = false;
        if (status) showStatus(status);
    } catch (error) {
        console.warn("Bookeeper could not close the camera.", error);
        showStatus("Could not close the camera cleanly. Reload this page to close it.");
    } finally {
        stopping = false;
    }
}

mirror.addEventListener("change", () => frame.classList.toggle("is-mirrored", mirror.checked));
end.addEventListener("click", () => stopCamera("Camera stopped."));
window.addEventListener("pagehide", () => stopCamera());
readText.addEventListener("click", async () => {
    if (!scanner || stopping) return;
    if (!frame.hasAttribute("data-text-guide")) {
        frame.setAttribute("data-text-guide", "");
        showStatus("Fit the printed ISBN label and number in the orange guide, then tap Read printed ISBN again.");
        return;
    }
    const activeScanner = scanner;
    const stillScanning = () => scanner === activeScanner && !stopping;
    readText.disabled = true;
    showStatus("Reading printed ISBN text. Keep the label and number in the guide.");
    let worker;
    try {
        const script = new URL(controls.dataset.ocr, document.baseURI);
        loadOcr ??= loadScript(script.href).catch((error) => { loadOcr = null; throw error; });
        await loadOcr;
        if (!stillScanning()) return;
        worker = await Tesseract.createWorker("eng", Tesseract.OEM.LSTM_ONLY, {
            workerPath: new URL("worker.min.js", script).href,
            corePath: new URL("tesseract-core-lstm.wasm.js", script).href,
            langPath: new URL(".", script).href,
            workerBlobURL: false,
        });
        if (!stillScanning()) return;
        await worker.setParameters({tessedit_pageseg_mode: Tesseract.PSM.SINGLE_LINE});
        const crop = document.createElement("canvas");
        for (let attempt = 0; attempt < 2 && stillScanning(); attempt++) {
            if (attempt) await new Promise((resolve) => setTimeout(resolve, 150));
            if (!stillScanning()) return;
            const video = view.querySelector("video");
            if (!video?.videoWidth || video.readyState < 2) continue;
            crop.width = Math.round(video.videoWidth * .9);
            crop.height = Math.round(video.videoHeight * .11);
            crop.getContext("2d").drawImage(
                video, Math.round(video.videoWidth * .05), Math.round(video.videoHeight * .27),
                crop.width, crop.height, 0, 0, crop.width, crop.height,
            );
            const {data} = await worker.recognize(crop);
            if (!stillScanning()) return;
            const value = printedIsbn(data.text);
            if (value) {
                acceptIsbn(value, "Printed ISBN read");
                return;
            }
        }
        if (stillScanning()) showStatus("No valid ISBN found in the guide. Move the printed line into the box and try again, or type the ISBN.");
    } catch (error) {
        console.warn("Bookeeper could not read printed ISBN text.", error);
        if (stillScanning()) showStatus("Text recognition could not run. Use the barcode or enter the ISBN manually.");
    } finally {
        if (worker) await worker.terminate();
        if (stillScanning()) readText.disabled = false;
    }
});

begin.addEventListener("click", async () => {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        showStatus("The phone camera requires a trusted HTTPS connection. You can still type or paste the ISBN.");
        return;
    }
    begin.disabled = true;
    showStatus("Loading the barcode scanner…");
    try {
        load ??= loadScript(controls.dataset.reader).catch((error) => { load = null; throw error; });
        await load;
    } catch (error) {
        console.warn("Bookeeper could not load the barcode scanner.", error);
        showStatus("The scanner could not load. You can still type or paste the ISBN.");
        begin.disabled = false;
        return;
    }
    view.hidden = false;
    showStatus("Opening the camera. Allow camera access if prompted.");
    try {
        scanner = new Html5Qrcode(view.id, {formatsToSupport: [Html5QrcodeSupportedFormats.EAN_13]});
        await scanner.start({facingMode: "environment"}, {
            fps: 8,
            videoConstraints: {facingMode: {ideal: "environment"}, width: {ideal: 1280}, height: {ideal: 720}},
        }, (value) => {
            if (stopping || !isbn13(value)) return;
            acceptIsbn(value, "ISBN scanned");
        });
        if (scanner && !stopping) {
            end.hidden = readText.hidden = previewOptions.hidden = false;
            showStatus("Camera on. Scanning for a 13-digit ISBN barcode…");
            scanHint = setTimeout(() => {
                showStatus("Still scanning. No ISBN barcode detected yet. Try Read printed ISBN to scan the printed number instead.");
            }, 5000);
        }
    } catch (error) {
        console.warn("Bookeeper could not open the camera.", error);
        scanner = null;
        view.hidden = true;
        begin.disabled = false;
        showStatus("Could not open the camera. Check browser permissions or type the ISBN manually.");
    }
});
