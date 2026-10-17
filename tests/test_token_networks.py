# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where an API token is accepted from, and whether the API is on at all (checklist I-33).

A token could always be issued for some networks; it could not be changed afterwards
without issuing another and breaking whatever used the first. It can now, narrowing
without ceremony and widening only in sudo mode, and the command line does the same.
A server whose scripts never use the API can turn it off for tokens while the console,
which signs in with a session, carries on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.web.api.auth import _narrows
from noust.web.auth import CSRF_HEADER_NAME, STATE_DIR_ENV
from noust.web.server import get_token_manager
from tests.test_web_accounts import (  # noqa: F401 - the fixtures of the account tests
    accounts,
    build,
    cheap_hashes,
    make_account,
    read_audit,
    sign_in,
    store,
)


class TestNarrowing:
    @pytest.mark.parametrize(
        ("old", "new", "narrower"),
        [
            (None, ["10.0.0.0/8"], True),
            ([], ["10.0.0.0/8"], True),
            (["10.0.0.0/8"], ["10.1.0.0/16"], True),
            (["10.0.0.0/8"], ["10.1.2.3/32", "10.9.0.0/16"], True),
            (["10.0.0.0/8"], ["10.0.0.0/8"], True),
            (["10.0.0.0/8"], ["11.0.0.0/8"], False),
            (["10.0.0.0/8"], ["10.0.0.0/8", "192.168.0.0/16"], False),
            (["10.0.0.0/16"], ["10.0.0.0/8"], False),
            (["10.0.0.0/8"], [], False),
            (["10.0.0.0/8"], None, False),
            (["2001:db8::/32"], ["10.0.0.0/8"], False),
            (["10.0.0.0/8"], ["2001:db8::/48"], False),
        ],
    )
    def test_only_taking_addresses_away_is_narrowing(
        self, old: list[str] | None, new: list[str] | None, narrower: bool
    ) -> None:
        assert _narrows(old, new) is narrower


