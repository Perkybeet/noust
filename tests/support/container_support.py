# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the tests of container applications share: a Docker that remembers its images.

The fake answers the way the real one does for the commands a container
application issues (``build``, ``pull``, ``inspect``, ``images``, ``image tag``
and ``rm``, ``compose ps`` and ``up``), so a deployment can be run end to end
and its argv asserted afterwards. Nothing here runs a process.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.deployers import container as container_module
from noust.deployers import docker_compose

DOMAIN = "shop.example.com"
APP = "shop-example-com"
IMAGE_ID = "sha256:" + "c" * 64
DIGEST = "sha256:" + "d" * 64
PREVIOUS_ID = "sha256:" + "1" * 64


class Docker(FakeRunner):
    """
    Docker and git as a container application sees them.

    Attributes:
        images: The image references the host has, as ``repository:tag``.
        containers: The state ``compose ps`` reports for the application.
        fail: Commands (their first two words after ``docker``) that fail.
        serving: What ``docker inspect`` says the running container was created from.
        commit: What the repository's HEAD is.
    """

    def __init__(self) -> None:
        super().__init__()
        self.images: set[str] = set()
        self.containers = "running"
        self.fail: set[str] = set()
        self.serving: str | None = None
        self.commit: str | None = None
        #: What the health probe answers; a test turns it off to fail a gate.
        self.healthy = True
        self.exposed: dict[str, dict] = {"8080/tcp": {}}
        #: Called with each ``compose up`` so a test can make the next state worse.
        self.on_up: list[Any] = []

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv

        def answer(stdout: str = "", stderr: str = "", code: int = 0) -> CommandResult:
            return replace(result, stdout=stdout, stderr=stderr, exit_code=code)

        if args[0] == "git":
            if "rev-parse" in args and "--short" in args:
                return answer((self.commit + "\n") if self.commit else "", code=0)
            return result
        if args[0] != "docker":
            return result
        verb = " ".join(args[1:3]) if args[1] in ("image", "compose") else args[1]
        if verb in self.fail:
            return answer(stdout=f"{verb} failed on purpose", stderr="boom", code=1)
        if args[1] == "build":
            self.images.add(args[args.index("--tag") + 1])
            return answer("#1 DONE")
        if args[1] == "pull":
            return answer("Status: Downloaded newer image")
        if args[1] == "inspect" and "--type" in args:
            return answer(
                f"{IMAGE_ID}|{json.dumps([f'ghcr.io/owner/app@{DIGEST}'])}|"
                f"{json.dumps(self.exposed)}"
            )
        if args[1] == "inspect" and "{{json .Config.Env}}" in args:
            return answer("[]")
        if args[1] == "inspect":
            if self.serving is None:
                return answer("")
            return answer(f"{PREVIOUS_ID}|{self.serving}|app|{APP}\n")
        if args[1] == "images":
            reference = args[args.index("--filter") + 1].split("=", 1)[1]
            repository = reference.split(":", 1)[0]
            return answer(
                "\n".join(sorted(i for i in self.images if i.startswith(repository + ":")))
            )
        if args[1:3] == ("image", "tag"):
            self.images.add(args[4])
            return answer()
        if args[1:3] == ("image", "rm"):
            if args[3] not in self.images:
                return answer(stderr=f"No such image: {args[3]}", code=1)
            self.images.discard(args[3])
            return answer()
        if args[1] == "compose":
            if "ps" in args and "-q" in args:
                return answer("c0ffee01c0ffee01\n" if self.serving else "")
            if "ps" in args:
                return answer(
                    json.dumps({"Service": "app", "Name": "app-1", "State": self.containers})
                )
            if "up" in args:
                for hook in self.on_up:
                    hook(args)
                return answer()
            if "logs" in args:
                return answer("app-1  | listening on 8080\n")
        return result

    def compose_calls(self) -> list[tuple[str, ...]]:
        """The ``docker compose`` subcommands, without the project and file flags."""
        calls = []
        for call in self.calls:
            if call[:2] != ("docker", "compose"):
                continue
            rest = list(call[2:])
            while rest and rest[0] in ("-p", "-f"):
                rest = rest[2:]
            calls.append(tuple(rest))
        return calls

    def docker_calls(self, *words: str) -> list[tuple[str, ...]]:
        """Every docker call that starts with these words."""
        return [
            call for call in self.calls if call[1 : 1 + len(words)] == words and call[0] == "docker"
        ]


class FakeServices:
    """The service manager, for the parts of a deployment that start and probe a unit."""

    started: list[str] = []
    restarted: list[str] = []
    deleted: list[str] = []

    def __init__(self, verbose: bool = False, runner: Any = None, **_: Any) -> None:
        pass

    def start(self, name: str) -> None:
        FakeServices.started.append(name)

    def restart(self, name: str) -> None:
        FakeServices.restarted.append(name)

    def logs(self, name: str, lines: int = 50) -> str:
        return "journal line"

    def get_service_config(self, name: str) -> str | None:
        return None

    def delete_service(self, name: str) -> None:
        FakeServices.deleted.append(name)


def install_fakes(monkeypatch: pytest.MonkeyPatch, store: NoustStore) -> Iterator[Docker]:
    """Wire a Docker, the store and the service manager in; used by the fixtures."""
    FakeServices.started = []
    FakeServices.restarted = []
    FakeServices.deleted = []
    fake = Docker()
    set_runner(fake)
    monkeypatch.setattr(docker_compose, "get_store", lambda: store)
    monkeypatch.setattr(docker_compose, "wait_until_healthy", lambda url, **kw: fake.healthy)
    monkeypatch.setattr(docker_compose.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(docker_compose, "ServiceManager", FakeServices)
    monkeypatch.setattr(container_module, "ServiceManager", FakeServices)
    yield fake
    set_runner(None)


def root_of(tmp_path: Path) -> Path:
    """Where the application lives in a test."""
    return tmp_path / "apps" / APP
