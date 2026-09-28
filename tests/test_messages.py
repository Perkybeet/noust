# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`wasm.core.messages`.

What is defended:

- **Both locales are the same catalog**: identical keys, identical
  ``str.format`` placeholders, no empty text - the property a plain Python
  dict cannot enforce on its own.
- **``message`` renders a key** and refuses one that is missing, or missing a
  placeholder, rather than sending an operator a half-built sentence.
- **``normalize_locale`` only ever answers "en" or "es"**, whatever a stale or
  hand-edited config file holds.
- **``plural`` follows the one/other rule** both English and Spanish use for
  the nouns this project counts.
"""

from __future__ import annotations

import string

import pytest

from wasm.core.messages import DEFAULT_LOCALE, MESSAGES, message, normalize_locale, plural

_FORMATTER = string.Formatter()


def _placeholders(template: str) -> set[str]:
    """
    Args:
        template: A ``str.format`` template.

    Returns:
        Every named field it references.
    """
    return {field for _, field, _, _ in _FORMATTER.parse(template) if field}


class TestCatalogShape:
    """Both locales must be the same catalog, key for key."""

    def test_every_key_has_both_locales(self) -> None:
        for key, catalog in MESSAGES.items():
            assert set(catalog) == {"en", "es"}, key

    def test_no_locale_is_empty(self) -> None:
        for key, catalog in MESSAGES.items():
            for locale, text in catalog.items():
                assert text.strip(), f"{key} ({locale}) is empty"

    def test_both_locales_of_a_key_use_the_same_placeholders(self) -> None:
        for key, catalog in MESSAGES.items():
            assert _placeholders(catalog["en"]) == _placeholders(catalog["es"]), key

    def test_the_spanish_text_actually_differs_from_english(self) -> None:
        """A key present in both locales that says the same thing was never translated."""
        untranslated = [
            key
            for key, catalog in MESSAGES.items()
            if catalog["en"].strip().lower() == catalog["es"].strip().lower()
        ]
        # "Commit: {commit}" is deliberately identical: "commit" is used
        # untranslated in Spanish technical writing.
        assert untranslated == ["deploy_commit"]


class TestMessage:
    """Rendering one catalog entry."""

    def test_renders_english_by_default_locale(self) -> None:
        assert DEFAULT_LOCALE == "en"
        assert message("deploy_started_title", "en", domain="shop.example.com") == (
            "Deploying shop.example.com"
        )

    def test_renders_spanish(self) -> None:
        assert message("deploy_started_title", "es", domain="shop.example.com") == (
            "Desplegando shop.example.com"
        )

    def test_unknown_key_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="not-a-real-key"):
            message("not-a-real-key", "en")

    def test_missing_placeholder_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="deploy_started_title"):
            message("deploy_started_title", "en")


class TestNormalizeLocale:
    """Coercing a raw config value to a supported locale."""

    @pytest.mark.parametrize("value", ["es", "ES", " es ", "Es"])
    def test_accepts_spanish_case_and_space_insensitively(self, value: str) -> None:
        assert normalize_locale(value) == "es"

    @pytest.mark.parametrize("value", ["en", "", None, "fr", "spanish", 42])
    def test_everything_else_is_english(self, value: object) -> None:
        assert normalize_locale(value) == "en"


class TestPlural:
    """
    The one/other rule both locales share.

    English's own "day(s)" predates 2.3 and is a byte-for-byte contract
    tests/test_monitor_safety.py asserts on, so it does not vary with count;
    Spanish gets the real singular and plural.
    """

    @pytest.mark.parametrize("locale,word", [("en", "day(s)"), ("es", "día")])
    def test_one(self, locale: str, word: str) -> None:
        assert plural("day", locale, 1) == word  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("count", "locale", "word"),
        [
            (0, "en", "day(s)"),
            (2, "en", "day(s)"),
            (0, "es", "días"),
            (14, "es", "días"),
        ],
    )
    def test_everything_else(self, count: int, locale: str, word: str) -> None:
        assert plural("day", locale, count) == word  # type: ignore[arg-type]
