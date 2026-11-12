# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whose a container, an image, a volume or a network is (``noust.core.docker.owners``).

The notices of 2026 against the tools that manage Docker repeat one pattern (Dokploy 130, Coolify 61,
Portainer 19): the id of a container is not tied to the person it belongs to before the permission is
checked, so a member who may touch their own containers may open a shell in anyone's. The owner of an
object is therefore decided in one place, and from what Noust *registered*: the application's
directory and Compose project in the store, the database instances' names in theirs. What an object
says of itself (its labels) is a claim that is checked against that and never grants anything.

Every origin has its case: an application, a database engine, Noust's own container and helper, and
nobody. Then the limits: a container that wears the labels of another project, a path that only starts
with an application's, a path that climbs out of it, an image two applications use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.docker.models import NOBODY, Container, Image, Network, Owner, OwnerKind, Volume
from noust.core.docker.owners import AppRecord, OwnerIndex, Registry, registry_from_store
from noust.core.store import App, NoustStore

SHOP = "/var/www/apps/shop-example-com"
BLOG = "/var/www/apps/blog-example-com"
RELEASE = "20261112-101500-abc1234"


def container(name: str = "shop-web-1", **changes: object) -> Container:
    base: dict[str, object] = {
        "id": ("c" * 12) + name.ljust(52, "0")[:52],
        "name": name,
        "image": "nginx:1.27",
        "state": "running",
        "project": "shop-example-com",
        "service": "web",
        "working_dir": SHOP,
    }
    base.update(changes)
    return Container(**base)  # type: ignore[arg-type]


REGISTRY = Registry(
    apps=(
        AppRecord(domain="shop.example.com", path=SHOP),
        AppRecord(domain="blog.example.com", path=BLOG),
        AppRecord(domain="proggest.es", path="/opt/proggest", project="proggest"),
    ),
    engines=("pg16",),
    own_ids=frozenset({"d" * 64}),
)


def index(*containers: Container, registry: Registry = REGISTRY) -> OwnerIndex:
    return OwnerIndex(registry, containers)


SHOP_OWNER = Owner(OwnerKind.APP, "shop.example.com", "shop-example-com")


class TestAContainer:
    def test_a_container_run_from_an_applications_directory_is_the_applications(self) -> None:
        assert index().of_container(container()) == SHOP_OWNER

    def test_a_stack_in_a_subdirectory_of_the_application_is_the_applications_too(self) -> None:
        sub = container(working_dir=f"{SHOP}/deploy", project="deploy")

        assert index().of_container(sub) == Owner(OwnerKind.APP, "shop.example.com", "deploy")

    def test_an_adopted_stack_is_found_where_it_runs_with_the_project_the_store_pinned(
        self,
    ) -> None:
        adopted = container(project="proggest", working_dir="/opt/proggest")

        assert index().of_container(adopted) == Owner(OwnerKind.APP, "proggest.es", "proggest")

    def test_a_container_of_a_database_instance_is_the_engines_by_its_name(self) -> None:
        engine = container("noust-db-pg16", project="noust-db-pg16", working_dir="/var/lib/noust/x")

        assert index().of_container(engine) == Owner(OwnerKind.DATABASE, "pg16", "noust-db-pg16")

    def test_the_container_noust_runs_in_is_noust_whatever_its_labels_say(self) -> None:
        own = container("noust", id="d" * 64, project="", working_dir="")

        assert index().of_container(own) == Owner(OwnerKind.NOUST, "central")

    def test_a_short_id_of_the_container_noust_runs_in_is_found_too(self) -> None:
        own = container("noust", id="d" * 64, project="", working_dir="")
        registry = Registry(own_ids=frozenset({"d" * 12}))

        assert index(registry=registry).of_container(own).kind is OwnerKind.NOUST

    def test_what_noust_started_for_a_copy_is_noust(self) -> None:
        helper = container("tar-1", helper="volume-backup", project="", working_dir="")

        assert index().of_container(helper) == Owner(OwnerKind.NOUST, "helper")

    def test_what_noust_did_not_make_is_nobodys(self) -> None:
        other = container("portainer", project="", working_dir="", image="portainer/portainer-ce")

        assert index().of_container(other) == NOBODY
        assert NOBODY.unowned

    def test_a_directory_that_only_starts_like_an_applications_is_not_the_applications(
        self,
    ) -> None:
        evil = container(working_dir=f"{SHOP}-evil")

        assert index().of_container(evil) == NOBODY

    def test_a_path_that_climbs_out_of_the_applications_is_the_one_it_lands_in(self) -> None:
        climbs = container(working_dir=f"{SHOP}/../blog-example-com")

        assert index().of_container(climbs).subject == "blog.example.com"

    def test_the_most_specific_application_wins(self) -> None:
        nested = Registry(
            apps=(
                AppRecord("outer.example.com", "/srv/apps"),
                AppRecord("inner.example.com", "/srv/apps/inner"),
            )
        )

        found = index(registry=nested).of_container(
            container(working_dir="/srv/apps/inner/web", project="web")
        )

        assert found.subject == "inner.example.com"


