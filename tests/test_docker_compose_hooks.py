# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deploy hooks in a Docker Compose stack (spec 3.2 section 1.1, Compose part).

A hook runs in a one-off container of the image the deployment just built:
``docker compose run --rm --no-deps [-w workdir] --entrypoint "" <service>
<argv>``, through the runner, after the build and before anything is
recreated. ``pre_deploy`` that fails leaves what served serving, with the
hook's output verbatim; ``post_deploy`` runs after the gate and a failure
leaves the stack deployed with warnings. A hook marked ``migrates`` marks the
deployment ``schema_changed``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, NoustStore
from noust.deployers import docker_compose
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers.hooks import SCHEMA_CHANGED_PREFIX, HookFailedError

DOMAIN = "stack.example.com"
PREVIOUS = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"

COMPOSE_FILE = """\
services:
  web:
    build: .
    ports:
      - "127.0.0.1:8080:80"
    depends_on: [db]
  db:
    image: postgres:16
"""

HOOKS = """\
hooks:
  pre_deploy:
    - run: npx prisma migrate deploy
      service: web
      workdir: /app
      migrates: true
  post_deploy:
    - run: ./scripts/purge-cache.sh
"""


class Docker(FakeRunner):
    """Docker and git as a stack being deployed sees them."""

    def __init__(self) -> None:
        super().__init__()
        self.failing_hook: str | None = None

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv

        def answer(stdout: str = "", stderr: str = "", code: int = 0) -> CommandResult:
            return replace(result, stdout=stdout, stderr=stderr, exit_code=code)

        if args[:2] == ("docker", "inspect"):
            return answer("sha256:0ld|stack-web|web|stack\n")
        if args[:2] == ("docker", "compose"):
            if "ps" in args and "-q" in args:
                return answer("c0ffee01c0ffee01\n")
            if "ps" in args:
                return answer(json.dumps({"Service": "web", "Name": "w", "State": "running"}))
            if "run" in args and self.failing_hook and self.failing_hook in args:
                return answer(stdout="Error: P3009 migrate found failed migrations", code=1)
        if args[0] == "git" and "--verify" in args:
            return answer(PREVIOUS + "\n")
        return result

    def compose_calls(self) -> list[tuple[str, ...]]:
        """The docker compose subcommands, without the project and file flags."""
        calls = []
        for call in self.calls:
            if call[:2] != ("docker", "compose"):
                continue
            rest = list(call[2:])
            while rest and rest[0] in ("-p", "-f"):
                rest = rest[2:]
            calls.append(tuple(rest))
        return calls

    def index_of(self, *words: str) -> int:
        """Position of the first compose call containing all the words."""
        return next(i for i, c in enumerate(self.compose_calls()) if all(w in c for w in words))


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test's directory, where the deployer looks."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(docker_compose, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> Iterator[Docker]:
    """The fake docker, installed as the process-wide runner too."""
    fake = Docker()
    set_runner(fake)
    monkeypatch.setattr(docker_compose, "wait_until_healthy", lambda url, **kw: True)
    monkeypatch.setattr(docker_compose.time, "sleep", lambda seconds: None)
    yield fake
    set_runner(None)


def stack_tree(root: Path, *, hooks: str | None = HOOKS) -> Path:
    """A stack's checkout, with or without a noust.yaml."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    (root / "docker-compose.yml").write_text(COMPOSE_FILE)
    if hooks is not None:
        (root / "noust.yaml").write_text(hooks)
    return root


def deployed(store: NoustStore, root: Path) -> App:
    """The stack's row."""
    return store.create_app(
        App(domain=DOMAIN, app_type="docker-compose", app_path=str(root), port=8080)
    )


def updater(root: Path, runner: FakeRunner) -> DockerComposeDeployer:
    """The deployer lifecycle._rebuild_compose builds."""
    deployer = DockerComposeDeployer(runner=runner)
    deployer.app_path = root
    deployer.app_name = root.name
    deployer.domain = DOMAIN
    deployer.previous_commit = PREVIOUS
    return deployer


# -- Updates ------------------------------------------------------------------------


