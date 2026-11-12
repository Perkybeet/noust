# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a node does when its central applies the fleet's settings, and what it refuses.

The real node application, the real fleet token, the real routes. What is pinned: a push signed
with a person's change is a write the node takes, with the confirmation of sudo mode the central
gives for the fleet's settings and for nothing else; the headers have to agree; a node held below
``admin`` takes none of it; the node's audit trail names the person and the change; and what the
node learns of a setting is recorded by the central alone, and read by anybody who may read
settings.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet import policy
from noust.fleet.settings.managed import ManagedSettings
from noust.fleet.settings.signature import SyncSignature
from noust.web.auth import FLEET_ACTOR_HEADER, FLEET_ACTOR_ROLE_HEADER, FLEET_ELEVATED_HEADER
from noust.web.server import create_app, get_token_manager
from tests.support.fleet_node_security import client_from, fleet_headers
from tests.support.web_auth import make_config

SIGNATURE = SyncSignature(person="ana", change="c0123456789ab")


@pytest.fixture
def app(sandbox: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    return create_app(make_config(sandbox))


@pytest.fixture
def fleet_token(app: Any) -> str:
    return str(get_token_manager().create_fleet_token("fleet-nas")["token"])


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def signed(token: str, signature: SyncSignature = SIGNATURE, **extra: str) -> dict[str, str]:
    """The headers of a push of the fleet's settings."""
    return fleet_headers(
        token,
        **{
            FLEET_ACTOR_HEADER: signature.actor,
            FLEET_ELEVATED_HEADER: signature.elevation,
            **extra,
        },
    )


def put_variable(app: Any, headers: dict[str, str], name: str = "REGION", value: str = "eu") -> Any:
    return client_from(app).put(
        f"/api/variables/server/{name}", json={"value": value}, headers=headers
    )


class TestAPushOfTheFleetsSettings:
    def test_a_signed_push_writes_a_setting_with_the_confirmation_the_central_gave(
        self, app: Any, fleet_token: str
    ) -> None:
        response = put_variable(app, signed(fleet_token))

        assert response.status_code == 200, response.text
        names = [v["name"] for v in response.json()["variables"]]
        assert "REGION" in names

    def test_the_same_write_without_the_confirmation_is_refused_as_before(
        self, app: Any, fleet_token: str
    ) -> None:
        headers = fleet_headers(
            fleet_token, **{FLEET_ACTOR_HEADER: "ana", FLEET_ACTOR_ROLE_HEADER: "admin"}
        )

        response = put_variable(app, headers)

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    @pytest.mark.parametrize("elevated", ["1", "change:c000000000000", "", "change:"])
    def test_a_confirmation_that_does_not_name_the_change_confirms_nothing(
        self, app: Any, fleet_token: str, elevated: str
    ) -> None:
        headers = signed(fleet_token)
        headers[FLEET_ELEVATED_HEADER] = elevated

        response = put_variable(app, headers)

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_the_reference_of_a_change_is_worth_nothing_to_a_request_that_is_not_signed(
        self, app: Any, fleet_token: str
    ) -> None:
        headers = fleet_headers(
            fleet_token,
            **{
                FLEET_ACTOR_HEADER: "ana",
                FLEET_ACTOR_ROLE_HEADER: "admin",
                FLEET_ELEVATED_HEADER: SIGNATURE.elevation,
            },
        )

        response = put_variable(app, headers)

        assert response.json()["error"] == "elevation_required"

    def test_a_signature_that_is_not_the_format_is_a_bad_request(
        self, app: Any, fleet_token: str
    ) -> None:
        headers = signed(fleet_token)
        headers[FLEET_ACTOR_HEADER] = "fleet-sync on behalf of ana (change nope)"

        response = put_variable(app, headers)

        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"


POLICY = {
    "updates_check": False,
    "ssl_email": "ops@example.com",
    "backup_max_per_app": 5,
    "delivery_max_minutes": 30,
}


class TestTheServerPolicyIsOneOfThem:
    """The route the policy is written by is admitted as a push, and the rest of ``/api/config`` is not."""

    def put_policy(self, app: Any, headers: dict[str, str], **changes: Any) -> Any:
        return client_from(app).put(
            "/api/config/policy", json={**POLICY, **changes}, headers=headers
        )

    def test_a_signed_push_writes_it_with_the_confirmation_the_central_gave(
        self, app: Any, fleet_token: str
    ) -> None:
        response = self.put_policy(app, signed(fleet_token))

        assert response.status_code == 200, response.text
        read = client_from(app).get("/api/config/policy", headers=signed(fleet_token)).json()
        assert read == {**POLICY, "customized": True}

    def test_the_same_write_without_the_confirmation_is_refused_as_before(
        self, app: Any, fleet_token: str
    ) -> None:
        headers = fleet_headers(
            fleet_token, **{FLEET_ACTOR_HEADER: "ana", FLEET_ACTOR_ROLE_HEADER: "admin"}
        )

        response = self.put_policy(app, headers)

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_a_node_held_below_admin_takes_none_of_it(
        self, app: Any, fleet_token: str, store: NoustStore
    ) -> None:
        policy.set_access(policy.FleetAccess("deploy"), store=store)

        response = self.put_policy(app, signed(fleet_token))

        assert response.status_code == 403
        assert response.json()["error"] == "fleet_sync_ceiling"

    def test_the_central_records_it_and_the_node_notices_what_its_operator_changes(
        self, app: Any, fleet_token: str
    ) -> None:
        assert self.put_policy(app, signed(fleet_token)).status_code == 200
        recorded = client_from(app).put(
            "/api/managed-settings/policy/general", json={"version": 4}, headers=signed(fleet_token)
        )
        assert recorded.status_code == 200, recorded.text
        assert (recorded.json()["resource"], recorded.json()["state"]) == ("policy", "managed")

        # Its own operator changes one of the four, by whatever way: here, the configuration.
        config = Config()
        config.reload()
        config.set("ssl.email", "me@example.com")
        config.write()
        listed = client_from(app).get("/api/managed-settings", headers=signed(fleet_token)).json()

        assert [(i["resource"], i["state"]) for i in listed["items"]] == [("policy", "overridden")]
        assert "policy" in listed["kinds"]

    def test_what_beside_the_four_keys_the_node_changes_is_not_a_change_of_the_policy(
        self, app: Any, fleet_token: str
    ) -> None:
        self.put_policy(app, signed(fleet_token))
        client_from(app).put(
            "/api/managed-settings/policy/general", json={"version": 4}, headers=signed(fleet_token)
        )
        config = Config()
        config.reload()
        config.set("backup.directory", "/srv/backups")
        config.set("deploy.max_concurrent_builds", 4)
        config.write()

        listed = client_from(app).get("/api/managed-settings", headers=signed(fleet_token)).json()

        assert [(i["resource"], i["state"]) for i in listed["items"]] == [("policy", "managed")]

    def test_the_whole_configuration_stays_out_of_a_push_s_reach(
        self, app: Any, fleet_token: str
    ) -> None:
        # The loose routes are still not routes of the fleet's settings.
        for method in ("PUT", "PATCH"):
            response = client_from(app).request(
                method, "/api/config", headers=signed(fleet_token), json={}
            )
            assert response.status_code == 403
            assert response.json()["error"] == "fleet_sync_refused"


class TestOnlyTheFleetsSettings:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("DELETE", "/api/variables/server/REGION"),
            ("POST", "/api/apps/shop.example.com/restart"),
            ("GET", "/api/apps"),
            ("PUT", "/api/config"),
            ("PATCH", "/api/config"),
            ("POST", "/api/databases/databases"),
            ("DELETE", "/api/notifications/channels/ops"),
            ("DELETE", "/api/registries/ghcr"),
        ],
    )
    def test_a_signed_push_to_anything_else_is_refused_whatever_it_claims(
        self, app: Any, fleet_token: str, method: str, path: str
    ) -> None:
        response = client_from(app).request(method, path, headers=signed(fleet_token), json={})

        assert response.status_code == 403
        body = response.json()
        assert body["error"] == "fleet_sync_refused"
        assert body["coded"]["code"] == "fleet_settings.sync_route"

    def test_a_signed_push_may_not_open_a_stream(self, app: Any, fleet_token: str) -> None:
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with client_from(app).websocket_connect(
                "/ws/logs/shop.example.com", headers=signed(fleet_token)
            ):
                pass

    def test_what_a_push_holds_is_what_its_routes_ask_and_not_a_role(
        self, app: Any, fleet_token: str
    ) -> None:
        response = client_from(app).get("/api/auth/session", headers=signed(fleet_token))

        held = set(response.json()["permissions"])
        assert {"settings.manage", "server.manage", "backups.manage", "apps.manage"} <= held
        assert not held & {"apps.deploy", "databases.manage", "security.manage", "accounts.manage"}

    def test_a_push_ignores_the_role_the_central_names(self, app: Any, fleet_token: str) -> None:
        response = client_from(app).get(
            "/api/auth/session", headers=signed(fleet_token, **{FLEET_ACTOR_ROLE_HEADER: "admin"})
        )

        assert "apps.deploy" not in response.json()["permissions"]


