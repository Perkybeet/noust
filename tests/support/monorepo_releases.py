# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_monorepo_releases.py`` that other tests build on."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.appdir.releases import ReleaseManager
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore, ReleaseRecord, Service
from noust.deployers import lifecycle, migrate, monorepo, monorepo_releases
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers import sandbox_trial
from noust.deployers.helpers.release_build import StagedRelease
from noust.deployers.monorepo import MonorepoDeployer

DOMAIN = "mono.example.com"
APP = "mono-example-com"
FIRST = "20200101-000000-aaaaaaa"
COMMIT = "b" * 40
WORKSPACES = (("web", 3001), ("api", 3002))


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test directory, where every module that reads one looks."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for module in (monorepo, build_sandbox, sandbox_trial, lifecycle, migrate):
        monkeypatch.setattr(module, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing here needs real time to pass."""
    monkeypatch.setattr(monorepo.time, "sleep", lambda seconds: None)


class Probe:
    """The HTTP probe, answering from a script instead of the network."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        #: Decides, for a URL, whether it does not answer; a test replaces it.
        self.fails: Callable[[str], bool] = lambda url: False

    def __call__(self, url: str, *, on_attempt: Any = None, **kwargs: Any) -> bool:
        self.urls.append(url)
        if self.fails(url):
            if on_attempt is not None:
                on_attempt("Health check attempt 1 failed: HTTP 502")
            return False
        return True


class Units:
    """The service manager, as the workspaces' units answer it: restarts are recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        #: What ``current`` pointed at for each restart, which is the release the unit started on.
        self.on: list[str] = []
        self.root: Path | None = None

    def restart(self, name: str) -> None:
        self.calls.append(("restart", name))
        if self.root is not None:
            self.on.append(os.readlink(self.root / "current"))

    def logs(self, name: str, lines: int = 50) -> str:
        return f"{name}: Error: listen EADDRINUSE"

    def get_status(self, name: str) -> dict[str, Any]:
        return {"exists": True, "active": True}


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Probe:
    fake = Probe()
    monkeypatch.setattr(monorepo, "wait_until_healthy", fake)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", fake)
    return fake


def write_release(path: Path, *, generation: str = "old") -> Path:
    """A release of the monorepo: the workspaces, each with the file its unit loads."""
    for name, port in WORKSPACES:
        workspace = path / "apps" / name
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "package.json").write_text(f'{{"name": "{name}"}}')
        env = workspace / ".env.production"
        env.write_text(f"PORT={port}\nSECRET={generation}-{name}\n")
        env.chmod(0o600)
    (path / "package.json").write_text(json.dumps({"name": "mono", "scripts": {}}))
    return path


def released_monorepo(
    tmp_path: Path, store: NoustStore, *, layout: str = "releases", sandboxed: bool = False
) -> tuple[Path, App]:
    """A monorepo with one release serving, two workspace units, and its ``shared/.env``."""
    root = tmp_path / "apps" / APP
    first = write_release(root / "releases" / FIRST)
    (root / "shared").mkdir()
    (root / "shared" / ".env").write_text("DATABASE_URL=postgresql://x\n")
    (root / "shared" / ".env").chmod(0o600)
    (first / ".env").symlink_to("../../shared/.env")
    (root / "current").symlink_to(Path("releases") / FIRST)
    app = store.create_app(
        App(
            domain=DOMAIN,
            app_type="monorepo",
            app_path=str(root),
            port=3001,
            layout=layout,
            source="https://example.com/mono.git",
            branch="main",
            env_vars={
                "_workspaces": json.dumps(
                    [
                        {"name": name, "path": f"apps/{name}", "port": port}
                        for name, port in WORKSPACES
                    ]
                )
            },
        )
    )
    for name, port in WORKSPACES:
        store.create_service(
            Service(
                name=f"{APP}-{name}",
                app_id=app.id,
                unit_file=f"/etc/systemd/system/{APP}-{name}.service",
                working_directory=str(root / "current" / "apps" / name),
                port=port,
            )
        )
    assert app.id is not None
    store.record_release(
        ReleaseRecord(
            id=FIRST,
            app_id=app.id,
            git_commit="aaaaaaa",
            created_at="2020-01-01T00:00:00+00:00",
            status="active",
            path=str(first),
        )
    )
    store.mark_release_active(app.id, FIRST)
    if sandboxed:
        build_sandbox.adopt_new_app(DOMAIN, preview=False, store=store)
    return root, app


@pytest.fixture
def staging(monkeypatch: pytest.MonkeyPatch) -> list[StagedRelease]:
    """Replace the fetch of a release with an export of a tree that has the workspaces."""
    staged: list[StagedRelease] = []

    def fake(
        source: str,
        branch: str | None,
        *,
        releases: ReleaseManager,
        source_manager: Any,
        logger: Any,
        commit: str | None = None,
    ) -> StagedRelease:
        path = releases.new_release_dir(COMMIT)
        write_release(path, generation="fresh")
        for name, _port in WORKSPACES:
            # What a fresh checkout has: no environment file, which the deployment wrote.
            (path / "apps" / name / ".env.production").unlink()
        release = StagedRelease(path=path, commit=COMMIT, manager=releases)
        staged.append(release)
        return release

    monkeypatch.setattr(monorepo_releases, "stage_release", fake)
    return staged


def deployer(root: Path, runner: FakeRunner, units: Units) -> MonorepoDeployer:
    """The deployer lifecycle._update_release builds."""
    instance = MonorepoDeployer(runner=runner)
    instance.configure(
        DOMAIN, "https://example.com/mono.git", app_path=root, branch="main", port=3001
    )
    instance.service_manager = units  # type: ignore[assignment]
    units.root = root
    return instance
