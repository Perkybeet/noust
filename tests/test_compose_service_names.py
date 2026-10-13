# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The services of a Compose stack, as Compose lists them (checklist M-39).

The console's logs tab offers one service of a stack at a time, and it needs the list
from the one place that knows the project, the file and the profiles: the deployer. The
list is what ``docker compose config --services`` prints, so a service that is not running
(the one that crashed, which is the one worth reading) is in it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import CommandResult, FakeRunner
from noust.deployers.docker_compose import DockerComposeDeployer


class Compose(FakeRunner):
    """Docker answering ``config --services`` with what a test sets."""

    def __init__(self, services: str, *, works: bool = True) -> None:
        super().__init__()
        self.services = services
        self.works = works

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        if result.argv[:2] == ("docker", "compose") and result.argv[-2:] == (
            "config",
            "--services",
        ):
            if not self.works:
                return replace(result, exit_code=1, stderr="no configuration file provided")
            return replace(result, stdout=self.services)
        return result


@pytest.fixture
def stack(tmp_path: Path) -> Callable[..., tuple[DockerComposeDeployer, Compose]]:
    def make(services: str = "web\ndb\nworker\n", *, works: bool = True):
        root = tmp_path / "shop"
        root.mkdir(exist_ok=True)
        (root / "docker-compose.yml").write_text(
            "services:\n  web:\n    image: nginx\n", encoding="utf-8"
        )
        runner = Compose(services, works=works)
        deployer = DockerComposeDeployer(runner=runner)
        deployer.configure("shop.example.com", "https://example.com/shop.git", app_path=root)
        deployer.compose_path = root / "docker-compose.yml"
        return deployer, runner

    return make


def test_it_lists_what_compose_prints_one_service_per_line(stack) -> None:
    deployer, _ = stack()

    assert deployer.service_names() == ["web", "db", "worker"]


def test_it_asks_compose_for_the_stacks_own_project_and_file(stack) -> None:
    deployer, runner = stack()

    deployer.service_names()

    asked = [call for call in runner.calls if call[-2:] == ("config", "--services")]
    assert len(asked) == 1
    assert "-p" in asked[0] and "-f" in asked[0]


def test_it_ignores_blank_lines_and_a_name_it_would_not_pass_on(stack) -> None:
    deployer, _ = stack("web\n\n  db  \nnot a service; reboot\n")

    assert deployer.service_names() == ["web", "db"]


def test_a_stack_compose_cannot_read_has_no_list_rather_than_an_error(stack) -> None:
    deployer, _ = stack(works=False)

    assert deployer.service_names() == []
