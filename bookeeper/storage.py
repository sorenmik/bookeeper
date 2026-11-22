import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, TypedDict

from filelock import FileLock


LocationKind = Literal["room", "bookcase", "shelf", "pile"]


class Location(TypedDict):
    id: str
    kind: LocationKind
    name: str
    parent_id: str


class BookCopy(TypedDict):
    id: str
    title: str
    author: str
    series: str
    isbn: str
    tags: list[str]
    location_id: str


class Library(TypedDict):
    session_key: str
    users: dict[str, str]
    copies: dict[str, BookCopy]
    locations: dict[str, Location]


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
