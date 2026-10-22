# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app container``, ``noust create --image`` and ``noust setup railpack``.

They decide nothing themselves: the settings are
:mod:`noust.deployers.helpers.container_settings` (``tests/test_container_settings.py``)
and the installer is :mod:`noust.managers.railpack` (``tests/test_railpack_installer.py``).
Pinned here: what the options turn into, that no secret is ever an argument,
and what a person is told.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.commands import setup as setup_cmd
from noust.cli.commands import webapp
from noust.core.container_store import (
    ContainerSettings,
    ContainerStore,
    build_secret_name,
    registry_secret_name,
)
from noust.core.exceptions import DeploymentError
from noust.core.logger import Logger
from noust.core.runner import set_runner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers import railpack

DOMAIN = "shop.example.com"
APP = "shop-example-com"
PASSWORD = "hunter2-registry-password"


@pytest.fixture(autouse=True)
def _real_runner() -> Iterator[None]:
    yield
    set_runner(None)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


@pytest.fixture
def secrets(store: NoustStore) -> SecretStore:
    return SecretStore(root=store.db_path.parent / "secrets")


def invoke(args: list[str], stdin: str | None = None) -> Result:
    return CliRunner().invoke(root_cli, args, input=stdin)


def deployed(store: NoustStore, settings: ContainerSettings | None = None) -> None:
    store.create_app(
        App(
            domain=DOMAIN,
            app_type="container",
            app_path="/var/www/apps/shop",
            source="https://example.com/shop.git",
        )
    )
    ContainerStore(store).save(DOMAIN, settings or ContainerSettings(), actor="test")


def stored(store: NoustStore) -> ContainerSettings:
    found = ContainerStore(store).get(DOMAIN)
    assert found is not None
    return found


class TestShow:
    def test_as_json_it_is_the_whole_settings_and_no_password(self, store, secrets):
        deployed(
            store,
            ContainerSettings(
                method="image",
                image="ghcr.io/o/a:1",
                registry_username="ci",
                registry_secret=registry_secret_name(APP),
                volumes=("/data",),
            ),
        )
        secrets.write(registry_secret_name(APP), PASSWORD)

        result = invoke(["app", "container", "show", DOMAIN, "--json"])

        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["image"] == "ghcr.io/o/a:1"
        assert body["registry_username"] == "ci"
        assert body["volumes"] == ["/data"]
        assert PASSWORD not in result.output

    def test_a_person_reads_where_the_image_comes_from(self, store, log):
        deployed(store, ContainerSettings(target="runtime", build_args={"NODE_ENV": "production"}))

        result = invoke(["app", "container", "show", DOMAIN])

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "a Dockerfile in the repository" in text
        assert "runtime" in text and "NODE_ENV=production" in text

    def test_an_application_of_another_type_is_said(self, store, log):
        store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path="/x"))

        result = invoke(["app", "container", "show", DOMAIN])

        assert result.exit_code != 0
        assert "not a container application" in str(result.exception)