class TestOverTheApi:
    @pytest.fixture(autouse=True)
    def ready(self, sandbox: Path) -> None:
        self.sandbox = sandbox
        self.client = build(sandbox, trusted_proxies=[])
        self.codes = make_account("maria", "operator")
        self.other_codes = make_account("olga", "operator")
        login = sign_in(self.client, "maria", self.codes[0])
        self.headers = {CSRF_HEADER_NAME: login["csrf_token"]}

    def token(self, owner: str = "maria", **kwargs: Any) -> dict[str, Any]:
        return get_token_manager().create_api_token(
            f"ci-{owner}", "read", owner=accounts().find(owner), **kwargs
        )

    def patch(self, token_id: int, networks: list[str]) -> Any:
        return self.client.patch(
            f"/api/auth/tokens/{token_id}", json={"allowed_cidrs": networks}, headers=self.headers
        )

    def elevate(self) -> None:
        response = self.client.post(
            "/api/auth/elevate", json={"code": self.codes[1]}, headers=self.headers
        )
        assert response.status_code == 200, response.text

    def test_a_person_restricts_their_own_token_without_sudo_mode(self) -> None:
        issued = self.token()
        bearer = {"Authorization": f"Bearer {issued['token']}"}
        assert self.client.get("/api/auth/verify", headers=bearer).status_code == 200

        changed = self.patch(issued["id"], ["10.1.0.0/16"])

        assert changed.status_code == 200, changed.text
        assert changed.json()["allowed_cidrs"] == ["10.1.0.0/16"]
        refused = self.client.get("/api/auth/verify", headers=bearer)
        assert refused.status_code == 401 and refused.json()["error"] == "token_network"
        inside = TestClient(self.client.app, client=("10.1.2.3", 50000))
        assert inside.get("/api/auth/verify", headers=bearer).status_code == 200

    def test_widening_it_asks_for_sudo_mode(self) -> None:
        issued = self.token(allowed_cidrs=["10.1.0.0/16"])

        asked = self.patch(issued["id"], ["10.0.0.0/8"])
        assert asked.status_code == 403 and asked.json()["error"] == "elevation_required"
        assert get_token_manager().get_api_token(issued["id"])["allowed_cidrs"] == ["10.1.0.0/16"]

        self.elevate()
        assert self.patch(issued["id"], ["10.0.0.0/8"]).status_code == 200

    def test_lifting_the_restriction_asks_for_sudo_mode_too(self) -> None:
        issued = self.token(allowed_cidrs=["10.1.0.0/16"])

        assert self.patch(issued["id"], []).status_code == 403
        self.elevate()
        lifted = self.patch(issued["id"], [])

        assert lifted.status_code == 200 and lifted.json()["allowed_cidrs"] is None

    def test_narrowing_within_what_it_had_needs_nothing(self) -> None:
        issued = self.token(allowed_cidrs=["10.0.0.0/8"])

        assert self.patch(issued["id"], ["10.1.2.0/24"]).status_code == 200

    def test_nobody_changes_another_persons_token(self) -> None:
        theirs = self.token("olga")

        missing = self.patch(theirs["id"], ["10.0.0.0/8"])

        assert missing.status_code == 404
        assert get_token_manager().get_api_token(theirs["id"])["allowed_cidrs"] is None

    def test_a_revoked_token_is_not_there_to_change(self) -> None:
        issued = self.token()
        get_token_manager().revoke_api_token(issued["id"])

        assert self.patch(issued["id"], ["10.0.0.0/8"]).status_code == 404

    def test_a_fleet_token_is_not_restricted_here(self) -> None:
        fleet = get_token_manager().create_fleet_token("fleet-nas")

        refused = self.client.patch(
            f"/api/auth/tokens/{fleet['id']}",
            json={"allowed_cidrs": ["10.0.0.0/8"]},
            headers=self.headers,
        )

        assert refused.status_code in (400, 404)
        assert get_token_manager().get_api_token(fleet["id"])["allowed_cidrs"] is None

    @pytest.mark.parametrize("entry", ["not-a-network", "10.0.0.0/33", "300.1.1.1", ""])
    def test_something_that_is_not_a_network_is_refused(self, entry: str) -> None:
        issued = self.token()

        assert self.patch(issued["id"], [entry]).status_code in (400, 422)
        assert get_token_manager().get_api_token(issued["id"])["allowed_cidrs"] is None

    def test_the_change_is_on_the_record(self) -> None:
        issued = self.token()

        self.patch(issued["id"], ["10.1.0.0/16"])

        events = [e for e in read_audit(self.sandbox) if e["action"] == "auth.token.update"]
        assert events and events[-1]["actor"] == "maria"
        assert "10.1.0.0/16" in json.dumps(events[-1])
        assert "anywhere" in json.dumps(events[-1])


class TestTheApiSwitch:
    @pytest.fixture(autouse=True)
    def ready(self, sandbox: Path, real_config: Config) -> None:
        self.config = real_config
        self.client = build(sandbox, trusted_proxies=[])
        self.codes = make_account("maria", "operator")

    def test_on_by_default(self) -> None:
        issued = get_token_manager().create_api_token("ci", "read", owner=accounts().find("maria"))
        bearer = {"Authorization": f"Bearer {issued['token']}"}

        assert self.client.get("/api/auth/verify", headers=bearer).status_code == 200

    def test_off_a_token_is_refused_and_a_session_is_not(self) -> None:
        issued = get_token_manager().create_api_token("ci", "read", owner=accounts().find("maria"))
        bearer = {"Authorization": f"Bearer {issued['token']}"}
        self.config.set("web.api_enabled", False)

        refused = self.client.get("/api/auth/verify", headers=bearer)
        signed_in = sign_in(self.client, "maria", self.codes[0])

        assert refused.status_code == 403 and refused.json()["error"] == "api_disabled"
        assert signed_in["success"] is True
        assert self.client.get("/api/auth/verify").status_code == 200

    def test_turning_it_back_on_restores_the_token(self) -> None:
        issued = get_token_manager().create_api_token("ci", "read", owner=accounts().find("maria"))
        bearer = {"Authorization": f"Bearer {issued['token']}"}
        self.config.set("web.api_enabled", False)
        assert self.client.get("/api/auth/verify", headers=bearer).status_code == 403

        self.config.set("web.api_enabled", True)

        assert self.client.get("/api/auth/verify", headers=bearer).status_code == 200

    def test_a_centrals_fleet_token_keeps_working(self) -> None:
        fleet = get_token_manager().create_fleet_token("fleet-nas")
        self.config.set("web.api_enabled", False)

        payload = get_token_manager().verify_api_token(fleet["token"], "127.0.0.1")

        assert payload is not None and payload["scope"] == "fleet"

    def test_a_typo_leaves_it_on_instead_of_locking_the_scripts_out(self) -> None:
        issued = get_token_manager().create_api_token("ci", "read", owner=accounts().find("maria"))
        bearer = {"Authorization": f"Bearer {issued['token']}"}
        with pytest.raises(Exception, match="true or false"):
            self.config.set("web.api_enabled", "maybe")

        assert self.client.get("/api/auth/verify", headers=bearer).status_code == 200


