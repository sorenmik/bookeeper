import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, NotRequired, TypedDict

from filelock import FileLock


LocationKind = Literal["room", "bookcase", "shelf", "pile"]


class Location(TypedDict):
    id: str
    kind: LocationKind
    name: str
    parent_id: str
    x: NotRequired[int]
    y: NotRequired[int]


class BookCopy(TypedDict):
    id: str
    title: str
    author: str
    series: str
    isbn: str
    tags: list[str]
    location_id: str
    summary: NotRequired[str]
    genre: NotRequired[str]
    format: NotRequired[str]
    publisher: NotRequired[str]
    publication_date: NotRequired[str]
    language: NotRequired[str]
    pages: NotRequired[int | None]
    cover_id: NotRequired[int | None]
    added_on: NotRequired[str]


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


class Library(TypedDict):
    session_key: str
    users: dict[str, str]
    copies: dict[str, BookCopy]
    locations: dict[str, Location]
    reading: NotRequired[dict[str, Reading]]
    loans: NotRequired[dict[str, Loan]]
    wishlists: NotRequired[dict[str, dict[str, BookCopy]]]
    notes: NotRequired[dict[str, dict[str, str]]]
    quote: NotRequired[dict[str, str]]
    quote_state: NotRequired[dict[str, Any]]


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "library.json"
        self.lock = FileLock(directory / "library.lock", timeout=10)

    def _read(self) -> Library:
        try:
            with self.path.open(encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            return {"session_key": "", "users": {}, "copies": {}, "locations": {}}

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
