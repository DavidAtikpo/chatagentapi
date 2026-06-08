import calendar
import re
import unicodedata
from datetime import date
from typing import Any

_MONTHS: dict[str, int] = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}


def _normalize(text: str) -> str:
    lowered = text.lower()
    normalized = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _extract_year(label: str, today: date) -> int:
    match = re.search(r"(20\d{2})", label)
    return int(match.group(1)) if match else today.year


def _extract_month(label: str) -> int | None:
    normalized = _normalize(label)
    for name, month in _MONTHS.items():
        if re.search(rf"\b{re.escape(name)}\b", normalized):
            return month
    return None


def _extract_end_day(label: str) -> int | None:
    normalized = _normalize(label)
    exam = re.search(r"examen\s*(?:le\s*)?(\d{1,2})", normalized)
    if exam:
        return int(exam.group(1))
    days = re.findall(r"\bau\s+(\d{1,2})\b", normalized)
    if days:
        return int(days[-1])
    return None


def parse_session_end_date(label: str, today: date | None = None) -> date | None:
    """Return the last relevant day for a session label (exam day or month end)."""
    today = today or date.today()
    month = _extract_month(label)
    if month is None:
        return None

    year = _extract_year(label, today)
    day = _extract_end_day(label)

    normalized = _normalize(label)
    if ("nov" in normalized and "dec" in normalized) or ("novembre" in normalized and "decembre" in normalized):
        month = 12
        if day and day <= 7:
            pass
        elif day is None:
            day = 4

    if day:
        try:
            return date(year, month, day)
        except ValueError:
            pass

    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day)


def is_upcoming_session(label: str, today: date | None = None) -> bool:
    today = today or date.today()
    normalized = _normalize(label)

    if any(
        phrase in normalized
        for phrase in (
            "non programme",
            "non programmee",
            "pas encore programm",
            "dates non encore programm",
            "du au (examen )",
        )
    ):
        end = parse_session_end_date(label, today)
        if end is None:
            month = _extract_month(label)
            year = _extract_year(label, today)
            if month is None:
                return True
            return date(year, month, 1) >= date(today.year, today.month, 1)
        return end >= today

    end = parse_session_end_date(label, today)
    if end is None:
        return True
    return end >= today


def sort_sessions_by_date(sessions: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()

    def sort_key(session: dict[str, Any]) -> tuple[int, date]:
        label = session.get("label", "")
        end = parse_session_end_date(label, today)
        if end is None:
            return (1, date.max)
        return (0, end)

    return sorted(sessions, key=sort_key)


def filter_upcoming_sessions(
    sessions: list[dict[str, Any]], today: date | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    today = today or date.today()
    upcoming = [s for s in sessions if is_upcoming_session(s.get("label", ""), today)]
    ordered = sort_sessions_by_date(upcoming, today)
    if limit is not None:
        return ordered[:limit]
    return ordered
