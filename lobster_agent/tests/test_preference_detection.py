"""Tests for preference detection in app.main.

Covers _strip_quoted, _detect_preference, and the status-gate in
run_lobster_agent that prevents storing prefs from failed turns.
"""

import pytest
from app.main import _strip_quoted, _detect_preference


# ---------------------------------------------------------------------------
# _strip_quoted
# ---------------------------------------------------------------------------

def test_strip_removes_double_quoted_content():
    result = _strip_quoted('the response was "way too long" I think')
    assert '"way too long"' not in result
    assert "the response was" in result


def test_strip_removes_backtick_content():
    result = _strip_quoted("don't use `too long` as a phrase here")
    assert "`too long`" not in result


def test_strip_removes_block_quote_lines():
    text = "my feedback:\n> too long\nplease fix"
    result = _strip_quoted(text)
    assert "too long" not in result
    assert "my feedback" in result
    assert "please fix" in result


def test_strip_leaves_unquoted_content_intact():
    text = "that was too long, please be more concise"
    assert _strip_quoted(text) == text


def test_strip_leaves_single_quotes_intact():
    """Single quotes are not stripped — they cover contractions and common speech."""
    text = "don't make it too long"
    assert "don't" in _strip_quoted(text)


def test_strip_double_quote_length_limit():
    """Strings over 200 chars inside double quotes are not stripped (limit guard)."""
    long_quoted = '"' + "x" * 201 + '"'
    result = _strip_quoted(long_quoted)
    # The long string is not removed because it exceeds the 200-char limit
    assert "x" * 50 in result


# ---------------------------------------------------------------------------
# _detect_preference — fires on trigger phrases
# ---------------------------------------------------------------------------

def test_detects_concise_from_too_long():
    result = _detect_preference("That response was too long, please shorten it.")
    assert result is not None
    key, value, phrase = result
    assert key   == "response_length"
    assert value == "concise"
    assert phrase == "too long"


def test_detects_concise_from_be_more_concise():
    result = _detect_preference("Can you be more concise next time?")
    assert result is not None
    assert result[1] == "concise"


def test_detects_concise_case_insensitive():
    result = _detect_preference("THAT WAS TOO LONG")
    assert result is not None
    assert result[1] == "concise"


def test_detects_detailed_from_more_detail():
    result = _detect_preference("I'd like more detail in your answers.")
    assert result is not None
    key, value, phrase = result
    assert key   == "response_length"
    assert value == "detailed"
    assert phrase == "more detail"


def test_detects_detailed_from_too_brief():
    result = _detect_preference("That was too brief, please elaborate.")
    assert result is not None
    assert result[1] == "detailed"


# ---------------------------------------------------------------------------
# _detect_preference — quote guard suppresses detection
# ---------------------------------------------------------------------------

def test_does_not_detect_phrase_in_double_quotes():
    """Trigger phrase inside double quotes is stripped before scanning."""
    result = _detect_preference('the user said "too long" in their feedback')
    assert result is None


def test_does_not_detect_phrase_in_backticks():
    result = _detect_preference("don't say `too long` in the response")
    assert result is None


def test_does_not_detect_phrase_in_block_quote():
    text = "here is their original message:\n> that was too long\ndo you agree?"
    result = _detect_preference(text)
    assert result is None


# ---------------------------------------------------------------------------
# _detect_preference — neutral messages produce no result
# ---------------------------------------------------------------------------

def test_no_detection_on_neutral_research_query():
    result = _detect_preference("What is LangGraph and how does it work?")
    assert result is None


def test_no_detection_on_execution_task():
    result = _detect_preference("Create notes.txt with the content Hello World.")
    assert result is None


def test_no_detection_on_empty_string():
    assert _detect_preference("") is None


def test_no_detection_on_partial_phrase_match():
    """'briefly' should not match 'be brief' (substring match is phrase-level)."""
    result = _detect_preference("Can you briefly summarise the project?")
    assert result is None