class TestSet:
    def test_it_changes_what_is_named_and_says_when_it_takes_effect(self, store, log):
        deployed(store)

        result = invoke(
            ["app", "container", "set", DOMAIN, "--target", "runtime", "--container-port", "9000"]
        )

        assert result.exit_code == 0, result.output
        assert stored(store) == ContainerSettings(target="runtime", container_port=9000)
        assert f"noust update {DOMAIN}" in "\n".join(log)

    def test_build_arguments_and_volumes_are_added_to_what_there_is(self, store):
        deployed(store, ContainerSettings(build_args={"A": "1"}, volumes=("/data",)))

        invoke(["app", "container", "set", DOMAIN, "--build-arg", "B=2", "--volume", "/uploads"])

        assert stored(store).build_args == {"A": "1", "B": "2"}
        assert stored(store).volumes == ("/data", "/uploads")

    def test_they_are_removed_by_name(self, store):
        deployed(store, ContainerSettings(build_args={"A": "1", "B": "2"}, volumes=("/a", "/b")))

        invoke(
            ["app", "container", "set", DOMAIN, "--remove-build-arg", "A", "--remove-volume", "/b"]
        )

        assert stored(store).build_args == {"B": "2"}
        assert stored(store).volumes == ("/a",)

    def test_clear_goes_back_to_the_default(self, store):
        deployed(store, ContainerSettings(target="runtime", container_port=9000))

        invoke(
            ["app", "container", "set", DOMAIN, "--clear", "target", "--clear", "container-port"]
        )

        assert stored(store) == ContainerSettings()

    def test_the_password_comes_on_stdin_and_never_as_an_argument(self, store, secrets):
        deployed(store, ContainerSettings(method="image", image="ghcr.io/o/a:1"))

        result = invoke(
            [
                "app",
                "container",
                "set",
                DOMAIN,
                "--registry-user",
                "ci",
                "--registry-password-stdin",
            ],
            stdin=PASSWORD + "\n",
        )

        assert result.exit_code == 0, result.output
        assert secrets.read(registry_secret_name(APP)) == PASSWORD
        assert stored(store).registry_username == "ci"
        assert PASSWORD not in result.output

    def test_there_is_no_option_that_takes_the_password_as_an_argument(self):
        from noust.cli.commands.app_container import container

        names = {opt for param in container.commands["set"].params for opt in param.opts}
        assert "--registry-password" not in names
        assert "--registry-password-stdin" in names

    def test_a_password_that_was_not_piped_in_is_asked_for(self, store):
        deployed(store, ContainerSettings(method="image", image="ghcr.io/o/a:1"))

        result = invoke(
            [
                "app",
                "container",
                "set",
                DOMAIN,
                "--registry-user",
                "ci",
                "--registry-password-stdin",
            ],
            stdin="",
        )

        assert result.exit_code != 0
        assert "piped in on standard input" in result.output + str(result.exception)

    def test_a_build_secret_is_read_from_a_file(self, store, secrets, tmp_path):
        deployed(store)
        npmrc = tmp_path / "npmrc"
        npmrc.write_text("//registry.npmjs.org/:_authToken=npm_SECRETVALUE\n")

        result = invoke(["app", "container", "set", DOMAIN, "--build-secret", f"npmrc={npmrc}"])

        assert result.exit_code == 0, result.output
        assert stored(store).build_secrets == ("npmrc",)
        assert (
            secrets.read(build_secret_name(APP, "npmrc"))
            == "//registry.npmjs.org/:_authToken=npm_SECRETVALUE"
        )

    def test_a_build_secret_is_removed_by_id(self, store, secrets):
        deployed(store)
        secrets.write(build_secret_name(APP, "npmrc"), "x")
        ContainerStore(store).set_build_secrets(DOMAIN, ["npmrc"])

        invoke(["app", "container", "set", DOMAIN, "--remove-build-secret", "npmrc"])

        assert stored(store).build_secrets == ()
        assert secrets.read(build_secret_name(APP, "npmrc")) is None

    def test_the_account_is_removed(self, store, secrets):
        deployed(store, ContainerSettings(method="image", image="ghcr.io/o/a:1"))
        invoke(
            [
                "app",
                "container",
                "set",
                DOMAIN,
                "--registry-user",
                "ci",
                "--registry-password-stdin",
            ],
            stdin=PASSWORD,
        )

        invoke(["app", "container", "set", DOMAIN, "--clear-registry"])

        assert stored(store).registry_username is None
        assert secrets.read(registry_secret_name(APP)) is None

    def test_naming_nothing_is_a_usage_error(self, store):
        deployed(store)

        result = invoke(["app", "container", "set", DOMAIN])

        assert result.exit_code == 2
        assert "Nothing to change" in result.output

    def test_a_value_that_is_not_valid_changes_nothing_and_says_which(self, store, log):
        deployed(store, ContainerSettings(target="runtime"))

        result = invoke(
            ["app", "container", "set", DOMAIN, "--volume", "relative", "--target", "x"]
        )

        assert result.exit_code != 0
        assert stored(store) == ContainerSettings(target="runtime")
        assert "volume" in str(result.exception).lower()

    def test_a_secret_looking_argument_is_pointed_at_build_secrets(self, store, log):
        deployed(store)

        result = invoke(["app", "container", "set", DOMAIN, "--build-arg", "NPM_TOKEN=x"])

        assert result.exit_code != 0
        assert "build secret" in str(result.exception)

    def test_an_argument_without_a_value_is_a_usage_error(self, store):
        deployed(store)

        result = invoke(["app", "container", "set", DOMAIN, "--build-arg", "NOVALUE"])

        assert result.exit_code == 2
        assert "NAME=VALUE" in result.output

    def test_a_rehearsal_changes_nothing(self, store, log):
        deployed(store)

        result = invoke(["--dry-run", "app", "container", "set", DOMAIN, "--target", "runtime"])

        assert result.exit_code == 0, result.output
        assert stored(store) == ContainerSettings()
        assert "Would change" in "\n".join(log)


