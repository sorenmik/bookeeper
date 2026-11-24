import os
import re
import secrets
from datetime import UTC, date, datetime
from pathlib import Path
from random import Random
from threading import Lock, Thread
from uuid import uuid4

import click
from filelock import Timeout
from flask import Flask, abort, flash, g, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.wrappers import Response

from .catalog import (
    LookupUnavailable,
    fill_book_metadata,
    find_duplicate,
    lookup_isbn,
    normalize_isbn,
    positive_number,
    search_copies,
)
from .places import UNPLACED, filter_options, mini_map, parse_slot, slot_label, slot_labels, slot_options, valid_filter
from .shelves import register_shelves
from .workflows import READING_STATUSES, reading_for, register_workflows
from .backup import MAX_BACKUP, register_backups
from .dashboard import activity
from .quotes import needs_refresh, quote_for_display, refresh_quote
from .version import build_version
from .storage import BookCopy, Store


def create_app(config: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        DATA_DIR=os.environ.get("BOOKEEPER_DATA_DIR") or app.instance_path,
        PUBLIC_URL=os.environ.get("BOOKEEPER_PUBLIC_URL", ""),
        MAX_CONTENT_LENGTH=32 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("BOOKEEPER_SECURE_COOKIES") == "1",
    )
    if config is not None:
        app.config.update(config)
    store = Store(Path(app.config["DATA_DIR"]).resolve())
    if not app.secret_key:
        key = store.read()["session_key"]
        if not key:
            with store.edit() as library:
                if not library["session_key"]:
                    library["session_key"] = secrets.token_hex(32)
                key = library["session_key"]
        app.secret_key = key

    fallback_hash = generate_password_hash(secrets.token_hex(24))
    version = build_version()

    @app.context_processor
    def inject_version() -> dict[str, str]:
        return {"build_version": version}

    quotes_enabled = bool(app.config.get("QUOTES", os.environ.get("BOOKEEPER_QUOTES") != "0" and not app.testing))
    quote_lookup = Lock()

    def start_quote_refresh(today: date) -> None:
        if not quote_lookup.acquire(blocking=False):
            return

        def work() -> None:
            try:
                refresh_quote(store, today)
            except Exception:
                app.logger.exception("Quote of the day lookup failed")
            finally:
                quote_lookup.release()

        Thread(target=work, daemon=True).start()

    existing_accounts = "An account already exists. Use add-user to create another household sign-in."

    def save_user(username: str, password: str, *, first_only: bool = False) -> None:
        username = username.lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,39}", username):
            raise click.ClickException("Use 1 to 40 letters, digits, dots, dashes or underscores.")
        if not password:
            raise click.ClickException("Enter a password.")
        password_hash = generate_password_hash(password)
        with store.edit() as library:
            if first_only and library["users"]:
                raise click.ClickException(existing_accounts)
            if username in library["users"]:
                raise click.ClickException("That username already exists.")
            library["users"][username] = password_hash
        click.echo(f"Created account: {username}")

    @app.cli.command("add-user")
    @click.argument("username")
    @click.password_option()
    def add_user(username: str, password: str) -> None:
        """Create a household sign-in from the host computer."""
        save_user(username, password)

    @app.cli.command("create-first-user")
    def create_first_user() -> None:
        """Set up the first household sign-in from the host computer."""
        if store.read()["users"]:
            raise click.ClickException(existing_accounts)
        username = click.prompt("Username")
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        save_user(username, password, first_only=True)

    @app.cli.command("seed-demo")
    def seed_demo() -> None:
        """Add 200 synthetic copies to an empty, separate test library."""
        if store.path.parent == Path(app.instance_path).resolve():
            raise click.ClickException("Set BOOKEEPER_DATA_DIR to a separate test directory first.")
        genres = ("Fantasy", "Mystery", "History", "Science Fiction", "Travel")
        rng = Random(200)
        with store.edit() as library:
            if library["copies"]:
                raise click.ClickException("The test catalog must be empty.")
            for index, (adjective, subject) in enumerate((
                (adjective, subject)
                for adjective in ("Amber", "Brass", "Distant", "Hidden", "Lunar", "Quiet", "Silver", "Winter", "Glass", "Velvet")
                for subject in ("Archive", "Atlas", "Beacon", "Cabinet", "Clock", "Compass", "Crossing", "Garden", "Harbor", "Island",
                                "Lantern", "Map", "Orchard", "Observatory", "River", "Shore", "Station", "Valley", "Voyage", "Wood")
            )):
                key = uuid4().hex
                library["copies"][key] = BookCopy(
                    id=key, title=f"The {adjective} {subject}", author=f"Sample Author {index % 27 + 1}",
                    series="", isbn="", tags=["test-book"], location_id="",
                    genre=rng.choice(genres), format=rng.choice(("Paperback", "Hardcover")),
                    added_on=date.today().isoformat(),
                )
        click.echo("Added 200 sample books.")

    @app.before_request
    def load_user() -> Response | None:
        session.setdefault("csrf", secrets.token_urlsafe(32))
        g.user = session.get("user")
        users = store.read()["users"] if g.user else {}
        g.owner = bool(g.user and g.user == next(iter(users), None))
        if request.method == "POST":
            if request.endpoint == "backups.restore" and g.owner:
                request.max_content_length = MAX_BACKUP + 64 * 1024
            token = request.form.get("csrf", "").encode()
            if not secrets.compare_digest(token, session["csrf"].encode()):
                abort(400, "This form expired or is invalid. Go back, reload the page and try again.")
        if g.user and g.user not in users:
            session.clear()
            g.user = None
        if not g.user and request.endpoint not in {None, "login", "static"}:
            return redirect(url_for("login"))
        return None

    @app.after_request
    def prevent_private_caching(response: Response) -> Response:
        if request.endpoint in {"home", "household.wishlist", "edit_copy", "login"}:
            response.cache_control.no_store = True
        return response

    @app.errorhandler(Timeout)
    def library_busy(_error: Timeout) -> tuple[str, int]:
        return render_template("error.html", message="The library is busy. Please try again."), 503

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def form_error(error: HTTPException) -> tuple[str, int]:
        return render_template("error.html", message=error.description), error.code or 500

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Response | str:
        if g.user:
            return redirect(url_for("home"))
        users = store.read()["users"]
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            password_hash = users.get(username)
            if check_password_hash(password_hash or fallback_hash, password):
                if password_hash:
                    session.clear()
                    session["user"] = username
                    return redirect(url_for("home"))
            flash("Username or password is incorrect.", "error")
        return render_template("login.html", has_users=bool(users))

    @app.post("/logout")
    def logout() -> Response:
        session.clear()
        return redirect(url_for("login"))

    @app.get("/scanner.js")
    def scanner() -> Response:
        return send_from_directory(
            Path(app.root_path).parent / "node_modules" / "html5-qrcode",
            "html5-qrcode.min.js",
        )

    @app.get("/scanner-ocr/<filename>")
    def scanner_ocr(filename: str) -> Response:
        assets = {
            "tesseract.min.js": Path("tesseract.js") / "dist",
            "worker.min.js": Path("tesseract.js") / "dist",
            "tesseract-core-lstm.wasm.js": Path("tesseract.js-core"),
            "eng.traineddata.gz": Path("@tesseract.js-data") / "eng" / "4.0.0_best_int",
        }
        directory = assets.get(filename)
        if directory is None:
            abort(404)
        return send_from_directory(Path(app.root_path).parent / "node_modules" / directory, filename)

    @app.get("/")
    def home() -> str:
        if any(field in request.args for field in ("q", "location", "reading", "loan")):
            return catalog()
        library = store.read()
        today = date.today()
        if quotes_enabled and needs_refresh(library, today, datetime.now(UTC)):
            start_quote_refresh(today)
        books = list(reversed(library["copies"].values()))
        wishlist = list(reversed(library.get("wishlists", {}).get(g.user, {}).values()))
        return render_template(
            "home.html",
            featured=next((book for book in books if book.get("cover_id")), books[0] if books else None),
            quote=quote_for_display(library) if quotes_enabled else None,
            recent=books[:8],
            wishlist=wishlist[:4],
            wishlist_count=len(wishlist),
            activity=activity(library, date.today()),
        )

    @app.get("/catalog")
    def catalog() -> str:
        library = store.read()
        query = request.args.get("q", "")
        selected = request.args.get("location", "")
        reading_status = request.args.get("reading", "")
        loan_status = request.args.get("loan", "")
        lent = {loan["copy_id"] for loan in library.get("loans", {}).values() if not loan["returned"]}
        if selected and not valid_filter(selected, library):
            abort(404, "That location does not exist.")
        return render_template(
            "catalog.html",
            copies=search_copies(
                (
                    copy for copy in library["copies"].values()
                    if (not reading_status or reading_for(library, copy["id"])["status"] == reading_status)
                    and (not loan_status or loan_status == ("lent" if copy["id"] in lent else "available"))
                ), library["units"], query, selected
            ),
            labels=slot_labels(library),
            location_options=filter_options(library),
            unplaced=UNPLACED,
            query=query,
            selected=selected,
            reading=library.get("reading", {}),
            reading_status=reading_status,
            reading_statuses=READING_STATUSES,
            loan_status=loan_status,
        )

    class CopyFormError(ValueError):
        pass

    @app.route("/copies/new", defaults={"copy_id": None}, methods=["GET", "POST"])
    @app.route("/copies/<copy_id>/edit", methods=["GET", "POST"])
    def edit_copy(copy_id: str | None) -> Response | str | tuple[str, int]:
        library = store.read()
        if copy_id is not None and copy_id not in library["copies"]:
            abort(404)
        existing = library["copies"].get(copy_id) if copy_id is not None else None
        book = existing or BookCopy(id="", title="", author="", series="", isbn="", tags=[], location_id="")
        error = ""
        duplicate = False
        notice = ""
        if request.method == "POST":
            book = BookCopy(
                id=copy_id or uuid4().hex,
                title=request.form.get("title", "").strip(),
                author=request.form.get("author", "").strip(),
                series=request.form.get("series", "").strip(),
                isbn=request.form.get("isbn", "").strip(),
                tags=list(dict.fromkeys(tag.strip() for tag in request.form.get("tags", "").split(",") if tag.strip())),
                location_id=request.form.get("location_id", ""),
                summary=request.form.get("summary", "").strip(),
                genre=request.form.get("genre", "").strip(),
                format=request.form.get("format", "").strip(),
                publisher=request.form.get("publisher", "").strip(),
                publication_date=request.form.get("publication_date", "").strip(),
                language=request.form.get("language", "").strip(),
                pages=positive_number(request.form.get("pages", "")),
                cover_id=positive_number(request.form.get("cover_id", "")),
            )
            action = request.form.get("action", "save")
            try:
                if action == "lookup":
                    if not book["isbn"]:
                        raise CopyFormError("Enter an ISBN before looking up the book.")
                elif not book["title"]:
                    raise CopyFormError("Enter a title.")
                if any(len(field) > 250 for field in (book["title"], book["author"], book["series"], book["publisher"])):
                    raise CopyFormError("Title, author, series and publisher must each be at most 250 characters.")
                if len(book["summary"]) > 5000:
                    raise CopyFormError("Summary is limited to 5,000 characters.")
                if any(len(field) > 80 for field in (book["genre"], book["format"], book["publication_date"], book["language"])):
                    raise CopyFormError("Genre, format, publication date and language are limited to 80 characters each.")
                if request.form.get("pages", "").strip() and book["pages"] is None:
                    raise CopyFormError("Enter a positive whole number for pages, or leave it blank.")
                if request.form.get("cover_id", "").strip() and book["cover_id"] is None:
                    raise CopyFormError("Enter a positive Open Library cover ID, or leave it blank.")
                if len(book["tags"]) > 30 or any(len(tag) > 60 for tag in book["tags"]):
                    raise CopyFormError("Use at most 30 tags, each at most 60 characters.")
                if book["isbn"]:
                    try:
                        book["isbn"] = normalize_isbn(book["isbn"])
                    except ValueError as exc:
                        raise CopyFormError(str(exc)) from exc
                if action == "lookup":
                    try:
                        match = lookup_isbn(book["isbn"])
                    except LookupUnavailable:
                        notice = "Open Library is unavailable right now. You can still enter the details manually."
                    else:
                        if match is None:
                            notice = "Open Library did not find this ISBN. You can enter the details manually."
                        else:
                            book = fill_book_metadata(book, match)
                            notice = "ISBN found. Review the details and fill in anything Open Library left blank before saving."
                else:
                    with store.edit() as latest:
                        if copy_id is not None and copy_id not in latest["copies"]:
                            abort(404)
                        if book["location_id"] and parse_slot(book["location_id"], latest["units"]) is None:
                            raise CopyFormError("Choose a slot from the list, or leave the copy unplaced.")
                        if copy_id is None and request.form.get("allow_duplicate") != "yes":
                            if find_duplicate(book, latest["copies"].values()):
                                duplicate = True
                                raise CopyFormError("A matching book is already in the library.")
                        if copy_id is None:
                            book["added_on"] = date.today().isoformat()
                        elif added_on := latest["copies"][copy_id].get("added_on"):
                            book["added_on"] = added_on
                        latest["copies"][book["id"]] = book
            except CopyFormError as exc:
                error = str(exc)
                library = store.read()
            else:
                if action != "lookup":
                    flash("Copy updated." if copy_id is not None else "Copy added.", "success")
                    return redirect(url_for("catalog"))
        page = render_template(
            "copy.html",
            book=book,
            editing=copy_id is not None,
            error=error,
            duplicate=duplicate,
            notice=notice,
            reading=reading_for(library, copy_id or ""),
            reading_statuses=READING_STATUSES,
            note=library.get("notes", {}).get(g.user, {}).get(copy_id or "", ""),
            matches=(
                [
                    (item["id"], slot_label(item["location_id"], library) or "Unplaced")
                    for item in library["copies"].values() if item["isbn"] == book["isbn"]
                ] if book["isbn"] and request.form.get("action") == "lookup" else []
            ),
            slot_groups=slot_options(library),
            location_path=slot_label(book["location_id"], library),
            mini_map=mini_map(book["location_id"], library),
        )
        if error:
            return page, 409 if duplicate else 400
        return page

    @app.post("/copies/<copy_id>/delete")
    def delete_copy(copy_id: str) -> Response:
        with store.edit() as library:
            if copy_id not in library["copies"]:
                abort(404)
            if any(
                loan["copy_id"] == copy_id and not loan["returned"]
                for loan in library.get("loans", {}).values()
            ):
                abort(409, "Mark this copy returned before removing it.")
            del library["copies"][copy_id]
            library.get("reading", {}).pop(copy_id, None)
            for personal_notes in library.get("notes", {}).values():
                personal_notes.pop(copy_id, None)
            for loan in library.get("loans", {}).values():
                if loan["copy_id"] == copy_id:
                    loan["copy_id"] = ""
        flash("Copy removed.", "success")
        return redirect(url_for("catalog"))

    register_shelves(app, store)
    register_workflows(app, store)
    register_backups(app, store)
    return app
