# Bookeeper

Bookeeper is a private home library catalog. Household members use separate
sign-ins and share the same collection. Each physical copy can have its own
location, even if several copies share an ISBN.

## Run locally on Linux

From the project folder in a terminal:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
```

Then create the first account and start the application:

```bash
bash create-admin.sh
bash service.sh
```

The setup script prompts for a username and a non-empty password. It only
works before any account exists. The menu shows the current status. Choose
**1** to start Bookeeper on localhost, **2** to stop it, **3** to restart it,
**4** to start it on all network interfaces, or **0** to exit without changes.
Starting or restarting runs `npm ci` first if the scanner packages are missing
or `package-lock.json` has changed since the last install.
Starting on all interfaces makes Bookeeper accessible to other devices on your
LAN; only use it on a trusted network.

To add another household member, run:

```bash
.venv/bin/python -m flask --app bookeeper add-user alice
```

Replace `alice` with the username. Open <http://127.0.0.1:5000> and sign in.
To run Flask in the foreground instead, use:

```bash
.venv/bin/python -m flask --app bookeeper run
```

## Using Bookeeper

### Scanning ISBNs

The camera scanner reads ISBN barcodes and, at the same time, recognises
printed ISBN numbers with OCR (Tesseract.js) for older books without a
barcode. Both engines run in the browser from the local `node_modules` copy.
Only the middle third of the camera image is scanned, and the preview is
cropped to match, so hold the ISBN in the visible band.

After a scan, Bookeeper looks the ISBN up straight away. Distinct sounds (and
vibration on phones) tell you a code was read, the book was found, or the
lookup failed, so you don't have to watch the screen. **Scan an ISBN** on the
front page opens the camera directly.

### Bulk add

**Catalog → Bulk add** adds books unplaced. To add them to a specific shelf,
use **Locations → Bulk add to a shelf** and click the slot first. Then scan
one book after another: each ISBN found is added immediately, and the same
ISBN scanned twice in a row is ignored. Books that could not be found are
listed at the end so you can set them aside for manual entry.

### Wishlist

**Scan to wishlist** opens the camera on the wishlist form. You can also type
an ISBN and choose **Look up ISBN**. The lookup fills in the title and author
and keeps all other book details (summary, genre, format, publisher,
publication date, language, pages and cover), so they move to the catalog
with the book when you choose **Mark bought and move to catalog**.

### Reading

The front page shows the books you are reading now, most recently started
first. A book can be marked **Finished** without a finish date if you don't
remember it; it then counts as read but is left out of the monthly chart.

### TODO list

**TODO** is a shared list where anyone in the household can suggest feature
ideas. Remove an idea once it is done or dropped.

The **Version** label in the footer links to the GitHub repository.

## Backing up and testing

In **Backup**, the first account can save, download, or restore a shared
snapshot and export a CSV catalog. A restore saves the outgoing catalog before
replacing it. The shared snapshot excludes credentials, personal notes, and
private wishlists. For migration to a new instance, create its first account
before restoring a snapshot.

Library data defaults to `instance/library.json`. Set `BOOKEEPER_DATA_DIR`
to use another local folder, accessible only to the account running the app.
The JSON file contains password hashes and the session signing key; keep it
private. If you need to migrate the entire installation (including accounts
and private data), stop Bookeeper and transfer the complete data directory
securely outside the web interface.

To try 200 synthetic copies in a separate catalog, use an empty directory
and create its own test sign-in:

```bash
export BOOKEEPER_DATA_DIR="$PWD/instance/demo"
.venv/bin/python -m flask --app bookeeper seed-demo
.venv/bin/python -m flask --app bookeeper create-first-user
.venv/bin/python -m flask --app bookeeper run --port 5001
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The tests use temporary data folders and do not contact the book databases.

## ISBN lookup

Lookup tries Open Library, then the Libris union catalogue, then Google Books,
then the Royal Danish Library, and uses the first match. Danish ISBNs (978-87)
ask the Royal Danish Library first, since it holds a legal-deposit copy of
every Danish book. Google Books rate-limits keyless requests heavily; set
`BOOKEEPER_GOOGLE_BOOKS_KEY` to a free Google Books API key to make that
fallback reliable.

Later sources fill in fields the first match left blank. Summaries are
chosen in the book's own language when possible, otherwise in Danish or
English, and further sources are asked until such a summary is found.

