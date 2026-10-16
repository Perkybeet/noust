# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stack whose compose file requires a value is refused before it is built (checklist B-47).

Proggest's production file asks for ``${PERSONNEL_ENCRYPTION_KEY:?...}``. Without it in the
``.env``, ``docker compose build`` and ``up`` both stop with Compose's own message, minutes in.
Run here over the real deployer and the real file with only processes faked: a deploy and an
update refuse first, name the variable with the file's words and the command that sets it, build
nothing, and touch nothing that serves; a preview, whose variables are derived from its parent's,
is not judged.
"""

# The Proggest fixtures are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers import docker_compose
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import compose_variables
from noust.managers.nginx_manager import NginxManager
from tests.test_docker_compose_sites import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    certs,
    deployed_row,
    root,
    stack,
    store,
    web,
)


def built(runner: FakeRunner) -> bool:
    """Whether a ``docker compose ... build`` ran."""
    return any(call[:2] == ("docker", "compose") and "build" in call for call in runner.calls)


def test_a_deploy_without_a_required_value_stops_before_building(
    stack: DockerComposeDeployer, root: Path, runner: FakeRunner
) -> None:
    (root / ".env").write_text("OTHER=1\n")

    with pytest.raises(DeploymentError) as caught:
        stack.deploy()

    assert "requires a value for PERSONNEL_ENCRYPTION_KEY" in caught.value.message
    assert "PERSONNEL_ENCRYPTION_KEY requerido - definir en .env" in caught.value.details
    assert f"noust env set {DOMAIN} PERSONNEL_ENCRYPTION_KEY=<value>" in caught.value.details
    assert not built(runner)


def test_a_deploy_with_it_builds(stack: DockerComposeDeployer, runner: FakeRunner) -> None:
    stack.deploy()

    assert built(runner)


def test_a_variable_with_no_default_and_no_value_is_said_in_the_log(
    stack: DockerComposeDeployer, monkeypatch: pytest.MonkeyPatch
) -> None:
    said: list[str] = []
    monkeypatch.setattr(stack.logger, "warning", said.append)

    stack.deploy()

    assert any("nothing sets it: Compose will use an empty string" in line for line in said)


def test_an_update_without_a_required_value_stops_before_recording_or_building(
    stack: DockerComposeDeployer,
    web: NginxManager,
    store: NoustStore,
    root: Path,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployed_row(store, root)
    (root / ".env").write_text("OTHER=1\n")
    monkeypatch.setattr(docker_compose, "wait_until_healthy", lambda url, **kw: True)
    monkeypatch.setattr(docker_compose.time, "sleep", lambda seconds: None)

    with pytest.raises(DeploymentError, match="requires a value for PERSONNEL_ENCRYPTION_KEY"):
        stack.update()

    assert not built(runner)


def test_a_preview_is_not_judged_by_its_parents_file(
    stack: DockerComposeDeployer, root: Path
) -> None:
    (root / ".env").write_text("OTHER=1\n")
    stack.preview_parent = "proggest.example.com"

    stack._require_variables()  # does not raise


def test_the_report_of_a_deployed_stack_reads_its_file_and_its_env(
    stack: DockerComposeDeployer, store: NoustStore, root: Path
) -> None:
    app = deployed_row(store, root)

    found = compose_variables.report_for(app)

    names = {variable.name: variable for variable in found.variables}
    assert found.file == "docker-compose.prod.yml"
    assert names["PERSONNEL_ENCRYPTION_KEY"].status == compose_variables.SET
    assert names["PERSONNEL_ENCRYPTION_KEY"].required
    assert "0123456789abcdef" not in str(found.to_dict())
    (root / ".env").write_text("")
    assert [v.name for v in compose_variables.report_for(app).missing] == [
        "PERSONNEL_ENCRYPTION_KEY"
    ]
