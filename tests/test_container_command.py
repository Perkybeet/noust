# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The command and the entrypoint of a container application.

An image's own ENTRYPOINT and CMD are what runs unless the application says otherwise (Dokploy
lets a command and its arguments replace them). Here they are two settings, argument vectors and
never a shell line: a list of words, or a line split into words by the one definition of that
(``split_command``). They reach the compose file as lists with every ``$`` doubled, so what the
operator typed is what the process gets and Compose does not read the host's environment into it.
None keeps the image's own, an empty list clears it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.container_store import ContainerSettings, ContainerStore
from noust.core.exceptions import ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.deployers.helpers.container_compose import ContainerPlan, build_document, render_compose
from noust.deployers.helpers.container_settings import (
    change_container,
    describe_container,
    settings_for_new_app,
)
from noust.managers.service_manager import ResourceLimits
from noust.validators.container import check_command, split_command

DOMAIN = "shop.example.com"
APP = "shop-example-com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def secrets(store: NoustStore) -> SecretStore:
    return SecretStore(root=store.db_path.parent / "secrets")


def deployed(store: NoustStore, settings: ContainerSettings | None = None) -> None:
    store.create_app(
        App(
            domain=DOMAIN,
            app_type="container",
            source="https://example.com/shop.git",
            app_path="/var/www/apps/shop",
        )
    )
    ContainerStore(store).save(DOMAIN, settings or ContainerSettings(), actor="test")


def service_of(settings: ContainerSettings) -> dict[str, Any]:
    plan = ContainerPlan(
        app_name=APP,
        domain=DOMAIN,
        release_id="20261017-101500-abc1234",
        host_port=3001,
        settings=settings,
        limits=ResourceLimits(),
    )
    return build_document(plan)["services"]["app"]  # type: ignore[no-any-return]


class TestTheWords:
    def test_a_line_is_split_the_way_a_shell_would_without_running_one(self) -> None:
        assert split_command("node 'dist/my server.js' --port 8080", field="command") == (
            "node",
            "dist/my server.js",
            "--port",
            "8080",
        )

    def test_a_shell_operator_is_just_a_word(self) -> None:
        assert split_command("echo a && rm -rf /", field="command") == (
            "echo",
            "a",
            "&&",
            "rm",
            "-rf",
            "/",
        )

    def test_an_open_quote_is_refused_naming_the_setting(self) -> None:
        with pytest.raises(ValidationError) as caught:
            split_command("echo 'oops", field="entrypoint")

        assert caught.value.field == "entrypoint"

    def test_a_list_of_strings_is_accepted_as_it_is(self) -> None:
        assert check_command(["sh", "-c", "migrate && serve"], field="command") == (
            "sh",
            "-c",
            "migrate && serve",
        )

    def test_an_empty_list_is_a_decision_not_an_absence(self) -> None:
        assert check_command([], field="entrypoint") == ()

    @pytest.mark.parametrize(
        "bad", ["node server.js", [1, 2], ["ok", None], ["a\x00b"], ["x" * 5000], ["w"] * 65]
    )
    def test_anything_else_is_refused(self, bad: object) -> None:
        with pytest.raises(ValidationError) as caught:
            check_command(bad, field="command")

        assert caught.value.field == "command"


class TestTheComposeFile:
    def test_neither_is_written_when_the_image_keeps_its_own(self) -> None:
        service = service_of(ContainerSettings(method="image", image="ghcr.io/o/a:1"))

        assert "command" not in service and "entrypoint" not in service

    def test_both_are_lists_of_words(self) -> None:
        service = service_of(
            ContainerSettings(
                method="image",
                image="ghcr.io/o/a:1",
                entrypoint=("/app/run",),
                command=("serve", "--port", "8080"),
            )
        )

        assert service["entrypoint"] == ["/app/run"]
        assert service["command"] == ["serve", "--port", "8080"]

    def test_a_dollar_is_the_processs_not_composes(self) -> None:
        service = service_of(
            ContainerSettings(
                method="image", image="ghcr.io/o/a:1", command=("sh", "-c", "echo $HOME ${X}")
            )
        )

        assert service["command"] == ["sh", "-c", "echo $$HOME $${X}"]

    def test_empty_clears_what_the_image_has(self) -> None:
        service = service_of(
            ContainerSettings(method="image", image="ghcr.io/o/a:1", entrypoint=())
        )

        assert service["entrypoint"] == []

    def test_the_file_is_deterministic_and_loads_back(self) -> None:
        plan = ContainerPlan(
            app_name=APP,
            domain=DOMAIN,
            release_id="20261017-101500-abc1234",
            host_port=3001,
            settings=ContainerSettings(command=("a", "b: c")),
            limits=ResourceLimits(),
        )

        text = render_compose(plan)

        assert text == render_compose(plan)
        assert yaml.safe_load(text)["services"]["app"]["command"] == ["a", "b: c"]


