"""Tests for CI.DES session date parsing (cross-month labels)."""

from datetime import date

from app.services.session_dates import (
    filter_upcoming_sessions,
    is_upcoming_session,
    parse_session_end_date,
)


def test_cross_month_june_july_exam():
    label = "7. 2026 / juillet: du 29 juin au 03 juillet (Examen 4)"
    assert parse_session_end_date(label, date(2026, 6, 18)) == date(2026, 7, 4)


def test_cross_month_june_july_is_upcoming():
    label = "7. 2026 / juillet: du 29 juin au 03 juillet (Examen 4)"
    assert is_upcoming_session(label, date(2026, 6, 18)) is True
    assert is_upcoming_session(label, date(2026, 7, 5)) is False


def test_single_month_session():
    label = "6. 2026 / juin: du 15 au 19 (Examen 20)"
    assert parse_session_end_date(label, date(2026, 6, 1)) == date(2026, 6, 20)


def test_nov_dec_abbreviation():
    label = "12. 2026/ decembre : du 30 nov au 04 dec (Examen 05)"
    assert parse_session_end_date(label, date(2026, 11, 1)) == date(2026, 12, 5)


def test_filter_keeps_july_session_in_june():
    sessions = [
        {"label": "6. 2026 / juin: du 15 au 19 (Examen 20)", "url": "a", "region": "france"},
        {"label": "7. 2026 / juillet: du 29 juin au 03 juillet (Examen 4)", "url": "b", "region": "france"},
    ]
    today = date(2026, 6, 18)
    upcoming = filter_upcoming_sessions(sessions, today)
    labels = [s["label"] for s in upcoming]
    assert any("29 juin" in lbl for lbl in labels)