class TestAContainerThatPretends:
    """The owner is what Noust registered, never what the container says of itself."""

    def test_the_project_of_another_application_without_its_directory_is_nobodys(self) -> None:
        pretender = container(project="proggest", working_dir="/tmp/attacker")

        assert index().of_container(pretender) == NOBODY

    def test_the_directory_of_an_application_with_the_wrong_pinned_project_is_nobodys(self) -> None:
        pretender = container(project="not-proggest", working_dir="/opt/proggest")

        assert index().of_container(pretender) == NOBODY

    def test_a_directory_with_no_project_is_nobodys(self) -> None:
        assert index().of_container(container(project="")) == NOBODY

    def test_a_label_that_says_it_is_a_database_instance_does_not_make_it_one(self) -> None:
        liar = container("mine", instance="pg16", project="", working_dir="")

        assert index().of_container(liar) == NOBODY

    def test_a_name_is_the_engines_only_with_a_row_of_the_store_behind_it(self) -> None:
        registry = Registry(engines=())
        lookalike = container("noust-db-pg16", project="", working_dir="")

        assert index(registry=registry).of_container(lookalike) == NOBODY

    def test_the_labels_can_only_take_access_away(self) -> None:
        """A container of an application that says it is Noust's helper is Noust's: more restricted."""
        claims = container(helper="volume-backup")

        assert index().of_container(claims).kind is OwnerKind.NOUST


class TestAVolume:
    def test_the_volume_compose_made_for_an_application_is_the_applications(self) -> None:
        volume = Volume(name="shop-example-com_data", project="shop-example-com", key="data")

        assert index().of_volume(volume) == SHOP_OWNER

    def test_a_volume_named_by_the_file_is_the_applications_when_its_container_mounts_it(
        self,
    ) -> None:
        volume = Volume(name="shared-data", project="shop-example-com", key="data")
        mounting = container(mounts=("shared-data",))

        assert index(mounting).of_volume(volume) == SHOP_OWNER

    def test_a_volume_that_only_wears_the_label_is_nobodys(self) -> None:
        forged = Volume(name="loot", project="shop-example-com", key="data")

        assert index().of_volume(forged) == NOBODY

    def test_the_project_of_an_adopted_stack_is_its_pinned_one(self) -> None:
        volume = Volume(name="proggest_db", project="proggest", key="db")

        assert index().of_volume(volume) == Owner(OwnerKind.APP, "proggest.es", "proggest")

    def test_the_data_volume_of_a_database_instance_is_the_engines(self) -> None:
        data = Volume(name="noust-db-pg16-data")
        config = Volume(name="noust-db-pg16-config")

        assert index().of_volume(data) == Owner(OwnerKind.DATABASE, "pg16", "noust-db-pg16")
        assert index().of_volume(config).kind is OwnerKind.DATABASE

    def test_a_volume_somebody_made_by_hand_is_nobodys(self) -> None:
        assert index().of_volume(Volume(name="pgdata")) == NOBODY

    def test_the_volumes_the_default_project_of_a_directory_names_are_found_before_a_container_runs(
        self,
    ) -> None:
        """Stopped for good: no container, and the project is what Compose derives from the directory."""
        volume = Volume(name="blog-example-com_uploads", project="blog-example-com", key="uploads")

        assert index().of_volume(volume).subject == "blog.example.com"


class TestANetwork:
    def test_the_default_network_of_a_project_is_the_applications(self) -> None:
        network = Network(id="1", name="shop-example-com_default", project="shop-example-com")

        assert index().of_network(network) == SHOP_OWNER

    def test_a_network_a_container_of_the_application_is_on_is_the_applications(self) -> None:
        network = Network(id="1", name="backend", project="shop-example-com")

        assert index(container(networks=("backend",))).of_network(network) == SHOP_OWNER

    def test_a_network_that_only_wears_the_label_is_nobodys(self) -> None:
        assert index().of_network(Network(id="1", name="x", project="shop-example-com")) == NOBODY

    def test_a_network_of_a_database_instance_is_the_engines(self) -> None:
        network = Network(id="1", name="noust-db-pg16_default", project="noust-db-pg16")

        assert index().of_network(network) == Owner(OwnerKind.DATABASE, "pg16", "noust-db-pg16")

    def test_dockers_own_networks_and_the_ones_made_by_hand_are_nobodys(self) -> None:
        assert index().of_network(Network(id="1", name="bridge", driver="bridge")) == NOBODY
        assert index().of_network(Network(id="2", name="mine")) == NOBODY


