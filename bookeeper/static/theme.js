"use strict";

// Loaded before the page renders so the chosen theme applies without a flash.
(() => {
    let stored = null;
    try {
        stored = localStorage.getItem("bookeeper-theme");
    } catch {
        // Storage may be unavailable; fall back to the default theme.
    }
    document.documentElement.dataset.theme = ["simple", "burnley", "future"].includes(stored) ? stored : "simple";
})();