class Spy:
    """A deployer that records how ``noust create`` configured it."""

    def __init__(self) -> None:
        self.configured: dict[str, Any] = {}
        self.fail: Exception | None = None

    def configure(self, **kwargs: Any) -> None:
        self.configured = kwargs

    def deploy(self) -> bool:
        if self.fail is not None:
            raise self.fail
        return True


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Spy:
    deployer = Spy()
    monkeypatch.setattr(webapp, "get_deployer", lambda app_type, verbose=False: deployer)
    monkeypatch.setattr(
        webapp, "check_deployment_ready", lambda app_type, package_manager, verbose: (True, [], [])
    )
    return deployer


class TestCreate:
    def test_an_image_needs_no_source_and_becomes_the_source(self, store, spy):
        result = invoke(["create", "-d", DOMAIN, "--image", "ghcr.io/owner/app:1.4", "-p", "3100"])

        assert result.exit_code == 0, result.output
        assert spy.configured["source"] == "ghcr.io/owner/app:1.4"
        container = spy.configured["container"]
        assert container["method"] == "image"
        assert container["image"] == "ghcr.io/owner/app:1.4"

    def test_the_type_is_container_whatever_was_left_on_auto(self, store, spy, monkeypatch):
        seen: list[str] = []
        monkeypatch.setattr(
            webapp,
            "get_deployer",
            lambda app_type, verbose=False: (seen.append(app_type), spy)[1],
        )

        invoke(["create", "-d", DOMAIN, "--image", "nginx", "-p", "3100"])

        assert seen == ["container"]

    def test_the_account_goes_in_on_stdin_and_the_job_gets_only_its_name(self, store, spy, secrets):
        result = invoke(
            [
                "create",
                "-d",
                DOMAIN,
                "--image",
                "ghcr.io/o/a:1",
                "-p",
                "3100",
                "--registry-user",
                "ci",
                "--registry-password-stdin",
            ],
            stdin=PASSWORD,
        )

        assert result.exit_code == 0, result.output
        assert secrets.read(registry_secret_name(APP)) == PASSWORD
        assert PASSWORD not in str(spy.configured)
        assert spy.configured["container"]["registry_secret"] == registry_secret_name(APP)

    def test_a_dockerfile_build_with_its_options(self, store, spy, tmp_path):
        result = invoke(
            [
                "create",
                "-d",
                DOMAIN,
                "-s",
                "https://example.com/shop.git",
                "-t",
                "container",
                "-p",
                "3100",
                "--dockerfile",
                "docker/Dockerfile",
                "--context",
                "api",
                "--target",
                "prod",
                "--build-arg",
                "A=1",
                "--volume",
                "/data",
                "--restart",
                "always",
                "--container-port",
                "9000",
            ],
        )

        assert result.exit_code == 0, result.output
        container = spy.configured["container"]
        assert container["method"] == "dockerfile"
        assert container["dockerfile"] == "docker/Dockerfile"
        assert container["context"] == "api"
        assert container["target"] == "prod"
        assert container["build_args"] == {"A": "1"}
        assert container["volumes"] == ("/data",)
        assert container["restart_policy"] == "always"
        assert container["container_port"] == 9000

    def test_railpack_is_a_builder_of_a_repository(self, store, spy):
        result = invoke(
            [
                "create",
                "-d",
                DOMAIN,
                "-s",
                "https://example.com/shop.git",
                "-p",
                "3100",
                "--builder",
                "railpack",
            ]
        )

        assert result.exit_code == 0, result.output
        assert spy.configured["container"]["method"] == "railpack"

    def test_a_container_without_options_configures_the_deployer_as_any_other_type(
        self, store, spy
    ):
        invoke(
            [
                "create",
                "-d",
                DOMAIN,
                "-s",
                "https://example.com/shop.git",
                "-t",
                "container",
                "-p",
                "3100",
            ]
        )

        assert "container" not in spy.configured

    def test_an_image_and_a_source_are_two_answers(self, store, spy):
        result = invoke(
            ["create", "-d", DOMAIN, "-s", "https://example.com/x.git", "--image", "nginx"]
        )

        assert result.exit_code == 2
        assert "--image is the source" in result.output
        assert spy.configured == {}

    def test_container_options_for_another_type_are_refused(self, store, spy):
        result = invoke(
            [
                "create",
                "-d",
                DOMAIN,
                "-s",
                "https://example.com/x.git",
                "-t",
                "nextjs",
                "--target",
                "prod",
            ]
        )

        assert result.exit_code == 2
        assert "--type container" in result.output

    def test_a_secret_never_reaches_the_command_line_of_a_build(
        self, store, spy, tmp_path, secrets
    ):
        npmrc = tmp_path / "npmrc"
        npmrc.write_text("//registry:_authToken=SECRETVALUE\n")

        result = invoke(
            [
                "create",
                "-d",
                DOMAIN,
                "-s",
                "https://example.com/shop.git",
                "-p",
                "3100",
                "--build-secret",
                f"npmrc={npmrc}",
            ]
        )

        assert result.exit_code == 0, result.output
        assert "SECRETVALUE" not in str(spy.configured)
        assert secrets.read(build_secret_name(APP, "npmrc")) == "//registry:_authToken=SECRETVALUE"

    def test_what_was_given_is_forgotten_when_the_application_never_came_to_exist(
        self, store, spy, secrets
    ):
        spy.fail = DeploymentError("The image could not be pulled")

        result = invoke(
            [
                "create",
                "-d",
                DOMAIN,
                "--image",
                "ghcr.io/o/a:1",
                "-p",
                "3100",
                "--registry-user",
                "ci",
                "--registry-password-stdin",
            ],
            stdin=PASSWORD,
        )

        assert result.exit_code != 0
        assert secrets.read(registry_secret_name(APP)) is None

    def test_nothing_to_deploy_from_is_said_with_the_three_ways(self, store, spy):
        result = invoke(["create", "-d", DOMAIN])

        assert result.exit_code == 2
        assert "--image" in result.output

    def test_a_database_is_refused_for_a_container(self, store, spy):
        result = invoke(["create", "-d", DOMAIN, "--image", "nginx", "--database", "postgresql"])

        assert result.exit_code == 2
        assert "container" in result.output


