import csv
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from flask import Blueprint, Flask, Response, abort, flash, g, redirect, render_template, request, send_file, url_for

from .catalog import COPY_LOCATIONS, LOCATION_PARENTS, location_label
from .storage import Library, Store
from .workflows import READING_STATUSES, iso_date

MAX_BACKUP = 16 * 1024 * 1024
SNAPSHOT = re.compile(r"bookeeper-\d{8}T\d{6}-[a-f0-9]{8}\.json")


def validate_backup(data: bytes) -> dict:
    def need(condition: bool) -> None:
        if not condition:
            raise ValueError("The file is not a valid Bookeeper shared-library snapshot.")

    def records(key: str) -> dict:
        table = payload.get(key)
        if not isinstance(table, dict):
            raise ValueError("A backup table is missing.")
        for identifier, record in table.items():
            need(bool(re.fullmatch(r"[0-9a-f]{32}", identifier)) and isinstance(record, dict))
        return table

    def strings(item: dict, *keys: str) -> bool:
        return all(isinstance(item.get(key), str) for key in keys)

    if len(data) > MAX_BACKUP:
        raise ValueError("The snapshot is larger than 16 MiB.")
    payload = json.loads(data)
    if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] != 1:
        raise ValueError("Unsupported backup version.")
    need(set(payload) == {"version", "copies", "locations", "reading", "loans"})
    locations, copies, reading, loans = (records(key) for key in ("locations", "copies", "reading", "loans"))
    for key, item in locations.items():
        need(set(item) <= {"id", "kind", "name", "parent_id", "x", "y"})
        need(strings(item, "id", "kind", "name", "parent_id") and item["id"] == key and bool(item["name"].strip()))
        need(item["kind"] in LOCATION_PARENTS)
        parent_kind = LOCATION_PARENTS[item["kind"]]
        parent = item["parent_id"]
        need(not parent if parent_kind is None else parent in locations and locations[parent].get("kind") == parent_kind)
        for axis in ("x", "y"):
            need(axis not in item or type(item[axis]) is int and 0 <= item[axis] <= 100)
    for key, book in copies.items():
        need(set(book) <= {"id", "title", "author", "series", "isbn", "location_id", "tags", "summary", "genre", "format"})
        need(strings(book, "id", "title", "author", "series", "isbn", "location_id") and book["id"] == key)
        need(bool(book["title"].strip()) and isinstance(book.get("tags"), list))
        need(all(isinstance(tag, str) for tag in book["tags"]))
        need(all(field not in book or isinstance(book[field], str) for field in ("summary", "genre", "format")))
        location = book["location_id"]
        need(not location or location in locations and locations[location]["kind"] in COPY_LOCATIONS)
    for key, item in reading.items():
        need(set(item) == {"status", "dates"})
        need(key in copies and item.get("status") in READING_STATUSES and isinstance(item.get("dates"), list))
        need(all(isinstance(value, str) and bool(iso_date(value)) for value in item["dates"]))
    outstanding = set()
    for item in loans.values():
        need(set(item) == {"direction", "copy_id", "title", "person", "started", "due", "returned"})
        need(strings(item, "direction", "copy_id", "title", "person", "started", "due", "returned"))
        need(item["direction"] in {"lent", "borrowed"})
        copy_id = item["copy_id"]
        if item["direction"] == "borrowed":
            need(not copy_id)
        else:
            need(copy_id in copies or (not copy_id and bool(item["returned"])))
            if not item["returned"]:
                need(copy_id not in outstanding)
                outstanding.add(copy_id)
        iso_date(item["started"])
        for field in ("due", "returned"):
            if item[field]:
                need(iso_date(item[field]) >= item["started"])
    return payload


def snapshot_bytes(library: Library) -> bytes:
    return json.dumps({
        "version": 1, "copies": library["copies"], "locations": library["locations"],
        "reading": library.get("reading", {}), "loans": library.get("loans", {}),
    }, ensure_ascii=False, indent=2).encode("utf-8")


def csv_cell(value: str) -> str:
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def register_backups(app: Flask, store: Store) -> None:
    pages = Blueprint("backups", __name__, url_prefix="/backup")
    directory = store.path.parent / "snapshots"

    @pages.before_request
    def owner_only() -> None:
        if not g.owner:
            abort(403, "Only the first household account can manage library backups.")

    def save_snapshot(library: Library) -> str:
        data = snapshot_bytes(library)
        if len(data) > MAX_BACKUP:
            abort(409, "The shared library is too large for a 16 MiB snapshot.")
        directory.mkdir(exist_ok=True)
        name = f"bookeeper-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}.json"
        pending = None
        try:
            with NamedTemporaryFile(dir=directory, delete=False) as file:
                pending = Path(file.name)
                file.write(data)
            pending.replace(directory / name)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
        return name

    @pages.get("")
    def index() -> str:
        snapshots = sorted((path.name for path in directory.glob("*.json") if SNAPSHOT.fullmatch(path.name)), reverse=True)
        return render_template("backup.html", snapshots=snapshots)

    @pages.post("/snapshot")
    def snapshot() -> Response:
        save_snapshot(store.read())
        flash("Shared library snapshot saved.", "success")
        return redirect(url_for("backups.index"))

    @pages.get("/snapshot/<name>")
    def download(name: str) -> Response:
        if not SNAPSHOT.fullmatch(name) or not (directory / name).is_file():
            abort(404)
        response = send_file(directory / name, mimetype="application/json", as_attachment=True, max_age=0)
        response.cache_control.no_store = True
        return response

    @pages.post("/restore")
    def restore() -> Response:
        if request.form.get("confirm") != "yes":
            abort(400, "Confirm that you want to replace the current shared library.")
        upload = request.files.get("snapshot")
        name = request.form.get("saved", "")
        if upload and not name:
            data = upload.stream.read(MAX_BACKUP + 1)
        elif name and not upload and SNAPSHOT.fullmatch(name) and (directory / name).is_file():
            with (directory / name).open("rb") as file:
                data = file.read(MAX_BACKUP + 1)
        else:
            abort(400, "Choose one saved snapshot or upload one snapshot file.")
        try:
            restored = validate_backup(data)
        except (ValueError, RecursionError) as exc:
            abort(400, f"Could not restore the snapshot: {exc}")
        with store.edit() as library:
            save_snapshot(library)
            library["copies"] = restored["copies"]
            library["locations"] = restored["locations"]
            library["reading"] = restored["reading"]
            library["loans"] = restored["loans"]
        flash("Shared library restored. A snapshot of the previous catalog was saved.", "success")
        return redirect(url_for("backups.index"))

    @pages.get("/catalog.csv")
    def export() -> Response:
        library = store.read()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        fields = ("title", "author", "series", "isbn", "genre", "format", "summary")
        writer.writerow([*fields, "tags", "location"])
        for book in library["copies"].values():
            writer.writerow([csv_cell(book.get(field, "")) for field in fields] + [
                csv_cell(", ".join(book["tags"])),
                csv_cell(location_label(book["location_id"], library["locations"])),
            ])
        return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")), mimetype="text/csv",
                         as_attachment=True, download_name="bookeeper-catalog.csv", max_age=0)

    app.register_blueprint(pages)
