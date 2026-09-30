# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`noust.core.notifications.model`.

What is defended:

- **Text that someone else chose cannot reach a renderer as it came**: control
  characters, terminal escapes and bidirectional overrides are gone by
  construction, so no channel has to remember to remove them.
- **The five state glyphs are text**: none of them has the Unicode Emoji
  property, so no client draws them as a coloured picture.
- **A kind nothing can filter is refused at construction**, the guard
  ``NotificationEvent`` used to be.
"""

from __future__ import annotations

from datetime import timezone

import pytest

from noust.core.notifications.model import (
    EVENT_KINDS,
    OFF_BY_DEFAULT,
    STATE_GLYPHS,
    Excerpt,
    Fact,
    Notification,
    State,
    clean_inline,
    clean_line,
)

#: Code points that carry the Unicode Emoji property and that a hurried
#: "success"/"warning" glyph tends to be: they render as coloured pictures.
EMOJI_GLYPHS = "✔✖⚠❌✅▶◀⭐❗"


def make(**overrides: object) -> Notification:
    fields: dict = {
        "kind": "deploy_success",
        "code": "deploy.succeeded",
        "state": State.OK,
        "locale": "en",
        "title": "Deployed",
        "subject": "shop.example.com",
        "summary": "The new version is live.",
        "server": "web-1",
    }
    fields.update(overrides)
    return Notification(**fields)


class TestGlyphs:
    def test_every_state_has_a_glyph(self) -> None:
        assert set(STATE_GLYPHS) == set(State)

    def test_the_glyphs_are_the_documented_ones(self) -> None:
        assert STATE_GLYPHS == {
            State.OK: "●",
            State.PROGRESS: "◐",
            State.WARNING: "▲",
            State.FAILED: "✕",
            State.INFO: "○",
        }

    def test_none_of_them_is_an_emoji(self) -> None:
        for glyph in STATE_GLYPHS.values():
            assert len(glyph) == 1
            assert glyph not in EMOJI_GLYPHS
            assert "️" not in glyph

    def test_every_state_glyph_is_distinct(self) -> None:
        assert len(set(STATE_GLYPHS.values())) == len(State)


class TestCleaning:
    def test_terminal_escapes_are_removed(self) -> None:
        assert clean_line("\x1b[31mred\x1b[0m text") == "red text"
        assert clean_line("\x1b]0;title\x07after") == "after"

    def test_control_characters_are_removed(self) -> None:
        assert clean_line("a\x00b\x07c\x7fd") == "abcd"

    def test_bidirectional_overrides_are_removed(self) -> None:
        assert clean_line("safe\u202egnp.exe\u202c") == "safegnp.exe"
        assert clean_line("a\u2066b\u2069c") == "abc"

    def test_a_line_keeps_its_indentation(self) -> None:
        assert clean_line("    at Module._compile (node:internal)") == (
            "    at Module._compile (node:internal)"
        )

    def test_a_line_loses_its_trailing_space_and_carriage_return(self) -> None:
        assert clean_line("value   \r") == "value"

    def test_an_inline_value_becomes_one_line(self) -> None:
        assert clean_inline("first\nsecond\t\tthird") == "first second third"

    def test_an_inline_value_is_trimmed(self) -> None:
        assert clean_inline("  padded  ") == "padded"

    def test_a_zero_width_space_is_removed_so_defusing_is_only_the_renderers(self) -> None:
        assert clean_inline("@\u200beveryone") == "@everyone"


class TestConstruction:
    def test_defaults_are_utc_and_unique(self) -> None:
        first, second = make(), make()

        assert first.ts.tzinfo is timezone.utc
        assert first.id != second.id
        assert len(first.id) == 36

    def test_every_text_field_is_cleaned(self) -> None:
        notification = make(
            title="Deployed\x1b[0m",
            subject="shop\u202e.example.com",
            summary="line one\nline two",
            server="web\x001",
            facts=(Fact("commit", "Comm\tit", "abc\n(main)"),),
            excerpt=Excerpt("Journal", ("ok \x1b[31mbad\x1b[0m", "x\u202ey")),
        )

        assert notification.title == "Deployed"
        assert notification.subject == "shop.example.com"
        assert notification.summary == "line one line two"
        assert notification.server == "web1"
        assert notification.facts[0].label == "Comm it"
        assert notification.facts[0].value == "abc (main)"
        assert notification.excerpt is not None
        assert notification.excerpt.lines == ("ok bad", "xy")

    def test_a_multi_line_excerpt_line_is_split_not_kept(self) -> None:
        excerpt = Excerpt("Journal", ("one\ntwo", "three"))

        assert excerpt.lines == ("one", "two", "three")

    def test_the_headline_is_state_and_subject(self) -> None:
        assert make().headline == "Deployed · shop.example.com"

    def test_the_headline_without_a_subject_is_the_title(self) -> None:
        assert make(subject="").headline == "Deployed"

    def test_glyph_follows_state(self) -> None:
        assert make(state=State.FAILED).glyph == "✕"

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="deploy_sucess"):
            make(kind="deploy_sucess")

    def test_the_test_and_report_kinds_are_accepted(self) -> None:
        assert make(kind="test").kind == "test"
        assert make(kind="report").kind == "report"

    @pytest.mark.parametrize("kind", EVENT_KINDS)
    def test_every_event_kind_is_accepted(self, kind: str) -> None:
        assert make(kind=kind).kind == kind

    def test_off_by_default_kinds_are_kinds(self) -> None:
        assert set(OFF_BY_DEFAULT) <= set(EVENT_KINDS)
        assert "deploy_started" in OFF_BY_DEFAULT
        assert "backup_success" in OFF_BY_DEFAULT

    def test_a_notification_is_immutable(self) -> None:
        with pytest.raises(AttributeError):
            make().title = "other"  # type: ignore[misc]
