import os
import re
import secrets
from pathlib import Path
from uuid import uuid4

import click
from filelock import Timeout
from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.wrappers import Response

from .catalog import (
    COPY_LOCATIONS,
    LOCATION_PARENTS,
    LookupUnavailable,
    find_duplicate,
    fold,
    location_label,
    lookup_isbn,
    normalize_isbn,
    search_copies,
)
from .storage import BookCopy, LocationKind, Store


def create_app(config: dict[str, object] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        DATA_DIR=os.environ.get("BOOKEEPER_DATA_DIR") or app.instance_path,
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

    @app.before_request
    def load_user() -> Response | None:
        session.setdefault("csrf", secrets.token_urlsafe(32))
        if request.method == "POST":
            token = request.form.get("csrf", "").encode()
            if not secrets.compare_digest(token, session["csrf"].encode()):
                abort(400, "This form expired or is invalid. Go back, reload the page and try again.")
        g.user = session.get("user")
        if g.user and g.user not in store.read()["users"]:
            session.clear()
            g.user = None
        if not g.user and request.endpoint not in {None, "login", "static"}:
            return redirect(url_for("login"))
        return None

    @app.errorhandler(Timeout)
    def library_busy(_error: Timeout) -> tuple[str, int]:
        return render_template("error.html", message="The library is busy. Please try again."), 503

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def form_error(error: HTTPException) -> tuple[str, int]:
        return render_template("error.html", message=error.description), error.code or 500

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Response | str:
        if g.user:
            return redirect(url_for("catalog"))
        users = store.read()["users"]
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            password_hash = users.get(username)
            if check_password_hash(password_hash or fallback_hash, password):
                if password_hash:
                    session.clear()
                    session["user"] = username
                    return redirect(url_for("catalog"))
            flash("Username or password is incorrect.", "error")
        return render_template("login.html", has_users=bool(users))

    @app.post("/logout")
    def logout() -> Response:
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def catalog() -> str:
        library = store.read()
        locations = library["locations"]
        query = request.args.get("q", "")
        selected = request.args.get("location", "")
        if selected and selected not in locations:
            abort(404, "That location does not exist.")
        return render_template(
            "catalog.html",
            copies=search_copies(library["copies"].values(), locations, query, selected),
            labels={key: location_label(key, locations) for key in locations},
            query=query,
            selected=selected,
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
            )
            action = request.form.get("action", "save")
            try:
                if action == "lookup":
                    if not book["isbn"]:
                        raise CopyFormError("Enter an ISBN before looking up the book.")
                elif not book["title"]:
                    raise CopyFormError("Enter a title.")
                if any(len(field) > 250 for field in (book["title"], book["author"], book["series"])):
                    raise CopyFormError("Title, author and series must each be at most 250 characters.")
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
                            title, author = match
                            book["title"] = book["title"] or title
                            book["author"] = book["author"] or author
                            notice = "ISBN found. Review the details and save the copy when ready."
                else:
                    with store.edit() as latest:
                        if copy_id is not None and copy_id not in latest["copies"]:
                            abort(404)
                        if book["location_id"]:
                            location = latest["locations"].get(book["location_id"])
                            if location is None or location["kind"] not in COPY_LOCATIONS:
                                raise CopyFormError("Choose a shelf or pile, or leave the copy unassigned.")
                        if copy_id is None and request.form.get("allow_duplicate") != "yes":
                            if find_duplicate(book, latest["copies"].values()):
                                duplicate = True
                                raise CopyFormError("A matching book is already in the library.")
                        latest["copies"][book["id"]] = book
            except CopyFormError as exc:
                error = str(exc)
                library = store.read()
            else:
                if action != "lookup":
                    flash("Copy updated." if copy_id is not None else "Copy added.", "success")
                    return redirect(url_for("catalog"))
        locations = library["locations"]
        page = render_template(
            "copy.html",
            book=book,
            editing=copy_id is not None,
            error=error,
            duplicate=duplicate,
            notice=notice,
            labels={
                key: location_label(key, locations)
                for key, location in locations.items()
                if location["kind"] in COPY_LOCATIONS
            },
        )
        if error:
            return page, 409 if duplicate else 400
        return page

    @app.post("/copies/<copy_id>/delete")
    def delete_copy(copy_id: str) -> Response:
        with store.edit() as library:
            if copy_id not in library["copies"]:
                abort(404)
            del library["copies"][copy_id]
        flash("Copy removed.", "success")
        return redirect(url_for("catalog"))

    @app.get("/locations")
    def locations() -> str:
        entries = store.read()["locations"]
        return render_template(
            "locations.html",
            locations=entries,
            labels={key: location_label(key, entries) for key in entries},
        )

    @app.post("/locations")
    def add_location() -> Response:
        kind: LocationKind
        match request.form.get("kind"):
            case "room" | "bookcase" | "shelf" | "pile" as kind:
                pass
            case _:
                abort(400, "Choose a valid location type.")
        name = request.form.get("name", "").strip()
        parent_id = request.form.get("parent_id", "")
        if not name or len(name) > 80:
            abort(400, "Location names must be between 1 and 80 characters.")
        with store.edit() as library:
            entries = library["locations"]
            parent = entries.get(parent_id) if parent_id else None
            parent_kind = LOCATION_PARENTS[kind]
            if parent_kind is None:
                if parent_id:
                    abort(400, "Rooms cannot have a parent location.")
            elif parent is None or parent["kind"] != parent_kind:
                abort(400, f"A {kind} must be inside a {parent_kind}.")
            if any(item["parent_id"] == parent_id and fold(item["name"]) == fold(name) for item in entries.values()):
                abort(409, "A location with that name already exists here.")
            key = uuid4().hex
            entries[key] = {"id": key, "kind": kind, "name": name, "parent_id": parent_id}
        flash(f"{kind.title()} added.", "success")
        return redirect(url_for("locations"))

    @app.post("/locations/<location_id>/delete")
    def delete_location(location_id: str) -> Response:
        with store.edit() as library:
            entries = library["locations"]
            if location_id not in entries:
                abort(404)
            if any(item["parent_id"] == location_id for item in entries.values()) or any(
                book["location_id"] == location_id for book in library["copies"].values()
            ):
                abort(409, "This location is not empty. Move its books or remove its child locations first.")
            del entries[location_id]
        flash("Location removed.", "success")
        return redirect(url_for("locations"))

    return app
