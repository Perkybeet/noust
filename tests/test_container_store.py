# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the store keeps of a container application: its build, its image, its registry account.

The settings live in a table of their own (``app_container``, schema v14),
keyed by the application and removed with it. That is what lets them survive
what rewrites an ``apps`` row (a redeploy writes back the whole row it read),
and each column has one validated setter.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import schema_v14
from noust.core import store as store_module
from noust.core.container_store import (
    DEFAULT_PORTS,
    ContainerSettings,
    ContainerStore,
    build_secret_name,
    forget_secrets,
    registry_secret_name,
)
from noust.core.exceptions import ValidationError
from noust.core.fs import RecordingFileSystem
from noust.core.secrets import SecretStore
from noust.core.store import App, AppType, NoustStore
from noust.deployers.helpers.registration import StoreRegistrar

DOMAIN = "shop.example.com"
DIGEST = "sha256:" + "b" * 64


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def containers(store: NoustStore) -> ContainerStore:
    store.create_app(
        App(domain=DOMAIN, app_type=AppType.CONTAINER.value, app_path="/var/www/apps/shop")
    )
    return ContainerStore(store)


def _tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    finally:
        conn.close()


def _columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(app_container)")}
    finally:
        conn.close()


class TestTheType:
    def test_container_is_an_application_type(self):
        assert AppType.CONTAINER.value == "container"

    def test_a_container_runs_in_compose_like_a_stack(self):
        assert store_module.is_compose_backed("container")
        assert store_module.is_compose_backed("docker-compose")
        assert not store_module.is_compose_backed("nodejs")
        assert not store_module.is_compose_backed(None)


class TestTheSchema:
    def test_the_fragment_is_the_container_lanes_own(self):
        assert "app_container" in schema_v14.CONTAINER_SQL

    def test_a_fresh_store_has_the_table(self, store: NoustStore, tmp_path: Path):
        assert "app_container" in _tables(tmp_path / "noust.db")
        assert {
            "app_id",
            "method",
            "image",
            "dockerfile",
            "context",
            "target",
            "build_args",
            "build_secrets",
            "container_port",
            "volumes",
            "restart_policy",
            "registry_server",
            "registry_username",
            "registry_secret",
            "digest",
            "updated_by",
            "updated_at",
        } <= _columns(tmp_path / "noust.db")

    def test_a_v13_store_climbs_and_keeps_its_rows(self, tmp_path: Path):
        db_path = tmp_path / "noust.db"
        NoustStore.reset_instance()
        first = NoustStore(db_path, fs=RecordingFileSystem())
        first.create_app(App(domain=DOMAIN, app_type="nextjs", app_path="/opt/shop"))
        NoustStore.reset_instance()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DROP TABLE app_container")
            conn.execute("UPDATE schema_version SET version = 13 WHERE version > 13")
            conn.commit()
        finally:
            conn.close()
        assert "app_container" not in _tables(db_path)

        reopened = NoustStore(db_path, fs=RecordingFileSystem())
        try:
            assert "app_container" in _tables(db_path)
            assert reopened.get_app(DOMAIN) is not None
            # Nothing is invented for an application that was not a container.
            assert ContainerStore(reopened).get(DOMAIN) is None
        finally:
            NoustStore.reset_instance()

    def test_a_store_stamped_v14_without_the_table_is_completed(self, tmp_path: Path):
        db_path = tmp_path / "noust.db"
        NoustStore.reset_instance()
        NoustStore(db_path, fs=RecordingFileSystem())
        NoustStore.reset_instance()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DROP TABLE app_container")
            conn.commit()
        finally:
            conn.close()

        NoustStore(db_path, fs=RecordingFileSystem())
        try:
            assert "app_container" in _tables(db_path)
        finally:
            NoustStore.reset_instance()

    def test_applying_the_fragment_twice_changes_nothing(self, store: NoustStore, tmp_path: Path):
        before = _columns(tmp_path / "noust.db")
        with store._ddl_transaction() as cursor:
            store._migrate_v13_to_v14(cursor)
        assert _columns(tmp_path / "noust.db") == before


