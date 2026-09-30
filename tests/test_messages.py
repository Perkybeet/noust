# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`noust.core.messages`.

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

import re
import string

import pytest

from noust.core.messages import DEFAULT_LOCALE, MESSAGES, message, normalize_locale, plural

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
        untranslated = {
            key
            for key, catalog in MESSAGES.items()
            if catalog["en"].strip().lower() == catalog["es"].strip().lower()
        }
        # Names that are the same word in both languages: "commit" and "CPU"
        # are used untranslated in Spanish technical writing, and a product
        # or a value is not a word to translate.
        assert untranslated == {
            "fact.commit",
            "fact.cpu",
            "trigger.cli",
            "trigger.webhook",
            "channel.webhook",
            "channel.slack",
            "channel.discord",
            "channel.telegram",
        }

    #: English function words that no Spanish sentence contains. A hurried
    #: translation leaves one behind; this is how a key that only looks
    #: translated is caught.
    ENGLISH_WORDS = frozenset(
        {"the", "did", "not", "is", "was", "and", "with", "from", "have", "been", "of", "failed"}
    )

    def test_no_spanish_sentence_keeps_an_english_word(self) -> None:
        leaks = {}
        for key, catalog in MESSAGES.items():
            words = set(re.findall(r"[a-zA-Z']+", re.sub(r"\{[^}]*\}", "", catalog["es"]).lower()))
            found = words & self.ENGLISH_WORDS
            if found:
                leaks[key] = sorted(found)
        assert leaks == {}

    def test_every_key_belongs_to_a_known_group(self) -> None:
        groups = {key.split(".", 1)[0] for key in MESSAGES}
        assert groups == {
            "title",
            "summary",
            "fact",
            "trigger",
            "channel",
            "severity",
            "ui",
            "excerpt",
        }

    def test_every_title_has_a_summary(self) -> None:
        titles = {key.split(".", 1)[1] for key in MESSAGES if key.startswith("title.")}
        summaries = {key.split(".", 1)[1] for key in MESSAGES if key.startswith("summary.")}

        assert titles <= summaries
        # The one summary that is a variant of another's title.
        assert summaries - titles == {"cert.expiring_today"}

    def test_a_title_never_carries_a_placeholder(self) -> None:
        """The subject and the facts live elsewhere; a title is a state and nothing else."""
        for key, catalog in MESSAGES.items():
            if key.startswith("title."):
                assert _placeholders(catalog["en"]) == set(), key


class TestMessage:
    """Rendering one catalog entry."""

    def test_renders_english_by_default_locale(self) -> None:
        assert DEFAULT_LOCALE == "en"
        assert message("title.deploy.started", "en") == "Deploying"

    def test_renders_spanish(self) -> None:
        assert message("title.deploy.started", "es") == "Desplegando"

    def test_renders_placeholders(self) -> None:
        assert message("summary.test", "es", channel="Telegram") == (
            "Si ves esto, el canal Telegram está bien configurado."
        )

    def test_unknown_key_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="not-a-real-key"):
            message("not-a-real-key", "en")

    def test_missing_placeholder_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match=r"summary\.test"):
            message("summary.test", "en")


class TestNormalizeLocale:
    """Coercing a raw config value to a supported locale."""

    @pytest.mark.parametrize("value", ["es", "ES", " es ", "Es"])
    def test_accepts_spanish_case_and_space_insensitively(self, value: str) -> None:
        assert normalize_locale(value) == "es"

    @pytest.mark.parametrize("value", ["en", "", None, "fr", "spanish", 42])
    def test_everything_else_is_english(self, value: object) -> None:
        assert normalize_locale(value) == "en"


class TestPlural:
    """The one/other rule both locales share."""

    @pytest.mark.parametrize(
        ("key", "locale", "word"),
        [
            ("day", "en", "day"),
            ("day", "es", "día"),
            ("time", "en", "time"),
            ("time", "es", "vez"),
            ("process", "en", "process"),
            ("process", "es", "proceso"),
        ],
    )
    def test_one(self, key: str, locale: str, word: str) -> None:
        assert plural(key, locale, 1) == word  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("key", "count", "locale", "word"),
        [
            ("day", 0, "en", "days"),
            ("day", 2, "en", "days"),
            ("day", 0, "es", "días"),
            ("day", 14, "es", "días"),
            ("time", 3, "es", "veces"),
            ("process", 2, "en", "processes"),
        ],
    )
    def test_everything_else(self, key: str, count: int, locale: str, word: str) -> None:
        assert plural(key, locale, count) == word  # type: ignore[arg-type]
