import html
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from random import Random
from urllib.parse import quote as urlquote, urlencode, urlsplit
from urllib.request import Request, urlopen

from .storage import BookCopy, Library, Store

USER_AGENT = "Bookeeper/1.0 (private home library; quote of the day)"
RETRY_AFTER = timedelta(hours=1)
MISS_TTL = timedelta(days=14)
MAX_TRIES = {"wikiquote": 15, "gutenberg": 4}
DEADLINE = 60.0
MAX_JSON = 2 * 1024 * 1024
MAX_TEXT = 8 * 1024 * 1024
SKIPPED_SECTIONS = re.compile(
    r"(?:misattribut|disputed|attributed|quotes? about|about|see also|external|reference|further reading|notes)\b",
    re.IGNORECASE,
)
TITLE_ABBREVIATION = re.compile(r"(?:\b(?:Mr|Mrs|Ms|Dr|St|Capt|Col|Gen|Sr|Jr|Prof|Rev|Hon|Lt|Mme|Messrs|vs)|\b[A-Z])\.$")
SPEECH_TAG = re.compile(r"\b(?:said|asked|replied|cried|exclaimed|answered|whispered|shouted)\b", re.IGNORECASE)


class Unavailable(Exception):
    pass


@dataclass(frozen=True)
class Quote:
    text: str
    source: str
    url: str


def _get(url: str, limit: int) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urlopen(request, timeout=10) as response:
            data = response.read(limit + 1)
    except OSError as exc:
        raise Unavailable from exc
    if len(data) > limit:
        raise Unavailable
    return data


def _json(url: str) -> dict:
    try:
        payload = json.loads(_get(url, MAX_JSON))
    except ValueError as exc:
        raise Unavailable from exc
    if not isinstance(payload, dict):
        raise Unavailable
    return payload


def book_key(book: BookCopy) -> str:
    return f"{book['title'].strip().casefold()}|{book.get('author', '').strip().casefold()}"


def _words(text: str) -> str:
    return re.sub(r"^(?:the|a|an) ", "", " ".join(re.findall(r"[a-z0-9]+", text.casefold())))


def _display_name(author: str) -> str:
    first = re.split(r"[;&]|\band\b", author)[0].strip()
    if "," in first:
        surname, _, given = first.partition(",")
        if surname.strip().casefold() == given.strip().casefold():
            return surname.strip()
        return f"{given.strip()} {surname.strip()}".strip()
    return first


def _surname(author: str) -> str:
    name = _display_name(author).split()
    return name[-1] if name else ""


def _title_variants(title: str) -> list[str]:
    bare = re.sub(r"^(?:the|a|an) ", "", title.strip(), flags=re.IGNORECASE)
    return [title.strip(), f"The {bare}"] if bare == title.strip() else [title.strip(), bare]


def _clean_wikitext(text: str) -> str:
    text = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", text, flags=re.DOTALL)
    for _ in range(3):
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    return text


def _clean_quote(line: str) -> str | None:
    line = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", line)
    line = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", line)
    line = re.sub(r"<[^>]+>", "", line).replace("'''", "").replace("''", "")
    line = " ".join(html.unescape(line).split())
    if not 40 <= len(line) <= 300 or re.search(r"[{}\[\]|<>=]|https?:", line) or line.endswith(":"):
        return None
    return line


def wikiquote_lines(wikitext: str, title: str | None = None) -> list[str]:
    """Quotes from a Wikiquote page; with a title, only the section about that work."""
    wanted = _words(title) if title else None
    skip_level = work_level = None
    quotes = []
    for line in _clean_wikitext(wikitext).splitlines():
        heading = re.fullmatch(r"(=+)\s*(.*?)\s*\1\s*", line)
        if heading:
            level, name = len(heading[1]), heading[2]
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if work_level is not None and level <= work_level:
                work_level = None
            if skip_level is None and SKIPPED_SECTIONS.search(name):
                skip_level = level
            if wanted and skip_level is None and work_level is None and _words(re.sub(r"\(.*?\)", "", name)) == wanted:
                work_level = level
            continue
        if skip_level is not None or (wanted and work_level is None):
            continue
        if re.match(r"\*(?![*:#])", line):
            cleaned = _clean_quote(line[1:])
            if cleaned:
                quotes.append(cleaned)
    return quotes


def _wikiquote_page(page: str) -> tuple[str, str] | None:
    query = urlencode({
        "action": "parse", "page": page, "prop": "wikitext", "redirects": 1, "format": "json", "formatversion": 2,
    })
    parsed = _json(f"https://en.wikiquote.org/w/api.php?{query}").get("parse")
    if not isinstance(parsed, dict) or not isinstance(parsed.get("wikitext"), str):
        return None
    if re.search(r"\{\{\s*disambig", parsed["wikitext"], re.IGNORECASE):
        return None
    return str(parsed.get("title") or page), parsed["wikitext"]


def wikiquote_quote(title: str, author: str, rng: Random) -> Quote | None:
    surname = _surname(author)
    if not surname:
        return None
    for variant in _title_variants(title):
        work = _wikiquote_page(variant)
        if work and surname.casefold() in work[1].casefold():
            quotes = wikiquote_lines(work[1])
            if quotes:
                return Quote(rng.choice(quotes), "Wikiquote", f"https://en.wikiquote.org/wiki/{urlquote(work[0].replace(' ', '_'))}")
    page = _wikiquote_page(_display_name(author))
    if page:
        quotes = wikiquote_lines(page[1], title)
        if quotes:
            return Quote(rng.choice(quotes), "Wikiquote", f"https://en.wikiquote.org/wiki/{urlquote(page[0].replace(' ', '_'))}")
    return None


