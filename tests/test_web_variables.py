# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Shared variables, secret managers and an application's references over HTTP
(checklist B-43, B-44, B-45, B-46, B-48).

The rules are the stores' and are tested there. Pinned here: who may call each
route and what a caller sees of teams it does not belong to, that a secret is
masked until an administrator in sudo mode asks, that a provider's token never
leaves, and what the application's environment answers about references,
audiences and what Noust sets itself.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.deployers.helpers import app_env as app_env_module
from noust.web.permissions import Permission
from tests.teams_support import World, build_world

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path, service_user="www-data", service_group="www-data"
        ),
    )
    try:
        yield built
    finally:
        NoustStore.reset_instance()


TEAM = "/api/teams/acme/variables"
PROJECT = "/api/teams/acme/projects/default/variables"
STAGE = "/api/teams/acme/projects/default/environments/production/variables"


class TestAccessAndScope:
    def test_a_team_admin_manages_its_own_variables(self, world: World) -> None:
        put = world.client.put(f"{TEAM}/REGION", json={"value": "eu"}, headers=world.as_("alice"))

        assert put.status_code == 200, put.text
        listed = world.client.get(TEAM, headers=world.as_("alice")).json()
        assert [(v["name"], v["value"]) for v in listed["variables"]] == [("REGION", "eu")]
        assert listed["syntax"] == "{{team.NAME}}"

    def test_another_teams_variables_do_not_exist_for_it(self, world: World) -> None:
        world.client.put(
            "/api/teams/globex/variables/X", json={"value": "1"}, headers=world.as_("bob")
        )

        assert (
            world.client.get("/api/teams/globex/variables", headers=world.as_("alice")).status_code
            == 404
        )
        assert (
            world.client.put(
                "/api/teams/globex/variables/X", json={"value": "2"}, headers=world.as_("alice")
            ).status_code
            == 404
        )

    def test_a_viewer_reads_and_cannot_write(self, world: World) -> None:
        world.client.put(f"{TEAM}/REGION", json={"value": "eu"}, headers=world.as_("alice"))

        assert world.client.get(TEAM, headers=world.as_("dana")).status_code == 200
        refused = world.client.put(
            f"{TEAM}/REGION", json={"value": "us"}, headers=world.as_("dana")
        )
        assert refused.status_code == 403

    def test_the_servers_variables_are_not_a_teams(self, world: World) -> None:
        assert (
            world.client.get("/api/variables/server", headers=world.as_("alice")).status_code == 403
        )
        put = world.client.put(
            "/api/variables/server/REGION", json={"value": "eu"}, headers=world.as_("admin")
        )
        assert put.status_code == 200, put.text

    def test_a_project_and_an_environment_have_their_own(self, world: World) -> None:
        a = world.client.put(f"{PROJECT}/NAME", json={"value": "shop"}, headers=world.as_("alice"))
        b = world.client.put(
            f"{STAGE}/NAME", json={"value": "shop-prod"}, headers=world.as_("alice")
        )

        assert a.status_code == 200 and b.status_code == 200
        project = world.client.get(PROJECT, headers=world.as_("alice")).json()
        stage = world.client.get(STAGE, headers=world.as_("alice")).json()
        assert project["variables"][0]["value"] == "shop"
        assert stage["variables"][0]["value"] == "shop-prod"
        assert stage["label"] == "acme/default/production"

    def test_a_bad_name_is_refused(self, world: World) -> None:
        response = world.client.put(f"{TEAM}/1bad", json={"value": "x"}, headers=world.as_("alice"))

        assert response.status_code in (400, 422)

    def test_a_value_with_a_newline_is_refused(self, world: World) -> None:
        response = world.client.put(
            f"{TEAM}/A", json={"value": "x\nUser=root"}, headers=world.as_("alice")
        )

        assert response.status_code in (400, 422)

    def test_changing_variables_is_on_the_audit_trail_without_values(self, world: World) -> None:
        from noust.core.audit.catalog import EVENTS

        assert {"variables.set", "variables.delete", "secrets.provider.save"} <= set(EVENTS)


