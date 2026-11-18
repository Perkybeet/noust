# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What ``journalctl`` says when a search finds nothing, told from what it says when it fails."""

from __future__ import annotations

import pytest

from noust.core.journalctl import says_nothing_matched
from noust.core.runner import CommandResult


def result(*, stdout: str = "", stderr: str = "", exit_code: int = 1) -> CommandResult:
    return CommandResult(argv=("journalctl",), exit_code=exit_code, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize(
    "answer",
    [
        result(stdout="-- No entries --\n"),
        result(stderr="-- No entries --\n"),
        result(stdout="-- No entries --"),
    ],
)
def test_the_no_entries_notice_of_a_failed_exit_means_nothing_matched(
    answer: CommandResult,
) -> None:
    assert says_nothing_matched(answer) is True


@pytest.mark.parametrize(
    "answer",
    [
        result(stdout="line one\nline two\n", exit_code=0),
        result(stdout="-- No entries --\n", exit_code=0),
        result(stderr="Failed to open /var/log/journal: No such file or directory\n"),
        result(stderr="Compiled without pattern matching support\n"),
        result(stdout="-- No entries --\n", stderr="Failed to read the journal\n"),
        result(),
    ],
)
def test_a_success_and_a_failure_that_says_anything_else_do_not(answer: CommandResult) -> None:
    assert says_nothing_matched(answer) is False