def gutenberg_sentences(text: str) -> list[str]:
    start = re.search(r"\*\*\* ?START OF[^\n]*", text)
    end = re.search(r"\*\*\* ?END OF", text)
    body = text[start.end():end.start() if end else len(text)] if start else text
    margin = len(body) // 20
    body = body[margin:len(body) - margin]
    sentences = []
    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = " ".join(paragraph.split())
        position = 0
        for match in re.finditer(r"[.!?][”\"’']?\s+(?=[A-Z“\"‘])|$", paragraph):
            candidate = paragraph[position:match.end()].strip()
            if TITLE_ABBREVIATION.search(paragraph[position:match.start() + 1]):
                continue
            position = match.end()
            if _usable_sentence(candidate):
                sentences.append(candidate)
    return sentences


def _usable_sentence(sentence: str) -> bool:
    return bool(
        70 <= len(sentence) <= 220
        and re.fullmatch(r"[A-Z“\"‘][^_\[\]*\d]*[.!?][”\"’']?", sentence)
        and sentence.count("“") == sentence.count("”")
        and sentence.count('"') % 2 == 0
        and not SPEECH_TAG.search(sentence)
        and not sentence.isupper()
    )


def _text_url(formats: object) -> str | None:
    if not isinstance(formats, dict):
        return None
    for kind, url in formats.items():
        if str(kind).startswith("text/plain") and isinstance(url, str):
            parts = urlsplit(url)
            if parts.hostname in {"gutenberg.org", "www.gutenberg.org"}:
                return parts._replace(scheme="https").geturl()
    return None


def _same_title(left: str, right: str) -> bool:
    left, right = _words(left), _words(right)
    return bool(left and right) and (left == right or left.startswith(right + " ") or right.startswith(left + " "))


def gutenberg_quote(title: str, author: str, rng: Random) -> Quote | None:
    surname = _surname(author)
    if not surname:
        return None
    query = urlencode({"search": f"{title} {surname}"})
    results = _json(f"https://gutendex.com/books/?{query}").get("results")
    for result in results if isinstance(results, list) else []:
        if not isinstance(result, dict) or not isinstance(result.get("id"), int):
            continue
        names = " ".join(str(a.get("name", "")) for a in result.get("authors", []) if isinstance(a, dict))
        url = _text_url(result.get("formats"))
        if url and _same_title(title, str(result.get("title", ""))) and surname.casefold() in names.casefold():
            sentences = gutenberg_sentences(_get(url, MAX_TEXT).decode("utf-8-sig", errors="replace"))
            if sentences:
                return Quote(rng.choice(sentences), "Project Gutenberg", f"https://www.gutenberg.org/ebooks/{result['id']}")
            return None
    return None


Fetcher = Callable[[str, str, Random], Quote | None]
SOURCES: tuple[tuple[str, Fetcher], ...] = (("wikiquote", wikiquote_quote), ("gutenberg", gutenberg_quote))


@cache
def fallback_quotes() -> list[dict[str, str]]:
    with (Path(__file__).parent / "fallback_quotes.json").open(encoding="utf-8") as file:
        return json.load(file)


def fallback_quote(today: date) -> dict[str, str]:
    """A famous quote that is the same all day and cycles through the whole list."""
    entry = fallback_quotes()[today.toordinal() * 37 % len(fallback_quotes())]
    return {
        "date": today.isoformat(), "text": entry["text"], "source": "Wikiquote", "url": entry["url"],
        "title": "", "author": entry["author"], "book_key": "",
    }


def needs_refresh(library: Library, today: date, now: datetime) -> bool:
    if library.get("quote", {}).get("date") == today.isoformat():
        return False
    attempted = library.get("quote_state", {}).get("attempted_at")
    return not attempted or now - datetime.fromisoformat(attempted) >= RETRY_AFTER


def quote_for_display(library: Library) -> dict | None:
    quote = library.get("quote")
    if not isinstance(quote, dict):
        return None
    if not quote.get("book_key"):
        return quote
    copy_id = next((key for key, book in library["copies"].items() if book_key(book) == quote["book_key"]), None)
    return {**quote, "copy_id": copy_id} if copy_id else None


def refresh_quote(store: Store, today: date, rng: Random | None = None, sources: tuple[tuple[str, Fetcher], ...] = SOURCES) -> bool:
    rng = rng or Random()
    library = store.read()
    now = datetime.now(timezone.utc)
    today_text = today.isoformat()
    cutoff = (today - MISS_TTL).isoformat()
    misses = library.get("quote_state", {}).get("misses", {})
    books = {book_key(book): book for book in library["copies"].values() if book["author"].strip()}
    found = None
    new_misses = []
    started = time.monotonic()
    for name, fetch in sources:
        pool = [key for key in books if misses.get(f"{name}:{key}", "") < cutoff]
        rng.shuffle(pool)
        for key in pool[:MAX_TRIES.get(name, 6)]:
            if time.monotonic() - started > DEADLINE:
                break
            try:
                result = fetch(books[key]["title"], books[key]["author"], rng)
            except Unavailable:
                continue
            if result is None:
                new_misses.append(f"{name}:{key}")
                continue
            found = (key, result)
            break
        if found:
            break
    with store.edit() as current:
        state = current.setdefault("quote_state", {})
        state["attempted_at"] = now.isoformat()
        kept = {key: seen for key, seen in state.get("misses", {}).items() if seen >= cutoff}
        kept.update({key: today_text for key in new_misses})
        state["misses"] = kept
        if current.get("quote", {}).get("date") != today_text:
            if found:
                key, result = found
                current["quote"] = {
                    "date": today_text, "text": result.text, "source": result.source, "url": result.url,
                    "title": books[key]["title"], "author": books[key]["author"], "book_key": key,
                }
            else:
                current["quote"] = fallback_quote(today)
    return found is not None