class TestSecrets:
    def test_a_secret_is_masked_until_an_administrator_asks(self, world: World) -> None:
        world.client.put(
            f"{TEAM}/STRIPE_SECRET_KEY",
            json={"value": "sk_live_abcdefghijklmnop"},
            headers=world.as_("alice"),
        )

        masked = world.client.get(TEAM, headers=world.as_("alice")).json()["variables"][0]
        clear = world.client.get(f"{TEAM}?unmask=true", headers=world.as_("alice")).json()[
            "variables"
        ][0]

        assert masked["secrecy"]["secret"] is True
        assert "sk_live" not in masked["value"]
        assert clear["value"] == "sk_live_abcdefghijklmnop"

    def test_a_viewer_cannot_unmask(self, world: World) -> None:
        world.client.put(
            f"{TEAM}/TOKEN", json={"value": "abcdef123456"}, headers=world.as_("alice")
        )

        assert world.client.get(f"{TEAM}?unmask=true", headers=world.as_("dana")).status_code == 403

    def test_an_operators_mark_wins(self, world: World) -> None:
        world.client.put(
            f"{TEAM}/PUBLIC_NOTE",
            json={"value": "visible", "secret": False},
            headers=world.as_("alice"),
        )
        world.client.put(
            f"{TEAM}/FLAVOUR",
            json={"value": "hidden-value", "secret": True},
            headers=world.as_("alice"),
        )

        by_name = {
            v["name"]: v
            for v in world.client.get(TEAM, headers=world.as_("alice")).json()["variables"]
        }
        assert by_name["PUBLIC_NOTE"]["value"] == "visible"
        assert "hidden-value" not in by_name["FLAVOUR"]["value"]


class TestUsage:
    def test_who_uses_a_variable_is_listed_within_what_the_caller_sees(self, world: World) -> None:
        world.client.put(f"{TEAM}/REGION", json={"value": "eu"}, headers=world.as_("alice"))
        put = world.client.put(
            f"/api/apps/{ACME}/env",
            json={"variables": {"R": "{{team.REGION}}"}},
            headers=world.as_("alice"),
        )
        assert put.status_code == 200, put.text

        usage = world.client.get(f"{TEAM}/REGION/usage", headers=world.as_("alice")).json()

        assert usage["applications"] == [ACME]

    def test_deleting_one_still_in_use_says_who_uses_it(self, world: World) -> None:
        world.client.put(f"{TEAM}/REGION", json={"value": "eu"}, headers=world.as_("alice"))
        world.client.put(
            f"/api/apps/{ACME}/env",
            json={"variables": {"R": "{{team.REGION}}"}},
            headers=world.as_("alice"),
        )

        gone = world.client.delete(f"{TEAM}/REGION", headers=world.as_("alice"))

        assert gone.status_code == 200
        assert gone.json()["applications"] == [ACME]
        assert world.client.delete(f"{TEAM}/REGION", headers=world.as_("alice")).status_code == 404


class TestAnApplicationsEnvironment:
    def test_references_audience_and_what_noust_sets(self, world: World) -> None:
        world.client.put(f"{TEAM}/REGION", json={"value": "eu"}, headers=world.as_("alice"))
        put = world.client.put(
            f"/api/apps/{ACME}/env",
            json={
                "variables": {"R": "{{team.REGION}}", "NPM_TOKEN": "npm_abcdefghijkl", "A": "1"},
                "audience": {"NPM_TOKEN": "build"},
            },
            headers=world.as_("alice"),
        )
        assert put.status_code == 200, put.text

        env = world.client.get(f"/api/apps/{ACME}/env", headers=world.as_("alice")).json()

        assert env["references"] == {"R": "{{team.REGION}}"}
        assert env["audience"] == {"NPM_TOKEN": "build"}
        assert env["variables"]["R"] == "eu"
        assert "npm_abcdefghijkl" not in str(env["variables"])
        assert env["predefined"]["NOUST_DOMAIN"] == ACME
        assert env["predefined"]["NOUST_URL"].endswith(ACME)

    def test_writing_back_what_was_read_keeps_the_references(self, world: World) -> None:
        world.client.put(f"{TEAM}/REGION", json={"value": "eu"}, headers=world.as_("alice"))
        world.client.put(
            f"/api/apps/{ACME}/env",
            json={"variables": {"R": "{{team.REGION}}"}},
            headers=world.as_("alice"),
        )
        read = world.client.get(
            f"/api/apps/{ACME}/env?unmask=true", headers=world.as_("alice")
        ).json()

        world.client.put(
            f"/api/apps/{ACME}/env",
            json={"variables": {**read["variables"], "B": "2"}},
            headers=world.as_("alice"),
        )

        again = world.client.get(f"/api/apps/{ACME}/env", headers=world.as_("alice")).json()
        assert again["references"] == {"R": "{{team.REGION}}"}

    def test_a_reference_that_resolves_to_nothing_is_a_422_that_says_so(self, world: World) -> None:
        response = world.client.put(
            f"/api/apps/{ACME}/env",
            json={"variables": {"R": "{{team.NOPE}}"}},
            headers=world.as_("alice"),
        )

        assert response.status_code == 422
        assert "no variable NOPE" in response.text

    def test_a_name_noust_sets_is_refused(self, world: World) -> None:
        response = world.client.put(
            f"/api/apps/{ACME}/env",
            json={"variables": {"NOUST_URL": "https://elsewhere"}},
            headers=world.as_("alice"),
        )

        assert response.status_code == 422

    def test_resolving_again_reports_what_changed(self, world: World) -> None:
        world.client.put(f"{TEAM}/REGION", json={"value": "eu"}, headers=world.as_("alice"))
        world.client.put(
            f"/api/apps/{ACME}/env",
            json={"variables": {"R": "{{team.REGION}}"}},
            headers=world.as_("alice"),
        )
        world.client.put(f"{TEAM}/REGION", json={"value": "us"}, headers=world.as_("alice"))

        resolved = world.client.post(f"/api/apps/{ACME}/env/resolve", headers=world.as_("alice"))

        assert resolved.status_code == 200, resolved.text
        assert resolved.json() == {"domain": ACME, "changed": ["R"], "restart_required": True}
        again = world.client.post(f"/api/apps/{ACME}/env/resolve", headers=world.as_("alice"))
        assert again.json()["changed"] == []

    def test_a_viewer_cannot_resolve(self, world: World) -> None:
        response = world.client.post(f"/api/apps/{ACME}/env/resolve", headers=world.as_("dana"))

        assert response.status_code == 403


