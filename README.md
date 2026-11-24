# Bookeeper

Bookeeper is a private home library catalog. Household members use separate
sign-ins and share the same collection. Each physical copy can have its own
location, even if several copies share an ISBN.

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

The setup script prompts for a username and a password of at least 12
characters. It only works before any account exists. The first account can manage shared
backups. To add another household member, run
`.\.venv\Scripts\python.exe -m flask --app bookeeper add-user alice` in PowerShell,
replacing `alice` with their username. To change a password, run
`.\.venv\Scripts\python.exe -m flask --app bookeeper set-password alice`; this
also signs that account out on every device.

Signing out ends that account's sessions on every device. Sessions also expire
after 7 days without use. After 10 sign-in attempts within 15 minutes for the
same username (or from the same client address), further attempts are refused
until the window passes. The count is kept in memory and resets when Bookeeper restarts.

The menu shows the current status. Choose **1** to start Bookeeper in the
background (with Waitress on `127.0.0.1:5000`), **2** to stop it, **3** to restart it, or **0** to exit without
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

### Phone access over home-network HTTPS

Keep the Flask development server (`flask run`) local. To serve Bookeeper to phones, stop it and
run Waitress on loopback behind an HTTPS reverse proxy on the same machine:

```powershell
$env:BOOKEEPER_PUBLIC_URL = 'https://books.home.arpa'
$env:BOOKEEPER_SECURE_COOKIES = '1'
.\.venv\Scripts\waitress-serve.exe --listen=127.0.0.1:5000 --max-request-body-size=16842752 --call bookeeper:create_app
```

For example, with Caddy on the same machine, point `books.home.arpa` in local
DNS to the host's LAN IP (`192.168.1.20` in this example) and use:

```caddyfile
{
    auto_https disable_redirects
}
books.home.arpa {
    bind 192.168.1.20
    tls internal
    reverse_proxy 127.0.0.1:5000
}
```

Allow inbound HTTPS only from your home subnet. Do not forward a router port.
Install and trust the proxy's local **root certificate** on each device, or
use a certificate already trusted by the devices. Keep the CA private key on
the proxy. The Connect QR code uses `BOOKEEPER_PUBLIC_URL`; use that HTTPS URL
for both signing in and phone scanning. Typing or pasting ISBNs still works
without the camera. The scanner tells you when the camera is open and when it
hasn't found an ISBN yet. **Mirror preview** flips only the displayed image,
not the camera input used for recognition. If the barcode won't scan, tap
**Read printed ISBN**, line up the printed ISBN label and number in the orange
guide, then tap the button again. Text recognition runs in your browser;
camera images are not uploaded. Run `npm ci` to install both scanners locally.

### Access from outside the home network

Prefer a tunnel over a router port-forward: Cloudflare Tunnel with Cloudflare
Access, or Tailscale. Both connect outbound, so no inbound port is opened. Run
Waitress on `127.0.0.1:5000` as above, set `BOOKEEPER_PUBLIC_URL` to the public
HTTPS address and `BOOKEEPER_SECURE_COOKIES=1`. With secure cookies on, the session cookie is named
`__Host-session` and responses include HSTS.

Bookeeper sees every proxied request as coming from `127.0.0.1`. It then limits sign-in
attempts per username only. To also limit them per client address, set
`BOOKEEPER_CLIENT_IP_HEADER` to the header your proxy uses for the visitor's
address, such as `CF-Connecting-IP` for Cloudflare. For a comma-separated header such as `X-Forwarded-For`,
the last entry is used. Only set it when Bookeeper is reachable solely through that proxy; otherwise
visitors could fake the header.

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

Python dependencies are pinned with hashes in `requirements.txt`, generated
from the version ranges in `requirements.in`. To update them, edit
`requirements.in` and run the `uv pip compile` command shown at the top of
`requirements.txt`.

See [todo.md](./todo.md) for the project requirements.