class TestSetupRailpack:
    def test_it_installs_the_pinned_release_and_says_what_it_is_for(self, monkeypatch, log, store):
        installed: list[bool] = []

        def install(self: railpack.RailpackInstaller, *, force: bool = False, machine=None):
            installed.append(force)
            return railpack.InstallOutcome(railpack.VERSION, Path("/usr/local/bin/railpack"), True)

        monkeypatch.setattr(railpack.RailpackInstaller, "install", install)

        result = invoke(["setup", "railpack"])

        assert result.exit_code == 0, result.output
        assert installed == [False]
        assert "--builder railpack" in "\n".join(log)

    def test_force_installs_it_again(self, monkeypatch, store):
        seen: list[bool] = []
        monkeypatch.setattr(
            railpack.RailpackInstaller,
            "install",
            lambda self, *, force=False, machine=None: (
                seen.append(force),
                railpack.InstallOutcome(railpack.VERSION, Path("/x"), True),
            )[1],
        )

        invoke(["setup", "railpack", "--force"])

        assert seen == [True]

    def test_a_download_that_does_not_match_its_checksum_is_an_error_exit(
        self, monkeypatch, store, log
    ):
        def install(self, *, force=False, machine=None):
            raise DeploymentError(
                "The Railpack download does not match its published checksum",
                details="Nothing was installed.",
            )

        monkeypatch.setattr(railpack.RailpackInstaller, "install", install)

        result = invoke(["setup", "railpack"])

        assert result.exit_code != 0

    def test_the_argparse_dispatch_knows_it_too(self, monkeypatch, store):
        monkeypatch.setattr(
            railpack.RailpackInstaller,
            "install",
            lambda self, *, force=False, machine=None: railpack.InstallOutcome(
                railpack.VERSION, Path("/x"), False
            ),
        )

        from argparse import Namespace

        assert setup_cmd.handle_setup(Namespace(action="railpack", verbose=False)) == 0
