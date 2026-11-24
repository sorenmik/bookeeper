import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, TypedDict

from filelock import FileLock


UnitKind = Literal["cabinet", "shelf", "box", "pile"]


class Storage(TypedDict):
    id: str
    name: str


class UnitRequired(TypedDict):
    id: str
    storage_id: str
    kind: UnitKind
    name: str
    cols: int
    rows: int
    x: int
    y: int
    slot_names: dict[str, str]


class Unit(UnitRequired, total=False):
    color: str


class LastMove(TypedDict):
    description: str
    previous: dict[str, str]


class BookCopyRequired(TypedDict):
    id: str
    title: str
    author: str
    series: str
    isbn: str
    tags: list[str]
    location_id: str


class BookCopy(BookCopyRequired, total=False):
    summary: str
    genre: str
    format: str
    publisher: str
    publication_date: str
    language: str
    pages: int | None
    cover_id: int | None
    added_on: str


class Reading(TypedDict):
    status: str
    dates: list[str]


class Loan(TypedDict):
    direction: Literal["lent", "borrowed"]
    copy_id: str
    title: str
    person: str
    started: str
    due: str
    returned: str


class LibraryRequired(TypedDict):
    session_key: str
    users: dict[str, str]
    copies: dict[str, BookCopy]
    storages: dict[str, Storage]
    units: dict[str, Unit]


class Library(LibraryRequired, total=False):
    sessions: dict[str, str]
    slot_capacity: int
    last_move: LastMove
    reading: dict[str, Reading]
    loans: dict[str, Loan]
    wishlists: dict[str, dict[str, BookCopy]]
    notes: dict[str, dict[str, str]]
    quote: dict[str, str]
    quote_state: dict[str, Any]


def upgrade(library: Library) -> Library:
    """Bring older files up to date. Floor-plan locations are discarded and their books become unplaced."""
    if "locations" in library:
        del library["locations"]  # type: ignore[typeddict-item]
        for copy in library["copies"].values():
            copy["location_id"] = ""
    library.setdefault("storages", {})
    library.setdefault("units", {})
    return library


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "library.json"
        self.lock = FileLock(directory / "library.lock", timeout=10)

    def _read(self) -> Library:
        try:
            with self.path.open(encoding="utf-8") as file:
                return upgrade(json.load(file))
        except FileNotFoundError:
            return {"session_key": "", "users": {}, "copies": {}, "storages": {}, "units": {}}

    def read(self) -> Library:
        with self.lock:
            return self._read()

    @contextmanager
    def edit(self) -> Iterator[Library]:
        with self.lock:
            library = self._read()
            yield library
            pending = None
            try:
                with NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent, delete=False
                ) as file:
                    pending = Path(file.name)
                    json.dump(library, file, indent=2, ensure_ascii=False)
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())
                pending.replace(self.path)
            finally:
                if pending is not None:
                    pending.unlink(missing_ok=True)