class TestTheNodesCeiling:
    @pytest.mark.parametrize("level", ["deploy", "read"])
    def test_a_node_held_below_admin_takes_none_of_it(
        self, app: Any, fleet_token: str, store: NoustStore, level: str
    ) -> None:
        policy.set_access(policy.FleetAccess(level), store=store)  # type: ignore[arg-type]

        response = put_variable(app, signed(fleet_token))

        assert response.status_code == 403
        body = response.json()
        assert body["error"] == "fleet_sync_ceiling"
        assert body["coded"]["code"] == "fleet_settings.sync_ceiling"
        assert "noust fleet access --level admin" in body["coded"]["params"]["command"]

    def test_the_ceiling_is_read_on_every_request(
        self, app: Any, fleet_token: str, store: NoustStore
    ) -> None:
        assert put_variable(app, signed(fleet_token)).status_code == 200
        policy.set_access(policy.FleetAccess("deploy"), store=store)
        assert put_variable(app, signed(fleet_token), "OTHER").status_code == 403
        policy.set_access(policy.FleetAccess("admin"), store=store)
        assert put_variable(app, signed(fleet_token), "OTHER").status_code == 200


class TestTheAuditTrail:
    def test_the_node_names_the_person_and_the_change(
        self, app: Any, fleet_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "noust.web.api.variables.audit_record",
            lambda event, **kw: recorded.append({"event": event, **kw}),
        )

        put_variable(app, signed(fleet_token))

        events = [e for e in recorded if e["event"] == "variables.set"]
        assert events, recorded
        actor = events[0]["actor"]
        assert actor.name == "ana (change c0123456789ab)"
        assert actor.via == "fleet:fleet-nas"