class TestReadingAndWriting:
    def test_an_application_without_a_row_has_no_settings(self, containers: ContainerStore):
        assert containers.get(DOMAIN) is None
        assert containers.get("other.example.com") is None

    def test_a_saved_dockerfile_application_reads_back(self, containers: ContainerStore):
        saved = ContainerSettings(
            method="dockerfile",
            dockerfile="docker/Dockerfile.prod",
            context="services/api",
            target="runtime",
            build_args={"NODE_ENV": "production"},
            build_secrets=("npmrc",),
            container_port=3000,
            volumes=("/data", "/uploads"),
            restart_policy="always",
        )
        assert containers.save(DOMAIN, saved, actor="admin") is True

        assert containers.get(DOMAIN) == saved

    def test_a_registry_image_with_an_account_reads_back(self, containers: ContainerStore):
        saved = ContainerSettings(
            method="image",
            image="ghcr.io/owner/app:1.4",
            registry_server="ghcr.io",
            registry_username="ci",
            registry_secret=registry_secret_name("shop-example-com"),
            digest=DIGEST,
        )
        containers.save(DOMAIN, saved, actor="admin")

        assert containers.get(DOMAIN) == saved

    def test_saving_again_replaces_the_row(self, containers: ContainerStore):
        containers.save(DOMAIN, ContainerSettings(target="a"), actor="admin")
        containers.save(DOMAIN, ContainerSettings(target="b"), actor="admin")

        found = containers.get(DOMAIN)
        assert found is not None and found.target == "b"

    def test_saving_for_an_unknown_application_changes_nothing(self, containers: ContainerStore):
        assert containers.save("nobody.example.com", ContainerSettings(), actor="a") is False

    def test_the_default_port_depends_on_how_the_image_is_made(self):
        assert ContainerSettings(method="dockerfile").effective_port == DEFAULT_PORTS["dockerfile"]
        assert ContainerSettings(method="railpack").effective_port == DEFAULT_PORTS["railpack"]
        assert (
            ContainerSettings(method="image", image="nginx").effective_port
            == DEFAULT_PORTS["image"]
        )
        assert ContainerSettings(container_port=9000).effective_port == 9000


class TestAnImageNeedsItsReference:
    def test_a_registry_image_without_an_image_is_refused(self, containers: ContainerStore):
        with pytest.raises(ValidationError) as caught:
            containers.save(DOMAIN, ContainerSettings(method="image"), actor="admin")
        assert caught.value.field == "image"

    def test_a_build_with_an_image_reference_is_refused(self, containers: ContainerStore):
        with pytest.raises(ValidationError) as caught:
            containers.save(
                DOMAIN, ContainerSettings(method="dockerfile", image="nginx"), actor="admin"
            )
        assert caught.value.field == "image"

    def test_an_image_has_no_build_settings(self, containers: ContainerStore):
        with pytest.raises(ValidationError) as caught:
            containers.save(
                DOMAIN, ContainerSettings(method="image", image="nginx", target="x"), actor="admin"
            )
        assert caught.value.field == "target"

    def test_railpack_has_no_dockerfile_or_target(self, containers: ContainerStore):
        with pytest.raises(ValidationError):
            containers.save(
                DOMAIN, ContainerSettings(method="railpack", dockerfile="Dockerfile"), actor="a"
            )

    def test_a_railpack_secret_is_an_environment_variable_name(self, containers: ContainerStore):
        with pytest.raises(ValidationError) as caught:
            containers.save(
                DOMAIN,
                ContainerSettings(method="railpack", build_secrets=("npm.rc",)),
                actor="admin",
            )
        assert caught.value.field == "build_secrets"

    @pytest.mark.parametrize("name", ["LD_PRELOAD", "DOCKER_HOST", "PATH", "HTTPS_PROXY", "HOME"])
    def test_a_railpack_secret_cannot_steer_the_commands_that_build(
        self, containers: ContainerStore, name: str
    ):
        with pytest.raises(ValidationError) as caught:
            containers.save(
                DOMAIN,
                ContainerSettings(method="railpack", build_secrets=(name,)),
                actor="admin",
            )
        assert caught.value.field == "build_secrets"
        assert "keeps for itself" in caught.value.message

    def test_a_railpack_name_is_a_variable_or_a_secret_not_both(self, containers: ContainerStore):
        with pytest.raises(ValidationError) as caught:
            containers.save(
                DOMAIN,
                ContainerSettings(
                    method="railpack", build_args={"NPM_RC": "x"}, build_secrets=("NPM_RC",)
                ),
                actor="admin",
            )
        assert caught.value.field == "build_secrets"

    def test_a_railpack_build_may_have_variables_and_secrets(self, containers: ContainerStore):
        settings = ContainerSettings(
            method="railpack", build_args={"RAILPACK_NODE_VERSION": "22"}, build_secrets=("NPM_RC",)
        )
        assert containers.save(DOMAIN, settings, actor="admin")

    def test_an_account_needs_both_halves(self, containers: ContainerStore):
        with pytest.raises(ValidationError) as caught:
            containers.save(
                DOMAIN,
                ContainerSettings(method="image", image="ghcr.io/o/a:1", registry_username="ci"),
                actor="admin",
            )
        assert caught.value.field == "registry_secret"

    def test_the_account_is_for_the_registry_the_image_comes_from(self, containers: ContainerStore):
        with pytest.raises(ValidationError) as caught:
            containers.save(
                DOMAIN,
                ContainerSettings(
                    method="image",
                    image="ghcr.io/o/a:1",
                    registry_server="registry.example.com",
                    registry_username="ci",
                    registry_secret="container/shop-example-com/registry",
                ),
                actor="admin",
            )
        assert caught.value.field == "registry_server"


