import json
import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, fields, replace
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

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
    # Open Library "notes" are cataloguing remarks ("Translated from the Polish."), not descriptions.
    value = record.get("description")
    if isinstance(value, dict):
        value = value.get("value")
    return value.strip()[:5000] if isinstance(value, str) else ""


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


def _fetch(url: str, accept: str = "application/json") -> bytes:
    request = Request(url, headers={"Accept": accept})
    try:
        with urlopen(request, timeout=5) as response:
            data = response.read(1024 * 1024 + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise LookupUnavailable from exc
    if len(data) > 1024 * 1024:
        raise LookupUnavailable("Lookup response is too large.")
    return data


def _fetch_json(url: str) -> dict:
    try:
        payload = json.loads(_fetch(url))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LookupUnavailable from exc
    if not isinstance(payload, dict):
        raise LookupUnavailable
    return payload


_LANGUAGES = {
    "da": "Danish", "dan": "Danish", "en": "English", "eng": "English", "sv": "Swedish", "swe": "Swedish",
    "no": "Norwegian", "nb": "Norwegian", "nor": "Norwegian", "de": "German", "ger": "German", "deu": "German",
    "fr": "French", "fre": "French", "fra": "French", "es": "Spanish", "spa": "Spanish",
    "pl": "Polish", "pol": "Polish",
}


def _language(code: str) -> str:
    return _LANGUAGES.get(code.casefold(), code)


def _strings(value: object) -> list[str]:
    return [text for item in (value if isinstance(value, list) else [value]) if (text := _text(item))]


def _person(name: str) -> str:
    """Turn catalogue headings such as "Austen, Jane, 1775-1817" into "Jane Austen"."""
    parts = [part.strip() for part in name.strip().rstrip(".").split(",")]
    parts = [part for part in parts if part and not re.fullmatch(r"[0-9 ?.-]+", part)]
    return f"{parts[1]} {parts[0]}" if len(parts) >= 2 else " ".join(parts)


def lookup_libris(isbn: str) -> BookMetadata | None:
    payload = _fetch_json("https://libris.kb.se/xsearch?" + urlencode({"query": f"isbn:{isbn}", "format": "json", "n": 1}))
    result = payload.get("xsearch")
    records = _items(result.get("list")) if isinstance(result, dict) else []
    record = records[0] if records and isinstance(records[0], dict) else {}
    title = re.sub(r"\s+:\s+", ": ", _text(record.get("title"))).rstrip(" /")
    if not title:
        return None
    publishers = [re.sub(r"^.*\s:\s+|^[\s;:]+", "", item) for item in _strings(record.get("publisher"))]
    date = next((match.group() for item in _strings(record.get("date")) if (match := re.search(r"[0-9]{4}", item))), "")
    return BookMetadata(
        title=title[:250],
        author=_limited_names([_person(name) for name in _strings(record.get("creator"))], 250),
        publisher=_limited_names([name for name in publishers if name], 250),
        publication_date=date,
        language=_limited_names([_language(code) for code in _strings(record.get("language"))], 80),
    )


def lookup_google_books(isbn: str) -> BookMetadata | None:
    query = {"q": f"isbn:{isbn}", "maxResults": 1}
    # Keyless requests share a small quota and often get HTTP 429.
    if key := os.environ.get("BOOKEEPER_GOOGLE_BOOKS_KEY", "").strip():
        query["key"] = key
    payload = _fetch_json("https://www.googleapis.com/books/v1/volumes?" + urlencode(query))
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
        language=_language(_text(info.get("language")))[:80],
        pages=positive_number(info.get("pageCount")),
    )


_MARC = "{http://www.loc.gov/MARC21/slim}"


def _marc_values(record: ElementTree.Element, tags: str, codes: str) -> list[str]:
    """Subfield values from MARC datafields, joined per field, with trailing ISBD punctuation removed."""
    values = []
    for field in record.iter(f"{_MARC}datafield"):
        if field.get("tag") in tags.split():
            parts = [_text(sub.text) for sub in field.iter(f"{_MARC}subfield") if sub.get("code") in codes]
            if value := re.sub(r"\s*[/:;,.=]+$", "", " ".join(part for part in parts if part)).strip():
                values.append(value)
    return values


