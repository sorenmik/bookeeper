# Bookeeper

Bookeeper is a private home library catalog. Household members use separate
sign-ins and share the same collection. Each physical copy can have its own
location, even if several copies share an ISBN.

## Run locally on Windows

From the project folder in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app bookeeper add-user alice
```

The `add-user` command prompts for a password. Run it once per household
member.

Then open Git Bash in the project folder and run:

```bash
bash service.sh
```

The menu shows the current status. Choose **1** to start Bookeeper in the
background, **2** to stop it, or **0** to exit without changes. Run the same
command again to check status or stop it. The menu only controls the server it
started. If you prefer to run Flask in the foreground, use
`.\.venv\Scripts\python.exe -m flask --app bookeeper run` in PowerShell instead.

Open <http://127.0.0.1:5000> and sign in. Under **Locations**, add a
room, then a bookcase and shelf or a standalone pile. Use **Add a copy** to
enter a book manually or use its ISBN to fill in the title and author from Open
Library. A copy can also be left unassigned.

The development server is intended for local testing only. To make Bookeeper
available to phones, use a production WSGI server behind a trusted HTTPS
endpoint on the home network. Set `BOOKEEPER_SECURE_COOKIES=1` for that HTTPS
deployment. Do not expose the development server or forward its port to the Internet.

Library data defaults to `instance\library.json`. Set `BOOKEEPER_DATA_DIR`
to use another local folder, accessible only to the account running the app.
The JSON file contains password hashes and the session signing key; keep it
private. To copy it for migration before the in-app backup workflow is ready,
stop the app first.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests use temporary data folders and do not contact Open Library.
See [todo.md](./todo.md) for features beyond the initial catalog milestone.