class TestEverySetterValidates:
    CASES = [
        ("set_method", "buildpacks", "method"),
        ("set_image", "-v /:/host nginx", "image"),
        ("set_dockerfile", "../Dockerfile", "dockerfile"),
        ("set_context", "/etc", "context"),
        ("set_target", "a b", "target"),
        ("set_build_args", {"DB_PASSWORD": "x"}, "build_args"),
        ("set_build_secrets", ["a", "a"], "build_secrets"),
        ("set_container_port", 70000, "container_port"),
        ("set_volumes", ["data"], "volumes"),
        ("set_restart_policy", "sometimes", "restart_policy"),
        ("set_digest", "not-a-digest", "digest"),
    ]

    @pytest.mark.parametrize(("setter", "value", "field"), CASES)
    def test_a_bad_value_is_refused_and_nothing_is_written(
        self, containers: ContainerStore, setter: str, value: object, field: str
    ):
        containers.save(DOMAIN, ContainerSettings(), actor="admin")
        before = containers.get(DOMAIN)

        with pytest.raises(ValidationError) as caught:
            getattr(containers, setter)(DOMAIN, value)

        assert caught.value.field == field
        assert containers.get(DOMAIN) == before

    def test_the_setters_write_one_column_each(self, containers: ContainerStore):
        containers.save(DOMAIN, ContainerSettings(), actor="admin")

        assert containers.set_dockerfile(DOMAIN, "docker/Dockerfile")
        assert containers.set_context(DOMAIN, "services/api")
        assert containers.set_target(DOMAIN, "prod")
        assert containers.set_build_args(DOMAIN, {"A": "1"})
        assert containers.set_build_secrets(DOMAIN, ["npmrc"])
        assert containers.set_container_port(DOMAIN, 8081)
        assert containers.set_volumes(DOMAIN, ["/data"])
        assert containers.set_restart_policy(DOMAIN, "on-failure")
        assert containers.set_digest(DOMAIN, DIGEST)

        assert containers.get(DOMAIN) == ContainerSettings(
            dockerfile="docker/Dockerfile",
            context="services/api",
            target="prod",
            build_args={"A": "1"},
            build_secrets=("npmrc",),
            container_port=8081,
            volumes=("/data",),
            restart_policy="on-failure",
            digest=DIGEST,
        )

    def test_a_setter_is_false_for_an_application_without_settings(
        self, containers: ContainerStore
    ):
        assert containers.set_target(DOMAIN, "prod") is False

    def test_clearing_optional_values(self, containers: ContainerStore):
        containers.save(
            DOMAIN, ContainerSettings(target="a", container_port=80, digest=DIGEST), actor="a"
        )

        containers.set_target(DOMAIN, None)
        containers.set_container_port(DOMAIN, None)
        containers.set_digest(DOMAIN, None)

        assert containers.get(DOMAIN) == ContainerSettings()

    def test_the_registry_account_is_set_as_a_whole(self, containers: ContainerStore):
        containers.save(
            DOMAIN, ContainerSettings(method="image", image="ghcr.io/o/a:1"), actor="admin"
        )

        containers.set_registry(DOMAIN, "ghcr.io", "ci", registry_secret_name("shop-example-com"))
        found = containers.get(DOMAIN)
        assert found is not None
        assert (found.registry_server, found.registry_username) == ("ghcr.io", "ci")

        containers.set_registry(DOMAIN, None, None, None)
        found = containers.get(DOMAIN)
        assert found is not None
        assert (found.registry_server, found.registry_username, found.registry_secret) == (
            None,
            None,
            None,
        )

    def test_the_registry_account_is_refused_half_given(self, containers: ContainerStore):
        containers.save(DOMAIN, ContainerSettings(method="image", image="nginx"), actor="admin")
        with pytest.raises(ValidationError):
            containers.set_registry(DOMAIN, "ghcr.io", None, None)


