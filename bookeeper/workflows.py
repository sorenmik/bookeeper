from collections import Counter
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

import qrcode
import qrcode.image.svg
from flask import Blueprint, Flask, Response, abort, current_app, flash, g, redirect, render_template, request, url_for

from .catalog import author_sort_key, find_duplicate, fold, normalize_isbn
from .dashboard import activity
from .places import slot_labels
from .storage import BookCopy, Library, Loan, Reading, Store


READING_STATUSES = ("unread", "reading", "finished", "abandoned")


def reading_for(library: Library, copy_id: str) -> Reading:
    return library.get("reading", {}).get(copy_id, {"status": "unread", "dates": []})


def currently_reading(library: Library) -> list[BookCopy]:
    """Copies marked Reading, most recently started first; copies without a start time come last."""
    records = [
        (record.get("status_since", ""), library["copies"][key])
        for key, record in library.get("reading", {}).items()
        if record["status"] == "reading" and key in library["copies"]
    ]
    return [book for _since, book in sorted(records, key=lambda item: item[0], reverse=True)]


def iso_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Use YYYY-MM-DD for dates.")
    return value


def register_workflows(app: Flask, store: Store) -> None:
    pages = Blueprint("household", __name__)

    @pages.post("/copies/<copy_id>/reading")
    def record_reading(copy_id: str) -> Response:
        status = request.form.get("status", "")
        finished = request.form.get("finished", "")
        if status not in READING_STATUSES:
            abort(400, "Choose a reading status.")
        if finished:
            try:
                iso_date(finished)
            except ValueError:
                abort(400, "Enter a valid finish date.")
            if status != "finished" or finished > date.today().isoformat():
                abort(400, "A finish date requires Finished status and cannot be in the future.")
        with store.edit() as library:
            if copy_id not in library["copies"]:
                abort(404)
            record = reading_for(library, copy_id)
            if record["status"] != status:
                record["status_since"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            record["status"] = status
            if finished:
                record["dates"].append(finished)
                record["dates"].sort(reverse=True)
            library.setdefault("reading", {})[copy_id] = record
        flash("Reading history updated.", "success")
        return redirect(url_for("edit_copy", copy_id=copy_id))

    @pages.post("/copies/<copy_id>/note")
    def save_note(copy_id: str) -> Response:
        note = request.form.get("note", "").strip()
        if len(note) > 5000:
            abort(400, "Keep personal notes under 5,000 characters.")
        with store.edit() as library:
            if copy_id not in library["copies"]:
                abort(404)
            notes = library.setdefault("notes", {}).setdefault(g.user, {})
            if note:
                notes[copy_id] = note
            else:
                notes.pop(copy_id, None)
        flash("Personal note saved.", "success")
        return redirect(url_for("edit_copy", copy_id=copy_id))

    @pages.route("/wishlist", methods=["GET", "POST"])
    def wishlist() -> Response | str:
        if request.method == "GET":
            library = store.read()
            books = library.get("wishlists", {}).get(g.user, {})
            duplicates = {key for key, book in books.items() if find_duplicate(book, library["copies"].values())}
            return render_template("wishlist.html", books=books.values(), duplicates=duplicates)
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        isbn = request.form.get("isbn", "").strip()
        if not title or max(len(title), len(author)) > 250:
            abort(400, "Enter a title. Title and author can use up to 250 characters each.")
        if isbn:
            try:
                isbn = normalize_isbn(isbn)
            except ValueError as exc:
                abort(400, str(exc))
        key = uuid4().hex
        with store.edit() as library:
            book = BookCopy(id=key, title=title, author=author, isbn=isbn, series="", tags=[], location_id="")
            library.setdefault("wishlists", {}).setdefault(g.user, {})[key] = book
        flash("Book added to your wishlist.", "success")
        return redirect(url_for("household.wishlist"))

    @pages.post("/wishlist/<key>/<action>")
    def change_wishlist(key: str, action: str) -> Response:
        if action not in {"bought", "remove"}:
            abort(404)
        with store.edit() as library:
            books = library.get("wishlists", {}).get(g.user, {})
            if key not in books:
                abort(404)
            book = books.pop(key)
            if action == "bought":
                copy_id = uuid4().hex
                library["copies"][copy_id] = {**book, "id": copy_id, "added_on": date.today().isoformat()}
        flash("Book moved to the shared catalog." if action == "bought" else "Wishlist entry removed.", "success")
        return redirect(url_for("household.wishlist"))

    @pages.route("/loans", methods=["GET", "POST"])
    def loans() -> Response | str:
        today = date.today().isoformat()
        if request.method == "GET":
            library = store.read()
            entries = library.get("loans", {})
            lent = {loan["copy_id"] for loan in entries.values() if not loan["returned"]}
            return render_template(
                "loans.html", loans=sorted(
                    entries.items(), key=lambda item: (not item[1]["returned"], item[1]["started"]), reverse=True,
                ),
                available=sorted(
                    (copy for copy in library["copies"].values() if copy["id"] not in lent),
                    key=lambda copy: fold(copy["title"]),
                ), today=today, labels=slot_labels(library),
            )
        direction = request.form.get("direction")
        person = request.form.get("person", "").strip()
        started = request.form.get("started", "") or today
        copy_id = request.form.get("copy_id", "")
        title = request.form.get("title", "").strip()
        if direction not in ("lent", "borrowed") or not person or len(person) > 120:
            abort(400, "Choose a loan direction and enter a name up to 120 characters.")
        try:
            iso_date(started)
        except ValueError:
            abort(400, "Enter a valid start date.")
        if started > today:
            abort(400, "The start cannot be in the future.")
        with store.edit() as library:
            entries = library.setdefault("loans", {})
            if direction == "lent":
                if copy_id not in library["copies"]:
                    abort(400, "Choose a copy from the library.")
                if any(entry["copy_id"] == copy_id and not entry["returned"] for entry in entries.values()):
                    abort(409, "This copy is already lent out.")
                title = library["copies"][copy_id]["title"]
            elif not title or len(title) > 250:
                abort(400, "Enter a title up to 250 characters.")
            else:
                copy_id = ""
            entries[uuid4().hex] = Loan(
                direction=direction, copy_id=copy_id, title=title, person=person, started=started, due="", returned=""
            )
        flash("Loan recorded.", "success")
        return redirect(url_for("household.loans"))

    @pages.post("/loans/<key>/return")
    def return_loan(key: str) -> Response:
        with store.edit() as library:
            loan = library.get("loans", {}).get(key)
            if loan is None:
                abort(404)
            if loan["returned"]:
                abort(409, "This loan was already returned.")
            loan["returned"] = date.today().isoformat()
        flash("Book marked returned.", "success")
        return redirect(url_for("household.loans"))

    @pages.get("/stats")
    def stats() -> str:
        library = store.read()
        today = date.today()
        counts = {status: 0 for status in READING_STATUSES}
        genres: Counter[str] = Counter()
        genre_labels: dict[str, str] = {}
        for key, book in library["copies"].items():
            counts[reading_for(library, key)["status"]] += 1
            subjects = {}
            for label in book.get("genre", "").split(","):
                if name := fold(label):
                    subjects[name] = label.strip()
            for name, label in subjects.items():
                genre_labels.setdefault(name, label)
            genres.update(subjects.keys())
        return render_template(
            "stats.html", counts=counts, activity=activity(library, today), year=today.year,
            genres=[(genre_labels[name], count) for name, count in genres.most_common(6)],
        )

    @pages.get("/sorting")
    def sorting() -> str:
        library = store.read()
        order = [field for field in request.args.getlist("by") if field] or ["genre", "author"]
        allowed = ("genre", "format", "author", "series", "title")
        if any(key not in allowed for key in order) or len(order) > len(allowed):
            abort(400, "Choose valid sorting fields.")
        def sort_value(copy: BookCopy, field: str) -> str:
            value = copy.get(field, "")
            return author_sort_key(value) if field == "author" else fold(value)

        copies = sorted(
            library["copies"].values(),
            key=lambda copy: tuple((not value, value) for value in (sort_value(copy, field) for field in order)),
        )
        return render_template(
            "sorting.html", order=order, allowed=allowed, copies=copies, labels=slot_labels(library),
        )

    def public_url() -> str:
        url = current_app.config["PUBLIC_URL"] or request.url_root
        try:
            parts = urlsplit(url)
            if parts.scheme in {"http", "https"} and parts.hostname and not parts.username and len(url) <= 400:
                # Phone browsers only allow camera access on HTTPS pages.
                return parts._replace(scheme="https").geturl()
        except ValueError:
            pass
        abort(400, "Set BOOKEEPER_PUBLIC_URL to a valid HTTPS address.")

    @pages.get("/connect")
    def connect() -> str:
        return render_template("connect.html", public_url=public_url())

    @pages.get("/connect/qr.svg")
    def qr() -> Response:
        image = qrcode.make(public_url(), image_factory=qrcode.image.svg.SvgPathImage)
        return Response(image.to_string(), mimetype="image/svg+xml")

    app.register_blueprint(pages)
