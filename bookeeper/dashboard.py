from collections import Counter
from dataclasses import dataclass
from datetime import date

from .storage import Library


@dataclass(frozen=True)
class MonthActivity:
    label: str
    short_label: str
    added: int
    finished: int


@dataclass(frozen=True)
class LibraryActivity:
    copies: int
    copies_read: int
    finished_this_year: int
    undated_copies: int
    months: list[MonthActivity]
    peak: int


def activity(library: Library, today: date) -> LibraryActivity:
    copies = library["copies"]
    dated = [stamp for book in copies.values() if (stamp := book.get("added_on"))]
    histories = [record["dates"] for key, record in library.get("reading", {}).items() if key in copies]
    # Finished without a date means the finish date is unknown: counted as read, left out of the monthly chart.
    read = sum(
        bool(record["dates"]) or record["status"] == "finished"
        for key, record in library.get("reading", {}).items() if key in copies
    )
    added = Counter(stamp[:7] for stamp in dated)
    finished = Counter(stamp[:7] for read_dates in histories for stamp in read_dates)
    last = today.year * 12 + today.month - 1
    months = []
    for index in range(last - 11, last + 1):
        start = date(index // 12, index % 12 + 1, 1)
        key = start.isoformat()[:7]
        months.append(MonthActivity(start.strftime("%b %Y"), start.strftime("%b"), added[key], finished[key]))
    return LibraryActivity(
        copies=len(copies),
        copies_read=read,
        finished_this_year=sum(count for month, count in finished.items() if month.startswith(f"{today.year}-")),
        undated_copies=len(copies) - len(dated),
        months=months,
        peak=max(1, *(max(month.added, month.finished) for month in months)),
    )
