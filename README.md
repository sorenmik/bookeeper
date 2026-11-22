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
Dark mode uses near-black surfaces with orange highlights; the Theme
menu also offers a warm light mode or follows your system setting. Copies added before date
tracking began still count in the catalog total but not in the chart’s “Added”
series. The **Stats** tab also shows the reading-status breakdown and top genres.
Under **Locations**, use **Add location** to create a room, then a bookcase
and shelf or a standalone pile. Use **Add wish** on the wishlist to open its
entry form. Use **Add a copy** to
enter a book manually or look up its ISBN. When Open Library has the data,
lookup fills blank fields for title, author, summary, genre (suggested from
subjects), format, pages, publisher, publication date, language, and cover.
Review the details before saving. A copy can also be left unassigned.

### Phone access over home-network HTTPS

Keep the development server local. To serve Bookeeper to phones, stop it and
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
without the camera. The npm package provides a locally served scanner.

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
