from uuid import uuid4

from flask import Flask, Response, abort, flash, redirect, render_template, request, url_for

from .catalog import fold
from .places import (
    COLOR_PATTERN, DEFAULT_CAPACITY, MAX_SIDE, SEPARATOR, SINGLE_SLOT, UNIT_KINDS, UNPLACED, fit_size, in_location,
    ink_for, parse_slot,
    slot_label, slot_counts, slot_name, slot_options, sorted_units, unit_label, unit_slots, valid_filter,
)
from .storage import Library, Store, Unit

MAX_POSITION = 199
WRAP_AT = 24


def clean_name(value: str, what: str) -> str:
    value = value.strip()
    if not value or len(value) > 80:
        abort(400, f"{what} names must be between 1 and 80 characters.")
    return value


def whole(value: str, low: int, high: int, what: str) -> int:
    if value.strip().isascii() and value.strip().isdecimal() and low <= int(value) <= high:
        return int(value)
    abort(400, f"{what} must be a whole number from {low} to {high}.")


def local_path(value: str, fallback: str) -> str:
    return value if value.startswith("/") and not value.startswith(("//", "/\\")) else fallback


def free_position(library: Library, storage_id: str, cols: int) -> tuple[int, int]:
    others = [unit for unit in library["units"].values() if unit["storage_id"] == storage_id]
    if not others:
        return 0, 0
    x = max(unit["x"] + max(unit["cols"], 2) for unit in others) + 1
    if x + cols <= WRAP_AT:
        return x, 0
    return 0, max(unit["y"] + unit["rows"] for unit in others) + 2


def reassign_orphans(library: Library, target: str) -> int:
    """Give books whose slot no longer exists a new slot, or leave them unplaced."""
    if target and parse_slot(target, library["units"]) is None:
        abort(400, "Choose an existing slot for the displaced books, or leave them unplaced.")
    count = 0
    for book in library["copies"].values():
        if book["location_id"] and parse_slot(book["location_id"], library["units"]) is None:
            book["location_id"] = target
            count += 1
    return count


def books_note(count: int) -> str:
    return f" {count} {'book was' if count == 1 else 'books were'} moved." if count else ""


