import json
import re
from collections.abc import Iterable
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .storage import BookCopy, Location, LocationKind


LOCATION_PARENTS: dict[LocationKind, LocationKind | None] = {
    "room": None,
    "bookcase": "room",
    "shelf": "bookcase",
    "pile": "room",
}
COPY_LOCATIONS = {"shelf", "pile"}


class LookupUnavailable(Exception):
    pass


def lookup_isbn(isbn: str) -> tuple[str, str] | None:
    key = f"ISBN:{isbn}"
    query = urlencode({"bibkeys": key, "format": "json", "jscmd": "data"})
    request = Request(f"https://openlibrary.org/api/books?{query}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read(256 * 1024 + 1))
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LookupUnavailable from exc
    if not isinstance(payload, dict):
        raise LookupUnavailable
    if key not in payload:
        return None
    book = payload[key]
    if not isinstance(book, dict) or not isinstance(book.get("title"), str):
        raise LookupUnavailable
    authors = book.get("authors", [])
    if not isinstance(authors, list):
        raise LookupUnavailable
    names = [
        item["name"].strip()
        for item in authors
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()
    ]
    title = book["title"].strip()
    if not title:
        raise LookupUnavailable
    return title[:250], ", ".join(names)[:250]


def normalize_isbn(value: str) -> str:
    isbn = re.sub(r"[\s-]", "", value).upper()
    if re.fullmatch(r"[0-9]{9}[0-9X]", isbn):
        digits = [int(char) for char in isbn[:9]] + [10 if isbn[-1] == "X" else int(isbn[-1])]
        if sum((10 - i) * digit for i, digit in enumerate(digits)) % 11 == 0:
            base = "978" + isbn[:9]
            return base + str(_isbn13_check_digit(base))
    if (
        re.fullmatch(r"97[89][0-9]{10}", isbn)
        and int(isbn[-1]) == _isbn13_check_digit(isbn[:-1])
    ):
        return isbn
    raise ValueError("Enter a valid ISBN-10 or ISBN-13, or leave it blank.")


def _isbn13_check_digit(base: str) -> int:
    return -sum(int(char) * (1 if i % 2 == 0 else 3) for i, char in enumerate(base)) % 10


def fold(value: str) -> str:
    return " ".join(value.casefold().split())


def find_duplicate(candidate: BookCopy, copies: Iterable[BookCopy]) -> bool:
    name = (fold(candidate["title"]), fold(candidate["author"]))
    return any(
        item["id"] != candidate["id"]
        and (
            (bool(candidate["isbn"]) and candidate["isbn"] == item["isbn"])
            or name == (fold(item["title"]), fold(item["author"]))
        )
        for item in copies
    )


def location_path(location_id: str, locations: dict[str, Location]) -> list[Location]:
    path = []
    while location_id:
        location = locations[location_id]
        path.append(location)
        location_id = location["parent_id"]
    return path[::-1]


def location_label(location_id: str, locations: dict[str, Location]) -> str:
    return " / ".join(item["name"] for item in location_path(location_id, locations))


def search_copies(
    copies: Iterable[BookCopy], locations: dict[str, Location], query: str, location_id: str
) -> list[BookCopy]:
    try:
        isbn_query = normalize_isbn(query)
    except ValueError:
        isbn_query = None
    words = fold(query).split()
    results = []
    for copy in copies:
        if location_id and location_id not in {
            item["id"] for item in location_path(copy["location_id"], locations)
        }:
            continue
        text = fold(" ".join([copy["title"], copy["author"], copy["series"], copy["isbn"], *copy["tags"]]))
        if isbn_query == copy["isbn"] or all(word in text for word in words):
            results.append(copy)
    return sorted(results, key=lambda item: (fold(item["title"]), fold(item["author"])))
