from __future__ import annotations

import re
from datetime import date, datetime


def parse_date(value: object, default_year: int | None = None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return None
    text = text.replace(" ", "")

    patterns = [
        (r"(\d{4})年(\d{1,2})月(\d{1,2})日", None),
        (r"(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})", None),
    ]
    for pattern, _ in patterns:
        match = re.search(pattern, text)
        if match:
            y, m, d = map(int, match.groups())
            return _safe_iso(y, m, d)

    if default_year:
        match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
        if match:
            m, d = map(int, match.groups())
            return _safe_iso(default_year, m, d)

        match = re.search(r"(\d{1,2})[-/](\d{1,2})", text)
        if match:
            m, d = map(int, match.groups())
            return _safe_iso(default_year, m, d)

    return None


def date_year(value: str | None) -> int | None:
    parsed = parse_date(value)
    if not parsed:
        return None
    return int(parsed[:4])


def day_diff(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None


def display_date(value: str | None, full: bool = True) -> str:
    parsed = parse_date(value)
    if not parsed:
        return ""
    if full:
        return parsed
    return parsed[5:]


def _safe_iso(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None