def test_hooks_run_in_the_new_image_between_the_build_and_the_recreate(
    tmp_path: Path, store: NoustStore, docker: Docker
) -> None:
    root = stack_tree(tmp_path / "stack")
    deployed(store, root)

    result = updater(root, docker).update()

    calls = docker.compose_calls()
    pre = (
        "run", "--rm", "--no-deps", "-w", "/app", "--entrypoint", "", "web",
        "npx", "prisma", "migrate", "deploy",
    )  # fmt: skip
    post = ("run", "--rm", "--no-deps", "--entrypoint", "", "web", "./scripts/purge-cache.sh")
    assert pre in calls and post in calls
    build = docker.index_of("build")
    up = docker.index_of("up", "-d", "--remove-orphans")
    assert build < calls.index(pre) < up < calls.index(post)
    assert result.schema_changed is True
    assert [entry["run"] for entry in result.hooks] == [
        "npx prisma migrate deploy",
        "./scripts/purge-cache.sh",
    ]
    row = store.list_deployments(DOMAIN)[0]
    assert row.status == "success" and row.schema_changed is True
    assert [entry["phase"] for entry in json.loads(row.hooks or "[]")] == [
        "pre_deploy",
        "post_deploy",
    ]


def test_a_failing_pre_deploy_hook_recreates_nothing_and_says_why(
    tmp_path: Path, store: NoustStore, docker: Docker
) -> None:
    root = stack_tree(tmp_path / "stack")
    deployed(store, root)
    docker.failing_hook = "prisma"

    with pytest.raises(HookFailedError) as caught:
        updater(root, docker).update()

    assert "pre_deploy" in caught.value.message and "exited with 1" in caught.value.message
    assert "P3009 migrate found failed migrations" in (caught.value.output or "")
    assert not [c for c in docker.compose_calls() if "up" in c]
    # The tree goes back to the commit that serves, as after a failed build.
    assert any(call[0] == "git" and "checkout" in call for call in docker.calls)
    row = store.list_deployments(DOMAIN)[0]
    assert row.status == "failed"
    assert json.loads(row.hooks or "[]")[0]["ok"] is False


def test_a_failing_post_deploy_hook_leaves_the_stack_deployed_with_warnings(
    tmp_path: Path, store: NoustStore, docker: Docker
) -> None:
    root = stack_tree(tmp_path / "stack")
    deployed(store, root)
    docker.failing_hook = "./scripts/purge-cache.sh"

    result = updater(root, docker).update()

    assert result.warnings and "post_deploy" in result.warnings[0]
    row = store.list_deployments(DOMAIN)[0]
    assert row.status == "success"
    assert row.warnings and "purge-cache" in row.warnings


