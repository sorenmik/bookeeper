import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .places import in_location
from .storage import BookCopy, Unit


class LookupUnavailable(Exception):
    pass


@dataclass(frozen=True)
class BookMetadata:
    title: str
    author: str = ""
    summary: str = ""
    genre: str = ""
    format: str = ""
    publisher: str = ""
    publication_date: str = ""
    language: str = ""
    pages: int | None = None
    cover_id: int | None = None


def _open_library(path: str) -> dict:
    request = Request(f"https://openlibrary.org{path}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            data = response.read(256 * 1024 + 1)
        payload = json.loads(data) if len(data) <= 256 * 1024 else None
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise LookupUnavailable from exc
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LookupUnavailable from exc
    if not isinstance(payload, dict):
        raise LookupUnavailable
    return payload


def _summary(record: dict) -> str:
    for field in ("description", "notes"):
        value = record.get(field)
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()[:5000]
    return ""


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _items(value: object) -> list:
    return value if isinstance(value, list) else []


def _names(items: object) -> list[str]:
    return [
        name for item in _items(items)
        if (name := _text(item.get("name") if isinstance(item, dict) else item))
    ]


def _limited_names(names: list[str], limit: int) -> str:
    result = ""
    for name in dict.fromkeys(names):
        combined = f"{result}, {name}" if result else name
        if len(combined) > limit:
            return result or name[:limit]
        result = combined
    return result


def _optional_record(path: str) -> dict:
    try:
        return _open_library(path)
    except LookupUnavailable:
        logging.getLogger(__name__).warning("Open Library did not return optional metadata for %s.", path)
        return {}


@lru_cache(maxsize=128)
def _language_name(path: str) -> str:
    # Do not cache a network failure as a language name.
    return _text(_open_library(path + ".json").get("name")) or path.rsplit("/", 1)[-1]


def positive_number(value: object) -> int | None:
    if type(value) is int and 0 < value <= 999_999_999:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value.strip()):
        return int(value) or None
    return None


