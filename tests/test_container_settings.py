# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Seeing and changing a container application's settings: the one implementation.

``noust app container`` and ``/api/apps/{d}/container`` both call it, so what
they allow is decided here: the change is validated whole, secrets go to the
secret store and never into the row, a change is audited by name, and an
application cannot be moved between an image and a repository.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.applock import AppBusyError
from noust.core.container_store import (
    ContainerSettings,
    ContainerStore,
    build_secret_name,
    registry_secret_name,
)
from noust.core.exceptions import NoustError, ValidationError
from noust.core.fs import RealFileSystem
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore, ReleaseRecord
from noust.deployers.helpers.container_settings import (
    change_container,
    describe_container,
    settings_for_new_app,
)
from tests.test_applock import Holder

DOMAIN = "shop.example.com"
APP = "shop-example-com"
PASSWORD = "hunter2-registry-password"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "secrets", fs=RealFileSystem())


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        audit, "record", lambda event, **kw: events.append((event, kw.get("details") or {}))
    )
    return events


def deployed(store: NoustStore, settings: ContainerSettings | None = None, **row: Any) -> App:
    app = store.create_app(
        App(
            domain=DOMAIN,
            app_type=row.pop("app_type", "container"),
            source=row.pop("source", "https://example.com/shop.git"),
            app_path="/var/www/apps/shop",
            **row,
        )
    )
    ContainerStore(store).save(DOMAIN, settings or ContainerSettings(), actor="test")
    return app


def change(store, secrets, changes: dict[str, Any]):
    return change_container(DOMAIN, changes, actor="admin", store=store, secrets=secrets)


class TestDescribing:
    def test_the_settings_and_the_release_that_serves(self, store):
        app = deployed(store, ContainerSettings(target="runtime", container_port=3000))
        assert app.id is not None
        store.record_release(
            ReleaseRecord(
                id="20261006-101500-ab12cd3",
                app_id=app.id,
                created_at="2026-10-06T10:15:00+00:00",
                status="active",
                path="noust-shop-example-com:20261006-101500-ab12cd3",
            )
        )

        view = describe_container(DOMAIN, store=store)

        assert view.settings["target"] == "runtime"
        assert view.settings["container_port"] == 3000
        assert view.release == "20261006-101500-ab12cd3"
        assert view.to_dict()["domain"] == DOMAIN

    def test_the_effective_port_is_shown_when_none_was_set(self, store):
        deployed(store, ContainerSettings(method="railpack"))

        assert describe_container(DOMAIN, store=store).settings["container_port"] == 3000

    def test_the_password_is_never_in_it(self, store, secrets):
        deployed(
            store,
            ContainerSettings(
                method="image",
                image="ghcr.io/o/a:1",
                registry_username="ci",
                registry_secret=registry_secret_name(APP),
            ),
        )
        secrets.write(registry_secret_name(APP), PASSWORD)

        view = describe_container(DOMAIN, store=store)

        assert view.settings["registry_password_set"] is True
        assert view.settings["registry_server"] == "ghcr.io"
        assert PASSWORD not in str(view.to_dict())

    def test_an_application_of_another_type_is_refused(self, store):
        deployed(store, app_type="nodejs")

        with pytest.raises(ValidationError) as caught:
            describe_container(DOMAIN, store=store)
        assert "not a container application" in caught.value.message

    def test_an_unknown_application_is_said(self, store):
        with pytest.raises(NoustError) as caught:
            describe_container(DOMAIN, store=store)
        assert "not found" in caught.value.message