class TestWhatTheNodeSaysItManages:
    def test_anybody_who_may_read_settings_reads_what_is_managed(
        self, app: Any, fleet_token: str
    ) -> None:
        response = client_from(app).get(
            "/api/managed-settings",
            headers=fleet_headers(
                fleet_token, **{FLEET_ACTOR_HEADER: "ana", FLEET_ACTOR_ROLE_HEADER: "viewer"}
            ),
        )

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    def test_the_central_records_a_setting_as_the_fleets_after_writing_it(
        self, app: Any, fleet_token: str
    ) -> None:
        assert put_variable(app, signed(fleet_token)).status_code == 200

        response = client_from(app).put(
            "/api/managed-settings/variable/REGION",
            json={"version": 12},
            headers=signed(fleet_token),
        )

        assert response.status_code == 200, response.text
        row = response.json()
        assert (row["resource"], row["name"], row["state"]) == ("variable", "REGION", "managed")
        assert (row["central"], row["version"], row["applied_by"]) == ("nas", 12, "ana")
        assert row["change"] == "c0123456789ab"
        listed = client_from(app).get("/api/managed-settings", headers=signed(fleet_token))
        assert [i["name"] for i in listed.json()["items"]] == ["REGION"]

    def test_nobody_else_can_record_one(self, app: Any, fleet_token: str) -> None:
        headers = fleet_headers(
            fleet_token, **{FLEET_ACTOR_HEADER: "ana", FLEET_ACTOR_ROLE_HEADER: "admin"}
        )

        response = client_from(app).put(
            "/api/managed-settings/variable/REGION", json={"version": 1}, headers=headers
        )

        assert response.status_code == 403
        assert response.json()["error"] == "fleet_sync_required"

    def test_recording_what_the_node_does_not_hold_is_a_not_found_with_its_sentence(
        self, app: Any, fleet_token: str
    ) -> None:
        response = client_from(app).put(
            "/api/managed-settings/variable/GHOST", json={"version": 1}, headers=signed(fleet_token)
        )

        assert response.status_code == 404
        assert response.json()["coded"]["code"] == "fleet_settings.managed_missing"

    def test_a_kind_this_node_does_not_know_is_a_bad_request(
        self, app: Any, fleet_token: str
    ) -> None:
        response = client_from(app).put(
            "/api/managed-settings/firewall/x", json={"version": 1}, headers=signed(fleet_token)
        )

        assert response.status_code == 400
        assert response.json()["coded"]["code"] == "fleet_settings.unknown_kind"

    def test_a_change_made_on_the_node_shows_the_next_time_it_is_read(
        self, app: Any, fleet_token: str
    ) -> None:
        put_variable(app, signed(fleet_token))
        client_from(app).put(
            "/api/managed-settings/variable/REGION",
            json={"version": 3},
            headers=signed(fleet_token),
        )
        # Its own operator changes it, by whatever route: here, the store.
        from noust.core.variables.model import SERVER
        from noust.core.variables.store import VariableStore

        VariableStore().set(SERVER, 0, "REGION", "us", actor="op")

        listed = client_from(app).get("/api/managed-settings", headers=signed(fleet_token))

        assert [i["state"] for i in listed.json()["items"]] == ["overridden"]

    def test_a_server_that_leaves_the_fleet_keeps_what_was_last_applied_as_its_own(
        self, app: Any, fleet_token: str
    ) -> None:
        # The central removes the node: its token revokes itself on the node.
        put_variable(app, signed(fleet_token))
        client_from(app).put(
            "/api/managed-settings/variable/REGION",
            json={"version": 3},
            headers=signed(fleet_token),
        )
        headers = fleet_headers(fleet_token, **{FLEET_ACTOR_HEADER: "ana"})

        revoked = client_from(app).post("/api/auth/fleet/revoke", headers=headers)

        assert revoked.status_code == 200, revoked.text
        assert ManagedSettings().list() == []
        from noust.core.variables.model import SERVER
        from noust.core.variables.store import VariableStore

        kept = VariableStore().get(SERVER, 0, "REGION")
        assert kept is not None and kept.value == "eu"

    def test_the_central_lets_go_and_the_node_keeps_the_setting(
        self, app: Any, fleet_token: str
    ) -> None:
        put_variable(app, signed(fleet_token))
        client_from(app).put(
            "/api/managed-settings/variable/REGION",
            json={"version": 3},
            headers=signed(fleet_token),
        )

        released = client_from(app).delete(
            "/api/managed-settings/variable/REGION", headers=signed(fleet_token)
        )

        assert released.status_code == 200
        assert released.json() == {"released": True}
        assert ManagedSettings().list() == []
        again = client_from(app).delete(
            "/api/managed-settings/variable/REGION", headers=signed(fleet_token)
        )
        assert again.json() == {"released": False}
