"""
test_placeholder_match.py
Tests for placeholder extraction and multiset comparison.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(__file__, "..", "..", "..", "..")))

from qazaq_loc_qa.backend.fixes.engine import extract_placeholders, placeholders_match, validate_fix


class TestExtractPlaceholders:
    def test_curly_name(self):
        assert "{name}" in extract_placeholders("Hello {name}")

    def test_curly_count(self):
        assert "{count}" in extract_placeholders("{count} items")

    def test_curly_index(self):
        assert "{0}" in extract_placeholders("Item {0}")

    def test_percent_s(self):
        assert "%s" in extract_placeholders("Hello %s")

    def test_angle_ph(self):
        assert "<ph>" in extract_placeholders("Value <ph>")

    def test_multiple(self):
        result = extract_placeholders("{name} has {count} items at {0}")
        assert len(result) == 3

    def test_repeated(self):
        result = extract_placeholders("{name} and {name}")
        assert result.count("{name}") == 2


class TestPlaceholdersMatch:
    def test_match_same(self):
        assert placeholders_match("Hello {name}", "Привет {name}") is True

    def test_mismatch_different_name(self):
        assert placeholders_match("Hello {name}", "Привет {user}") is False

    def test_mismatch_count(self):
        assert placeholders_match("{name} has {count}", "{name} только") is False

    def test_match_repeated(self):
        assert placeholders_match("{x} and {x}", "Перевод {x} и {x}") is True

    def test_mismatch_repeated_count(self):
        assert placeholders_match("{x} and {x}", "Перевод {x} только") is False

    def test_match_percent_s(self):
        assert placeholders_match("Hello %s", "Привет %s") is True

    def test_mismatch_percent_s(self):
        assert placeholders_match("Hello %s world %s", "Привет %s") is False

    def test_match_angle(self):
        assert placeholders_match("Value <ph>", "Мән <ph>") is True

    def test_empty_both(self):
        assert placeholders_match("No placeholders", "Плейсхолдерлер жоқ") is True

    def test_original_empty_suggested_has(self):
        assert placeholders_match("No placeholders", "Перевод {name}") is False
