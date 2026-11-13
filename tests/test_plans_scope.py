# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A rehearsal that belongs to one request.

``--dry-run`` replaces the runner and the filesystem of the whole process, which is right for a
command and wrong for a console that serves other requests while one of them asks what an update
would do. The scope is a context variable the two seams look at before the process-wide object:
what runs inside it is rehearsed, whatever else the process is doing.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable
from typing import Any

import pytest

from noust.core import rehearsal_scope
from noust.core.fs import DryRunFileSystem, RealFileSystem, get_fs, is_rehearsal, set_fs
from noust.core.runner import DryRunRunner, FakeRunner, get_runner, set_runner


@pytest.fixture
def process_wide() -> tuple[FakeRunner, RealFileSystem]:
    """The runner and filesystem the rest of the process uses while a request rehearses."""
    runner, filesystem = FakeRunner(), RealFileSystem()
    set_runner(runner)
    set_fs(filesystem)
    yield runner, filesystem
    set_runner(None)
    set_fs(None)


def in_another_thread(work: Callable[[], Any]) -> Any:
    """
    Run something in a thread that was not started from the caller's context.

    Args:
        work: What to run.

    Returns:
        What it returned.
    """
    box: list[Any] = []
    thread = threading.Thread(target=lambda: box.append(work()))
    thread.start()
    thread.join()
    return box[0]


def test_inside_the_scope_both_seams_answer_with_the_rehearsal(process_wide) -> None:
    runner, filesystem = process_wide
    rehearsing_runner = DryRunRunner(FakeRunner())
    rehearsing_fs = DryRunFileSystem()

    with rehearsal_scope.scoped(runner=rehearsing_runner, fs=rehearsing_fs):
        assert get_runner() is rehearsing_runner
        assert get_fs() is rehearsing_fs
        assert is_rehearsal()
        assert rehearsal_scope.active()

    assert get_runner() is runner
    assert get_fs() is filesystem
    assert not is_rehearsal()
    assert not rehearsal_scope.active()


def test_another_thread_does_not_see_it(process_wide) -> None:
    """The console serves other requests while this one rehearses: they keep acting for real."""
    runner, filesystem = process_wide

    with rehearsal_scope.scoped(runner=DryRunRunner(FakeRunner()), fs=DryRunFileSystem()):
        seen = in_another_thread(lambda: (get_runner(), get_fs(), is_rehearsal()))

    assert seen == (runner, filesystem, False)


def test_a_thread_given_a_copy_of_the_context_rehearses_too(process_wide) -> None:
    """Work a rehearsal hands to a worker with ``copy_context`` stays inside it."""
    rehearsing_runner = DryRunRunner(FakeRunner())

    with rehearsal_scope.scoped(runner=rehearsing_runner, fs=DryRunFileSystem()):
        context = contextvars.copy_context()
        seen = in_another_thread(lambda: context.run(get_runner))

    assert seen is rehearsing_runner


def test_scopes_nest_and_each_puts_back_the_one_before(process_wide) -> None:
    outer, inner = DryRunRunner(FakeRunner()), DryRunRunner(FakeRunner())

    with rehearsal_scope.scoped(runner=outer, fs=DryRunFileSystem()):
        with rehearsal_scope.scoped(runner=inner, fs=DryRunFileSystem()):
            assert get_runner() is inner
        assert get_runner() is outer


def test_an_error_inside_the_scope_still_puts_everything_back(process_wide) -> None:
    runner, _ = process_wide

    with pytest.raises(RuntimeError):
        with rehearsal_scope.scoped(runner=DryRunRunner(FakeRunner()), fs=DryRunFileSystem()):
            raise RuntimeError("the operation failed")

    assert get_runner() is runner
    assert not is_rehearsal()


def test_a_command_started_by_the_cli_dry_run_is_still_the_process_wide_one() -> None:
    """``--dry-run`` installs its objects with ``set_runner``; the scope does not replace that."""
    cli_runner = DryRunRunner(FakeRunner())
    set_runner(cli_runner)
    set_fs(DryRunFileSystem())
    try:
        assert not rehearsal_scope.active()
        assert get_runner() is cli_runner
        assert is_rehearsal()
    finally:
        set_runner(None)
        set_fs(None)