def test_a_gate_that_fails_after_a_migration_says_so_first(
    tmp_path: Path, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = stack_tree(tmp_path / "stack")
    deployed(store, root)
    monkeypatch.setattr(docker_compose, "wait_until_healthy", lambda url, **kw: False)

    with pytest.raises(DeploymentError) as caught:
        updater(root, docker).update()

    assert caught.value.message.startswith(SCHEMA_CHANGED_PREFIX)
    assert store.list_deployments(DOMAIN)[0].schema_changed is True


def test_update_without_project_file_matches_3_1(
    tmp_path: Path, store: NoustStore, docker: Docker
) -> None:
    """No noust.yaml: the same commands, in the same order, as before hooks existed."""
    root = stack_tree(tmp_path / "stack", hooks=None)
    deployed(store, root)

    result = updater(root, docker).update()

    assert [c[0] for c in docker.compose_calls()] == ["ps", "build", "up", "ps"]
    assert result.hooks == () and result.schema_changed is False
    row = store.list_deployments(DOMAIN)[0]
    assert row.status == "success" and row.hooks is None and row.schema_changed is False


def test_a_hook_naming_a_service_the_stack_lacks_is_refused_before_the_build(
    tmp_path: Path, store: NoustStore, docker: Docker
) -> None:
    root = stack_tree(
        tmp_path / "stack",
        hooks="hooks:\n  pre_deploy:\n    - run: migrate\n      service: backend\n",
    )
    deployed(store, root)

    with pytest.raises(HookFailedError, match="could not run") as caught:
        updater(root, docker).update()

    assert "backend" in str(caught.value)
    assert not [c for c in docker.compose_calls() if "up" in c]


def test_the_operator_hooks_replace_the_repository_ones(
    tmp_path: Path, store: NoustStore, docker: Docker
) -> None:
    root = stack_tree(tmp_path / "stack")
    deployed(store, root)
    store.set_app_hooks(DOMAIN, "hooks:\n  pre_deploy:\n    - run: ./check.sh\n", updated_by="ops")

    updater(root, docker).update()

    runs = [c for c in docker.compose_calls() if "run" in c]
    assert runs == [("run", "--rm", "--no-deps", "--entrypoint", "", "web", "./check.sh")]


# -- First deploys ------------------------------------------------------------------


def test_a_first_deploy_lets_the_hook_start_what_its_service_depends_on(
    tmp_path: Path, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing serves yet, and the migration needs its database running."""
    root = stack_tree(tmp_path / "stack")
    deployer = DockerComposeDeployer(runner=docker)
    deployer.configure(DOMAIN, "https://example.com/s.git", app_path=root, ssl=False)
    deployer.deploy_target = SimpleNamespace(undo_fetch=lambda fs, log: None)  # type: ignore[assignment]
    for step in ("_fetch_source", "_create_site", "_create_systemd_service"):
        monkeypatch.setattr(deployer, step, lambda: None)
    started: list[int] = []
    monkeypatch.setattr(
        deployer, "_start_and_verify", lambda: started.append(len(docker.compose_calls()))
    )

    deployer.deploy()

    calls = docker.compose_calls()
    pre = next(c for c in calls if "prisma" in c)
    assert "--no-deps" not in pre
    assert docker.index_of("build") < calls.index(pre) < started[0]
    post = next(c for c in calls if "./scripts/purge-cache.sh" in c)
    assert calls.index(post) >= started[0]
    assert store.list_deployments(DOMAIN)[0].schema_changed is True


def test_a_first_deploy_with_a_bad_hook_document_is_refused_before_building(
    tmp_path: Path, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = stack_tree(
        tmp_path / "stack", hooks="hooks:\n  pre_deploy:\n    - run: x\n      user: root\n"
    )
    deployer = DockerComposeDeployer(runner=docker)
    deployer.configure(DOMAIN, "https://example.com/s.git", app_path=root, ssl=False)
    deployer.deploy_target = SimpleNamespace(undo_fetch=lambda fs, log: None)  # type: ignore[assignment]
    monkeypatch.setattr(deployer, "_fetch_source", lambda: None)

    with pytest.raises(ValidationError):
        deployer.deploy()

    assert not [c for c in docker.compose_calls() if "build" in c]


# -- A refusal after the pull ------------------------------------------------------


def checked_out(docker: Docker) -> list[tuple[str, ...]]:
    """The git checkouts the deployer ran."""
    return [call for call in docker.calls if call[0] == "git" and "checkout" in call]


def test_an_update_refused_for_its_project_file_puts_the_serving_commit_back(
    tmp_path: Path, store: NoustStore, docker: Docker
) -> None:
    """Review E2: the pull already moved the tree; a refusal must not leave it there."""
    root = stack_tree(
        tmp_path / "stack", hooks="hooks:\n  pre_deploy:\n    - run: x\n      user: root\n"
    )
    deployed(store, root)

    with pytest.raises(ValidationError):
        updater(root, docker).update()

    assert [call for call in checked_out(docker) if PREVIOUS in call]
    assert not [c for c in docker.compose_calls() if c[0] in ("build", "up")]
    row = store.list_deployments(DOMAIN)[0]
    assert row.status == "failed"


def test_an_update_refused_for_a_missing_compose_file_puts_the_serving_commit_back(
    tmp_path: Path, store: NoustStore, docker: Docker
) -> None:
    root = stack_tree(tmp_path / "stack", hooks=None)
    (root / "docker-compose.yml").unlink()
    deployed(store, root)

    with pytest.raises(DeploymentError, match="No Docker Compose file"):
        updater(root, docker).update()

    assert [call for call in checked_out(docker) if PREVIOUS in call]


def test_a_refusal_with_no_commit_to_go_back_to_checks_nothing_out(
    tmp_path: Path, store: NoustStore, docker: Docker
) -> None:
    root = stack_tree(
        tmp_path / "stack", hooks="hooks:\n  pre_deploy:\n    - run: x\n      user: root\n"
    )
    deployed(store, root)
    deployer = updater(root, docker)
    deployer.previous_commit = None

    with pytest.raises(ValidationError):
        deployer.update()

    assert checked_out(docker) == []
