# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_compose_adopt.py`` that other tests build on."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import CommandResult, FakeRunner, is_read_only, set_runner
from noust.core.store import NoustStore, get_store
from noust.managers.nginx_manager import NginxManager
from noust.managers.service_manager import ServiceManager
from noust.managers.webserver import NGINX_BACKEND

DOMAIN = "proggest.es"
UNIT = "proggest-es"
PROJECT = "proggest"
COMMIT = "4f2463e"
REMOTE = "git@github.com:Perkybeet/proggest.git"
FIXTURES = Path(__file__).parent.parent / "fixtures" / "compose"

OPERATOR_SITE = """# Proggest, written by hand
upstream nestjs_upstream { server 127.0.0.1:3000; }
server {
    listen 80;
    server_name proggest.es www.proggest.es;
    location /api/ { proxy_pass http://nestjs_upstream; }
    location / { proxy_pass http://127.0.0.1:3001; }
}
"""

UNCHANGED = """[+] Running 4/0
 ✔ DRY-RUN MODE -  Container proggest-postgres  Running   0.0s
 ✔ DRY-RUN MODE -  Container proggest-redis     Running   0.0s
 ✔ DRY-RUN MODE -  Container proggest-backend   Running   0.0s
 ✔ DRY-RUN MODE -  Container proggest-frontend  Running   0.0s
end of 'compose up' output, interactive run is not supported in dry-run mode
"""

RECREATES = """[+] Running 4/0
 ✔ DRY-RUN MODE -  Container proggest-postgres  Running     0.0s
 ✔ DRY-RUN MODE -  Container proggest-redis     Running     0.0s
 ✔ DRY-RUN MODE -  Container proggest-backend   Recreated   0.0s
 ✔ DRY-RUN MODE -  Container proggest-frontend  Running     0.0s
"""


class Docker(FakeRunner):
    """Docker and git answering as Proggest's server does."""

    def __init__(self, compose_file: Path) -> None:
        super().__init__()
        self.compose_file = compose_file
        self.dry_run = UNCHANGED
        self.dry_run_exit = 0
        self.containers = [
            (f"{n:064x}", name, "running", PROJECT, str(compose_file), str(compose_file.parent))
            for n, name in enumerate(
                ("proggest-postgres", "proggest-redis", "proggest-backend", "proggest-frontend"),
                start=1,
            )
        ]

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv
        if args[:2] == ("docker", "ps"):
            return replace(result, stdout="".join("\t".join(c) + "\n" for c in self.containers))
        if args[:2] == ("docker", "compose") and "--dry-run" in args:
            return replace(result, stderr=self.dry_run, exit_code=self.dry_run_exit)
        if args[:1] == ("git",):
            if args[-3:] == ("rev-parse", "--abbrev-ref", "HEAD"):
                return replace(result, stdout="main\n")
            if args[-3:] == ("rev-parse", "--short", "HEAD"):
                return replace(result, stdout=f"{COMMIT}\n")
            if args[-3:] == ("config", "--get", "remote.origin.url"):
                return replace(result, stdout=f"{REMOTE}\n")
        return result

    def mutating(self) -> list[tuple[str, ...]]:
        """Every command that could change the stack, the tree or the unit's state."""
        changing = {"clone", "fetch", "reset", "clean", "checkout", "pull"}
        found = []
        for call in self.calls:
            if call[:1] == ("git",) and changing & set(call):
                found.append(call)
            # What only looks (the guard's ``config``, declared read-only to the runner) is not a
            # change, and adopting is allowed to look.
            if (
                call[:2] == ("docker", "compose")
                and "--dry-run" not in call
                and not is_read_only(call)
            ):
                found.append(call)
            if call[:2] == ("systemctl", "start") or call[:2] == ("systemctl", "restart"):
                found.append(call)
        return found


@pytest.fixture
def store() -> Iterator[NoustStore]:
    """The process-wide store, where conftest puts it."""
    NoustStore.reset_instance()
    yield get_store()
    NoustStore.reset_instance()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Proggest's checkout, with what git does not track beside it."""
    path = tmp_path / "opt" / "proggest-src"
    (path / ".git").mkdir(parents=True)
    shutil.copy(FIXTURES / "proggest.docker-compose.prod.yml", path / "docker-compose.prod.yml")
    (path / ".env").write_text("DB_PASSWORD=secret\nREDIS_PASSWORD=secret\n")
    (path / "uploads").mkdir()
    (path / "uploads" / "invoice.pdf").write_bytes(b"%PDF data")
    return path


@pytest.fixture
def docker(root: Path) -> Iterator[Docker]:
    """The fake docker and git, as the process-wide runner."""
    fake = Docker(root / "docker-compose.prod.yml")
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private unit directory, and nothing in the system ones."""
    managed = tmp_path / "etc/systemd/system"
    managed.mkdir(parents=True)
    monkeypatch.setattr(ServiceManager, "SYSTEMD_DIR", managed)
    monkeypatch.setattr(ServiceManager, "UNIT_SEARCH_DIRS", (managed,))
    return managed


@pytest.fixture
def web(tmp_path: Path, docker: Docker, store: NoustStore) -> NginxManager:
    """nginx over a temporary tree holding the operator's own site."""
    manager = NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )
    manager.sites_available.mkdir(parents=True)
    manager.sites_enabled.mkdir(parents=True)
    (manager.sites_available / "proggest").write_text(OPERATOR_SITE)
    (manager.sites_enabled / "proggest").symlink_to(manager.sites_available / "proggest")
    (manager.sites_available / "default").write_text("server { listen 80 default_server; }\n")
    return manager


def adopt(root: Path, web: NginxManager, **options: Any) -> Any:
    from noust.deployers.compose_adopt import adopt_stack

    return adopt_stack(DOMAIN, root, web=web, **options)
