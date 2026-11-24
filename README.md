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
**1** to start Bookeeper in the background, **2** to stop it, **3** to restart
it, or **0** to exit without changes.

To add another household member, run:

```bash
.venv/bin/python -m flask --app bookeeper add-user alice
```

Replace `alice` with the username. Open <http://127.0.0.1:5000> and sign in.
To run Flask in the foreground instead, use:

```bash
.venv/bin/python -m flask --app bookeeper run
```

## Run locally on Windows

From the project folder in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
```

Then open Git Bash in the project folder and run:

```bash
bash create-admin.sh
bash service.sh
```

The setup script prompts for a username and a non-empty password.
It only works before any account exists. The first account can manage shared
backups. To add another household member, run
`.\.venv\Scripts\python.exe -m flask --app bookeeper add-user alice` in PowerShell,
replacing `alice` with their username.

The menu shows the current status. Choose **1** to start Bookeeper in the
background, **2** to stop it, **3** to restart it, or **0** to exit without
changes. Restart waits for the current server to stop before starting a new
one. If it is already stopped, restart just starts it. The menu only controls
the server it started. If you prefer to run Flask in the foreground, use
`.\.venv\Scripts\python.exe -m flask --app bookeeper run` in PowerShell instead.

Open <http://127.0.0.1:5000> and sign in. The home page has catalog search,
recent additions, quick actions, a private wishlist preview, and a monthly
activity chart. The Bookend logo, outline icons, and bookshelf illustration are served
locally. On smaller screens, tap **Menu** to open navigation and theme settings.
The default **Simple** theme uses near-black surfaces with orange highlights. The Theme
menu also offers **Burnley, 1657**: a candlelit monastery scriptorium with parchment folios,
blackletter headings, illuminated capitals, and monks reading over their ale, and **Future**:
the Intergalactic Lending Library, with frosted glass panels over a nebula-and-planets backdrop, a
ship's-log ticker of library jokes, and the occasional saucer towing an overdue notice. **Summer evening**
puts you under an apple tree at sunset: a warm glass reading page over a golden sky with the moon
rising, drifting fireflies, and apple-red buttons. **Pirate tavern** is night-time pixel art in blue and
orange: an animated harbour backdrop, wood-framed panels, a custom bitmap font, a crackling fireplace,
and an adventure-game verb bar with an inventory. Animations
stop when your device asks for reduced motion. Copies added before date
tracking began still count in the catalog total but not in the chart’s “Added”
series. The **Stats** tab also shows the reading-status breakdown and top genres.
Under **Locations**, use **Add storage or unit** to create a storage (such as a
kitchen), then add cabinets (n×m grid of slots), shelves, boxes or piles and drag
them into place on the side-view map. Select a slot to see, rename or bulk-move its
books; the last move can be undone. Search results show each book’s full path. Use **Add wish** on the wishlist to open its
entry form. Use **Add a copy** to
enter a book manually or look up its ISBN. When Open Library has the data,
lookup fills blank fields for title, author, summary, genre (suggested from
subjects), format, pages, publisher, publication date, language, and cover.
Review the details before saving. A copy can also be left unassigned.

The home page also shows a **Quote of the day** taken from a book that is currently in
the catalog. The first visit on a new day looks one up in the background (the card appears
on the next page load) and the same quote is kept all day. Quotes come from
[Wikiquote](https://en.wikiquote.org) (CC BY-SA), falling back to a sentence from the book's
text on [Project Gutenberg](https://www.gutenberg.org) for public-domain books. Only the
title and author are sent. Books without a match are skipped. If no book has a quote (or the
lookups fail), the card shows one of 200 famous quotes bundled in `bookeeper/fallback_quotes.json`
(taken from Wikiquote's quote-of-the-day archive, author only, cycling daily). The quote is kept outside backups and snapshots. Set
`BOOKEEPER_QUOTES=0` to turn the feature off.

### Backing up and testing

In **Backup**, the first account can save, download, or restore a shared
snapshot and export a CSV catalog. A restore saves the outgoing catalog before
replacing it. The shared snapshot excludes credentials, personal notes, and
private wishlists. For migration to a new instance, create its first account
before restoring a snapshot.

Library data defaults to `instance\library.json`. Set `BOOKEEPER_DATA_DIR`
to use another local folder, accessible only to the account running the app.
The JSON file contains password hashes and the session signing key; keep it
private. If you need to migrate the entire installation (including accounts
and private data), stop Bookeeper and transfer the complete data directory
securely outside the web interface.

To try 200 synthetic copies in a separate catalog, use an empty directory
and create its own test sign-in:

```powershell
$env:BOOKEEPER_DATA_DIR = Join-Path $PWD 'instance\demo'
.\.venv\Scripts\python.exe -m flask --app bookeeper seed-demo
.\.venv\Scripts\python.exe -m flask --app bookeeper create-first-user
.\.venv\Scripts\python.exe -m flask --app bookeeper run --port 5001
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests use temporary data folders and do not contact Open Library.
See [todo.md](./todo.md) for the project requirements.
