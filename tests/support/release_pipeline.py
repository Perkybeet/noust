# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_release_pipeline.py`` that other tests build on."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.appdir.layout import RELEASES
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers import lifecycle
from noust.deployers.nodejs import NodeJSDeployer
from tests.support.deployer_patch_support import patch_deployer

DOMAIN = "rel.example.com"
#: Not any deployer's default, so a probe of the wrong port shows.
PORT = 3100
GIT_URL = "https://github.com/example/app.git"

PACKAGE_JSON = '{"name": "app", "scripts": {"start": "node server.js"}}\n'
LOCKFILE = '{"name": "app", "lockfileVersion": 3, "packages": {}}\n'
GOOD_SERVER = "require('http').createServer((q, s) => s.end('ok')).listen(3000);\n"
BROKEN_SERVER = "throw new Error('boom');\n"


class TreeRunner(FakeRunner):
    """
    A FakeRunner whose copies and installs leave real trees behind.

    Attributes:
        where: Every command with the directory it ran in, in order.
    """

    def __init__(self) -> None:
        super().__init__()
        self.where: list[tuple[tuple[str, ...], Path | None]] = []

    def run(self, argv: Any, *, cwd: Path | None = None, **kwargs: Any) -> Any:
        result = super().run(argv, cwd=cwd, **kwargs)
        self.where.append((tuple(argv), cwd))
        if list(argv[:3]) == ["cp", "-a", "--reflink=auto"] and result.success:
            shutil.copytree(argv[3], argv[4], symlinks=True)
        elif list(argv[:3]) == ["python3", "-m", "venv"] and result.success:
            Path(argv[3]).mkdir(parents=True, exist_ok=True)
        return result

    def stream(self, argv: Any, *, on_line: Any, cwd: Path | None = None, **kwargs: Any) -> Any:
        result = super().stream(argv, on_line=on_line, cwd=cwd, **kwargs)
        self.where.append((tuple(argv), cwd))
        if tuple(argv) in {("npm", "ci"), ("npm", "install")} and cwd is not None:
            marker = Path(cwd) / "node_modules" / ".installed-by"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(Path(cwd).name)
        return result

    def cwd_of(self, *argv: str) -> list[Path | None]:
        """
        Args:
            *argv: The command to look for.

        Returns:
            The directories it ran in, in order.
        """
        return [cwd for call, cwd in self.where if call == argv]


class FakeGit:
    """
    Stands in for SourceManager for git sources: a commit is a directory.

    Attributes:
        commits: Directory holding the tree of each commit id.
        head: The commit the branch points at.
        calls: What was asked of it.
    """

    def __init__(self) -> None:
        self.commits: dict[str, Path] = {}
        self.head: str | None = None
        self.calls: list[tuple[Any, ...]] = []
        # What the repository cache is on: a branch, or detached (at a tag).
        self.branch = "main"
        self.detached = False

    def publish(self, tree: Path) -> str:
        """
        Make a tree the new tip of the branch.

        Args:
            tree: Directory with the files of the commit.

        Returns:
            The commit id.
        """
        commit = hashlib.sha256(f"{len(self.commits)}:{tree}".encode()).hexdigest()[:40]
        self.commits[commit] = tree
        self.head = commit
        return commit

    def sync_cache(self, source: str, cache: Path, branch: str | None = None) -> str | None:
        self.calls.append(("sync", source, cache, branch))
        (cache / ".git").mkdir(parents=True, exist_ok=True)
        return self.head

    def export_commit(self, repository: Path, commit: str, destination: Path) -> None:
        self.calls.append(("export", commit, destination))
        shutil.copytree(self.commits[commit], destination, dirs_exist_ok=True)

    def get_repo_info(self, path: Path) -> dict[str, Any]:
        return {"branch": self.branch, "detached": self.detached, "commit": None}

    def fetch(self, *args: Any, **kwargs: Any) -> bool:
        raise AssertionError("a git source must go through the repository cache")


class FakeServices:
    """
    Stands in for ServiceManager.

    Attributes:
        units: Units created, with what they were created with.
        restarts: Every restart, with the release ``current`` pointed at.
    """

    def __init__(self, app_root: Path) -> None:
        self.app_root = app_root
        self.units: dict[str, dict[str, Any]] = {}
        self.restarts: list[str] = []

    def create_service(self, name: str, **kwargs: Any) -> None:
        self.units[name] = kwargs

    def enable(self, name: str) -> bool:
        return True

    def start(self, name: str) -> None:
        raise AssertionError("a release is activated with a restart, not a start")

    def restart(self, name: str) -> None:
        link = self.app_root / "current"
        self.restarts.append(os.readlink(link) if link.is_symlink() else "<no current>")

    def stop(self, name: str) -> bool:
        return True

    def get_status(self, name: str) -> dict[str, Any]:
        return {"exists": name in self.units, "active": True}

    def service_exists(self, name: str) -> bool:
        return name in self.units

    def serving_units(self, app: Any) -> list[str]:
        return [self.app_root.name]

    def delete_service(self, name: str) -> None:
        self.units.pop(name, None)

    def logs(self, name: str, lines: int = 50) -> str:
        return "Error: boom\n    at Object.<anonymous> (server.js:1:7)"


