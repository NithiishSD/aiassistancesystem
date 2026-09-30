"""Relative dates in stored facts are anchored to the day they were said
(OpenSpec change: anchor-relative-dates)."""

from datetime import datetime

import pytest

import fact_dates
import memory
from tests.test_fact_history import collection  # noqa: F401  (fixture)

NOW = datetime(2026, 9, 30, 10, 0)  # a Wednesday


@pytest.mark.parametrize("fact, anchored", [
    ("User's exam: next week", "User's exam: next week (week of 5 October 2026)"),
    ("User's quiz: this week", "User's quiz: this week (week of 28 September 2026)"),
    ("User's move date: last month", "User's move date: last month (August 2026)"),
    ("User's internship: starts next month", "User's internship: starts next month (October 2026)"),
    ("User's goal: graduate next year", "User's goal: graduate next year (2027)"),
    ("User's interview: tomorrow", "User's interview: tomorrow (1 October 2026)"),
    ("User's submission: day after tomorrow", "User's submission: day after tomorrow (2 October 2026)"),
    ("User's viva: today", "User's viva: today (30 September 2026)"),
    ("User's lab test: yesterday", "User's lab test: yesterday (29 September 2026)"),
    ("User's trip: in 3 days", "User's trip: in 3 days (3 October 2026)"),
    ("User's contest: in two weeks", "User's contest: in two weeks (around 14 October 2026)"),
    ("User's laptop: bought 2 years ago", "User's laptop: bought 2 years ago (2024)"),
    ("User's exam: on Friday at 9am", "User's exam: on Friday (2 October 2026) at 9am"),
    ("User's review: this Wednesday", "User's review: this Wednesday (7 October 2026)"),     # not today
])
def test_relative_date_is_followed_by_what_it_meant(fact, anchored):
    assert fact_dates.anchor_relative_dates(fact, NOW) == anchored


@pytest.mark.parametrize("fact, anchored", [
    ("User's presentation: next Friday", "User's presentation: next Friday (said on Wednesday 30 September 2026)"),
    ("User's electives: chosen last semester",
     "User's electives: chosen last semester (said on Wednesday 30 September 2026)"),
])
def test_expression_with_no_single_reading_records_the_day_it_was_said(fact, anchored):
    assert fact_dates.anchor_relative_dates(fact, NOW) == anchored


@pytest.mark.parametrize("fact", [
    "User's college: Northwind Institute", "User's weekly meeting: every Monday",
    "User's exam: 12 October 2026", "User's favourite day: Friday", "",
])
def test_facts_without_a_relative_date_are_unchanged(fact):
    assert fact_dates.anchor_relative_dates(fact, NOW) == fact


def test_only_the_first_expression_is_anchored_and_never_twice():
    once = fact_dates.anchor_relative_dates("User's note: said yesterday that tomorrow is fine", NOW)
    assert once == "User's note: said yesterday (29 September 2026) that tomorrow is fine"
    assert fact_dates.anchor_relative_dates(once, datetime(2027, 1, 1)) == once


def test_month_and_year_boundaries():
    december = datetime(2026, 12, 31, 23, 0)
    assert fact_dates.anchor_relative_dates("x: next month", december) == "x: next month (January 2027)"
    assert fact_dates.anchor_relative_dates("x: tomorrow", december) == "x: tomorrow (1 January 2027)"
    assert fact_dates.anchor_relative_dates("x: last month", datetime(2026, 1, 5)) == "x: last month (December 2025)"


def test_stored_fact_is_anchored_to_the_day_it_was_stored(collection, monkeypatch):  # noqa: F811
    monkeypatch.setattr(memory.time, "time", lambda: NOW.timestamp())
    item_id = memory.store("User's OS exam: next week")
    assert collection.get(ids=[item_id])["documents"] == ["User's OS exam: next week (week of 5 October 2026)"]
    conversation = memory.store("user said the exam is next week", content_type="conversation")
    assert collection.get(ids=[conversation])["documents"] == ["user said the exam is next week"]
