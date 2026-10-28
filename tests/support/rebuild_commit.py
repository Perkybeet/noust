# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_rebuild_commit.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.exceptions import SourceError
from noust.managers.source_manager import RemoteHead
from tests.support.release_pipeline import FakeGit


class HistoryGit(FakeGit):
    """FakeGit that resolves any commit it published and answers ls-remote."""

    def __init__(self) -> None:
        super().__init__()
        self.timeouts: list[int | None] = []

    def resolve_commit(self, repository: Path, commit: str) -> str:
        self.calls.append(("resolve", commit))
        matches = [c for c in self.commits if c.startswith(commit.lower())]
        if len(matches) != 1:
            raise SourceError(f"Commit {commit} does not exist in the repository")
        return matches[0]

    def remote_head(
        self, source: str, branch: str | None = None, *, timeout: int | None = None
    ) -> RemoteHead:
        self.calls.append(("ls-remote", source, branch))
        self.timeouts.append(timeout)
        assert self.head is not None
        return RemoteHead(branch=branch or "main", commit=self.head)


@pytest.fixture
def git() -> HistoryGit:
    """The fake repository, with history."""
    return HistoryGit()


def commit_of(machine: SimpleNamespace, release_id: str) -> str:
    """The full commit a release was built from."""
    short = release_id.split("-")[2]
    return next(c for c in machine.git.commits if c.startswith(short))
