# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A secret from a pipe is read quietly; at a terminal it is still not echoed.

``noust db config -p`` with the password on standard input printed "Warning: Password input may be
echoed." (from ``getpass``, which has no terminal to hide the input on), which is untrue and which a
script had to filter. The prompt every command reads a secret through is one place now.
"""

from __future__ import annotations

import io

import click
import pytest

from noust.cli import app as cli_app
from noust.cli import secret_prompt


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


class TestFromAPipe:
    def test_one_line_is_read_with_nothing_said(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("s3cret value\nnext line\n"))

        assert secret_prompt.read_hidden("Password: ") == "s3cret value"

        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == ""

    def test_the_line_ending_of_a_windows_pipe_is_not_part_of_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("s3cret\r\n"))

        assert secret_prompt.read_hidden() == "s3cret"

    def test_a_secret_that_ends_in_spaces_keeps_them(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("pass  \n"))

        assert secret_prompt.read_hidden() == "pass  "

    def test_a_pipe_that_ends_first_is_an_abort_not_an_empty_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))

        with pytest.raises(EOFError):
            secret_prompt.read_hidden()
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        monkeypatch.setattr(click.termui, "hidden_prompt_func", secret_prompt.read_hidden)
        with pytest.raises(click.Abort):
            click.prompt("Password", hide_input=True)


class TestAtATerminal:
    def test_it_is_still_read_without_echo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        asked: list[str] = []
        monkeypatch.setattr("sys.stdin", Terminal("ignored\n"))
        monkeypatch.setattr("getpass.getpass", lambda prompt="": asked.append(prompt) or "typed")

        assert secret_prompt.read_hidden("Password: ") == "typed"
        assert asked == ["Password: "]


class TestEveryPromptReadsThroughIt:
    def test_the_command_line_installs_it(self) -> None:
        assert cli_app is not None
        assert click.termui.hidden_prompt_func is secret_prompt.read_hidden

    def test_a_prompt_with_hidden_input_from_a_pipe_warns_of_nothing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("hunter2\n"))

        answer = click.prompt("Password", hide_input=True)

        captured = capsys.readouterr()
        assert answer == "hunter2"
        assert "echoed" not in captured.err and "echoed" not in captured.out
