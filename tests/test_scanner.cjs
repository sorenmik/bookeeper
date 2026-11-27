const assert = require("assert").strict;
const {readFileSync} = require("fs");
const {join} = require("path");
const {runInNewContext} = require("vm");

const element = {parentElement: {}, addEventListener() {}};
const scanner = readFileSync(join(__dirname, "..", "bookeeper", "static", "scanner.js"), "utf8");
const isbn13 = runInNewContext(`${scanner}\nisbn13`, {
    document: {querySelector: () => element},
    window: {addEventListener() {}},
});

assert.equal(isbn13("9780140621006"), true);
assert.equal(isbn13("9791032300046"), true);

assert.equal(isbn13("1234567890123"), false);
assert.equal(isbn13("9780140621007"), false);
assert.equal(isbn13("978014062100"), false);

const findIsbn = runInNewContext(`${scanner}\nfindIsbn`, {
    document: {querySelector: () => element},
    window: {addEventListener() {}},
});
assert.deepEqual({...findIsbn("Gyldendal 1987\nISBN 87-00-12345-X? no\nISBN: 87-7714-123-7")}, {value: "8777141237", labelled: true});
assert.deepEqual({...findIsbn("9780140621006")}, {value: "9780140621006", labelled: false});
assert.deepEqual({...findIsbn("ISBN 87-7714-123-7 1990")}, {value: "8777141237", labelled: true});
assert.equal(findIsbn("ISBN 87-7714-123-4"), null);
assert.equal(findIsbn("Printed 1990"), null);

const scanBand = runInNewContext(`${scanner}\nscanBand`, {
    document: {querySelector: () => element},
    window: {addEventListener() {}},
});
assert.deepEqual({...scanBand(1280, 720)}, {x: 0, y: 240, width: 1280, height: 240});

const lookup = {};
const form = {submitted: null, requestSubmit(button) { this.submitted = button; }};
const isbnInput = {value: "", form};
const status = {textContent: ""};
const fields = {"#isbn": isbnInput, "#isbn-lookup": lookup, "#scan-status": status};
const acceptIsbn = runInNewContext(`${scanner}\nacceptIsbn`, {
    document: {querySelector: (selector) => fields[selector] || element},
    window: {addEventListener() {}},
    clearTimeout() {},
});
acceptIsbn("9780140621006").then(() => {
    assert.equal(isbnInput.value, "9780140621006");
    assert.equal(form.submitted, lookup);
    console.log("Scanner ISBN checks passed.");
});
