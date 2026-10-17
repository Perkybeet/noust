# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Variables of several lines, literals and comments in a ``.env`` (checklist B-51).

A private key, a certificate or a JSON document is a value of several lines. It is written between
single quotes, where systemd's ``EnvironmentFile=``, Compose and dotenv all read everything up to
the closing quote as the value, a ``$`` stays a ``$``, and a comment above a variable survives the
file being written again. Pinned here:

- how a value that opens a quote and goes on is read, and that a quote nothing closes is read as it
  always was;
- what the writer produces for it and that what it wrote reads back byte for byte, and that it
  refuses what could not (a quote that would end the value early);
- what the validators let through: a line feed and nothing else, no single quote beside it;
- that a comment above a variable is read, written and kept through a write that says nothing
  about comments.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import SecurityError
from noust.deployers.helpers.env_manager import EnvEntry, EnvManager
from noust.validators.environment import (
    MAX_COMMENT_CHARS,
    EnvironmentValidationError,
    validate_env_comment,
    validate_env_value,
    validate_environment,
)

PEM = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\nZGVm==\n-----END PRIVATE KEY-----"


def read(text: str) -> dict[str, str]:
    return EnvManager.parse_env_text(text)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_a_single_quoted_value_goes_on_until_a_line_that_ends_with_the_quote() -> None:
    assert read(f"KEY='{PEM}'\nNEXT=1\n") == {"KEY": PEM, "NEXT": "1"}


def test_a_double_quoted_value_goes_on_the_same_way_and_keeps_its_escapes_as_written() -> None:
    assert read('JSON="{\n  \\"a\\": 1\n}"\n') == {"JSON": '{\n  \\"a\\": 1\n}'}


def test_the_lines_keep_their_indentation_and_their_blank_lines() -> None:
    assert read("K='  a\n\n    b  '\n") == {"K": "  a\n\n    b  "}


def test_trailing_spaces_after_the_closing_quote_are_not_the_value() -> None:
    assert read("K='a\nb'   \n") == {"K": "a\nb"}


def test_the_value_may_begin_or_end_with_a_line_break() -> None:
    assert read("K='\nsecond'\n") == {"K": "\nsecond"}
    assert read("K='first\n'\n") == {"K": "first\n"}


def test_the_lines_of_a_value_are_not_assignments_even_when_they_look_like_one() -> None:
    """The padding of a key's last line, ``ZGVm==``, reads as NAME= to a careless parser."""
    values = read("KEY='a\nINJECTED=1\nZGVm=='\nAFTER=2\n")

    assert values == {"KEY": "a\nINJECTED=1\nZGVm==", "AFTER": "2"}


def test_the_spaces_at_the_end_of_the_first_line_are_the_values() -> None:
    assert read("K='first   \nsecond'\n") == {"K": "first   \nsecond"}
    assert read("export  K = '  first  \nsecond  '  \n") == {"K": "  first  \nsecond  "}


def test_a_comment_inside_the_value_is_part_of_it() -> None:
    assert read("K='a\n# not a comment\nb'\n") == {"K": "a\n# not a comment\nb"}


def test_an_export_keyword_does_not_stop_a_value_from_going_on() -> None:
    assert read("export K='a\nb'\n") == {"K": "a\nb"}


