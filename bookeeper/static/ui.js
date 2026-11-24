"use strict";

const theme = document.querySelector("#theme");
theme.value = document.documentElement.dataset.theme;
if (!theme.value) theme.value = document.documentElement.dataset.theme = "simple";
theme.addEventListener("change", () => {
    document.documentElement.dataset.theme = theme.value;
    try {
        localStorage.setItem("bookeeper-theme", theme.value);
    } catch {
        // The selected theme still applies on this page.
    }
});

// Choosing a unit colour switches off "use the theme's colour".
document.querySelector("input[name=color]")?.addEventListener("input", (event) => {
    const reset = event.target.form.elements.default_color;
    if (reset) reset.checked = false;
});

// Pirate tavern theme: an adventure-game action line that names whatever the pointer is over.
const verbLine = document.querySelector(".verb-line");
if (verbLine) {
    const idle = verbLine.textContent;
    const nameOf = (el) => (el.getAttribute("aria-label") || el.textContent || el.value || el.placeholder || "")
        .replace(/\s+/g, " ").trim().slice(0, 40);
    document.addEventListener("pointerover", (event) => {
        if (document.documentElement.dataset.theme !== "pixel") return;
        const target = event.target.closest("a, button, summary, select, input, textarea");
        if (!target) {
            verbLine.textContent = idle;
        } else if (target.matches("input, textarea, select")) {
            const label = target.labels?.[0]
                ? [...target.labels[0].childNodes].filter((node) => node.nodeType === Node.TEXT_NODE)
                    .map((node) => node.textContent).join(" ").replace(/\s+/g, " ").trim()
                : "";
            verbLine.textContent = `${target.matches("select") ? "Pick" : "Write in"} ${label || "this"}`;
        } else {
            verbLine.textContent = `${target.matches("a") ? "Walk to" : "Use"} ${nameOf(target)}`;
        }
    });
}
const quips = document.querySelectorAll(".pixel-quips li");
if (quips.length) quips[Math.floor(Math.random() * quips.length)].classList.add("is-current");

const navToggle = document.querySelector(".nav-toggle");
if (navToggle) {
    navToggle.hidden = false;
    navToggle.addEventListener("click", () => {
        navToggle.setAttribute("aria-expanded", String(navToggle.getAttribute("aria-expanded") !== "true"));
    });
    navToggle.closest("nav").addEventListener("keydown", (event) => {
        if (event.key === "Escape" && navToggle.getAttribute("aria-expanded") === "true" && navToggle.offsetWidth) {
            event.preventDefault();
            navToggle.setAttribute("aria-expanded", "false");
            navToggle.focus();
        }
    });
}

for (const unit of document.querySelectorAll(".unit")) {
    const canvas = unit.closest(".zone-canvas");
    const status = document.querySelector(".floor-status");
    const handle = unit.querySelector(".unit-handle");
    let drag;
    let saving = false;
    const cellSize = () => {
        const probe = document.createElement("div");
        probe.style.width = "var(--cell)";
        canvas.append(probe);
        const size = probe.offsetWidth;
        probe.remove();
        return size;
    };
    function place(x, y) {
        unit.style.setProperty("--x", x);
        unit.style.setProperty("--y", y);
    }
    function cells(event) {
        const rect = canvas.getBoundingClientRect();
        const size = cellSize();
        return [
            Math.max(0, Math.min(199, Math.round((event.clientX - rect.left - canvas.clientLeft + canvas.scrollLeft - drag.dx) / size))),
            Math.max(0, Math.min(199, Math.round((event.clientY - rect.top - canvas.clientTop + canvas.scrollTop - drag.dy) / size))),
        ];
    }
    async function save(x, y) {
        if (saving) return;
        saving = true;
        const data = new URLSearchParams({csrf: canvas.dataset.csrf, x, y});
        try {
            const response = await fetch(unit.dataset.url, {method: "POST", body: data});
            if (response.status !== 204) throw new Error("Position not saved");
            unit.dataset.x = x;
            unit.dataset.y = y;
            status.textContent = "Position saved.";
        } catch {
            place(unit.dataset.x, unit.dataset.y);
            status.textContent = "Could not save the position. Reload and try again.";
        } finally {
            saving = false;
        }
    }
    handle.addEventListener("pointerdown", (event) => {
        if (!event.isPrimary || event.button !== 0 || saving) return;
        const rect = unit.getBoundingClientRect();
        drag = {id: event.pointerId, dx: event.clientX - rect.left, dy: event.clientY - rect.top};
        handle.setPointerCapture(event.pointerId);
        unit.classList.add("is-dragging");
    });
    handle.addEventListener("pointermove", (event) => {
        if (drag?.id === event.pointerId) place(...cells(event));
    });
    handle.addEventListener("pointercancel", () => {
        drag = null;
        unit.classList.remove("is-dragging");
        place(unit.dataset.x, unit.dataset.y);
    });
    handle.addEventListener("pointerup", (event) => {
        if (drag?.id !== event.pointerId) return;
        const [x, y] = cells(event);
        drag = null;
        unit.classList.remove("is-dragging");
        place(x, y);
        if (x !== Number(unit.dataset.x) || y !== Number(unit.dataset.y)) save(x, y);
    });
    handle.addEventListener("keydown", (event) => {
        const step = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]}[event.key];
        if (!step) return;
        event.preventDefault();
        const x = Math.max(0, Math.min(199, Number(unit.dataset.x) + step[0]));
        const y = Math.max(0, Math.min(199, Number(unit.dataset.y) + step[1]));
        place(x, y);
        save(x, y);
    });
}

document.querySelector(".slot.is-target")?.scrollIntoView({block: "center", inline: "center"});

for (const form of document.querySelectorAll("form[data-confirm]")) {
    form.addEventListener("submit", (event) => {
        const field = form.dataset.select;
        const chosen = field ? form.querySelectorAll(`input[name="${field}"]:checked`).length : 0;
        if (field && !chosen) {
            event.preventDefault();
            alert("Select at least one book first.");
            return;
        }
        const count = `${chosen} ${chosen === 1 ? "book" : "books"}`;
        if (!confirm(form.dataset.confirm.replace("{n}", count))) event.preventDefault();
    });
}

for (const all of document.querySelectorAll("[data-select-all]")) {
    all.addEventListener("change", () => {
        for (const box of all.form.querySelectorAll('input[name="book"]')) box.checked = all.checked;
    });
}