def _fetch_json(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            data = response.read(1024 * 1024 + 1)
        payload = json.loads(data) if len(data) <= 1024 * 1024 else None
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LookupUnavailable from exc
    if not isinstance(payload, dict):
        raise LookupUnavailable
    return payload


def lookup_google_books(isbn: str) -> BookMetadata | None:
    payload = _fetch_json("https://www.googleapis.com/books/v1/volumes?" + urlencode({"q": f"isbn:{isbn}", "maxResults": 1}))
    items = _items(payload.get("items"))
    info = items[0].get("volumeInfo") if items and isinstance(items[0], dict) else None
    if not isinstance(info, dict) or not _text(info.get("title")):
        return None
    title, subtitle = _text(info.get("title")), _text(info.get("subtitle"))
    return BookMetadata(
        title=(f"{title}: {subtitle}" if subtitle else title)[:250],
        author=_limited_names(_names(info.get("authors")), 250),
        summary=_text(info.get("description"))[:5000],
        genre=_limited_names(_names(info.get("categories")), 80),
        publisher=_text(info.get("publisher"))[:250],
        publication_date=_text(info.get("publishedDate"))[:80],
        language=_text(info.get("language"))[:80],
        pages=positive_number(info.get("pageCount")),
    )


# Ordered by how likely each source is to know an English or Danish ISBN.
_PROVIDERS = (lookup_google_books,)


def lookup_isbn(isbn: str) -> BookMetadata | None:
    """Try Open Library first, then fall back to other sources until one finds the book."""
    failure = None
    for provider in (lookup_open_library, *_PROVIDERS):
        try:
            if metadata := provider(isbn):
                return metadata
        except LookupUnavailable as exc:
            logging.getLogger(__name__).warning("Lookup provider unavailable for ISBN %s.", isbn)
            failure = exc
    if failure:
        raise failure
    return None


def lookup_open_library(isbn: str) -> BookMetadata | None:
    key = f"ISBN:{isbn}"
    query = urlencode({"bibkeys": key, "format": "json", "jscmd": "data"})
    error = None
    try:
        book = _open_library(f"/api/books?{query}").get(key, {})
        if not isinstance(book, dict):
            raise LookupUnavailable("Invalid Open Library ISBN response.")
    except LookupUnavailable as exc:
        book, error = {}, exc

    edition_path = _text(book.get("key"))
    if not re.fullmatch(r"/books/OL[0-9]+M", edition_path):
        url_match = re.search(r"(/books/OL[0-9]+M)(?:/|$)", _text(book.get("url")))
        edition_path = url_match.group(1) if url_match else ""
    try:
        edition = _open_library(f"{edition_path or f'/isbn/{isbn}'}.json")
    except LookupUnavailable as exc:
        if not book:
            raise exc from error
        logging.getLogger(__name__).warning("Open Library edition details unavailable for ISBN %s.", isbn)
        edition = {}
    if not book and not edition:
        if error:
            raise error
        return None

    title = _text(book.get("title")) or _text(edition.get("title"))
    if not title:
        raise LookupUnavailable("Open Library returned a record without a title.")
    works = _items(edition.get("works"))
    work_path = _text(works[0].get("key")) if works and isinstance(works[0], dict) else ""
    summary = _summary(edition) or _summary(book)
    subjects = _names(edition.get("subjects")) or _names(book.get("subjects"))
    if re.fullmatch(r"/works/OL[0-9]+W", work_path) and (not summary or not subjects):
        work = _optional_record(work_path + ".json")
        summary = summary or _summary(work)
        subjects = subjects or _names(work.get("subjects"))

    authors = _names(book.get("authors"))
    if not authors:
        for entry in _items(edition.get("authors"))[:5]:
            path = _text(entry.get("key")) if isinstance(entry, dict) else ""
            if re.fullmatch(r"/authors/OL[0-9]+A", path):
                name = _text(_optional_record(path + ".json").get("name"))
                if name:
                    authors.append(name)
    languages = []
    for entry in _items(edition.get("languages"))[:5]:
        path = _text(entry.get("key")) if isinstance(entry, dict) else ""
        if re.fullmatch(r"/languages/[a-z]{3}", path):
            try:
                languages.append(_language_name(path))
            except LookupUnavailable:
                logging.getLogger(__name__).warning("Open Library language name unavailable for %s.", path)
                languages.append(path.rsplit("/", 1)[-1])
    pages = positive_number(edition.get("number_of_pages")) or positive_number(book.get("number_of_pages"))
    if pages is None:
        pagination = re.fullmatch(r"([0-9]{1,9})(?:\s*(?:p\.?|pages))?", _text(edition.get("pagination")), re.I)
        pages = positive_number(pagination.group(1)) if pagination else None
    cover_id = next((cover for item in _items(edition.get("covers")) if (cover := positive_number(item))), None)
    if cover_id is None and isinstance(cover := book.get("cover"), dict):
        for size in ("large", "medium", "small"):
            match = re.fullmatch(r"https?://covers\.openlibrary\.org/b/id/([0-9]{1,9})-[SML]\.jpg", _text(cover.get(size)))
            if match and (cover_id := positive_number(match.group(1))):
                break
    return BookMetadata(
        title=title[:250], author=_limited_names(authors, 250), summary=summary,
        genre=_limited_names(subjects, 80), format=(_text(edition.get("physical_format")) or _text(book.get("physical_format")))[:80],
        publisher=_limited_names(_names(edition.get("publishers")) or _names(book.get("publishers")), 250),
        publication_date=(_text(edition.get("publish_date")) or _text(book.get("publish_date")))[:80],
        language=_limited_names(languages or _names(book.get("languages")), 80), pages=pages, cover_id=cover_id,
    )


def fill_book_metadata(book: BookCopy, metadata: BookMetadata) -> BookCopy:
    return {
        **book,
        "title": book["title"] or metadata.title,
        "author": book["author"] or metadata.author,
        "summary": book.get("summary") or metadata.summary,
        "genre": book.get("genre") or metadata.genre,
        "format": book.get("format") or metadata.format,
        "publisher": book.get("publisher") or metadata.publisher,
        "publication_date": book.get("publication_date") or metadata.publication_date,
        "language": book.get("language") or metadata.language,
        "pages": book.get("pages") or metadata.pages,
        "cover_id": book.get("cover_id") or metadata.cover_id,
    }


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


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text))


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance where swapping two neighbouring letters counts as one typo."""
    rows = [list(range(len(b) + 1))]
    for i, left in enumerate(a, 1):
        row = [i]
        for j, right in enumerate(b, 1):
            cost = min(rows[-1][j] + 1, row[j - 1] + 1, rows[-1][j - 1] + (left != right))
            if i > 1 and j > 1 and left == b[j - 2] and a[i - 2] == right:
                cost = min(cost, rows[-2][j - 2] + 1)
            row.append(cost)
        rows.append(row)
    return rows[-1][-1]


def _fuzzy_word(word: str, tokens: set[str]) -> bool:
    allowed = 0 if len(word) < 4 else 1 if len(word) < 8 else 2
    return bool(allowed) and any(
        abs(len(token) - len(word)) <= allowed and _edit_distance(word, token) <= allowed for token in tokens
    )


def search_copies(copies: Iterable[BookCopy], units: dict[str, Unit], query: str, location_id: str) -> list[BookCopy]:
    """Substring matches come first; misspelt words still match when close to a word in the book."""
    try:
        isbn_query = normalize_isbn(query)
    except ValueError:
        isbn_query = None
    words = fold(query).split()
    results = []
    for copy in copies:
        if location_id and not in_location(copy["location_id"], location_id, units):
            continue
        text = fold(" ".join([
            copy["title"], copy["author"], copy["series"], copy["isbn"], copy.get("genre", ""), *copy["tags"]
        ]))
        if isbn_query == copy["isbn"] or all(word in text for word in words):
            results.append((False, copy))
        elif all(word in text or _fuzzy_word(word, _tokens(text)) for word in words):
            results.append((True, copy))
    return [copy for _fuzzy, copy in sorted(
        results, key=lambda item: (item[0], fold(item[1]["title"]), fold(item[1]["author"]))
    )]
