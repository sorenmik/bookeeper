"use strict";

const page = document.querySelector("#bulk");
const banner = document.querySelector("#bulk-result");
const addedCount = document.querySelector("#bulk-added");
const addedList = document.querySelector("#bulk-added-list");
const failedBox = document.querySelector("#bulk-failed-box");
const failedList = document.querySelector("#bulk-failed-list");
const COOLDOWN_MS = 1500;
let added = 0;
let lastIsbn = "";

function toIsbn13(value) {
    if (value.length !== 10) return value;
    const base = "978" + value.slice(0, 9);
    const sum = [...base].reduce((total, digit, index) => total + Number(digit) * (index % 2 ? 3 : 1), 0);
    return base + String((10 - (sum % 10)) % 10);
}

function show(text, kind) {
    banner.textContent = text;
    banner.className = kind === "ok" ? "notice" : "notice error";
    banner.hidden = false;
}

function listItem(text) {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
}

async function addBook(isbn) {
    const body = new URLSearchParams({csrf: page.dataset.csrf, isbn, location_id: page.dataset.slot});
    const response = await fetch(page.dataset.url, {method: "POST", body});
    if (!response.ok) throw new Error(`Server answered ${response.status}`);
    return response.json();
}

window.bulkIsbnHandler = async (value, resume) => {
    const isbn = toIsbn13(value);
    if (isbn === lastIsbn) {
        show(`Ignored: ${isbn} is the same as the previous scan. Show the next book.`, "error");
        beep("error");
        setTimeout(resume, COOLDOWN_MS);
        return;
    }
    lastIsbn = isbn;
    show(`Scanned ${isbn}. Looking up the book…`, "ok");
    try {
        const result = await addBook(isbn);
        if (result.status === "added") {
            added += 1;
            addedCount.textContent = String(added);
            addedList.prepend(listItem(result.author ? `${result.title} · ${result.author}` : result.title));
            show(`Successful: ${result.title}`, "ok");
            beep("found");
        } else {
            failedBox.hidden = false;
            failedList.prepend(listItem(`${isbn} · ${result.message}`));
            show(`NOT ADDED: ${isbn}. ${result.message} Put this book aside and add it manually later.`, "error");
            beep("error");
        }
    } catch (error) {
        console.warn("Bookeeper could not add the book.", error);
        failedBox.hidden = false;
        failedList.prepend(listItem(`${isbn} · The server could not be reached.`));
        show(`NOT ADDED: ${isbn}. The server could not be reached. Put this book aside.`, "error");
        beep("error");
    }
    setTimeout(resume, COOLDOWN_MS);
};
