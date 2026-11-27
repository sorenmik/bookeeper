"use strict";

const controls = document.querySelector("#scanner-controls");
const begin = document.querySelector("#scan-start");
const end = document.querySelector("#scan-stop");
const previewOptions = document.querySelector("#scan-preview-options");
const mirror = document.querySelector("#scan-mirror");
const message = document.querySelector("#scan-status");
const view = document.querySelector("#scan-view");
const frame = view.parentElement;
let scanner;
let load;
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

function isbn10(value) {
    if (!/^\d{9}[\dX]$/.test(value)) return false;
    return [...value].reduce((sum, char, index) => sum + (char === "X" ? 10 : Number(char)) * (10 - index), 0) % 11 === 0;
}

// Finds a checksum-valid ISBN in OCR text such as "ISBN 87-7714-123-4".
function findIsbn(text) {
    const upper = text.toUpperCase();
    for (const match of upper.matchAll(/(?:\d[ -]?){9,16}[\dX]/g)) {
        const digits = match[0].replace(/[ -]/g, "");
        const value = [digits, digits.slice(0, 13), digits.slice(0, 10)].find((item) => isbn13(item) || isbn10(item));
        if (value) {
            const labelled = /ISBN[\s:-]*$/.test(upper.slice(Math.max(0, match.index - 8), match.index));
            return {value, labelled};
        }
    }
    return null;
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

async function loadBarcodeEngine() {
    if (!load) {
        load = (async () => {
            await loadScript(controls.dataset.reader);
            const wasm = new URL(controls.dataset.wasm, document.baseURI).href;
            await ZXingWASM.prepareZXingModule({
                overrides: {
                    locateFile: (path, prefix) => path.endsWith(".wasm") ? wasm : prefix + path,
                },
                fireImmediately: true,
            });
        })().catch((error) => {
            load = null;
            throw error;
        });
    }
    return load;
}

let ocr;

function loadOcr() {
    if (!ocr) {
        ocr = (async () => {
            await loadScript(controls.dataset.ocr);
            const files = new URL(controls.dataset.ocrFiles, document.baseURI).href.replace(/\/$/, "");
            const worker = await Tesseract.createWorker("eng", Tesseract.OEM.LSTM_ONLY, {
                workerPath: new URL(controls.dataset.ocrWorker, document.baseURI).href,
                corePath: files,
                langPath: files,
                workerBlobURL: false,
            });
            await worker.setParameters({
                tessedit_char_whitelist: "0123456789-XISBN: ",
                tessedit_pageseg_mode: Tesseract.PSM.SPARSE_TEXT,
            });
            return worker;
        })().catch((error) => {
            ocr = null;
            throw error;
        });
    }
    return ocr;
}

async function acceptIsbn(value) {
    // The bulk page handles each ISBN itself and keeps the camera running.
    if (window.bulkIsbnHandler) {
        window.bulkIsbnHandler(value, resumeScanning);
        return;
    }
    const isbn = document.querySelector("#isbn");
    isbn.value = value;
    showStatus("ISBN scanned. Looking up the book details…");
    await stopCamera();
    const lookup = document.querySelector("#isbn-lookup");
    if (isbn.form.requestSubmit) isbn.form.requestSubmit(lookup);
    else lookup.click();
}

function resumeScanning() {
    if (!scanner || scanner.stopped) return;
    scanner.resume();
}

class BarcodeCamera {
    constructor(element, onIsbn) {
        this.element = element;
        this.onIsbn = onIsbn;
        this.stream = null;
        this.video = null;
        this.canvas = document.createElement("canvas");
        this.context = this.canvas.getContext("2d", {willReadFrequently: true});
        this.ocrCanvas = document.createElement("canvas");
        this.running = false;
        this.stopped = false;
        this.timer = null;
        this.frameRequest = null;
    }

    async start() {
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: false,
            video: {
                facingMode: {ideal: "environment"},
                width: {ideal: 1280},
                height: {ideal: 720},
            },
        });
        // stop() may have run while the permission prompt was open.
        if (this.stopped) {
            stream.getTracks().forEach((track) => track.stop());
            throw new Error("Camera stopped before it opened.");
        }
        this.stream = stream;
        this.enableAutofocus();
        this.video = document.createElement("video");
        this.video.autoplay = true;
        this.video.muted = true;
        this.video.playsInline = true;
        this.video.srcObject = this.stream;
        this.element.replaceChildren(this.video);
        await this.video.play();
        if (this.stopped) return;
        this.running = true;
        this.scheduleScan();
        this.readText();
    }

    found(value) {
        if (!this.running) return;
        this.running = false;
        this.onIsbn(value);
    }

    resume() {
        if (this.running) return;
        this.running = true;
        this.scheduleScan();
        if (!this.readingText) this.readText();
    }

    // Runs alongside barcode scanning for older books that only print the ISBN as text.
    async readText() {
        this.readingText = true;
        try {
            await this.readTextFrames();
        } finally {
            this.readingText = false;
        }
    }

    async readTextFrames() {
        let worker;
        try {
            worker = await loadOcr();
        } catch (error) {
            console.warn("Bookeeper could not load text recognition; scanning barcodes only.", error);
            return;
        }
        const seen = new Set();
        while (this.running) {
            if (this.video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && this.video.videoWidth) {
                try {
                    const scale = Math.min(1, 1600 / this.video.videoWidth);
                    this.ocrCanvas.width = Math.round(this.video.videoWidth * scale);
                    this.ocrCanvas.height = Math.round(this.video.videoHeight * scale);
                    this.ocrCanvas.getContext("2d").drawImage(this.video, 0, 0, this.ocrCanvas.width, this.ocrCanvas.height);
                    const {data} = await worker.recognize(this.ocrCanvas);
                    const match = findIsbn(data.text || "");
                    // A checksum alone lets about 1 in 10 misreads through, so unlabelled numbers must be read twice.
                    if (match && (match.labelled || seen.has(match.value))) {
                        this.found(match.value);
                        return;
                    }
                    if (match) seen.add(match.value);
                } catch (error) {
                    console.warn("Bookeeper could not read text from a camera frame.", error);
                }
            }
            await new Promise((resolve) => setTimeout(resolve, 250));
        }
    }

    enableAutofocus() {
        const [track] = this.stream.getVideoTracks();
        const modes = track && track.getCapabilities ? track.getCapabilities().focusMode || [] : [];
        if (modes.includes("continuous")) {
            track.applyConstraints({advanced: [{focusMode: "continuous"}]}).catch(() => {});
        }
    }

    scheduleScan() {
        if (!this.running) return;
        if (this.video.requestVideoFrameCallback) {
            this.frameRequest = this.video.requestVideoFrameCallback(() => this.scanFrame());
        } else {
            this.timer = setTimeout(() => this.scanFrame(), 50);
        }
    }

    async scanFrame() {
        if (!this.running) return;
        if (this.video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && this.video.videoWidth) {
            try {
                const scale = Math.min(1, 1280 / this.video.videoWidth);
                const width = Math.round(this.video.videoWidth * scale);
                const height = Math.round(this.video.videoHeight * scale);
                if (this.canvas.width !== width || this.canvas.height !== height) {
                    this.canvas.width = width;
                    this.canvas.height = height;
                }
                this.context.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
                const pixels = this.context.getImageData(0, 0, this.canvas.width, this.canvas.height);
                const results = await ZXingWASM.readBarcodes(pixels, {
                    formats: ["EAN13"],
                    tryHarder: true,
                    tryRotate: true,
                    tryInvert: false,
                    tryDownscale: false,
                    maxNumberOfSymbols: 5,
                });
                const match = results.find((result) => isbn13(result.text));
                const value = match ? match.text : null;
                if (value && this.running) {
                    this.found(value);
                    return;
                }
            } catch (error) {
                console.warn("Bookeeper could not decode a camera frame.", error);
            }
        }
        this.scheduleScan();
    }

    async stop() {
        this.running = false;
        this.stopped = true;
        clearTimeout(this.timer);
        if (this.frameRequest !== null && this.video) this.video.cancelVideoFrameCallback(this.frameRequest);
        if (this.stream) this.stream.getTracks().forEach((track) => track.stop());
        if (this.video) {
            this.video.pause();
            this.video.srcObject = null;
        }
    }

    clear() {
        this.element.replaceChildren();
    }
}

