"use strict";

const theme = document.querySelector("#theme");
try {
    theme.value = localStorage.getItem("bookeeper-theme") || "auto";
} catch {
    theme.value = "auto";
}
if (!theme.value) theme.value = "auto";
document.documentElement.dataset.theme = theme.value;
theme.addEventListener("change", () => {
    document.documentElement.dataset.theme = theme.value;
    try {
        localStorage.setItem("bookeeper-theme", theme.value);
    } catch {
        // The selected theme still applies on this page.
    }
});

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

for (const item of document.querySelectorAll(".map-token")) {
    const floor = item.closest(".floor");
    const status = floor.parentElement.querySelector(".floor-status");
    const form = document.getElementById(item.dataset.form);
    let drag;
    let saving = false;
    function place(x, y) {
        item.style.left = `${(floor.clientWidth - item.offsetWidth) * x / 100}px`;
        item.style.top = `${(floor.clientHeight - item.offsetHeight) * y / 100}px`;
    }
    function position(event) {
        const rect = floor.getBoundingClientRect();
        const percent = (offset, dimension) => Math.round(Math.min(100, Math.max(0, 100 * offset / dimension)));
        return [
            percent(event.clientX - rect.left - floor.clientLeft - drag.dx, floor.clientWidth - item.offsetWidth),
            percent(event.clientY - rect.top - floor.clientTop - drag.dy, floor.clientHeight - item.offsetHeight),
        ];
    }
    place(item.dataset.x, item.dataset.y);
    window.addEventListener("resize", () => place(item.dataset.x, item.dataset.y));
    item.addEventListener("pointerdown", (event) => {
        if (!event.isPrimary || event.button !== 0 || saving) return;
        const rect = item.getBoundingClientRect();
        drag = {id: event.pointerId, dx: event.clientX - rect.left, dy: event.clientY - rect.top};
        item.setPointerCapture(event.pointerId);
    });
    item.addEventListener("pointermove", (event) => {
        if (drag?.id === event.pointerId) place(...position(event));
    });
    item.addEventListener("pointercancel", () => {
        drag = null;
        place(item.dataset.x, item.dataset.y);
    });
    item.addEventListener("pointerup", async (event) => {
        if (drag?.id !== event.pointerId) return;
        const [x, y] = position(event);
        drag = null;
        if (x === Number(item.dataset.x) && y === Number(item.dataset.y)) return;
        const data = new URLSearchParams(new FormData(form));
        data.set("x", x);
        data.set("y", y);
        data.set("drag", "1");
        saving = true;
        try {
            const response = await fetch(form.action, {method: "POST", body: data});
            if (response.status !== 204) throw new Error("Position not saved");
            item.dataset.x = form.elements.x.value = x;
            item.dataset.y = form.elements.y.value = y;
            place(x, y);
            status.textContent = "Position saved.";
        } catch {
            place(item.dataset.x, item.dataset.y);
            status.textContent = "Could not save the position. Reload or use the coordinate form.";
        } finally {
            saving = false;
        }
    });
    item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            form.elements.x.focus();
        }
    });
}
