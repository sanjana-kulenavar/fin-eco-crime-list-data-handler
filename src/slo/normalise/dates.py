"""Date parsing with explicit, per-format rules (no format guessing)."""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime
from typing import NamedTuple

MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}

MIN_YEAR = 1900


class DobParse(NamedTuple):
    date_from: date | None
    date_to: date | None
    precision: str  # day | month | year | range | unknown | unparseable | invalid
    circa: bool


_CIRCA = re.compile(r"^(?:circa|approx(?:imately)?\.?|about|ca\.|c\.|ca)\s*", re.I)
_YEAR_RANGE = re.compile(r"^(?:between\s+)?(\d{4})\s*(?:to|-|–|and)\s*(\d{4})$", re.I)
_ISO_DAY = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_ISO_MONTH = re.compile(r"^(\d{4})-(\d{1,2})$")
_SLASHED = re.compile(r"^(dd|\d{1,2})[/.](mm|\d{1,2})[/.](\d{4})$", re.I)
_PLACEHOLDER_DAY = {"dd", "00", "0"}
_PLACEHOLDER_MONTH = {"mm", "00", "0"}
_DAY_MON_YEAR = re.compile(r"^(\d{1,2})\s+([a-z]{3,9})\.?\s+(\d{4})$", re.I)
_MON_YEAR = re.compile(r"^([a-z]{3,9})\.?\s+(\d{4})$", re.I)
_YEAR = re.compile(r"^(\d{4})$")


def _year_span(y1: int, y2: int) -> tuple[date, date]:
    return date(y1, 1, 1), date(y2, 12, 31)


def _month_span(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


_RANGE_SPLIT = re.compile(r"\s+(?:to|and)\s+|\s+[-–]\s+", re.I)


def _parse_core(text: str, dayfirst: bool) -> tuple[date, date, str] | None:
    parts = _RANGE_SPLIT.split(text.removeprefix("between ").removeprefix("Between "))
    if len(parts) == 2:  # "01 Jan 1973 to 31 Dec 1973", "1961 - 1963"
        left, right = _parse_core(parts[0], dayfirst), _parse_core(parts[1], dayfirst)
        if left is None or right is None:
            return None
        lo, hi = sorted([left[0], right[1]])
        return lo, hi, "range"
    if m := _YEAR_RANGE.match(text):
        y1, y2 = int(m[1]), int(m[2])
        if y1 > y2:
            y1, y2 = y2, y1
        return (*_year_span(y1, y2), "range")
    if m := _ISO_DAY.match(text):
        d = date(int(m[1]), int(m[2]), int(m[3]))
        return d, d, "day"
    if m := _ISO_MONTH.match(text):
        return (*_month_span(int(m[1]), int(m[2])), "month")
    if m := _SLASHED.match(text):
        a, b, year = m[1].lower(), m[2].lower(), int(m[3])
        day_part, month_part = (a, b) if dayfirst else (b, a)
        if day_part in _PLACEHOLDER_DAY and month_part in _PLACEHOLDER_MONTH:
            return (*_year_span(year, year), "year")
        if day_part in _PLACEHOLDER_DAY:
            return (*_month_span(year, int(month_part)), "month")
        if month_part in _PLACEHOLDER_MONTH:
            return (*_year_span(year, year), "year")
        d = date(year, int(month_part), int(day_part))
        return d, d, "day"
    if m := _DAY_MON_YEAR.match(text):
        month = MONTHS.get(m[2].lower())
        if month is None:
            return None
        d = date(int(m[3]), month, int(m[1]))
        return d, d, "day"
    if m := _MON_YEAR.match(text):
        month = MONTHS.get(m[1].lower())
        if month is None:
            return None
        return (*_month_span(int(m[2]), month), "month")
    if m := _YEAR.match(text):
        y = int(m[1])
        return (*_year_span(y, y), "year")
    return None


def parse_dob(raw: str | None, circa: bool = False, *, dayfirst: bool = True) -> DobParse:
    """Parse a published date of birth into a date range plus precision.

    ``circa`` dates are widened by one year on each side. Dates before 1900 or in
    the future are returned with precision ``invalid`` and no dates.
    """
    text = (raw or "").strip()
    if not text:
        return DobParse(None, None, "unknown", circa)
    if m := _CIRCA.match(text):
        circa = True
        text = text[m.end() :].strip()
    try:
        parsed = _parse_core(text, dayfirst)
    except ValueError:  # e.g. 31/02/1970
        parsed = None
    if parsed is None:
        return DobParse(None, None, "unparseable", circa)
    date_from, date_to, precision = parsed
    if date_from.year < MIN_YEAR or date_from > date.today():
        return DobParse(None, None, "invalid", circa)
    if circa:
        date_from = date(date_from.year - 1, 1, 1)
        date_to = date(date_to.year + 1, 12, 31)
    return DobParse(date_from, date_to, precision, circa)


def parse_date(raw: str | None, fmt: str) -> date | None:
    """Strict single-format date parse for metadata fields (listing dates etc.).

    Also accepts ISO timestamps by retrying on the first 10 characters.
    """
    text = (raw or "").strip()
    for candidate in (text, text[:10]):
        if not candidate:
            continue
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None