class TestChanging:
    def test_what_is_named_changes_and_the_rest_stays(self, store, secrets):
        deployed(store, ContainerSettings(target="runtime", build_args={"A": "1"}))

        outcome = change(
            store, secrets, {"dockerfile": "docker/Dockerfile", "container_port": 9000}
        )

        assert outcome.changed == ("container_port", "dockerfile")
        kept = ContainerStore(store).get(DOMAIN)
        assert kept == ContainerSettings(
            target="runtime",
            build_args={"A": "1"},
            dockerfile="docker/Dockerfile",
            container_port=9000,
        )

    def test_nothing_named_changes_nothing(self, store, secrets, recorded):
        deployed(store)

        outcome = change(store, secrets, {})

        assert outcome.changed == ()

    def test_a_setting_goes_back_to_its_default_when_it_is_cleared(self, store, secrets):
        deployed(store, ContainerSettings(target="runtime", container_port=9000, context="api"))

        change(store, secrets, {"target": None, "container_port": None, "context": None})

        assert ContainerStore(store).get(DOMAIN) == ContainerSettings()

    def test_build_arguments_replace_the_set(self, store, secrets):
        deployed(store, ContainerSettings(build_args={"A": "1", "B": "2"}))

        change(store, secrets, {"build_args": {"C": "3"}})

        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None and kept.build_args == {"C": "3"}

    def test_the_change_is_audited_by_name_and_never_by_value(self, store, secrets, recorded):
        deployed(store)

        change(
            store,
            secrets,
            {"target": "runtime", "registry_password": PASSWORD, "registry_username": "ci",
             "registry_server": "ghcr.io"},
        )  # fmt: skip

        [(event, details)] = recorded
        assert event == "apps.container"
        assert "target" in details["changed"] and "registry_password" in details["changed"]
        assert PASSWORD not in str(recorded)

    def test_a_refused_change_writes_nothing_at_all(self, store, secrets, tmp_path):
        deployed(store, ContainerSettings(target="runtime"))

        with pytest.raises(ValidationError) as caught:
            change(
                store,
                secrets,
                {"target": "new", "volumes": ["not-absolute"], "registry_password": PASSWORD},
            )

        assert caught.value.field == "volumes"
        assert ContainerStore(store).get(DOMAIN) == ContainerSettings(target="runtime")
        # Not even the password it carried.
        assert secrets.read(registry_secret_name(APP)) is None

    def test_an_unknown_setting_is_named(self, store, secrets):
        deployed(store)

        with pytest.raises(ValidationError) as caught:
            change(store, secrets, {"privileged": True})

        assert caught.value.field == "privileged"

    def test_a_secret_looking_build_argument_points_at_build_secrets(self, store, secrets):
        deployed(store)

        with pytest.raises(ValidationError) as caught:
            change(store, secrets, {"build_args": {"NPM_TOKEN": "x"}})

        assert "build secret" in caught.value.details

    def test_an_application_in_the_middle_of_something_is_not_changed(self, store, secrets):
        deployed(store)

        with Holder(DOMAIN, "update"), pytest.raises(AppBusyError, match="update started at"):
            change(store, secrets, {"target": "runtime"})

        assert ContainerStore(store).get(DOMAIN) == ContainerSettings()

    def test_an_application_without_settings_is_said(self, store, secrets):
        store.create_app(App(domain=DOMAIN, app_type="container", app_path="/x"))

        with pytest.raises(NoustError) as caught:
            change(store, secrets, {"target": "x"})

        assert "no container settings" in caught.value.message


class TestImageOrRepository:
    def test_a_new_image_is_what_the_application_list_shows(self, store, secrets):
        deployed(
            store,
            ContainerSettings(method="image", image="ghcr.io/o/a:1"),
            source="ghcr.io/o/a:1",
        )

        change(store, secrets, {"image": "ghcr.io/o/a:2"})

        row = store.get_app(DOMAIN)
        assert row is not None and row.source == "ghcr.io/o/a:2"

    def test_an_image_application_cannot_become_a_repository(self, store, secrets):
        deployed(store, ContainerSettings(method="image", image="nginx"))

        with pytest.raises(ValidationError) as caught:
            change(store, secrets, {"method": "dockerfile", "image": None})

        assert caught.value.field == "method"
        assert "different applications" in caught.value.message

    def test_a_repository_cannot_become_an_image(self, store, secrets):
        deployed(store)

        with pytest.raises(ValidationError) as caught:
            change(store, secrets, {"method": "image", "image": "nginx"})

        assert caught.value.field == "method"

    def test_a_dockerfile_can_become_railpack_and_back(self, store, secrets):
        deployed(store, ContainerSettings(dockerfile="Dockerfile", target="x"))

        with pytest.raises(ValidationError):
            change(store, secrets, {"method": "railpack"})  # still has a Dockerfile and a stage
        change(store, secrets, {"method": "railpack", "dockerfile": None, "target": None})
        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None and kept.method == "railpack"
        change(store, secrets, {"method": "dockerfile"})
        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None and kept.method == "dockerfile"