class TestTheStore:
    def test_what_is_saved_is_read_back_and_none_stays_none(self, store: NoustStore) -> None:
        deployed(store, ContainerSettings(entrypoint=("/run",), command=()))

        kept = ContainerStore(store).get(DOMAIN)

        assert kept is not None
        assert kept.entrypoint == ("/run",) and kept.command == ()
        ContainerStore(store).save(DOMAIN, ContainerSettings(), actor="t")
        again = ContainerStore(store).get(DOMAIN)
        assert again is not None and again.entrypoint is None and again.command is None

    def test_each_has_its_own_validated_setter(self, store: NoustStore) -> None:
        deployed(store)
        containers = ContainerStore(store)

        assert containers.set_command(DOMAIN, ["serve"]) is True
        assert containers.set_entrypoint(DOMAIN, ["/run", "--x"]) is True
        with pytest.raises(ValidationError):
            containers.set_command(DOMAIN, ["a\x00"])

        kept = containers.get(DOMAIN)
        assert (
            kept is not None and kept.command == ("serve",) and kept.entrypoint == ("/run", "--x")
        )

    def test_a_save_of_the_whole_row_by_a_redeploy_keeps_them(self, store: NoustStore) -> None:
        deployed(store, ContainerSettings(command=("serve",)))
        containers = ContainerStore(store)
        before = containers.get(DOMAIN)
        assert before is not None

        containers.save(DOMAIN, before, actor="deploy")

        after = containers.get(DOMAIN)
        assert after is not None and after.command == ("serve",)


class TestChangingThem:
    def test_a_line_or_a_list_sets_them_and_the_audit_names_what_changed(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        deployed(store)

        change = change_container(
            DOMAIN,
            {"entrypoint": "/app/run --fast", "command": ["serve", "--port", "8080"]},
            actor="ana",
            store=store,
            secrets=secrets,
        )

        assert change.changed == ("command", "entrypoint")
        view = describe_container(DOMAIN, store=store).settings
        assert view["entrypoint"] == ["/app/run", "--fast"]
        assert view["command"] == ["serve", "--port", "8080"]

    def test_none_goes_back_to_the_images_own(
        self, store: NoustStore, secrets: SecretStore
    ) -> None:
        deployed(store, ContainerSettings(command=("serve",)))

        change_container(DOMAIN, {"command": None}, actor="ana", store=store, secrets=secrets)

        assert describe_container(DOMAIN, store=store).settings["command"] is None

    def test_a_new_application_can_be_given_them(self, secrets: SecretStore) -> None:
        settings = settings_for_new_app(
            DOMAIN,
            {"image": "ghcr.io/o/a:1", "method": "image", "command": "serve --port 80"},
            secrets=secrets,
        )

        assert settings.command == ("serve", "--port", "80")


class TestTheCommandLine:
    def run(self, *args: str) -> Any:
        return CliRunner().invoke(root_cli, ["app", "container", *args])

    def test_set_splits_the_lines_and_show_prints_them(self, store: NoustStore) -> None:
        deployed(store)

        done = self.run("set", DOMAIN, "--entrypoint", "/app/run", "--command", "serve -p 80")
        shown = self.run("show", DOMAIN, "--json")

        assert done.exit_code == 0, done.output
        data = json.loads(shown.output)
        assert data["entrypoint"] == ["/app/run"] and data["command"] == ["serve", "-p", "80"]

    def test_an_empty_line_clears_the_images_and_clear_gives_it_back(
        self, store: NoustStore
    ) -> None:
        deployed(store, ContainerSettings(command=("serve",)))

        self.run("set", DOMAIN, "--command", "")
        cleared = json.loads(self.run("show", DOMAIN, "--json").output)["command"]
        self.run("set", DOMAIN, "--clear", "command")
        given_back = json.loads(self.run("show", DOMAIN, "--json").output)["command"]

        assert cleared == [] and given_back is None

    def test_an_open_quote_changes_nothing(self, store: NoustStore) -> None:
        deployed(store, ContainerSettings(command=("serve",)))

        done = self.run("set", DOMAIN, "--command", "echo 'oops")

        assert done.exit_code != 0
        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None and kept.command == ("serve",)
