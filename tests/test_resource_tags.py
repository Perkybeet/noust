# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tags on databases and on systemd services (checklist M-07), below the API.

The rules of what a tag is are the applications' (``test_app_tags``); what is pinned here is what
is attached to: a database by its engine and name, a service by its unit, kept apart by kind; a
name nobody typed safely is refused; and a tag goes when the thing it labels goes, in the store,
so that every way of removing a database or a unit forgets it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.resource_tags import (
    KIND_DATABASE,
    KIND_SERVICE,
    ResourceTags,
    database_key,
)
from noust.core.store import NoustStore, Service
from noust.managers.database.service import DatabaseService
from tests.test_web_databases_api import make_engine, wire


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


class TestWhatIsTagged:
    def test_a_database_is_named_by_its_engine_and_its_name(self) -> None:
        assert database_key("postgresql", "shop") == "postgresql/shop"
        assert database_key("postgresql@pg16", "shop") == "postgresql@pg16/shop"

    def test_set_add_remove(self, store: NoustStore) -> None:
        tags = ResourceTags(store)

        assert tags.set(KIND_SERVICE, "worker.service", ["Prod", "eu"]) == ["eu", "prod"]
        assert tags.add(KIND_SERVICE, "worker.service", ["nightly"]) == ["eu", "nightly", "prod"]
        assert tags.remove(KIND_SERVICE, "worker.service", ["eu", "never-had"]) == [
            "nightly",
            "prod",
        ]
        assert tags.of(KIND_SERVICE, "worker.service") == ["nightly", "prod"]
        assert tags.of(KIND_SERVICE, "other.service") == []

    def test_a_service_and_a_database_with_the_same_name_do_not_share_tags(
        self, store: NoustStore
    ) -> None:
        tags = ResourceTags(store)

        tags.set(KIND_SERVICE, "shop", ["prod"])

        assert tags.of(KIND_DATABASE, "shop") == []
        assert tags.by_resource(KIND_DATABASE) == {}
        assert tags.with_tag(KIND_DATABASE, "prod") == []
        assert tags.with_tag(KIND_SERVICE, "prod") == ["shop"]

    def test_the_resources_with_a_tag_and_every_tagged_resource(self, store: NoustStore) -> None:
        tags = ResourceTags(store)
        tags.set(KIND_DATABASE, "postgresql/shop", ["prod", "customer-a"])
        tags.set(KIND_DATABASE, "mysql/blog", ["prod"])
        tags.set(KIND_DATABASE, "redis/0", ["cache"])

        assert tags.with_tag(KIND_DATABASE, " PROD ") == ["mysql/blog", "postgresql/shop"]
        assert tags.by_resource(KIND_DATABASE) == {
            "mysql/blog": ["prod"],
            "postgresql/shop": ["customer-a", "prod"],
            "redis/0": ["cache"],
        }

    def test_a_resource_carries_a_limited_number_of_tags(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError, match="16"):
            ResourceTags(store).set(KIND_SERVICE, "w.service", [f"t{i}" for i in range(17)])

    def test_setting_replaces_and_forgetting_empties(self, store: NoustStore) -> None:
        tags = ResourceTags(store)
        tags.set(KIND_SERVICE, "w.service", ["a", "b"])

        assert tags.set(KIND_SERVICE, "w.service", ["c"]) == ["c"]
        assert tags.forget(KIND_SERVICE, "w.service") == 1
        assert tags.of(KIND_SERVICE, "w.service") == []
        assert tags.forget(KIND_SERVICE, "w.service") == 0


class TestWhatIsRefused:
    @pytest.mark.parametrize(
        "name",
        [
            "",
            "a b",
            "x\n",
            "worker.service\n",
            "a/b/c",
            "/a",
            "a/",
            "a;b",
            "a" * 201,
            "ñ",
            "a\x00b",
            "a'b",
        ],
    )
    def test_a_name_that_is_not_one_is_refused_everywhere(
        self, store: NoustStore, name: str
    ) -> None:
        tags = ResourceTags(store)

        for call in (
            lambda: tags.of(KIND_SERVICE, name),
            lambda: tags.set(KIND_SERVICE, name, ["prod"]),
            lambda: tags.forget(KIND_SERVICE, name),
        ):
            with pytest.raises(ValidationError):
                call()

    def test_a_kind_that_is_not_one_is_refused(self, store: NoustStore) -> None:
        tags = ResourceTags(store)

        with pytest.raises(ValidationError, match="database and service"):
            tags.set("application", "shop.example.com", ["prod"])
        with pytest.raises(ValidationError):
            tags.by_resource("nope")
        with pytest.raises(ValidationError):
            tags.with_tag("nope", "prod")

    def test_a_bad_tag_is_refused_and_changes_nothing(self, store: NoustStore) -> None:
        tags = ResourceTags(store)
        tags.set(KIND_SERVICE, "w.service", ["prod"])

        with pytest.raises(ValidationError):
            tags.set(KIND_SERVICE, "w.service", ["fine", "not fine"])

        assert tags.of(KIND_SERVICE, "w.service") == ["prod"]


class TestTagsGoWithWhatTheyLabel:
    def test_deleting_a_service_forgets_its_tags(self, store: NoustStore) -> None:
        tags = ResourceTags(store)
        store.create_service(Service(name="worker", command="node w.js"))
        tags.set(KIND_SERVICE, "worker", ["prod"])
        tags.set(KIND_SERVICE, "other", ["prod"])

        assert store.delete_service("worker") is True

        assert tags.of(KIND_SERVICE, "worker") == []
        assert tags.of(KIND_SERVICE, "other") == ["prod"]

    def test_deleting_a_database_record_forgets_its_tags_even_without_a_record(
        self, store: NoustStore
    ) -> None:
        tags = ResourceTags(store)
        tags.set(KIND_DATABASE, "postgresql/shop", ["prod"])
        tags.set(KIND_DATABASE, "postgresql/blog", ["prod"])

        # An untracked database has no row; dropping it still ends its labels.
        assert store.delete_database("shop", "postgresql") is False

        assert tags.of(KIND_DATABASE, "postgresql/shop") == []
        assert tags.of(KIND_DATABASE, "postgresql/blog") == ["prod"]

    def test_dropping_or_forgetting_a_database_through_the_service_does(
        self, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = make_engine(tmp_path / "pg", seeded=False)
        engine.dbs = {"shop": {"owner": None, "size": None}, "blog": {"owner": None, "size": None}}
        wire(monkeypatch, [engine])
        tags = ResourceTags(store)
        tags.set(KIND_DATABASE, "postgresql/shop", ["prod"])
        tags.set(KIND_DATABASE, "postgresql/blog", ["prod"])
        service = DatabaseService()

        service.drop("postgresql", "shop", keep_backup=False)
        engine.dbs.pop("blog")
        service.forget("postgresql", "blog")

        assert tags.by_resource(KIND_DATABASE) == {}

    def test_the_views_of_a_database_carry_its_tags(
        self, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any
    ) -> None:
        engine = make_engine(tmp_path / "pg", seeded=False)
        engine.dbs = {"shop": {"owner": None, "size": None}, "blog": {"owner": None, "size": None}}
        wire(monkeypatch, [engine])
        ResourceTags(store).set(KIND_DATABASE, "postgresql/shop", ["prod", "eu"])
        service = DatabaseService()

        listed = {view.name: view.tags for view in service.list_databases()}

        assert listed == {"shop": ["eu", "prod"], "blog": []}
        assert service.get("postgresql", "shop").tags == ["eu", "prod"]