class TestSecrets:
    def test_the_password_goes_to_the_secret_store_and_the_row_names_it(
        self, store, secrets, tmp_path
    ):
        deployed(store, ContainerSettings(method="image", image="ghcr.io/o/a:1"))

        change(store, secrets, {"registry_username": "ci", "registry_password": PASSWORD})

        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None
        assert kept.registry_username == "ci"
        assert kept.registry_secret == registry_secret_name(APP)
        assert secrets.read(registry_secret_name(APP)) == PASSWORD
        assert PASSWORD.encode() not in (tmp_path / "noust.db").read_bytes()

    def test_a_username_without_a_password_is_refused(self, store, secrets):
        deployed(store, ContainerSettings(method="image", image="ghcr.io/o/a:1"))

        with pytest.raises(ValidationError) as caught:
            change(store, secrets, {"registry_username": "ci"})

        assert caught.value.field == "registry_secret"

    def test_the_account_is_removed_with_its_password(self, store, secrets):
        deployed(store, ContainerSettings(method="image", image="ghcr.io/o/a:1"))
        change(store, secrets, {"registry_username": "ci", "registry_password": PASSWORD})

        change(store, secrets, {"clear_registry": True})

        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None and kept.registry_username is None
        assert secrets.read(registry_secret_name(APP)) is None

    def test_a_new_password_replaces_the_old(self, store, secrets):
        deployed(store, ContainerSettings(method="image", image="ghcr.io/o/a:1"))
        change(store, secrets, {"registry_username": "ci", "registry_password": PASSWORD})

        change(store, secrets, {"registry_password": "another"})

        assert secrets.read(registry_secret_name(APP)) == "another"

    def test_a_build_secret_is_declared_by_giving_its_value(self, store, secrets):
        deployed(store)

        change(store, secrets, {"build_secret_values": {"npmrc": "//registry:_authToken=x"}})

        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None and kept.build_secrets == ("npmrc",)
        assert secrets.read(build_secret_name(APP, "npmrc")) == "//registry:_authToken=x"

    def test_a_build_secret_is_removed_with_its_value(self, store, secrets):
        deployed(store)
        change(store, secrets, {"build_secret_values": {"npmrc": "x", "pip": "y"}})

        change(store, secrets, {"build_secret_values": {"npmrc": None}})

        kept = ContainerStore(store).get(DOMAIN)
        assert kept is not None and kept.build_secrets == ("pip",)
        assert secrets.read(build_secret_name(APP, "npmrc")) is None
        assert secrets.read(build_secret_name(APP, "pip")) == "y"


class TestANewApplication:
    def test_it_validates_then_writes_the_secrets_and_names_them(self, secrets):
        settings = settings_for_new_app(
            DOMAIN,
            {
                "method": "image",
                "image": "ghcr.io/o/a:1",
                "registry_username": "ci",
                "registry_password": PASSWORD,
            },
            secrets=secrets,
        )

        assert settings.registry_secret == registry_secret_name(APP)
        assert secrets.read(registry_secret_name(APP)) == PASSWORD

    def test_a_refusal_leaves_no_secret_behind(self, secrets):
        with pytest.raises(ValidationError):
            settings_for_new_app(
                DOMAIN,
                {
                    "method": "image",
                    "image": "not valid",
                    "registry_username": "ci",
                    "registry_password": PASSWORD,
                },
                secrets=secrets,
            )

        assert secrets.read(registry_secret_name(APP)) is None

    def test_a_dockerfile_build_with_a_secret(self, secrets):
        settings = settings_for_new_app(
            DOMAIN, {"build_secret_values": {"npmrc": "x"}, "target": "prod"}, secrets=secrets
        )

        assert settings.build_secrets == ("npmrc",) and settings.target == "prod"
        assert secrets.read(build_secret_name(APP, "npmrc")) == "x"
