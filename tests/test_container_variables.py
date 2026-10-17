# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A container application has the variables a process application has (checklist B-43, B-46, B-48).

A variable only the build is given reaches ``docker build`` as a BuildKit secret taken from the
command's environment under a name of Noust's own (never an argument, which stays in the image's
history, and never argv); what Noust says about the deployment is a build argument for a
Dockerfile that declares it and is in the ``.env`` its container loads; an image from a registry,
which builds nothing, refuses a variable only the build is given; and an update resolves the
references again and tells the container its release.
"""

# The container harness is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.container_store import ContainerSettings
from noust.core.exceptions import DeploymentError
from noust.core.store import NoustStore
from noust.core.variables.model import SERVER
from noust.core.variables.store import VariableStore
from noust.deployers.helpers.app_env import read_environment_of, write_app_env
from noust.deployers.helpers.env_manager import EnvManager
from noust.validators.environment import EnvironmentValidationError
from tests.container_support import APP, DOMAIN, Docker, root_of
from tests.test_container_deployer import (  # noqa: F401  (pytest resolves fixtures by name)
    IMAGE,
    certs,
    docker,
    make,
    releases,
    store,
    update,
    web,
)

TOKEN = "npm-token-VALUE-123"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = root_of(tmp_path)
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "Dockerfile").write_text("FROM scratch\nEXPOSE 9000\n")
    return root


def deployer_for(make, **options):  # type: ignore[no-untyped-def]
    deployer = make(None, source="https://example.com/shop.git", replace_existing=True, **options)
    deployer.source_already_fetched = True
    return deployer


def file_values(tmp_path: Path) -> dict[str, str]:
    return EnvManager().read_env_file(root_of(tmp_path) / ".env")


def build_index(docker: Docker) -> int:
    return next(i for i, call in enumerate(docker.calls) if call[:2] == ("docker", "build"))


class TestAVariableOnlyTheBuildIsGiven:
    def test_it_is_a_build_secret_taken_from_the_environment_never_argv(
        self, make, docker, store, tmp_path, tree
    ):
        deployer_for(
            make, env_vars={"NPM_TOKEN": TOKEN}, env_audience={"NPM_TOKEN": "build"}
        ).deploy()

        index = build_index(docker)
        build = docker.calls[index]
        assert TOKEN not in " ".join(" ".join(call) for call in docker.calls)
        position = build.index("id=NPM_TOKEN,env=NOUST_BUILD_VAR_0")
        assert build[position - 1] == "--secret"
        assert docker.envs[index]["NOUST_BUILD_VAR_0"] == TOKEN
        assert "--build-arg" not in build[position - 3 : position + 1]

    def test_it_is_in_no_file_and_the_container_is_not_given_it(
        self, make, docker, store, tmp_path, tree
    ):
        deployer_for(
            make, env_vars={"NPM_TOKEN": TOKEN, "KEPT": "1"}, env_audience={"NPM_TOKEN": "build"}
        ).deploy()

        assert "NPM_TOKEN" not in file_values(tmp_path)
        assert file_values(tmp_path)["KEPT"] == "1"
        for index, call in enumerate(docker.calls):
            if call[:2] == ("docker", "compose"):
                assert "NPM_TOKEN" not in (docker.envs[index] or {})
        app = store.get_app(DOMAIN)
        assert app is not None
        assert read_environment_of(app).build_only == {"NPM_TOKEN": TOKEN}

    def test_a_dockerfile_that_declares_it_as_an_argument_is_told_why_it_gets_none(
        self, make, docker, store, tmp_path, tree, monkeypatch
    ):
        (tree / "Dockerfile").write_text("FROM scratch\nARG NPM_TOKEN\nEXPOSE 9000\n")
        deployer = deployer_for(
            make, env_vars={"NPM_TOKEN": TOKEN}, env_audience={"NPM_TOKEN": "build"}
        )
        said: list[str] = []
        monkeypatch.setattr(deployer.logger, "warning", said.append)

        deployer.deploy()

        assert any("ARG NPM_TOKEN" in line and "type=secret,id=NPM_TOKEN" in line for line in said)
        assert not any(
            call[:2] == ("docker", "build") and f"NPM_TOKEN={TOKEN}" in call for call in docker.calls
        )

    def test_a_registry_image_refuses_it_and_says_what_to_do(
        self, make, docker, store, tmp_path
    ):
        deployer = make(IMAGE, env_vars={"NPM_TOKEN": "t"}, env_audience={"NPM_TOKEN": "build"})

        deployer.deploy()
        app = store.get_app(DOMAIN)
        assert app is not None
        with pytest.raises(EnvironmentValidationError) as caught:
            write_app_env(app, {"NPM_TOKEN": "t"}, audience={"NPM_TOKEN": "build"})

        assert "nothing is built" in caught.value.message


class TestWhatNoustSaysAboutTheDeployment:
    def test_the_container_loads_it_from_its_env(self, make, docker, store, tmp_path, tree):
        docker.commit = "ab12cd3"

        deployer_for(make).deploy()

        [(release, _)] = releases(store)
        facts = file_values(tmp_path)
        assert facts["NOUST_DOMAIN"] == DOMAIN
        assert facts["NOUST_COMMIT"] == "ab12cd3"
        assert facts["NOUST_RELEASE"] == release
        assert facts["NOUST_URL"].endswith(f"://{DOMAIN}")

    def test_a_dockerfile_that_declares_them_is_given_them_as_arguments(
        self, make, docker, store, tmp_path, tree
    ):
        (tree / "Dockerfile").write_text("FROM scratch\nARG NOUST_URL\nARG NOUST_COMMIT\nEXPOSE 9000\n")
        docker.commit = "ab12cd3"

        deployer_for(make).deploy()

        build = docker.calls[build_index(docker)]
        arguments = [build[i + 1] for i, word in enumerate(build) if word == "--build-arg"]
        assert any(argument.startswith("NOUST_URL=") for argument in arguments)
        # The commit changes on every deploy, so a build that depended on it could not be reused.
        assert not any(argument.startswith("NOUST_COMMIT=") for argument in arguments)

    def test_one_that_declares_nothing_is_given_nothing(
        self, make, docker, store, tmp_path, tree
    ):
        deployer_for(make).deploy()

        build = docker.calls[build_index(docker)]
        assert "--build-arg" not in build

    def test_an_image_from_a_registry_is_told_too(self, make, docker, store, tmp_path):
        make(IMAGE).deploy()

        assert file_values(tmp_path)["NOUST_DOMAIN"] == DOMAIN


class TestReferences:
    def test_an_update_resolves_them_again_before_it_builds(
        self, make, docker, store, tmp_path, tree
    ):
        variables = VariableStore(store)
        variables.set(SERVER, 0, "REGION", "eu")
        deployer_for(make, env_vars={"REGION": "{{server.REGION}}"}).deploy()
        assert file_values(tmp_path)["REGION"] == "eu"
        variables.set(SERVER, 0, "REGION", "us")

        update(store, docker).update()

        assert file_values(tmp_path)["REGION"] == "us"

    def test_one_that_no_longer_resolves_stops_the_update_before_it_builds(
        self, make, docker, store, tmp_path, tree
    ):
        variables = VariableStore(store)
        variables.set(SERVER, 0, "REGION", "eu")
        deployer_for(make, env_vars={"REGION": "{{server.REGION}}"}).deploy()
        variables.delete(SERVER, 0, "REGION")
        builds_before = len(docker.docker_calls("build"))

        with pytest.raises(Exception, match="REGION"):
            update(store, docker).update()

        assert len(docker.docker_calls("build")) == builds_before

    def test_an_update_tells_the_container_its_new_release(
        self, make, docker, store, tmp_path, tree
    ):
        deployer_for(make).deploy()
        first = releases(store)[0][0]

        update(store, docker).update()

        newest = releases(store)[0][0]
        assert newest != first
        assert file_values(tmp_path)["NOUST_RELEASE"] == newest

    def test_a_failed_update_leaves_the_file_as_it_was(
        self, make, docker, store, tmp_path, tree
    ):
        deployer_for(make).deploy()
        before = {k: v for k, v in file_values(tmp_path).items() if k.startswith("NOUST_")}
        docker.healthy = False

        with pytest.raises(DeploymentError):
            update(store, docker).update()

        after = {k: v for k, v in file_values(tmp_path).items() if k.startswith("NOUST_")}
        assert after == before
        assert APP in str(root_of(tmp_path))