def lookup_royal_library(isbn: str) -> BookMetadata | None:
    """The Royal Danish Library receives a legal-deposit copy of every Danish book."""
    query = urlencode({
        "version": "1.2", "operation": "searchRetrieve", "query": f"alma.isbn={isbn}", "maximumRecords": 1,
    })
    data = _fetch(f"https://kbdk-kgl.alma.exlibrisgroup.com/view/sru/45KBDK_KGL?{query}", "application/xml")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise LookupUnavailable from exc
    record = root.find(f".//{_MARC}record")
    if record is None:
        return None
    title = re.sub(r"\s+:\s+", ": ", next(iter(_marc_values(record, "245", "ab")), ""))
    if not title:
        return None
    control = next((field.text or "" for field in record.iter(f"{_MARC}controlfield") if field.get("tag") == "008"), "")
    language = control[35:38].strip(" |") if len(control) >= 38 else ""
    pages = next((match.group(1) for value in _marc_values(record, "300", "a")
                  if (match := re.search(r"([0-9]{1,9})\s*(?:sider|s\.|pages|p\.)", value))), None)
    date = next((match.group() for value in _marc_values(record, "264 260", "c")
                 if (match := re.search(r"[0-9]{4}", value))), "")
    return BookMetadata(
        title=title[:250],
        author=_limited_names([_person(name) for name in _marc_values(record, "100 700", "a")], 250),
        summary=next(iter(_marc_values(record, "520", "a")), "")[:5000],
        genre=_limited_names(_marc_values(record, "650 655", "a"), 80),
        publisher=_limited_names(_marc_values(record, "264 260", "b"), 250),
        publication_date=date,
        language=_language(language)[:80] if language else "",
        pages=positive_number(pages),
    )


def lookup_isbn(isbn: str) -> BookMetadata | None:
    """Combine sources in order: the first match wins, and later sources only fill its blanks.

    Later sources are asked only while no summary in an acceptable language has been found.
    Raises LookupUnavailable only when every source failed; otherwise a miss means not found.
    """
    failures = 0
    result = None
    summaries = []
    # Ordered by how likely each source is to know the ISBN; 978-87 is the Danish ISBN range.
    providers = (lookup_open_library, lookup_libris, lookup_google_books, lookup_royal_library)
    if isbn.startswith("97887"):
        providers = (lookup_royal_library, lookup_open_library, lookup_libris, lookup_google_books)
    for provider in providers:
        try:
            metadata = provider(isbn)
        except LookupUnavailable:
            logging.getLogger(__name__).warning("%s was unavailable for ISBN %s.", getattr(provider, "__name__", provider), isbn)
            failures += 1
            continue
        if metadata:
            summaries.append(metadata.summary)
            result = metadata if result is None else replace(result, **{
                field.name: getattr(metadata, field.name)
                for field in fields(BookMetadata) if not getattr(result, field.name)
            })
            if _pick_summary(summaries, result.language, allow_fallback=False):
                break
    if result is None:
        if failures == len(providers):
            raise LookupUnavailable("No book database could be reached.")
        return None
    return replace(result, summary=_pick_summary(summaries, result.language))


# Frequent short words per language; enough to tell a paragraph's language apart.
_COMMON_WORDS = {
    "English": "the and of to in is that with for his her he she was it on as by from this",
    "Danish": "og i at det en til er som på de med han af for ikke der var hun den har sig",
    "Swedish": "och i att det som en på är av för med till den har inte han hon var sig",
    "German": "der die und das ist nicht mit sich auf ein eine zu den von sie er dem im",
    "French": "le la les et des est une un du que qui dans pour pas il elle au sur",
    "Spanish": "el la los las y de que en un una es por con para del se su al",
    "Polish": "i w nie na się z że do to jest jak o ale przez jego jej od po",
}
_COMMON_WORDS = {language: set(words.split()) for language, words in _COMMON_WORDS.items()}


def detect_language(text: str) -> str:
    """Best-guess language name for a paragraph, or "" when the text gives too few clues."""
    words = re.findall(r"\w+", text.casefold())[:300]
    scores = {language: sum(word in common for word in words) for language, common in _COMMON_WORDS.items()}
    if re.search(r"[ąćęłńśźż]", text, re.I):
        scores["Polish"] += 10
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else ""


def _pick_summary(summaries: list[str], book_language: str, allow_fallback: bool = True) -> str:
    """Prefer a summary in the book's language, then any Danish or English one, then one too short to detect.

    Lookup keeps asking sources until it finds the first kind (allow_fallback=False).
    """
    wanted = {name.strip() for name in book_language.split(",") if name.strip()} or {"Danish", "English"}
    readable = {"Danish", "Norwegian", "English"}  # Norwegian is too close to Danish for word counts.
    if "Danish" in wanted:
        wanted.add("Norwegian")
    detected = [(summary, detect_language(summary)) for summary in summaries if summary]
    tiers = (wanted, readable, {""}) if allow_fallback else (wanted,)
    return next((summary for tier in tiers for summary, language in detected if language in tier), "")


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


_NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def author_sort_key(author: str) -> str:
    """Sort key on the first author's surname: "Jane Austen" and "Austen, Jane" both give "austen jane"."""
    name = fold(re.split(r"\s*(?:;|&|\band\b|\bog\b)\s*", author, maxsplit=1)[0])
    head, comma, tail = name.partition(",")
    if comma and len(head.split()) == 1 and tail.strip():
        return f"{head} {tail.strip()}"
    words = head.split()
    while len(words) > 1 and words[-1] in _NAME_SUFFIXES:
        words.pop()
    return " ".join(words[-1:] + words[:-1])


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
