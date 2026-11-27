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

function acceptIsbn(value) {
    document.querySelector("#isbn").value = value;
    showStatus("ISBN scanned. Choose Look up ISBN to fill in the book details.");
    stopCamera();
}

class BarcodeCamera {
    constructor(element, onIsbn) {
        this.element = element;
        this.onIsbn = onIsbn;
        this.stream = null;
        this.video = null;
        this.canvas = document.createElement("canvas");
        this.context = this.canvas.getContext("2d", {willReadFrequently: true});
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
                    this.onIsbn(value);
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
    try {
        await loadBarcodeEngine();
    } catch (error) {
        console.warn("Bookeeper could not load the barcode scanner.", error);
        showStatus("The scanner could not load. You can still type or paste the ISBN.");
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
            showStatus("Camera on. Scanning for an ISBN barcode…");
            scanHint = setTimeout(() => {
                showStatus("Still scanning. Move the barcode closer and hold it steady, or enter the ISBN manually.");
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
