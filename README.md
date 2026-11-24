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

The tests use temporary data folders and do not contact Open Library.
See [todo.md](./todo.md) for the project requirements.
