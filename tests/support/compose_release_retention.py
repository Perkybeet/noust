# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_compose_release_retention.py`` that other tests build on."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, NoustStore
from noust.deployers import docker_compose, lifecycle
from noust.deployers.docker_compose import DockerComposeDeployer

DOMAIN = "stack.example.com"

COMPOSE_FILE = """\
services:
  web:
    build: .
    ports:
      - "18080:80"
  db:
    image: postgres:16
"""

#: What runs before an update: the old images.
OLD = [
    ("web", "stack-web:latest", "sha256:old0web", "stack"),
    ("db", "postgres:16", "sha256:old0pg16", "stack"),
]
#: What runs after the containers are recreated from the new build.
NEW = [
    ("web", "stack-web:latest", "sha256:new0web", "stack"),
    ("db", "postgres:16", "sha256:old0pg16", "stack"),
]


class Docker(FakeRunner):
    """
    Docker and git as a stack being updated and gone back sees them.

    Attributes:
        commit: The commit the tree is on, as ``git rev-parse --short HEAD`` says.
        running: What ``docker inspect`` says the containers run.
        after: What they run once ``up`` recreates them from a new build.
        kept: The tags ``docker images`` lists.
        tag_fails: Tags (or their start) that cannot be created.
        images_fail: ``docker images`` does not answer.
        removed: Tags ``docker image rm`` was asked to remove.
    """

    def __init__(self) -> None:
        super().__init__()
        self.commit = "1111111"
        self.running = list(OLD)
        self.after: list[tuple[str, str, str, str]] | None = None
        self.kept: set[str] = set()
        self.tag_fails: tuple[str, ...] = ()
        self.images_fail = False
        self.removed: list[str] = []

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv

        def answer(stdout: str = "", stderr: str = "", code: int = 0) -> CommandResult:
            return replace(result, stdout=stdout, stderr=stderr, exit_code=code)

        if args[:2] == ("docker", "inspect"):
            # docker inspect prints: image id, reference, service, project.
            return answer("".join(f"{row[2]}|{row[1]}|{row[0]}|{row[3]}\n" for row in self.running))
        if args[:3] == ("docker", "image", "tag"):
            destination = args[4]
            if self.tag_fails and destination.startswith(self.tag_fails):
                return answer(stderr=f"Error: cannot tag {args[3]}", code=1)
            if ":noust-" in destination:
                self.kept.add(destination)
            return answer()
        if args[:3] == ("docker", "image", "rm"):
            self.removed.append(args[3])
            self.kept.discard(args[3])
            return answer()
        if args[:2] == ("docker", "images"):
            if self.images_fail:
                return answer(stderr="Cannot connect to the Docker daemon", code=1)
            return answer("\n".join(sorted(self.kept)))
        if args[:2] == ("docker", "compose"):
            if "ps" in args and "-q" in args:
                return answer("c0ffee01c0ffee01\nc0ffee02c0ffee02\n")
            if "ps" in args:
                states = [
                    {"Service": row[0], "Name": f"stack-{row[0]}-1", "State": "running"}
                    for row in self.running
                ]
                return answer("\n".join(json.dumps(state) for state in states))
            if "up" in args and "--no-build" not in args and self.after is not None:
                self.running = list(self.after)
        if args[0] == "git":
            if "--short" in args:
                return answer(self.commit + "\n")
            if "--abbrev-ref" in args:
                return answer("main\n")
            if "--verify" in args:
                wanted = next(a for a in args if a.endswith("^{commit}")).removesuffix("^{commit}")
                return answer(wanted.ljust(40, "0") + "\n")
            if args[1:3] == ("checkout", "--force"):
                self.commit = args[-1][:7]
        if args[0] == "journalctl":
            return answer("up: done\n")
        return result

    def argv_with(self, *words: str) -> list[tuple[str, ...]]:
        """Every call containing all the given words."""
        return [call for call in self.calls if all(word in call for word in words)]

    def index_of(self, *words: str) -> int:
        """Position of the first call containing all the given words."""
        return next(i for i, call in enumerate(self.calls) if all(word in call for word in words))


class Probe:
    """The HTTP probe: answers from a script, failing the first ``fail_rounds`` asks of a URL."""

    def __init__(self) -> None:
        self.failing: set[str] = set()
        self.fail_rounds = 1
        self._seen: dict[str, int] = {}

    def fail_next(self, url: str, rounds: int = 1) -> None:
        """Make the next ``rounds`` asks of a URL fail, whatever was asked before."""
        self.failing.add(url)
        self.fail_rounds = rounds
        self._seen[url] = 0

    def __call__(
        self,
        url: str,
        *,
        retries: int = 5,
        delay: float = 2.0,
        on_attempt: Callable[[str], None] | None = None,
        accept: Callable[[int], bool] | None = None,
        within: float | None = None,
        **_: Any,
    ) -> bool:
        self._seen[url] = self._seen.get(url, 0) + 1
        return not (url in self.failing and self._seen[url] <= self.fail_rounds)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    monkeypatch.setattr(docker_compose, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_compose.time, "sleep", lambda seconds: None)


@pytest.fixture
def docker() -> Iterator[Docker]:
    fake = Docker()
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Probe:
    fake = Probe()
    monkeypatch.setattr(docker_compose, "wait_until_healthy", fake)
    return fake


def stack(store: NoustStore, root: Path, *, git: bool = True) -> App:
    root.mkdir(parents=True, exist_ok=True)
    if git:
        (root / ".git").mkdir(exist_ok=True)
    (root / "docker-compose.yml").write_text(COMPOSE_FILE)
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type="docker-compose",
            app_path=str(root),
            port=18080,
            status="running",
        )
    )


def deployer(root: Path, runner: FakeRunner) -> DockerComposeDeployer:
    made = DockerComposeDeployer(runner=runner)
    made.app_path = root
    made.app_name = root.name
    made.domain = DOMAIN
    made.previous_commit = "1111111"
    return made


def update(root: Path, runner: Docker, commit: str) -> DockerComposeDeployer:
    """One update that builds ``commit`` and passes the gate."""
    runner.commit = commit
    runner.after = [
        ("web", "stack-web:latest", f"sha256:{commit}web", "stack"),
        ("db", "postgres:16", "sha256:old0pg16", "stack"),
    ]
    made = deployer(root, runner)
    made.update()
    return made


@pytest.fixture
def root(tmp_path: Path, store: NoustStore) -> Path:
    path = tmp_path / "stack"
    stack(store, path)
    return path


def releases(store: NoustStore) -> list[Any]:
    app = store.get_app(DOMAIN)
    assert app is not None and app.id is not None
    return store.list_releases(app.id)
