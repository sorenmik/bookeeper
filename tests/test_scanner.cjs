const assert = require("assert").strict;
const {readFileSync} = require("fs");
const {join} = require("path");
const {runInNewContext} = require("vm");

const element = {parentElement: {}, dataset: {}, addEventListener() {}, click() {}};
const scanner = readFileSync(join(__dirname, "..", "bookeeper", "static", "scanner.js"), "utf8");

function load(name, {fields = {}, href = "https://example.test/copies/new", extra = {}} = {}) {
    return runInNewContext(`${scanner}\n${name}`, {
        document: {querySelector: (selector) => fields[selector] || element, addEventListener() {}},
        window: {addEventListener() {}, location: {href}, history: {replaceState() {}}},
        navigator: {},
        URL,
        clearTimeout() {},
        ...extra,
    });
}

const isbn13 = load("isbn13");
assert.equal(isbn13("9780140621006"), true);
assert.equal(isbn13("9791032300046"), true);

assert.equal(isbn13("1234567890123"), false);
assert.equal(isbn13("9780140621007"), false);
assert.equal(isbn13("978014062100"), false);

const findIsbn = load("findIsbn");
assert.deepEqual({...findIsbn("Gyldendal 1987\nISBN 87-00-12345-X? no\nISBN: 87-7714-123-7")}, {value: "8777141237", labelled: true});
assert.deepEqual({...findIsbn("9780140621006")}, {value: "9780140621006", labelled: false});
assert.deepEqual({...findIsbn("ISBN 87-7714-123-7 1990")}, {value: "8777141237", labelled: true});
assert.equal(findIsbn("ISBN 87-7714-123-4"), null);
assert.equal(findIsbn("Printed 1990"), null);

const scanBand = load("scanBand");
assert.deepEqual({...scanBand(1280, 720)}, {x: 0, y: 240, width: 1280, height: 240});

// Opening the add page with ?scan=1 starts the camera once and removes the flag from the URL.
let starts = 0;
let replaced = "";
runInNewContext(scanner, {
    document: {querySelector: (selector) => selector === "#scan-start" ? {...element, click() { starts += 1; }} : element, addEventListener() {}},
    window: {addEventListener() {}, location: {href: "https://example.test/copies/new?scan=1"}, history: {replaceState(_s, _t, url) { replaced = String(url); }}},
    navigator: {},
    URL,
});
assert.equal(starts, 1);
assert.equal(replaced, "https://example.test/copies/new");

// Scanned, looked up and failed lookups each vibrate differently.
const patterns = [];
const vibrating = {navigator: {vibrate: (pattern) => patterns.push(JSON.stringify(pattern))}};
const beep = load("beep", {extra: vibrating});
["scan", "found", "error"].forEach(beep);
assert.equal(new Set(patterns).size, 3);
patterns.length = 0;
load("0", {fields: {"#scanner-controls": {...element, dataset: {lookup: "failed"}}}, extra: vibrating});
assert.deepEqual(patterns, [JSON.stringify([250, 100, 250])]);

const lookup = {};
const form = {submitted: null, requestSubmit(button) { this.submitted = button; }};
const isbnInput = {value: "", form};
const status = {textContent: ""};
const acceptIsbn = load("acceptIsbn", {fields: {"#isbn": isbnInput, "#isbn-lookup": lookup, "#scan-status": status}});
acceptIsbn("9780140621006").then(() => {
    assert.equal(isbnInput.value, "9780140621006");
    assert.equal(form.submitted, lookup);
    console.log("Scanner ISBN checks passed.");
});
