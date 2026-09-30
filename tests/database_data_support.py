# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Test support for the data explorer, the SQL console v2 and the database metrics.

:class:`SqlRunner` answers a client call by what it carries - the script on
its stdin, or its argv - because every statement Noust builds travels on
stdin, which FakeRunner's argv prefixes cannot tell apart.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import NoustStore


@dataclass
class Answer:
    """One scripted answer: the text a call must carry, and what it prints."""

    needle: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    times: int | None = None


class SqlRunner(FakeRunner):
    """
    A FakeRunner that answers by the statement a call carries.

    Later answers win, like FakeRunner's scripts. An answer given ``times``
    is used that many times and then forgotten, for a failure followed by a
    success.
    """

    def __init__(self) -> None:
        """Answer the questions every engine asks on the way in."""
        super().__init__()
        self.answers: list[Answer] = []
        self.kwargs: list[dict[str, Any]] = []
        self.answer("SELECT 1 FROM pg_database", "1\n")
        self.answer("SHOW port", "5432\n")
        self.answer("is-active", "active\n")

    def answer(
        self,
        needle: str,
        stdout: str = "",
        *,
        stderr: str = "",
        exit_code: int = 0,
        times: int | None = None,
    ) -> SqlRunner:
        """
        Script an answer for any call carrying ``needle``.

        Args:
            needle: Text the call's stdin or argv must contain.
            stdout: What it prints.
            stderr: What it prints on stderr.
            exit_code: Its exit status.
            times: Use it this many times only.

        Returns:
            This runner.
        """
        self.answers.append(Answer(needle, stdout, stderr, exit_code, times))
        return self

    def run(self, argv, **kwargs) -> CommandResult:
        """
        Record the call, then answer it by what it carries.

        Args:
            argv: The argument vector.
            **kwargs: The runner's keyword arguments.

        Returns:
            The newest matching answer, else FakeRunner's.
        """
        self.kwargs.append(kwargs)
        result = super().run(argv, **kwargs)
        carried = (kwargs.get("input") or "") + "\n" + " ".join(str(arg) for arg in argv)
        for answer in reversed(self.answers):
            if answer.needle in carried and answer.times != 0:
                if answer.times is not None:
                    answer.times -= 1
                return CommandResult(
                    argv=result.argv,
                    exit_code=answer.exit_code,
                    stdout=answer.stdout,
                    stderr=answer.stderr,
                )
        return result

    def sent(self, needle: str) -> list[str]:
        """
        Every stdin script that carried ``needle``.

        Args:
            needle: Text to look for.

        Returns:
            The scripts.
        """
        return [text for text in self.inputs if text and needle in text]

    def calls_carrying(self, needle: str) -> list[tuple[str, ...]]:
        """
        Every argv of a call whose stdin or argv carried ``needle``.

        Args:
            needle: Text to look for.

        Returns:
            The argument vectors.
        """
        found = []
        for argv, kwargs in zip(self.calls, self.kwargs, strict=False):
            if needle in (kwargs.get("input") or "") or needle in " ".join(argv):
                found.append(argv)
        return found


class StubConfig:
    """A configuration that answers with whatever the test put in it."""

    def __init__(self, data: dict[str, Any] | None = None):
        """
        Args:
            data: Values keyed by top level configuration key.
        """
        self._data = data or {}

    def get(self, key: str, default: Any = None) -> Any:
        """
        Args:
            key: Configuration key.
            default: Value when absent.

        Returns:
            The value.
        """
        return self._data.get(key, default)


def installed_sql_runner() -> Iterator[SqlRunner]:
    """
    Install a :class:`SqlRunner` as the process-wide runner, over a fresh store.

    Each test module wraps it in its own ``sql_runner`` fixture
    (``yield from installed_sql_runner()``).

    Yields:
        The runner.
    """
    from noust.managers.database import browse, postgres
    from noust.managers.database import mysql as mysql_module

    NoustStore.reset_instance()
    browse.forget_cached()
    postgres._provisioned_at.clear()
    mysql_module._provisioned.clear()
    fake = SqlRunner()
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)
        browse.forget_cached()
        postgres._provisioned_at.clear()
        mysql_module._provisioned.clear()
        NoustStore.reset_instance()


def make_service(*managers: Any) -> Any:
    """
    Build a database service over the given managers.

    Args:
        *managers: Engine managers; each answers to its ENGINE_NAME.

    Returns:
        The service.
    """
    from noust.managers.database.service import DatabaseService

    by_name = {manager.ENGINE_NAME: manager for manager in managers}
    return DatabaseService(resolve=lambda name: by_name.get(name), engines=lambda: list(by_name))
