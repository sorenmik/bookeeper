const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const {test} = require("node:test");
const {runInNewContext} = require("node:vm");

const element = {parentElement: {}, addEventListener() {}};
const scanner = readFileSync(join(__dirname, "..", "bookeeper", "static", "scanner.js"), "utf8");
const printedIsbn = runInNewContext(`${scanner}\nprintedIsbn`, {
    document: {querySelector: () => element},
    window: {addEventListener() {}},
});

test("printed ISBNs use the same canonical ISBN-13 as barcode scans", () => {
    assert.equal(printedIsbn("ISBN 1-56044-661-7"), "9781560446613");
    assert.equal(printedIsbn("ISBN-13: 978-1-56044-661-3"), "9781560446613");
    assert.equal(printedIsbn("ISBN-10: 080442957X"), "9780804429573");
});

test("printed ISBNs still validate when OCR reads separators as periods", () => {
    assert.equal(printedIsbn("ISBN 1-56044.661.7"), "9781560446613");
    assert.equal(printedIsbn("ISBN 1-56044.661.8"), null);
});

test("OCR does not accept a price code, invalid checksum, or conflicting ISBN labels", () => {
    assert.equal(printedIsbn("ISBN 50695"), null);
    assert.equal(printedIsbn("9781560446613"), null);
    assert.equal(printedIsbn("ISBN 9781560446614"), null);
    assert.equal(printedIsbn("ISBN 9781560446613\nISBN 9780141439518"), null);
});