def register_shelves(app: Flask, store: Store) -> None:
    def storage_or_404(library: Library, key: str):
        if key not in library["storages"]:
            abort(404)
        return library["storages"][key]

    def unit_or_404(library: Library, key: str) -> Unit:
        if key not in library["units"]:
            abort(404)
        return library["units"][key]

    def unit_names_taken(library: Library, storage_id: str, name: str, ignore: str = "") -> bool:
        return any(
            unit["storage_id"] == storage_id and unit["id"] != ignore and fold(unit["name"]) == fold(name)
            for unit in library["units"].values()
        )

    @app.get("/locations")
    def locations() -> str:
        library = store.read()
        highlight = request.args.get("slot", "")
        if highlight and parse_slot(highlight, library["units"]) is None:
            abort(404, "That slot does not exist.")
        counts = slot_counts(library)
        capacity = library.get("slot_capacity", DEFAULT_CAPACITY)
        zones = []
        for storage in sorted(library["storages"].values(), key=lambda item: fold(item["name"])):
            units = []
            for unit in sorted_units(library):
                if unit["storage_id"] != storage["id"]:
                    continue
                grid = [
                    [
                        {"id": slot, "name": slot_name(unit, row, col), "count": counts[slot]}
                        for slot, r, col in unit_slots(unit) if r == row
                    ]
                    for row in range(1, unit["rows"] + 1)
                ]
                units.append({
                    **unit, "grid": grid, "ink": ink_for(unit.get("color", "")),
                    "total": sum(cell["count"] for line in grid for cell in line),
                })
            zones.append({
                **storage, "units": units,
                "height": max((unit["y"] + unit["rows"] for unit in units), default=0) + 1,
            })
        return render_template(
            "locations.html",
            zones=zones,
            capacity=capacity,
            highlight=highlight,
            unplaced_count=sum(1 for book in library["copies"].values() if not book["location_id"]),
            unplaced=UNPLACED,
            unit_kinds=UNIT_KINDS,
            storages=sorted(library["storages"].values(), key=lambda item: fold(item["name"])),
            last_move=library.get("last_move"),
            max_side=MAX_SIDE,
            single_slot=SINGLE_SLOT,
        )

    @app.post("/locations/capacity")
    def set_capacity() -> Response:
        value = whole(request.form.get("capacity", ""), 1, 9999, "Capacity")
        with store.edit() as library:
            library["slot_capacity"] = value
        flash("Slots now look full at {} books.".format(value), "success")
        return redirect(url_for("locations"))

    @app.post("/storages")
    def add_storage() -> Response:
        name = clean_name(request.form.get("name", ""), "Storage")
        with store.edit() as library:
            if any(fold(item["name"]) == fold(name) for item in library["storages"].values()):
                abort(409, "A storage with that name already exists.")
            key = uuid4().hex
            library["storages"][key] = {"id": key, "name": name}
        flash("Storage added.", "success")
        return redirect(url_for("locations"))

    @app.route("/storages/<key>", methods=["GET", "POST"])
    def edit_storage(key: str) -> Response | str:
        library = store.read()
        storage = storage_or_404(library, key)
        if request.method == "GET":
            return render_template(
                "storage.html", storage=storage,
                slot_groups=[
                    group for group in slot_options(library) if not group[0].startswith(storage["name"] + SEPARATOR)
                ],
                units=[unit for unit in sorted_units(library) if unit["storage_id"] == key],
                book_count=sum(
                    1 for book in library["copies"].values() if in_location(book["location_id"], key, library["units"])
                ),
            )
        name = clean_name(request.form.get("name", ""), "Storage")
        with store.edit() as library:
            storage_or_404(library, key)
            if any(item["id"] != key and fold(item["name"]) == fold(name) for item in library["storages"].values()):
                abort(409, "A storage with that name already exists.")
            library["storages"][key]["name"] = name
        flash("Storage renamed.", "success")
        return redirect(url_for("locations"))

    @app.post("/storages/<key>/delete")
    def delete_storage(key: str) -> Response:
        with store.edit() as library:
            storage_or_404(library, key)
            del library["storages"][key]
            for unit_id in [item["id"] for item in library["units"].values() if item["storage_id"] == key]:
                del library["units"][unit_id]
            moved = reassign_orphans(library, request.form.get("target", ""))
        flash("Storage removed with its units." + books_note(moved), "success")
        return redirect(url_for("locations"))

    @app.post("/units")
    def add_unit() -> Response:
        kind = request.form.get("kind", "")
        if kind not in UNIT_KINDS:
            abort(400, "Choose a valid unit type.")
        name = clean_name(request.form.get("name", ""), "Unit")
        cols = whole(request.form.get("cols", "1") or "1", 1, MAX_SIDE, "Columns")
        rows = whole(request.form.get("rows", "1") or "1", 1, MAX_SIDE, "Rows")
        cols, rows = fit_size(kind, cols, rows)
        with store.edit() as library:
            storage_id = request.form.get("storage_id", "")
            storage_or_404(library, storage_id)
            if unit_names_taken(library, storage_id, name):
                abort(409, "A unit with that name already exists in this storage.")
            key = uuid4().hex
            x, y = free_position(library, storage_id, cols)
            unit: Unit = {
                "id": key, "storage_id": storage_id, "kind": kind, "name": name,
                "cols": cols, "rows": rows, "x": x, "y": y, "slot_names": {},
            }
            library["units"][key] = unit
        flash(f"{UNIT_KINDS[kind]} added.", "success")
        return redirect(url_for("locations", slot=f"{key}:1:1"))

    @app.route("/units/<key>", methods=["GET", "POST"])
    def edit_unit(key: str) -> Response | str:
        library = store.read()
        unit = unit_or_404(library, key)
        if request.method == "GET":
            counts = slot_counts(library)
            return render_template(
                "unit.html", unit=unit, label=unit_label(unit, library), kinds=UNIT_KINDS,
                slot_groups=[group for group in slot_options(library) if group[0] != unit_label(unit, library)],
                book_count=sum(counts[slot] for slot, _row, _col in unit_slots(unit)),
                max_side=MAX_SIDE, single_slot=SINGLE_SLOT,
            )
        name = clean_name(request.form.get("name", ""), "Unit")
        with store.edit() as library:
            unit = unit_or_404(library, key)
            if unit_names_taken(library, unit["storage_id"], name, key):
                abort(409, "A unit with that name already exists in this storage.")
            cols = whole(request.form.get("cols", "1") or "1", 1, MAX_SIDE, "Columns")
            rows = whole(request.form.get("rows", "1") or "1", 1, MAX_SIDE, "Rows")
            unit["name"] = name
            color = request.form.get("color", "").lower()
            if request.form.get("default_color") or not color:
                unit.pop("color", None)
            elif COLOR_PATTERN.fullmatch(color):
                unit["color"] = color
            else:
                abort(400, "Choose a valid colour.")
            unit["cols"], unit["rows"] = fit_size(unit["kind"], cols, rows)
            unit["slot_names"] = {
                slot: label for slot, label in unit["slot_names"].items()
                if int(slot.split(":")[0]) <= unit["rows"] and int(slot.split(":")[1]) <= unit["cols"]
            }
            moved = reassign_orphans(library, request.form.get("target", ""))
        flash("Unit saved." + books_note(moved), "success")
        return redirect(url_for("locations", slot=f"{key}:1:1"))

    @app.post("/units/<key>/delete")
    def delete_unit(key: str) -> Response:
        with store.edit() as library:
            unit_or_404(library, key)
            del library["units"][key]
            moved = reassign_orphans(library, request.form.get("target", ""))
        flash("Unit removed." + books_note(moved), "success")
        return redirect(url_for("locations"))

    @app.post("/units/<key>/position")
    def move_unit(key: str) -> Response:
        x = whole(request.form.get("x", ""), 0, MAX_POSITION, "Position")
        y = whole(request.form.get("y", ""), 0, MAX_POSITION, "Position")
        with store.edit() as library:
            unit = unit_or_404(library, key)
            unit["x"], unit["y"] = x, y
        return Response(status=204)

    @app.route("/slots/<slot_id>", methods=["GET", "POST"])
    def show_slot(slot_id: str) -> Response | str:
        library = store.read()
        parsed = parse_slot(slot_id, library["units"])
        if parsed is None:
            abort(404)
        unit, row, col = parsed
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if len(name) > 80:
                abort(400, "Slot names can be at most 80 characters.")
            with store.edit() as latest:
                latest_unit = unit_or_404(latest, unit["id"])
                if name:
                    latest_unit["slot_names"][f"{row}:{col}"] = name
                else:
                    latest_unit["slot_names"].pop(f"{row}:{col}", None)
            flash("Slot renamed." if name else "Slot name reset.", "success")
            return redirect(url_for("show_slot", slot_id=slot_id))
        books = sorted(
            (book for book in library["copies"].values() if book["location_id"] == slot_id),
            key=lambda book: (fold(book["title"]), fold(book["author"])),
        )
        return render_template(
            "slot.html", unit=unit, row=row, col=col, slot_id=slot_id, books=books,
            label=slot_label(slot_id, library), name=slot_name(unit, row, col),
            custom_name=unit["slot_names"].get(f"{row}:{col}", ""),
            slot_groups=slot_options(library),
            capacity=library.get("slot_capacity", DEFAULT_CAPACITY),
            last_move=library.get("last_move"),
        )

    @app.post("/books/move")
    def move_books() -> Response:
        target = request.form.get("target", "")
        source = request.form.get("source", "")
        fallback = url_for("locations")
        with store.edit() as library:
            if target and parse_slot(target, library["units"]) is None:
                abort(400, "Choose an existing slot to move the books to.")
            if source:
                if not valid_filter(source, library):
                    abort(404)
                ids = [
                    book["id"] for book in library["copies"].values()
                    if in_location(book["location_id"], source, library["units"])
                ]
            else:
                ids = list(dict.fromkeys(request.form.getlist("book")))
                if any(key not in library["copies"] for key in ids):
                    abort(404)
            previous = {
                key: library["copies"][key]["location_id"] for key in ids if library["copies"][key]["location_id"] != target
            }
            if not previous:
                abort(400, "There are no books to move, or they are already there.")
            for key in previous:
                library["copies"][key]["location_id"] = target
            where = slot_label(target, library) or "Unplaced"
            description = f"Moved {len(previous)} {'book' if len(previous) == 1 else 'books'} to {where}."
            library["last_move"] = {"description": description, "previous": previous}
        flash(description, "success")
        return redirect(local_path(request.form.get("next", ""), fallback))

    @app.post("/books/undo-move")
    def undo_move() -> Response:
        with store.edit() as library:
            last = library.pop("last_move", None)
            if last is None:
                abort(409, "There is no move to undo.")
            for key, slot in last["previous"].items():
                if key in library["copies"]:
                    valid = not slot or parse_slot(slot, library["units"]) is not None
                    library["copies"][key]["location_id"] = slot if valid else ""
        flash("Undid: " + last["description"], "success")
        return redirect(local_path(request.form.get("next", ""), url_for("locations")))