def test_windows_line_endings_read_the_same(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_bytes(b"K='a\r\nb'\r\nN=1\r\n")

    assert EnvManager().read_env_file(path) == {"K": "a\nb", "N": "1"}


def test_a_quote_nothing_closes_is_the_line_it_always_was() -> None:
    assert read('A="never closed\nB=1\nC=2\n') == {
        "A": '"never closed',
        "B": "1",
        "C": "2",
    }


def test_a_quote_a_later_line_of_the_other_kind_ends_does_not_close_it() -> None:
    assert read("A=\"open\nB='x'\n") == {"A": '"open', "B": "x"}


def test_a_value_closed_on_its_line_is_not_looked_for_further_on() -> None:
    assert read("A='one'\nB='two'\n") == {"A": "one", "B": "two"}


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


def entries(text: str) -> list[EnvEntry]:
    return EnvManager.parse_env_entries(text)


def test_the_comment_above_a_variable_is_its_own() -> None:
    [entry] = entries("# Where the queue lives.\n# Change it with care.\nQUEUE=redis\n")

    assert entry == EnvEntry("QUEUE", "redis", "Where the queue lives.\nChange it with care.")


def test_a_blank_line_between_a_comment_and_a_variable_separates_them() -> None:
    [entry] = entries("# about nothing in particular\n\nQUEUE=redis\n")

    assert entry.comment == ""


def test_a_comment_belongs_to_the_next_variable_only() -> None:
    first, second = entries("# the first\nA=1\nB=2\n")

    assert (first.comment, second.comment) == ("the first", "")


def test_the_comment_above_a_value_of_several_lines_is_its_own() -> None:
    [entry] = entries("# the signing key\nKEY='a\nb'\n")

    assert entry.comment == "the signing key" and entry.value == "a\nb"


def test_comments_are_read_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# one\nA=1\nB=2\n# three\nC=3\n")

    assert EnvManager().read_env_comments(path) == {"A": "one", "C": "three"}


def test_a_file_that_is_not_there_or_is_a_link_has_no_comments(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.write_text("# secret\nA=1\n")
    (tmp_path / "link").symlink_to(target)

    manager = EnvManager()
    assert manager.read_env_comments(tmp_path / "nothing") == {}
    assert manager.read_env_comments(tmp_path / "link") == {}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def written(tmp_path: Path, values: dict[str, str], comments: dict[str, str] | None = None) -> str:
    path = tmp_path / ".env"
    EnvManager().write_env_file(path, values, comments)
    return path.read_text(encoding="utf-8")


def test_a_value_of_several_lines_is_written_between_single_quotes(tmp_path: Path) -> None:
    assert written(tmp_path, {"KEY": PEM}) == f"KEY='{PEM}'\n"


@pytest.mark.parametrize(
    "value",
    [
        PEM,
        "a\nb",
        "\nleading",
        "trailing\n",
        "a\n\nb",
        "  indented\n    more  ",
        "trailing spaces   \nnext   \nlast  ",
        'json {\n  "a": "b\\n"\n}',
        "has # hash\nand = equals\nand $dollar",
        "ends with a backslash\\\nnext",
    ],
)
def test_what_was_written_reads_back_byte_for_byte(tmp_path: Path, value: str) -> None:
    path = tmp_path / ".env"
    manager = EnvManager()

    manager.write_env_file(path, {"A": "x", "KEY": value, "Z": "y"})

    assert manager.read_env_file(path) == {"A": "x", "KEY": value, "Z": "y"}


def test_a_dollar_is_written_between_single_quotes_so_that_compose_does_not_expand_it(
    tmp_path: Path,
) -> None:
    assert written(tmp_path, {"PASSWORD": "pa$$w$ord"}) == "PASSWORD='pa$$w$ord'\n"
    assert written(tmp_path, {"URL": "https://x/${NAME}"}) == "URL='https://x/${NAME}'\n"


def test_a_value_with_a_dollar_and_a_single_quote_stays_as_it_was(tmp_path: Path) -> None:
    """No quoting carries both; it is left bare, as before."""
    assert written(tmp_path, {"P": "it's $5"}) == "P=it's $5\n"


def test_a_value_of_lines_that_holds_a_single_quote_falls_back_to_double_quotes(
    tmp_path: Path,
) -> None:
    """Only a hand-written file has one; the validators refuse it. It must still survive a rewrite."""
    value = "it's\nhand written"

    text = written(tmp_path, {"K": value})

    assert text == f'K="{value}"\n'
    assert EnvManager.parse_env_text(text) == {"K": value}


def test_a_value_that_would_end_early_is_refused_not_written_wrongly(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentValidationError, match="end with a quote"):
        written(tmp_path, {"K": 'it\'s "x"\nnext'})

    assert not (tmp_path / ".env").exists()


def test_the_comment_goes_above_its_variable(tmp_path: Path) -> None:
    text = written(
        tmp_path,
        {"A": "1", "QUEUE": "redis"},
        {"QUEUE": "Where the queue lives.\nChange it with care."},
    )

    assert text == "A=1\n# Where the queue lives.\n# Change it with care.\nQUEUE=redis\n"
    assert (
        EnvManager.parse_env_entries(text)[1].comment
        == "Where the queue lives.\nChange it with care."
    )


def test_a_write_that_says_nothing_about_comments_keeps_the_ones_the_file_has(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text("# about A\nA=1\n# about B\nB=2\n")

    EnvManager().write_env_file(path, {"A": "changed", "B": "2", "C": "new"})

    assert path.read_text() == "# about A\nA=changed\n# about B\nB=2\nC=new\n"


def test_a_comment_goes_with_a_variable_that_is_removed(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# about A\nA=1\n# about B\nB=2\n")

    EnvManager().write_env_file(path, {"B": "2"})

    assert path.read_text() == "# about B\nB=2\n"


def test_comments_given_as_empty_remove_every_one(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# about A\nA=1\n")

    EnvManager().write_env_file(path, {"A": "1"}, {})

    assert path.read_text() == "A=1\n"


def test_a_comment_cannot_open_a_line_of_its_own(tmp_path: Path) -> None:
    text = written(tmp_path, {"A": "1"}, {"A": "first\rEVIL=1\u2028EVIL2=2"})

    assert EnvManager.parse_env_text(text) == {"A": "1"}
    assert "EVIL" in text.splitlines()[0] and len(text.splitlines()) == 2


def test_the_file_is_still_private_and_a_link_is_still_refused(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    EnvManager().write_env_file(path, {"A": PEM})
    assert path.stat().st_mode & 0o777 == 0o600

    link = tmp_path / "link.env"
    link.symlink_to(path)
    with pytest.raises(SecurityError):
        EnvManager().write_env_file(link, {"A": "1"})


# ---------------------------------------------------------------------------
# What the validators let through
# ---------------------------------------------------------------------------


def test_a_line_feed_is_a_control_character_unless_the_value_may_have_lines() -> None:
    with pytest.raises(EnvironmentValidationError, match="newline"):
        validate_env_value("a\nb", name="K")

    assert validate_env_value("a\nb", name="K", multiline=True) == "a\nb"


@pytest.mark.parametrize(
    "value",
    ["a\r\nb", "a\x00b", "a\tb", "a\u2028b", "a\u2029b", "a\x85b", "a\x1bb", "a\x7fb"],
)
def test_nothing_else_is_let_through_in_a_value_of_lines(value: str) -> None:
    with pytest.raises(EnvironmentValidationError):
        validate_env_value(value, name="K", multiline=True)


def test_a_single_quote_beside_a_line_feed_is_refused_but_alone_it_is_fine() -> None:
    with pytest.raises(EnvironmentValidationError, match="several lines and a single quote"):
        validate_env_value("a'\nb", multiline=True)

    assert validate_env_value("it's fine", multiline=True) == "it's fine"


def test_the_whole_environment_is_judged_value_by_value() -> None:
    assert validate_environment({"KEY": PEM}, multiline=True) == {"KEY": PEM}
    with pytest.raises(EnvironmentValidationError):
        validate_environment({"KEY": PEM}, multiline=False)
    with pytest.raises(EnvironmentValidationError):
        validate_environment({"BAD NAME": "x"}, multiline=True)


def test_what_reaches_a_unit_inline_never_takes_lines() -> None:
    """The signature of the inline checks is the strict one: nothing above loosened it."""
    with pytest.raises(EnvironmentValidationError):
        validate_environment({"KEY": "a\nb"})


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("  what it is for  ", "what it is for"),
        ("one\n  two  \nthree", "one\ntwo\nthree"),
        ("", ""),
        ("   ", ""),
        ("has # a hash and = an equals", "has # a hash and = an equals"),
    ],
)
def test_a_comment_is_trimmed(comment: str, expected: str) -> None:
    assert validate_env_comment(comment) == expected


@pytest.mark.parametrize(
    "comment",
    [
        "a\rb",
        "a\x00b",
        "a\tb",
        "a\u2028b",
        "x" * (MAX_COMMENT_CHARS + 1),
        "\n".join("l" * 3 for _ in range(9)),
    ],
)
def test_a_comment_that_is_not_plain_short_text_is_refused(comment: str) -> None:
    with pytest.raises(EnvironmentValidationError):
        validate_env_comment(comment, name="K")


def test_a_comment_that_is_not_text_is_refused() -> None:
    with pytest.raises(EnvironmentValidationError, match="must be text"):
        validate_env_comment(5, name="K")