class FakeWeb:
    """Stands in for NginxManager/ApacheManager, keeping what it was asked to render."""

    def __init__(self) -> None:
        self.sites: dict[str, dict[str, Any]] = {}
        #: Sites the operator wrote, which a deploy must keep.
        self.own: set[str] = set()

    def is_running(self) -> bool:
        return True

    def site_exists(self, domain: str) -> bool:
        return domain in self.sites or domain in self.own

    def site_is_noust(self, domain: str) -> bool:
        return domain in self.sites and domain not in self.own

    def create_site(self, domain: str, template: str, context: dict[str, Any]) -> bool:
        self.sites[domain] = {"template": template, "context": context}
        return True

    update_site = create_site

    def enable_site(self, domain: str) -> bool:
        return True

    def disable_site(self, domain: str) -> bool:
        return True

    def delete_site(self, domain: str) -> bool:
        self.sites.pop(domain, None)
        return True

    def reload(self) -> bool:
        return True

    def load_configuration(self) -> str | None:
        return None


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A store in the test's directory, installed wherever it is looked up."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """The application directory."""
    return tmp_path / "apps" / "rel-example-com"


@pytest.fixture
def git() -> FakeGit:
    """The fake repository."""
    return FakeGit()


@pytest.fixture
def machine(root: Path, git: FakeGit, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """
    Everything a deploy talks to, faked, plus a probe that reads the active release.

    The probe answers like the real one would for the fixture server: a
    release whose server.js throws never answers.
    """
    services = FakeServices(root)
    web = FakeWeb()
    runner = TreeRunner()
    probes: list[str] = []

    def probe(url: str, **kwargs: Any) -> bool:
        probes.append(url)
        server = root / "current" / "server.js"
        if server.is_file() and "throw" in server.read_text():
            on_attempt = kwargs.get("on_attempt")
            if on_attempt is not None:
                on_attempt("Health check attempt 1 failed: [Errno 111] Connection refused")
            return False
        return True

    patch_deployer(monkeypatch, "wait_until_healthy", probe)
    return SimpleNamespace(services=services, web=web, runner=runner, git=git, probes=probes)


def write_tree(directory: Path, files: dict[str, str]) -> Path:
    """
    Materialise a project tree.

    Args:
        directory: Where to create it.
        files: Relative path to content.

    Returns:
        The directory.
    """
    for relative, content in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return directory


def node_tree(directory: Path, *, server: str = GOOD_SERVER, lockfile: str = LOCKFILE) -> Path:
    """A Node project with a lockfile, a server and an .env.example."""
    return write_tree(
        directory,
        {
            "package.json": PACKAGE_JSON,
            "package-lock.json": lockfile,
            "server.js": server,
            ".env.example": "SESSION_SECRET=\n",
        },
    )


def wire(deployer: Any, machine: SimpleNamespace) -> Any:
    """
    Point a deployer at the fakes.

    Args:
        deployer: The deployer.
        machine: The fakes.

    Returns:
        The deployer.
    """
    deployer.source_manager = machine.git
    deployer.service_manager = machine.services
    deployer.cert_manager = SimpleNamespace(obtain=lambda *a, **k: True)
    deployer._webserver_manager = lambda: machine.web
    deployer.pre_flight_check = lambda: True
    return deployer


def deploy_new(
    root: Path,
    machine: SimpleNamespace,
    *,
    deployer_class: type = NodeJSDeployer,
    layout: str | None = RELEASES,
    persistent: list[str] | None = None,
    source: str = GIT_URL,
) -> Any:
    """
    Deploy a new application.

    Returns:
        The deployer, after a successful deploy.
    """
    deployer = wire(deployer_class(verbose=False, runner=machine.runner), machine)
    deployer.configure(
        DOMAIN,
        source,
        port=PORT,
        ssl=False,
        app_path=root,
        layout=layout,
        persistent_paths=persistent,
    )
    assert deployer.deploy() is True
    return deployer


def update(
    machine: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    *,
    deployer_class: type = NodeJSDeployer,
    **kwargs: Any,
) -> Any:
    """
    Run the shared update sequence, with deployers wired to the fakes.

    Returns:
        What update_app returned.
    """
    monkeypatch.setattr(
        lifecycle,
        "get_deployer",
        lambda app_type, verbose=False: wire(
            deployer_class(verbose=False, runner=machine.runner), machine
        ),
    )
    return lifecycle.update_app(DOMAIN, **kwargs)


def active_id(root: Path) -> str:
    """The release ``current`` points at."""
    return Path(os.readlink(root / "current")).name
