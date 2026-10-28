# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_previews.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.forge_events import Forge, PullRequestAction, PullRequestEvent
from noust.core.store import App, NoustStore, PreviewRecord, PreviewSettings
from noust.integrations.github import comments
from noust.managers import previews

PARENT = "shop.example.com"
BASE = "previews.example.com"
REPO = "acme/shop"
NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of the test's own, with the application previewed in it."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(
        App(
            domain=PARENT,
            app_type="nodejs",
            source="https://github.com/Acme/Shop.git",
            branch="main",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
            layout="releases",
        )
    )
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Stop the clock at NOW; a test moves it by replacing the only item."""
    moment = [NOW]
    monkeypatch.setattr(previews, "utc_now", lambda: moment[0])
    return moment


@pytest.fixture(autouse=True)
def timer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Keep the sweep timer off the machine; record each sync instead."""
    synced: list[str] = []
    monkeypatch.setattr(previews, "sync_sweep_timer", lambda **_: synced.append("sync") or True)
    return synced


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture pull request comments; each gets the id ``c-<n>``."""
    calls: list[dict[str, Any]] = []

    def upsert(
        repository: str, number: int, body: str, comment_ref: str | None = None
    ) -> str | None:
        calls.append({"repository": repository, "number": number, "body": body, "ref": comment_ref})
        return comment_ref or f"c-{len(calls)}"

    monkeypatch.setattr(comments, "upsert_pr_comment", upsert)
    return calls


class FakeJob:
    """A queued job that never runs."""

    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.status = type("Status", (), {"value": "pending"})()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id}


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the jobs queued instead of running them."""
    queued: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> FakeJob:
            queued.append(kwargs)
            return FakeJob(f"job-{len(queued)}")

    monkeypatch.setattr(previews, "_jobs", Manager)
    return queued


class FakeContext:
    """What the job manager hands a job function."""

    job_id = "job-x"

    def __init__(self) -> None:
        self.logs: list[str] = []
        self.metadata: dict[str, Any] = {}

    def log(self, message: str, level: str = "info") -> None:
        self.logs.append(message)

    def update(self, step: str, progress: int) -> None:
        self.logs.append(step)

    def set_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value


def enable(store: NoustStore, *, max_previews: int = 3, ttl_hours: int = 168) -> PreviewSettings:
    """Turn previews on for the parent through the manager."""
    return previews.enable_previews(PARENT, BASE, max_previews=max_previews, ttl_hours=ttl_hours)


def event(
    action: PullRequestAction = PullRequestAction.OPENED,
    *,
    number: int = 7,
    repository: str = REPO,
    from_fork: bool = False,
    head_sha: str = "a" * 40,
    forge: Forge = Forge.GITHUB,
    clone_url: str = "https://github.com/acme/shop.git",
    author: str = "alice",
    association: str | None = "MEMBER",
    bot: bool = False,
) -> PullRequestEvent:
    """A pull request event against the parent's repository, by a member by default."""
    return PullRequestEvent(
        forge=forge,
        action=action,
        repository=repository,
        clone_url=clone_url,
        number=number,
        title="Add a feature",
        branch="feature/x",
        base_branch="main",
        head_sha=head_sha,
        from_fork=from_fork,
        installation_id=42,
        author=author,
        sender=author,
        bot=bot,
        author_association=association if forge is Forge.GITHUB else None,
    )


def child_domain(number: int = 7) -> str:
    return f"pr-{number}-shop-example-com.{BASE}"


def stored_preview(
    store: NoustStore, number: int = 7, *, expires_at: str | None = None, status: str = "ready"
) -> PreviewRecord:
    """Put a preview record in the store directly."""
    return store.save_preview(
        PreviewRecord(
            parent_domain=PARENT,
            domain=child_domain(number),
            number=number,
            branch="feature/x",
            provider="github",
            expires_at=expires_at or (NOW + timedelta(days=7)).isoformat(timespec="seconds"),
            head_sha="a" * 40,
            repository=REPO,
            status=status,
        )
    )


def deployed_child(store: NoustStore, number: int = 7, *, parent: str | None = PARENT) -> App:
    """Put a preview's application in the store."""
    app = store.create_app(
        App(domain=child_domain(number), app_type="nodejs", port=3100 + number, app_path="/x")
    )
    if parent is not None:
        store.set_preview_parent(app.domain, parent)
    return app
