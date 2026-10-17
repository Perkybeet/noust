"""
How a server fits together, read as a graph (checklist M-69): what it holds, how it connects, whose
it is, that nothing in it is a position, and that the API and the permission map agree.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, Database, NoustStore
from noust.core.teams import Scope, ScopedStore, TeamStore
from noust.managers import machine_map
from noust.managers.machine_map import MAX_NODES, MachineMapBuilder


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def make_app(
    store: NoustStore,
    domain: str,
    *,
    paths: list[str] | None = None,
    project_id: int | None = None,
    **extra: Any,
) -> App:
    return store.create_app(
        App(
            domain=domain,
            app_type="nodejs",
            app_path=f"/srv/{domain}",
            persistent_paths=paths or [],
            project_id=project_id,
            **extra,
        )
    )


def builder(
    store: NoustStore,
    scope: Scope | None = None,
    *,
    jobs: list[dict[str, Any]] | None = None,
    states: dict[str, str] | None = None,
    mounts: dict[str, list[str]] | None = None,
) -> MachineMapBuilder:
    return MachineMapBuilder(
        scoped=ScopedStore(scope or Scope.everything(), store),
        cron_jobs=lambda: jobs or [],
        states=lambda apps: {app.domain: (states or {}).get(app.domain, "running") for app in apps},
        mounts=lambda domain: (mounts or {}).get(domain, []),
    )


def ids(result: dict[str, Any], layer: str) -> list[str]:
    return [
        node["id"] for entry in result["layers"] if entry["id"] == layer for node in entry["nodes"]
    ]


def edge_set(result: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {(edge["source"], edge["target"], edge["relation"]) for edge in result["edges"]}


class TestWhatItHolds:
    def test_an_empty_server_is_an_empty_map(self, store: NoustStore) -> None:
        result = builder(store).build().to_dict()
        assert result == {"layers": [], "edges": [], "truncated": False, "projects": 0}

    def test_an_application_with_its_names_its_database_its_job_and_its_data(
        self, store: NoustStore
    ) -> None:
        shop = make_app(store, "shop.example.com", paths=["storage/uploads"])
        store.create_database(Database(app_id=shop.id, name="shop_prod", engine="postgresql"))
        store.add_domain("shop.example.com", "www.shop.example.com", "alias")
        jobs = [
            {
                "name": "shop-nightly",
                "app_domain": "shop.example.com",
                "on_calendar": "*-*-* 02:00:00",
                "enabled": True,
            }
        ]

        result = (
            builder(store, jobs=jobs, mounts={"shop.example.com": ["/data/media"]})
            .build()
            .to_dict()
        )

        assert [layer["id"] for layer in result["layers"]] == [
            "names",
            "applications",
            "databases",
            "scheduled",
            "data",
        ]
        assert ids(result, "names") == ["domain:shop.example.com", "domain:www.shop.example.com"]
        assert ids(result, "applications") == ["app:shop.example.com"]
        assert ids(result, "databases") == ["db:postgresql:shop_prod"]
        assert ids(result, "scheduled") == ["cron:shop-nightly"]
        assert ids(result, "data") == [
            "path:shop.example.com:storage/uploads",
            "path:shop.example.com:/data/media",
        ]
        assert edge_set(result) == {
            ("domain:shop.example.com", "app:shop.example.com", "serves"),
            ("domain:www.shop.example.com", "app:shop.example.com", "serves"),
            ("app:shop.example.com", "db:postgresql:shop_prod", "uses"),
            ("app:shop.example.com", "cron:shop-nightly", "runs"),
            ("app:shop.example.com", "path:shop.example.com:storage/uploads", "keeps"),
            ("app:shop.example.com", "path:shop.example.com:/data/media", "keeps"),
        }

    def test_a_layer_with_nothing_in_it_is_not_a_column(self, store: NoustStore) -> None:
        make_app(store, "plain.example.com")
        assert [layer["id"] for layer in builder(store).build().to_dict()["layers"]] == [
            "names",
            "applications",
        ]

    def test_a_database_no_application_uses_is_still_on_the_map_with_no_connection(
        self, store: NoustStore
    ) -> None:
        store.create_database(Database(name="orphan", engine="mysql"))
        result = builder(store).build().to_dict()
        assert ids(result, "databases") == ["db:mysql:orphan"]
        assert result["edges"] == []

    def test_an_application_says_how_it_is_in_the_apis_words(self, store: NoustStore) -> None:
        make_app(store, "a.example.com")
        make_app(store, "b.example.com")
        make_app(store, "c.example.com")
        make_app(store, "d.example.com")
        result = (
            builder(
                store,
                states={
                    "a.example.com": "running",
                    "b.example.com": "failed",
                    "c.example.com": "stopped",
                    "d.example.com": "running_unmanaged",
                },
            )
            .build()
            .to_dict()
        )
        by_id = {node["id"]: node for layer in result["layers"] for node in layer["nodes"]}
        assert (by_id["app:a.example.com"]["state"], by_id["app:a.example.com"]["state_word"]) == (
            "ok",
            "running",
        )
        assert by_id["app:b.example.com"]["state"] == "fail"
        assert by_id["app:c.example.com"]["state"] == "unknown"
        # A stack running outside its unit is not judged either way by a word this map does not know.
        assert by_id["app:d.example.com"]["state"] is None
        assert by_id["app:d.example.com"]["state_word"] == "running_unmanaged"

    def test_a_disabled_job_is_drawn_as_such(self, store: NoustStore) -> None:
        make_app(store, "shop.example.com")
        jobs = [
            {
                "name": "paused",
                "app_domain": "shop.example.com",
                "on_calendar": "daily",
                "enabled": False,
            }
        ]
        node = next(
            n
            for layer in builder(store, jobs=jobs).build().to_dict()["layers"]
            for n in layer["nodes"]
            if n["id"] == "cron:paused"
        )
        assert (node["state"], node["state_word"]) == ("unknown", "disabled")


class TestThereAreNoPositions:
    def test_the_same_server_is_the_same_map_whatever_the_order_it_was_made_in(
        self, store: NoustStore
    ) -> None:
        for domain in ("zeta.example.com", "alpha.example.com", "mid.example.com"):
            make_app(store, domain, paths=["data"])
        first = builder(store).build().to_dict()
        second = builder(store).build().to_dict()
        assert first == second
        assert ids(first, "applications") == [
            "app:alpha.example.com",
            "app:mid.example.com",
            "app:zeta.example.com",
        ]

    def test_no_node_carries_a_coordinate(self, store: NoustStore) -> None:
        make_app(store, "shop.example.com", paths=["data"])
        for layer in builder(store).build().to_dict()["layers"]:
            for node in layer["nodes"]:
                assert not {"x", "y", "position", "left", "top"} & set(node)


class TestWhoseItIs:
    @pytest.fixture
    def teams(self, store: NoustStore) -> dict[str, Any]:
        members = TeamStore(store)
        found = {}
        for slug in ("acme", "globex"):
            members.create_team(slug.title(), slug=slug)
            target = members.resolve_target(Scope.everything(), team=slug)
            app = make_app(
                store,
                f"{slug}.example.com",
                project_id=target.project_id,
                environment_id=target.environment_id,
            )
            store.create_database(
                Database(
                    app_id=app.id,
                    name=f"{slug}_db",
                    engine="mysql",
                    project_id=target.project_id,
                    environment_id=target.environment_id,
                )
            )
            found[slug] = target
        return found

    def team_scope(self, store: NoustStore, slug: str) -> Scope:
        team = TeamStore(store).require_team(slug)
        return Scope(team_roles={team.id: "viewer"})

    def test_a_team_sees_its_own_applications_and_databases_and_nothing_else(
        self, store: NoustStore, teams: dict[str, Any]
    ) -> None:
        result = builder(store, self.team_scope(store, "acme")).build().to_dict()
        assert ids(result, "applications") == ["app:acme.example.com"]
        assert ids(result, "databases") == ["db:mysql:acme_db"]
        assert "globex" not in str(result)

    def test_a_job_of_an_application_it_cannot_see_is_not_drawn_and_a_server_wide_one_is_only_for_the_instance(
        self, store: NoustStore, teams: dict[str, Any]
    ) -> None:
        jobs = [
            {"name": "acme-job", "app_domain": "acme.example.com", "enabled": True},
            {"name": "globex-job", "app_domain": "globex.example.com", "enabled": True},
            {"name": "rotate-logs", "app_domain": None, "enabled": True},
        ]
        acme = builder(store, self.team_scope(store, "acme"), jobs=jobs).build().to_dict()
        everything = builder(store, jobs=jobs).build().to_dict()
        assert ids(acme, "scheduled") == ["cron:acme-job"]
        assert ids(everything, "scheduled") == [
            "cron:acme-job",
            "cron:globex-job",
            "cron:rotate-logs",
        ]

    def test_projects_are_told_apart_only_when_there_is_more_than_one(
        self, store: NoustStore, teams: dict[str, Any]
    ) -> None:
        names = {teams["acme"].project_id: "acme-site", teams["globex"].project_id: "globex-site"}
        both = (
            MachineMapBuilder(
                scoped=ScopedStore(Scope.everything(), store),
                states=lambda apps: {},
                project_names=lambda project_id: names.get(project_id),
            )
            .build()
            .to_dict()
        )
        one = (
            MachineMapBuilder(
                scoped=ScopedStore(self.team_scope(store, "acme"), store),
                states=lambda apps: {},
                project_names=lambda project_id: names.get(project_id),
            )
            .build()
            .to_dict()
        )
        groups = {node["id"]: node["group"] for layer in both["layers"] for node in layer["nodes"]}
        assert groups["app:acme.example.com"] == "acme-site"
        assert groups["db:mysql:globex_db"] == "globex-site"
        assert both["projects"] == 2
        assert {node["group"] for layer in one["layers"] for node in layer["nodes"]} == {None}
        assert one["projects"] == 1


class TestItIsBounded:
    def test_past_the_ceiling_the_rest_is_left_out_and_the_map_says_so(
        self, store: NoustStore
    ) -> None:
        for number in range(MAX_NODES):
            make_app(store, f"app{number:04d}.example.com")
        result = builder(store).build().to_dict()
        assert result["truncated"] is True
        assert sum(len(layer["nodes"]) for layer in result["layers"]) == MAX_NODES
        present = {node["id"] for layer in result["layers"] for node in layer["nodes"]}
        assert all(
            edge["source"] in present and edge["target"] in present for edge in result["edges"]
        )


class TestTheRoute:
    @pytest.fixture
    def client(self, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        from noust.web.api import map as map_api
        from noust.web.api.auth import get_current_session

        make_app(store, "shop.example.com")
        monkeypatch.setattr(map_api, "default_builder", lambda: builder(store))
        app = FastAPI()
        app.include_router(map_api.router, prefix="/api/map")
        app.dependency_overrides[get_current_session] = lambda: {"type": "session"}
        return TestClient(app)

    def test_it_answers_the_map(self, client: TestClient) -> None:
        body = client.get("/api/map").json()
        assert ids(body, "applications") == ["app:shop.example.com"]
        assert body["truncated"] is False

    def test_its_permission_is_the_one_that_reads_applications(self) -> None:
        from noust.web.permissions.routes_map import ROUTES

        assert ROUTES[("GET", "/api/map")] == "apps.read"

    def test_the_default_builder_reads_this_server(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_app(store, "shop.example.com", paths=["storage"])
        monkeypatch.setattr("noust.managers.cron_manager.CronManager.list_jobs", lambda self: [])
        monkeypatch.setattr(
            "noust.core.app_state.resolve_states_with_status",
            lambda apps, services, probe=True: {
                app.domain: (type("S", (), {"label": "Running"})(), {}) for app in apps
            },
        )
        result = machine_map.default_builder().build().to_dict()
        node = next(
            n
            for layer in result["layers"]
            for n in layer["nodes"]
            if n["id"] == "app:shop.example.com"
        )
        assert (node["state"], node["state_word"]) == ("ok", "running")
        assert ids(result, "data") == ["path:shop.example.com:storage"]


class TestTheCommand:
    def run(
        self,
        store: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
        *args: str,
        jobs: list[dict[str, Any]] | None = None,
    ):
        from click.testing import CliRunner

        from noust.cli.app import cli as root_cli

        monkeypatch.setattr(
            "noust.cli.commands.map.default_builder", lambda: builder(store, jobs=jobs)
        )
        return CliRunner().invoke(root_cli, ["map", *args])

    def test_says_nothing_is_deployed_on_an_empty_server(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self.run(store, monkeypatch)
        assert result.exit_code == 0, result.output
        assert "Nothing is deployed" in result.output

    def test_prints_each_application_with_what_it_is_tied_to(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shop = make_app(store, "shop.example.com", paths=["storage"])
        store.create_database(Database(app_id=shop.id, name="shop_prod", engine="postgresql"))
        store.create_database(Database(name="loose", engine="mysql"))
        store.add_domain("shop.example.com", "www.shop.example.com", "alias")
        jobs = [
            {
                "name": "nightly",
                "app_domain": "shop.example.com",
                "on_calendar": "daily",
                "enabled": True,
            }
        ]

        result = self.run(store, monkeypatch, jobs=jobs)

        assert result.exit_code == 0, result.output
        assert "shop.example.com  running  (nodejs)" in result.output
        assert "names: shop.example.com, www.shop.example.com" in result.output
        assert "databases: shop_prod" in result.output
        assert "scheduled: nightly" in result.output
        assert "data: storage" in result.output
        assert "Not tied to an application:" in result.output
        assert "database: loose (mysql)" in result.output

    def test_prints_the_graph_the_api_answers_as_json(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json

        make_app(store, "shop.example.com")
        result = self.run(store, monkeypatch, "--json")
        assert ids(json.loads(result.output), "applications") == ["app:shop.example.com"]