class TestOnTheCommandLine:
    @pytest.fixture(autouse=True)
    def ready(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        directory = tmp_path / "state"
        directory.mkdir()
        monkeypatch.setenv(STATE_DIR_ENV, str(directory))
        self.runner = CliRunner()

    def run(self, *args: str) -> Any:
        return self.runner.invoke(root_cli, list(args))

    def listed(self) -> list[dict[str, Any]]:
        return json.loads(self.run("token", "list", "--json").output)["tokens"]

    def test_a_token_is_issued_for_some_networks(self) -> None:
        issued = self.run(
            "token", "create", "ci", "--allow-ip", "203.0.113.7", "--allow-ip", "10.0.0.0/8"
        )

        assert issued.exit_code == 0, issued.output
        assert self.listed()[0]["allowed_cidrs"] == ["203.0.113.7/32", "10.0.0.0/8"]
        assert "203.0.113.7/32, 10.0.0.0/8" in self.run("token", "list").output

    def test_a_token_without_networks_says_anywhere(self) -> None:
        self.run("token", "create", "ci")

        assert "anywhere" in self.run("token", "list").output

    def test_the_networks_of_a_live_token_are_replaced_and_lifted(self) -> None:
        self.run("token", "create", "ci", "--allow-ip", "10.0.0.0/8")

        replaced = self.run("token", "set", "1", "--allow-ip", "192.0.2.0/24")
        assert replaced.exit_code == 0, replaced.output
        assert self.listed()[0]["allowed_cidrs"] == ["192.0.2.0/24"]

        lifted = self.run("token", "set", "1", "--any-ip")
        assert lifted.exit_code == 0 and "anywhere" in lifted.output
        assert self.listed()[0]["allowed_cidrs"] is None

    def test_one_of_the_two_is_asked_for(self) -> None:
        self.run("token", "create", "ci")

        neither = self.run("token", "set", "1")
        both = self.run("token", "set", "1", "--allow-ip", "10.0.0.0/8", "--any-ip")

        assert neither.exit_code == 2 and both.exit_code == 2

    def test_something_that_is_not_a_network_is_refused_and_nothing_changes(self) -> None:
        self.run("token", "create", "ci", "--allow-ip", "10.0.0.0/8")

        refused = self.run("token", "set", "1", "--allow-ip", "not-a-network")

        assert refused.exit_code != 0
        assert "Not an address or a network" in str(refused.exception)
        assert self.listed()[0]["allowed_cidrs"] == ["10.0.0.0/8"]

    def test_a_revoked_or_unknown_token_is_said_so(self) -> None:
        self.run("token", "create", "ci")
        self.run("token", "revoke", "1", "--force")

        revoked = self.run("token", "set", "1", "--any-ip")
        unknown = self.run("token", "set", "99", "--any-ip")

        assert revoked.exit_code != 0 and unknown.exit_code != 0
        assert "No live API token" in revoked.output
