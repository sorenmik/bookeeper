from collections import Counter
from collections.abc import Iterator

from .storage import Library, Unit, UnitKind


UNIT_KINDS: dict[UnitKind, str] = {"cabinet": "Cabinet", "shelf": "Shelf", "box": "Box", "pile": "Pile"}
SINGLE_SLOT = {"box", "pile"}
MAX_SIDE = 20
DEFAULT_CAPACITY = 30
UNPLACED = "unplaced"
SEPARATOR = " › "


def fit_size(kind: str, cols: int, rows: int) -> tuple[int, int]:
    if kind in SINGLE_SLOT:
        return 1, 1
    return cols, 1 if kind == "shelf" else rows


def make_slot_id(unit_id: str, row: int, col: int) -> str:
    return f"{unit_id}:{row}:{col}"


def parse_slot(slot_id: str, units: dict[str, Unit]) -> tuple[Unit, int, int] | None:
    parts = slot_id.split(":")
    if len(parts) != 3 or parts[0] not in units or not all(part.isascii() and part.isdecimal() for part in parts[1:]):
        return None
    unit, row, col = units[parts[0]], int(parts[1]), int(parts[2])
    return (unit, row, col) if 1 <= row <= unit["rows"] and 1 <= col <= unit["cols"] else None


def slot_name(unit: Unit, row: int, col: int) -> str:
    if custom := unit["slot_names"].get(f"{row}:{col}"):
        return custom
    if unit["kind"] in SINGLE_SLOT:
        return ""
    return f"Slot {col}" if unit["rows"] == 1 else f"R{row}C{col}"


def unit_slots(unit: Unit) -> Iterator[tuple[str, int, int]]:
    for row in range(1, unit["rows"] + 1):
        for col in range(1, unit["cols"] + 1):
            yield make_slot_id(unit["id"], row, col), row, col


def unit_label(unit: Unit, library: Library) -> str:
    return SEPARATOR.join((library["storages"][unit["storage_id"]]["name"], unit["name"]))


def slot_label(slot_id: str, library: Library) -> str:
    """Full path such as 'Kitchen › Cabinet A › top-left'; empty when the book is unplaced."""
    parsed = parse_slot(slot_id, library["units"])
    if parsed is None:
        return ""
    unit, row, col = parsed
    return SEPARATOR.join(part for part in (unit_label(unit, library), slot_name(unit, row, col)) if part)


def mini_map(slot_id: str, library: Library) -> dict | None:
    """The unit holding a slot, with the slot's row and column, for drawing a small map."""
    parsed = parse_slot(slot_id, library["units"])
    if parsed is None:
        return None
    unit, row, col = parsed
    return {"name": unit_label(unit, library), "cols": unit["cols"], "rows": unit["rows"], "row": row, "col": col}


def slot_labels(library: Library) -> dict[str, str]:
    return {
        slot: slot_label(slot, library)
        for unit in library["units"].values() for slot, _row, _col in unit_slots(unit)
    }


def slot_counts(library: Library) -> Counter[str]:
    return Counter(book["location_id"] for book in library["copies"].values() if book["location_id"])


def sorted_units(library: Library) -> list[Unit]:
    return sorted(
        library["units"].values(),
        key=lambda unit: (library["storages"][unit["storage_id"]]["name"].casefold(), unit["name"].casefold()),
    )


def slot_options(library: Library) -> list[tuple[str, list[tuple[str, str]]]]:
    """Slots grouped by unit, for pickers."""
    return [
        (unit_label(unit, library), [(slot, slot_name(unit, row, col) or unit["name"]) for slot, row, col in unit_slots(unit)])
        for unit in sorted_units(library)
    ]


def filter_options(library: Library) -> list[tuple[str, str]]:
    """Storages, units and slots in tree order, for filtering by any level."""
    options = []
    for storage in sorted(library["storages"].values(), key=lambda item: item["name"].casefold()):
        options.append((storage["id"], storage["name"]))
        for unit in (item for item in sorted_units(library) if item["storage_id"] == storage["id"]):
            options.append((unit["id"], unit_label(unit, library)))
            if unit["kind"] not in SINGLE_SLOT:
                options.extend((slot, slot_label(slot, library)) for slot, _row, _col in unit_slots(unit))
    return options


def valid_filter(selected: str, library: Library) -> bool:
    return (
        selected == UNPLACED or selected in library["storages"] or selected in library["units"]
        or parse_slot(selected, library["units"]) is not None
    )


def in_location(slot_id: str, selected: str, units: dict[str, Unit]) -> bool:
    if selected == UNPLACED:
        return not slot_id
    unit = units.get(slot_id.split(":")[0]) if slot_id else None
    return unit is not None and selected in {slot_id, unit["id"], unit["storage_id"]}