class TestSecretManagers:
    def put(self, world: World, who: str = "admin", **body: Any) -> Any:
        payload = {
            "kind": "doppler",
            "settings": {"project": "shop", "config": "prd"},
            "token": "dp.st.the-token",
            **body,
        }
        return world.client.put(
            "/api/secret-providers/doppler", json=payload, headers=world.as_(who)
        )

    def test_the_token_never_comes_back(self, world: World) -> None:
        put = self.put(world)

        assert put.status_code == 200, put.text
        assert "the-token" not in put.text
        listed = world.client.get("/api/secret-providers", headers=world.as_("admin"))
        assert "the-token" not in listed.text
        assert listed.json()["providers"][0]["has_token"] is True
        assert listed.json()["providers"][0]["reference"] == "{{secret:doppler/PATH}}"

    def test_only_the_server_manages_them(self, world: World) -> None:
        assert self.put(world, who="alice").status_code == 403
        assert (
            world.client.get("/api/secret-providers", headers=world.as_("alice")).status_code == 403
        )

    def test_a_provider_for_a_team_is_stored_with_that_team(self, world: World) -> None:
        put = self.put(world, team="acme")

        assert put.json()["team"] == "acme"

    def test_the_kinds_say_what_each_takes(self, world: World) -> None:
        kinds = world.client.get("/api/secret-providers/kinds", headers=world.as_("admin")).json()

        by_kind = {k["kind"]: k for k in kinds["kinds"]}
        assert set(by_kind) == {"vault", "doppler", "infisical"}
        assert "project" in by_kind["doppler"]["required"]

    def test_removing_one_removes_its_token(self, world: World) -> None:
        self.put(world)

        gone = world.client.delete("/api/secret-providers/doppler", headers=world.as_("admin"))

        assert gone.status_code == 200 and gone.json()["total"] == 0
        assert (
            world.client.delete(
                "/api/secret-providers/doppler", headers=world.as_("admin")
            ).status_code
            == 404
        )

    def test_a_reference_to_one_in_a_variable_is_fetched_when_written(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.variables.providers import ProviderRegistry

        self.put(world)
        monkeypatch.setattr(
            ProviderRegistry, "fetch", lambda self, name, path, *, team_id=None: "fetched-value"
        )

        put = world.client.put(
            f"/api/apps/{ACME}/env",
            json={"variables": {"KEY": "{{secret:doppler/STRIPE}}"}},
            headers=world.as_("admin"),
        )

        assert put.status_code == 200, put.text
        env = world.client.get(
            f"/api/apps/{ACME}/env?unmask=true", headers=world.as_("admin")
        ).json()
        assert env["variables"]["KEY"] == "fetched-value"
        assert (
            env["secrets"]["KEY"]["secret"] is True
        )  # a fetched value is a secret whatever it looks like


def test_the_routes_have_permissions() -> None:
    from noust.web.permissions.registry import permission_for_route

    assert permission_for_route("PUT", "/api/secret-providers/{name}") == Permission.SETTINGS_MANAGE
    assert permission_for_route("GET", "/api/teams/{team}/variables") == Permission.APPS_READ
