# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A value in a log line cannot start another line (CWE-117, CodeQL ``py/log-injection``).

Sixty call sites log a name, a domain or a session that a request supplied. Escaping each of
them is how one gets forgotten, so the record every logger of the process makes does it
(``noust.core.logsafe``), whoever logs and whichever handler writes: the journal, a file, the
console. Importing the project's logger is what installs it.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest

from noust.core import logsafe


@pytest.fixture
def lines() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """A logger of its own that writes what a journal would receive."""
    logsafe.install()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("noust.test.logsafe")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        yield logger, stream
    finally:
        logger.handlers.clear()


class TestNeutralize:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("plain", "plain"),
            ("a\nb", "a\\nb"),
            ("a\r\nb", "a\\r\\nb"),
            ("a\tb", "a\tb"),
            ("a\x1b[31mred", "a\\x1b[31mred"),
            ("a\x00b", "a\\x00b"),
            ("a\u2028b\u2029c", "a\\u2028b\\u2029c"),
            ("a\x85b", "a\\x85b"),
            ("été 日本", "été 日本"),
            ("", ""),
        ],
    )
    def test_a_line_break_or_control_character_is_written_out(
        self, text: str, expected: str
    ) -> None:
        assert logsafe.neutralize(text) == expected

    def test_what_is_written_out_is_not_written_out_twice(self) -> None:
        assert logsafe.neutralize(logsafe.neutralize("a\nb")) == "a\\nb"


class TestEveryRecord:
    def test_a_value_given_as_an_argument_cannot_start_a_line(
        self, lines: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = lines

        logger.info("delete name=%s session=%s", "x\nINFO forged entry", "master")

        assert stream.getvalue().count("\n") == 1
        assert "name=x\\nINFO forged entry session=master" in stream.getvalue()

    def test_a_value_formatted_into_the_message_cannot_either(
        self, lines: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = lines
        name = "x\r\nINFO forged entry"

        logger.warning(f"delete name={name}")

        assert stream.getvalue().count("\n") == 1

    def test_a_dictionary_of_arguments_is_covered_too(
        self, lines: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = lines

        logger.info("delete name=%(name)s", {"name": "x\ny"})

        assert stream.getvalue() == "INFO delete name=x\\ny\n"

    def test_a_traceback_keeps_its_lines(self, lines: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = lines

        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("failed")

        assert stream.getvalue().count("\n") > 2
        assert "ValueError: boom" in stream.getvalue()

    def test_the_arguments_are_left_as_they_were_for_the_handlers_that_read_them(
        self, lines: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, _ = lines
        seen: list[logging.LogRecord] = []

        class Spy(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                seen.append(record)

        logger.addHandler(Spy())
        logger.info("n=%s", "a\nb")

        assert seen[0].args == ("a\nb",)

    def test_a_message_that_cannot_be_formatted_is_not_hidden(
        self, lines: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, _ = lines
        record = logger.makeRecord("n", logging.INFO, "f", 1, "%s %s", ("one",), None)

        with pytest.raises(TypeError):
            record.getMessage()


class TestInstalling:
    def test_it_is_idempotent_and_keeps_a_factory_somebody_else_chained(self) -> None:
        logsafe.install()
        first = logging.getLogRecordFactory()
        logsafe.install()

        assert logging.getLogRecordFactory() is first
        assert logsafe.is_installed()

    def test_the_projects_logger_is_what_installs_it(self) -> None:
        import noust.core.logger as project_logger

        assert project_logger.logsafe is logsafe
        assert logsafe.is_installed()
