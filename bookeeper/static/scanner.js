"use strict";

const controls = document.querySelector("#scanner-controls");
const begin = document.querySelector("#scan-start");
const end = document.querySelector("#scan-stop");
const message = document.querySelector("#scan-status");
const view = document.querySelector("#scan-view");
let scanner;
let load;
let stopping = false;

function isbn13(value) {
    if (!/^97[89]\d{10}$/.test(value)) return false;
    return [...value].reduce((sum, digit, index) => sum + Number(digit) * (index % 2 ? 3 : 1), 0) % 10 === 0;
}

function loadReader() {
    if (!load) {
        const script = document.createElement("script");
        script.src = controls.dataset.reader;
        load = new Promise((resolve, reject) => {
            script.onload = resolve;
            script.onerror = reject;
            document.head.append(script);
        }).catch((error) => {
            load = null;
            script.remove();
            throw error;
        });
    }
    return load;
}

async function stopCamera() {
    if (!scanner || stopping) return;
    stopping = true;
    try {
        await scanner.stop();
        scanner.clear();
        scanner = null;
        end.hidden = view.hidden = true;
        begin.disabled = false;
    } catch {
        message.textContent = "Could not close the camera cleanly. Reload this page to close it.";
    } finally {
        stopping = false;
    }
}

end.addEventListener("click", stopCamera);
window.addEventListener("pagehide", stopCamera);
begin.addEventListener("click", async () => {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        message.textContent = "The phone camera requires a trusted HTTPS connection. You can still type or paste the ISBN.";
        return;
    }
    begin.disabled = true;
    try {
        await loadReader();
    } catch {
        message.textContent = "The scanner could not load. You can still type or paste the ISBN.";
        begin.disabled = false;
        return;
    }
    view.hidden = false;
    try {
        scanner = new Html5Qrcode(view.id, {formatsToSupport: [Html5QrcodeSupportedFormats.EAN_13]});
        await scanner.start({facingMode: "environment"}, {fps: 8}, (value) => {
            if (stopping || !isbn13(value)) return;
            document.querySelector("#isbn").value = value;
            message.textContent = "ISBN scanned. Choose Look up ISBN to fill in the book details.";
            stopCamera();
        });
        if (scanner && !stopping) {
            end.hidden = false;
            message.textContent = "Point the camera at the book's ISBN barcode.";
        }
    } catch {
        scanner = null;
        view.hidden = true;
        begin.disabled = false;
        message.textContent = "Could not open the camera. Check browser permissions or type the ISBN manually.";
    }
});
