import calendar
import re
import unicodedata
from datetime import date
from typing import Any

_MONTHS: dict[str, int] = {
    "janvier": 1,
    "janv": 1,
    "jan": 1,
    "fevrier": 2,
    "fevr": 2,
    "fev": 2,
    "mars": 3,
    "mar": 3,
    "avril": 4,
    "avr": 4,
    "mai": 5,
    "juin": 6,
    "jun": 6,
    "juillet": 7,
    "juil": 7,
    "jul": 7,
    "aout": 8,
    "septembre": 9,
    "sept": 9,
    "sep": 9,
    "octobre": 10,
    "oct": 10,
    "novembre": 11,
    "nov": 11,
    "decembre": 12,
    "dec": 12,
}


def _normalize(text: str) -> str:
    lowered = text.lower()
    normalized = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _month_pattern() -> str:
    names = sorted(_MONTHS.keys(), key=len, reverse=True)
    return "|".join(re.escape(name) for name in names)


def _extract_year(label: str, today: date) -> int:
    match = re.search(r"(20\d{2})", label)
    return int(match.group(1)) if match else today.year


def _extract_all_months(label: str) -> list[int]:
    normalized = _normalize(label)
    found: list[tuple[int, int]] = []
    pattern = _month_pattern()
    for match in re.finditer(rf"\b({pattern})\b", normalized):
        month = _MONTHS.get(match.group(1))
        if month is not None:
            found.append((match.start(), month))
    found.sort(key=lambda item: item[0])
    months: list[int] = []
    for _, month in found:
        if not months or months[-1] != month:
            months.append(month)
    return months


def _extract_month(label: str) -> int | None:
    months = _extract_all_months(label)
    return months[0] if months else None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_session_end_date(label: str, today: date | None = None) -> date | None:
    """Return the last relevant day for a session label (exam day or training end)."""
    today = today or date.today()
    normalized = _normalize(label)
    months = _extract_all_months(label)
    if not months:
        return None

    year = _extract_year(label, today)
    last_month = months[-1]

    exam = re.search(r"examen\s*(?:le\s*)?(\d{1,2})", normalized)
    if exam:
        exam_day = int(exam.group(1))
        exam_month = last_month
        if len(months) >= 2 and exam_day < 15:
            exam_month = months[-1]
        parsed = _safe_date(year, exam_month, exam_day)
        if parsed:
            return parsed

    au_with_month = re.search(
        rf"\bau\s+(\d{{1,2}})\s+({_month_pattern()})\b",
        normalized,
    )
    if au_with_month:
        day = int(au_with_month.group(1))
        month = _MONTHS.get(au_with_month.group(2), last_month)
        parsed = _safe_date(year, month, day)
        if parsed:
            return parsed

    au_days = re.findall(r"\bau\s+(\d{1,2})\b", normalized)
    if au_days:
        parsed = _safe_date(year, last_month, int(au_days[-1]))
        if parsed:
            return parsed

    if ("nov" in normalized and "dec" in normalized) or (
        "novembre" in normalized and "decembre" in normalized
    ):
        parsed = _safe_date(year, 12, 4)
        if parsed:
            return parsed

    last_day = calendar.monthrange(year, last_month)[1]
    return date(year, last_month, last_day)


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
            months = _extract_all_months(label)
            year = _extract_year(label, today)
            if not months:
                return True
            return date(year, months[0], 1) >= date(today.year, today.month, 1)
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
