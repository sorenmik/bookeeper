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
console.log("Scanner ISBN checks passed.");