class TestWhatARedeployRewrites:
    def test_the_settings_survive_the_full_row_write_of_a_redeploy(
        self, store: NoustStore, containers: ContainerStore
    ):
        settings = ContainerSettings(
            dockerfile="Dockerfile.prod", build_args={"A": "1"}, volumes=("/data",)
        )
        containers.save(DOMAIN, settings, actor="admin")
        row = store.get_app(DOMAIN)
        assert row is not None

        # What every redeploy does: write back the row it read when it started.
        row.status = "failed"
        store.update_app(row)

        assert containers.get(DOMAIN) == settings

    def test_the_settings_survive_the_registrar_a_deploy_ends_with(
        self, store: NoustStore, containers: ContainerStore
    ):
        settings = ContainerSettings(target="runtime", container_port=4000)
        containers.save(DOMAIN, settings, actor="admin")

        StoreRegistrar(store).register_app(
            domain=DOMAIN,
            app_type=AppType.CONTAINER.value,
            source="https://example.com/shop.git",
            branch="main",
            port=3001,
            app_path=Path("/var/www/apps/shop"),
            webserver="nginx",
            ssl_enabled=True,
            status="running",
            is_static=False,
            env_vars={},
        )

        assert containers.get(DOMAIN) == settings

    def test_deleting_the_application_deletes_the_settings(
        self, store: NoustStore, containers: ContainerStore, tmp_path: Path
    ):
        containers.save(DOMAIN, ContainerSettings(), actor="admin")

        store.delete_app(DOMAIN)

        conn = sqlite3.connect(tmp_path / "noust.db")
        try:
            assert conn.execute("SELECT COUNT(*) FROM app_container").fetchone() == (0,)
        finally:
            conn.close()

    def test_a_new_application_on_the_domain_does_not_inherit_the_old_ones(
        self, store: NoustStore, containers: ContainerStore
    ):
        containers.save(DOMAIN, ContainerSettings(target="old"), actor="admin")
        store.delete_app(DOMAIN)
        store.create_app(App(domain=DOMAIN, app_type="container", app_path="/var/www/apps/shop"))

        assert containers.get(DOMAIN) is None


class TestTheSecretsKeptApart:
    def test_secret_names_live_under_the_application(self):
        assert registry_secret_name("shop-example-com") == "container/shop-example-com/registry"
        assert (
            build_secret_name("shop-example-com", "npmrc")
            == "container/shop-example-com/build/npmrc"
        )

    def test_a_secret_name_the_secret_store_would_refuse_is_refused_here(self):
        with pytest.raises(ValidationError):
            build_secret_name("shop-example-com", "../x")

    def test_forgetting_removes_the_account_and_every_build_secret(self, tmp_path: Path):
        secrets = SecretStore(root=tmp_path / "secrets", fs=RecordingFileSystem())
        secrets.write(registry_secret_name("shop-example-com"), "hunter2")
        secrets.write(build_secret_name("shop-example-com", "npmrc"), "//registry:_authToken=x")
        secrets.write(build_secret_name("other-example-com", "npmrc"), "keep")

        forget_secrets("shop-example-com", secrets)

        assert secrets.read(registry_secret_name("shop-example-com")) is None
        assert secrets.read(build_secret_name("shop-example-com", "npmrc")) is None
        assert secrets.read(build_secret_name("other-example-com", "npmrc")) == "keep"

    def test_the_row_never_holds_a_password(self, containers: ContainerStore, tmp_path: Path):
        containers.save(
            DOMAIN,
            ContainerSettings(
                method="image",
                image="ghcr.io/o/a:1",
                registry_server="ghcr.io",
                registry_username="ci",
                registry_secret=registry_secret_name("shop-example-com"),
            ),
            actor="admin",
        )

        raw = (tmp_path / "noust.db").read_bytes()
        assert b"hunter2" not in raw