class TestAnImage:
    def test_the_image_of_a_release_of_a_container_application_is_its_way_back(self) -> None:
        image = Image(id="sha256:1", tags=(f"noust-shop-example-com:{RELEASE}",))

        owned = index().of_image(image)

        assert owned == Owner(OwnerKind.APP, "shop.example.com")
        assert index().is_way_back(image) is True

    def test_the_image_a_stack_keeps_for_a_release_is_its_way_back(self) -> None:
        image = Image(id="sha256:1", tags=(f"shop-example-com-web:noust-{RELEASE}",))

        assert index().of_image(image) == Owner(OwnerKind.APP, "shop.example.com")
        assert index().is_way_back(image) is True

    def test_the_image_an_update_keeps_while_it_runs_is_a_way_back_too(self) -> None:
        image = Image(id="sha256:1", tags=("shop-example-com-web:noust_previous",))

        assert index().is_way_back(image) is True

    def test_an_image_one_application_runs_is_that_applications(self) -> None:
        image = Image(id="sha256:1", tags=("nginx:1.27",), used_by=("shop-web-1",))

        assert index(container()).of_image(image) == Owner(OwnerKind.APP, "shop.example.com")
        assert index(container()).is_way_back(image) is False

    def test_an_image_two_applications_run_is_nobodys(self) -> None:
        image = Image(id="sha256:1", tags=("nginx:1.27",), used_by=("shop-web-1", "blog-web-1"))
        both = (
            container(),
            container("blog-web-1", project="blog-example-com", working_dir=BLOG),
        )

        assert index(*both).of_image(image) == NOBODY

    def test_the_image_of_a_database_instance_is_the_engines(self) -> None:
        image = Image(id="sha256:1", tags=("postgres:16",), used_by=("noust-db-pg16",))
        engine = container("noust-db-pg16", project="noust-db-pg16", working_dir="")

        assert index(engine).of_image(image).kind is OwnerKind.DATABASE

    def test_an_image_nothing_runs_and_no_name_proves_is_nobodys(self) -> None:
        assert index().of_image(Image(id="sha256:1", tags=("nginx:1.27",))) == NOBODY
        assert index().of_image(Image(id="sha256:2")) == NOBODY

    def test_the_tag_of_a_release_of_another_application_is_not_this_ones(self) -> None:
        image = Image(id="sha256:1", tags=(f"noust-unknown-example-com:{RELEASE}",))

        assert index().of_image(image) == NOBODY
        # Still a way back: it is protected by what it looks like, whoever's it is.
        assert index().is_way_back(image) is True


class TestAProject:
    def test_a_project_is_its_containers_owners(self) -> None:
        owner = index(container(), container("shop-db-1", service="db")).of_project(
            "shop-example-com", SHOP
        )

        assert owner == SHOP_OWNER

    def test_a_project_whose_containers_are_nobodys_is_nobodys(self) -> None:
        stray = container("x", project="x", working_dir="/srv/x")

        assert index(stray).of_project("x", "/srv/x") == NOBODY

    def test_a_project_with_no_container_is_found_by_its_directory(self) -> None:
        assert index().of_project("shop-example-com", SHOP) == SHOP_OWNER


class TestWhatTheStoreRegistered:
    def test_the_registry_is_the_applications_and_the_instances_of_the_store(
        self, tmp_path: Path
    ) -> None:
        from noust.core.db_instance_store import DbInstanceStore

        NoustStore.reset_instance()
        store = NoustStore(tmp_path / "noust.db")
        try:
            store.create_app(
                App(domain="shop.example.com", app_type="docker-compose", app_path=SHOP)
            )
            store.create_app(
                App(
                    domain="proggest.es",
                    app_type="docker-compose",
                    app_path="/opt/proggest",
                    compose_project="proggest",
                )
            )
            store.create_app(App(domain="node.example.com", app_type="nodejs", app_path="/srv/n"))
            DbInstanceStore(store).create(
                name="pg16",
                flavour="postgresql",
                version="16",
                image="postgres",
                digest="sha256:" + "a" * 64,
                host_port=55432,
                memory_limit_mb=512,
                created_by="root",
            )

            registry = registry_from_store(store)

            assert sorted(registry.apps, key=lambda app: app.domain) == [
                AppRecord("node.example.com", "/srv/n"),
                AppRecord("proggest.es", "/opt/proggest", "proggest"),
                AppRecord("shop.example.com", SHOP),
            ]
            assert registry.engines == ("pg16",)
        finally:
            NoustStore.reset_instance()

    @pytest.mark.parametrize("own", [{}, {"HOSTNAME": "0123456789ab"}])
    def test_the_container_noust_runs_in_is_read_from_where_it_runs(
        self, own: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from noust.core.docker import owners

        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text(
            "1 2 0:1 /var/lib/docker/containers/" + "e" * 64 + "/hostname /etc/hostname rw - ext4\n"
        )
        for key, value in own.items():
            monkeypatch.setenv(key, value)

        found = owners.own_container_ids(mountinfo)

        assert ("e" * 64) in found
        if own:
            assert "0123456789ab" in found