async function stopCamera(status) {
    if (!scanner || stopping) return;
    clearTimeout(scanHint);
    stopping = true;
    try {
        await scanner.stop();
        scanner.clear();
        scanner = null;
        end.hidden = view.hidden = previewOptions.hidden = true;
        mirror.checked = false;
        frame.classList.remove("is-mirrored");
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

begin.addEventListener("click", async () => {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        showStatus("The phone camera requires a trusted HTTPS connection. You can still type or paste the ISBN.");
        return;
    }
    begin.disabled = true;
    showStatus("Loading the barcode scanner…");
    loadOcr().catch(() => {});
    try {
        await loadBarcodeEngine();
    } catch (error) {
        console.warn("Bookeeper could not load the barcode scanner.", error);
        const detail = error instanceof Event ? `${error.target?.src || "script"} failed to load` : error?.message || String(error);
        showStatus(`The scanner could not load (${detail}). You can still type or paste the ISBN.`);
        begin.disabled = false;
        return;
    }
    view.hidden = false;
    showStatus("Opening the camera. Allow camera access if prompted.");
    try {
        scanner = new BarcodeCamera(view, acceptIsbn);
        await scanner.start();
        if (scanner && !stopping) {
            end.hidden = previewOptions.hidden = false;
            showStatus("Camera on. Scanning for an ISBN barcode or a printed ISBN number…");
            scanHint = setTimeout(() => {
                showStatus("Still scanning. Hold the barcode or the printed ISBN closer and steady, or enter it manually.");
            }, 5000);
        }
    } catch (error) {
        console.warn("Bookeeper could not open the camera.", error);
        if (scanner) {
            await scanner.stop();
            scanner.clear();
            scanner = null;
        }
        view.hidden = true;
        begin.disabled = false;
        showStatus("Could not open the camera. Check browser permissions or type the ISBN manually.");
    }
});
