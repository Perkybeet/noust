# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/scim/v2`` over HTTP, and ``/api/auth/scim`` that configures it (checklist I-21).

What matters over HTTP is that the provider is authenticated by its own token and nothing
else, that its answers are the protocol's, and that retiring a person at the provider ends
the access they already hold: their session and their API tokens, not only their next
sign-in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.accounts.scim import SCHEMA_GROUP, SCHEMA_PATCH, SCHEMA_USER, ScimManager
from noust.web.auth import CSRF_HEADER_NAME
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

SCIM = "/api/scim/v2"
JSON = {"Content-Type": "application/scim+json"}


def manager() -> ScimManager:
    tokens = get_token_manager()
    return ScimManager(tokens.accounts.store, accounts=tokens.accounts)


def user_doc(name: str = "maria@acme.test", **extra: Any) -> dict[str, Any]:
    return {
        "schemas": [SCHEMA_USER],
        "userName": name,
        "externalId": f"ext-{name}",
        "displayName": "Maria Lopez",
        "emails": [{"value": name, "primary": True, "type": "work"}],
        "active": True,
        **extra,
    }


def patch_doc(*operations: dict[str, Any]) -> dict[str, Any]:
    return {"schemas": [SCHEMA_PATCH], "Operations": list(operations)}


class TestTheProviderIsAuthenticatedByItsOwnToken:
    @pytest.fixture(autouse=True)
    def ready(self, sandbox: Path) -> None:
        self.sandbox = sandbox
        self.client = build(sandbox, trusted_proxies=[])
        make_account("root-admin", "admin")
        _, self.token = manager().configure("entra", {"label": "Entra ID"})
        assert self.token is not None
        self.bearer = {"Authorization": f"Bearer {self.token}"}

    def test_a_provider_that_makes_many_calls_is_never_locked_out_for_it(self) -> None:
        # A real synchronisation is hundreds of calls, some of them answered 404 or 405. None of
        # them is a wrong credential: the lockout counts guesses, and this is a valid token.
        for _ in range(3):
            assert (
                self.client.get(f"{SCIM}/ServiceProviderConfig", headers=self.bearer).status_code
                == 200
            )
            assert (
                self.client.post(f"{SCIM}/ServiceProviderConfig", headers=self.bearer).status_code
                == 405
            )
            assert self.client.get(f"{SCIM}/Users/missing", headers=self.bearer).status_code == 404
            assert self.client.delete(f"{SCIM}/Nowhere", headers=self.bearer).status_code in (
                404,
                405,
            )
        for _ in range(20):
            assert self.client.get(f"{SCIM}/Users", headers=self.bearer).status_code == 200

        from noust.web.server import get_brute_force

        assert not get_brute_force().is_locked("testclient")

    def test_a_valid_token_is_counted_in_the_budget_of_the_authenticated(self) -> None:
        anonymous = self.client.get(f"{SCIM}/Users")
        valid = self.client.get(f"{SCIM}/Users", headers=self.bearer)

        from noust.web.server import get_authenticated_rate_limiter, get_rate_limiter

        assert anonymous.status_code == 401 and valid.status_code == 200
        assert int(anonymous.headers["x-ratelimit-limit"]) == get_rate_limiter().max_requests
        assert (
            int(valid.headers["x-ratelimit-limit"]) == get_authenticated_rate_limiter().max_requests
        )

    def test_guessing_tokens_still_locks_the_address_out(self) -> None:
        for number in range(12):
            self.client.get(
                f"{SCIM}/Users", headers={"Authorization": f"Bearer noust_scim_guess{number}"}
            )

        refused = self.client.get(f"{SCIM}/Users", headers=self.bearer)

        assert refused.status_code == 429 and refused.json()["error"] == "locked_out"
        from noust.web.server import get_brute_force

        get_brute_force().record_success("testclient")

    def test_the_token_of_a_source_reads_what_it_supports(self) -> None:
        response = self.client.get(f"{SCIM}/ServiceProviderConfig", headers=self.bearer)

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/scim+json")
        assert response.json()["patch"]["supported"] is True
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": "Bearer nope"},
            {"Authorization": "Bearer noust_scim_" + "a" * 43},
            {"Authorization": "Basic Zm9vOmJhcg=="},
        ],
    )
    def test_anything_else_is_a_401_that_says_nothing_about_why(
        self, headers: dict[str, str]
    ) -> None:
        response = self.client.get(f"{SCIM}/Users", headers=headers)

        assert response.status_code == 401
        assert "Bearer" in response.headers["www-authenticate"]
        assert "token" not in response.text.lower()

    def test_a_refused_token_is_on_the_record_and_counted_against_the_address(self) -> None:
        self.client.get(f"{SCIM}/Users", headers={"Authorization": "Bearer noust_scim_guess"})

        actions = [e["action"] for e in read_audit(self.sandbox)]
        assert "auth.scim.denied" in actions and "auth.credential" in actions

    def test_the_token_of_a_source_that_is_switched_off_is_refused(self) -> None:
        manager().configure("entra", {"enabled": False})

        assert self.client.get(f"{SCIM}/Users", headers=self.bearer).status_code == 401
        denied = [e for e in read_audit(self.sandbox) if e["action"] == "auth.scim.denied"]
        assert "switched off" in json.dumps(denied[-1])

    def test_an_api_token_is_not_a_scim_token(self) -> None:
        tokens = get_token_manager()
        issued = tokens.create_api_token("ci", "admin", owner=tokens.accounts.find("root-admin"))

        response = self.client.get(
            f"{SCIM}/Users", headers={"Authorization": f"Bearer {issued['token']}"}
        )

        assert response.status_code == 401

    def test_a_signed_in_administrator_is_not_a_provider(self) -> None:
        codes = make_account("sofia", "security")
        sign_in(self.client, "sofia", codes[0])

        assert self.client.get(f"{SCIM}/Users").status_code == 401

    def test_the_scim_token_opens_nothing_else(self) -> None:
        for path in ("/api/apps", "/api/auth/accounts", "/api/auth/tokens", "/api/auth/verify"):
            assert self.client.get(path, headers=self.bearer).status_code == 401, path

    def test_a_token_last_used_is_recorded(self) -> None:
        self.client.get(f"{SCIM}/Users", headers=self.bearer)

        source = manager().require_source("entra")
        assert source.last_used_at is not None and source.last_used_ip == "testclient"


class TestAnEntraSynchronisation:
    @pytest.fixture(autouse=True)
    def ready(self, sandbox: Path) -> None:
        self.sandbox = sandbox
        self.client = build(sandbox, trusted_proxies=[])
        make_account("root-admin", "admin")
        _, token = manager().configure(
            "entra",
            {
                "group_sync": True,
                "group_mappings": [{"group": "noust-ops", "role": "operator"}],
            },
        )
        self.headers = {"Authorization": f"Bearer {token}", **JSON}

    def post(self, path: str, body: dict[str, Any]) -> Any:
        return self.client.post(f"{SCIM}{path}", content=json.dumps(body), headers=self.headers)

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        return self.client.patch(f"{SCIM}{path}", content=json.dumps(body), headers=self.headers)

    def test_a_user_is_created_and_found_the_way_entra_does_it(self) -> None:
        created = self.post("/Users", user_doc())

        assert created.status_code == 201, created.text
        assert created.headers["content-type"].startswith("application/scim+json")
        body = created.json()
        assert created.headers["location"].endswith(f"{SCIM}/Users/{body['id']}")
        assert body["schemas"] == [SCHEMA_USER] and body["active"] is True
        found = self.client.get(
            f"{SCIM}/Users",
            params={"filter": 'userName eq "maria@acme.test"'},
            headers=self.headers,
        ).json()
        assert found["totalResults"] == 1 and found["Resources"][0]["id"] == body["id"]
        assert (
            self.client.get(f"{SCIM}/Users/{body['id']}", headers=self.headers).status_code == 200
        )

    def test_errors_are_the_protocols_own(self) -> None:
        self.post("/Users", user_doc())

        duplicate = self.post("/Users", user_doc())
        missing = self.client.get(f"{SCIM}/Users/nope", headers=self.headers)
        bad_filter = self.client.get(
            f"{SCIM}/Users", params={"filter": 'userName co "m"'}, headers=self.headers
        )

        assert duplicate.status_code == 409 and duplicate.json()["scimType"] == "uniqueness"
        assert duplicate.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
        assert missing.status_code == 404 and missing.json()["status"] == "404"
        assert bad_filter.status_code == 400 and bad_filter.json()["scimType"] == "invalidFilter"

    def test_deactivating_a_person_ends_the_session_and_the_token_they_already_hold(self) -> None:
        user_id = self.post("/Users", user_doc()).json()["id"]
        tokens = get_token_manager()
        maria = tokens.accounts.find("maria")
        assert maria is not None
        # The person signs in (through their provider) and holds a session and an API token.
        tokens.accounts.activate_external(maria.id)
        tokens.accounts.update_profile("maria", display_name="Maria")
        issued = tokens.create_api_token("ci", "read", owner=tokens.accounts.find("maria"))
        bearer = {"Authorization": f"Bearer {issued['token']}"}
        assert self.client.get("/api/auth/verify", headers=bearer).status_code == 200

        deactivated = self.patch(
            f"/Users/{user_id}", patch_doc({"op": "Replace", "path": "active", "value": "False"})
        )

        assert deactivated.status_code == 200 and deactivated.json()["active"] is False
        refused = self.client.get("/api/auth/verify", headers=bearer)
        assert refused.status_code == 401
        record = tokens.get_api_token(issued["id"])
        assert record is not None and record["revoked_at"] is not None

    def test_turning_the_person_back_on_does_not_resurrect_the_token(self) -> None:
        user_id = self.post("/Users", user_doc()).json()["id"]
        tokens = get_token_manager()
        maria = tokens.accounts.find("maria")
        assert maria is not None
        tokens.accounts.activate_external(maria.id)
        issued = tokens.create_api_token("ci", "read", owner=tokens.accounts.find("maria"))
        bearer = {"Authorization": f"Bearer {issued['token']}"}
        self.patch(
            f"/Users/{user_id}", patch_doc({"op": "replace", "path": "active", "value": False})
        )

        self.patch(
            f"/Users/{user_id}", patch_doc({"op": "replace", "path": "active", "value": True})
        )

        assert self.client.get("/api/auth/verify", headers=bearer).status_code == 401

    def test_delete_is_a_204_and_the_user_is_gone(self) -> None:
        user_id = self.post("/Users", user_doc()).json()["id"]

        deleted = self.client.delete(f"{SCIM}/Users/{user_id}", headers=self.headers)

        assert deleted.status_code == 204 and deleted.content == b""
        assert self.client.get(f"{SCIM}/Users/{user_id}", headers=self.headers).status_code == 404
        assert get_token_manager().accounts.require("maria").status == "disabled"

    def test_a_group_gives_its_role_and_leaving_it_takes_it_back(self) -> None:
        user_id = self.post("/Users", user_doc()).json()["id"]
        group = self.post("/Groups", {"displayName": "noust-ops", "members": [{"value": user_id}]})
        assert group.status_code == 201, group.text
        assert get_token_manager().accounts.require("maria").role == "operator"

        removed = self.patch(
            f"/Groups/{group.json()['id']}",
            patch_doc({"op": "Remove", "path": "members", "value": [{"value": user_id}]}),
        )

        assert removed.status_code == 200 and removed.json()["members"] == []
        assert get_token_manager().accounts.require("maria").role == "viewer"

    def test_a_source_sees_only_its_own(self) -> None:
        user_id = self.post("/Users", user_doc()).json()["id"]
        _, other = manager().configure("okta", {})
        theirs = {"Authorization": f"Bearer {other}", **JSON}

        assert self.client.get(f"{SCIM}/Users/{user_id}", headers=theirs).status_code == 404
        assert self.client.get(f"{SCIM}/Users", headers=theirs).json()["totalResults"] == 0

    def test_a_body_that_is_not_json_is_a_client_error(self) -> None:
        response = self.client.post(f"{SCIM}/Users", content="<xml/>", headers=self.headers)

        assert 400 <= response.status_code < 500

    def test_everything_a_provider_does_is_on_the_record_as_the_source(self) -> None:
        user_id = self.post("/Users", user_doc()).json()["id"]
        self.patch(
            f"/Users/{user_id}", patch_doc({"op": "replace", "path": "active", "value": False})
        )

        entries = read_audit(self.sandbox)
        actions = {e["action"]: e for e in entries if e["action"].startswith("auth.scim.")}
        assert {"auth.scim.provision", "auth.scim.deprovision"} <= set(actions)
        assert actions["auth.scim.deprovision"]["actor"].endswith("scim:entra")


class TestWhatAClientLibraryExpects:
    """
    Found by running a SCIM conformance client written by other people against the server
    (tests/integration/identity.py): each of these was wrong before.
    """

    @pytest.fixture(autouse=True)
    def ready(self, sandbox: Path) -> None:
        self.client = build(sandbox, trusted_proxies=[])
        make_account("root-admin", "admin")
        _, token = manager().configure("entra", {})
        self.headers = {"Authorization": f"Bearer {token}", **JSON}

    def made(self) -> dict[str, Any]:
        created = self.client.post(
            f"{SCIM}/Users", content=json.dumps(user_doc()), headers=self.headers
        )
        assert created.status_code == 201, created.text
        return dict(created.json())

    def test_resource_types_and_schemas_are_list_responses(self) -> None:
        for path, expected in (("/ResourceTypes", {"User", "Group"}), ("/Schemas", None)):
            answer = self.client.get(f"{SCIM}{path}", headers=self.headers).json()

            assert answer["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]
            assert answer["totalResults"] == len(answer["Resources"]) == 2
            if expected:
                assert {item["id"] for item in answer["Resources"]} == expected
        user = next(
            item
            for item in self.client.get(f"{SCIM}/Schemas", headers=self.headers).json()["Resources"]
            if item["id"] == SCHEMA_USER
        )
        name = next(
            attribute for attribute in user["attributes"] if attribute["name"] == "userName"
        )
        assert name["required"] is True and name["uniqueness"] == "server"
        assert user["meta"]["location"].endswith(f"{SCIM}/Schemas/{SCHEMA_USER}")

    def test_one_resource_type_or_schema_is_found_by_its_id(self) -> None:
        one = self.client.get(f"{SCIM}/ResourceTypes/User", headers=self.headers)
        schema = self.client.get(f"{SCIM}/Schemas/{SCHEMA_USER}", headers=self.headers)
        missing = self.client.get(f"{SCIM}/ResourceTypes/Nobody", headers=self.headers)

        assert one.status_code == 200 and one.json()["endpoint"] == "/Users"
        assert schema.status_code == 200 and schema.json()["name"] == "User"
        assert missing.status_code == 404 and missing.json()["status"] == "404"

    def test_a_path_or_a_method_the_server_does_not_have_answers_in_the_protocols_error(
        self,
    ) -> None:
        nothing = self.client.get(f"{SCIM}/Nowhere", headers=self.headers)
        wrong_method = self.client.delete(f"{SCIM}/ServiceProviderConfig", headers=self.headers)

        for answer, status in ((nothing, 404), (wrong_method, 405)):
            assert answer.status_code == status
            assert answer.headers["content-type"].startswith("application/scim+json")
            assert answer.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
            assert answer.json()["status"] == str(status)

    def test_excluded_attributes_are_left_out_and_the_identifier_never(self) -> None:
        user = self.made()

        plain = self.client.get(f"{SCIM}/Users/{user['id']}", headers=self.headers).json()
        shaped = self.client.get(
            f"{SCIM}/Users/{user['id']}",
            params={"excludedAttributes": "name,displayName,meta,id"},
            headers=self.headers,
        ).json()

        assert {"name", "displayName", "meta"} <= set(plain)
        assert not ({"name", "displayName", "meta"} & set(shaped))
        assert shaped["id"] == user["id"] and shaped["userName"] == "maria@acme.test"

    def test_attributes_name_what_comes_back_besides_the_identifier(self) -> None:
        user = self.made()

        shaped = self.client.get(
            f"{SCIM}/Users/{user['id']}",
            params={"attributes": "userName,emails.value"},
            headers=self.headers,
        ).json()

        assert set(shaped) == {"schemas", "id", "userName", "emails"}
        assert shaped["emails"] == [{"value": "maria@acme.test"}]

    def test_the_projection_reaches_a_list_and_a_created_user(self) -> None:
        created = self.client.post(
            f"{SCIM}/Users",
            params={"attributes": "userName"},
            content=json.dumps(user_doc("olga@acme.test")),
            headers=self.headers,
        ).json()
        listed = self.client.get(
            f"{SCIM}/Users", params={"excludedAttributes": "emails"}, headers=self.headers
        ).json()

        assert set(created) == {"schemas", "id", "userName"}
        assert all("emails" not in item for item in listed["Resources"])

    def test_a_query_can_be_posted_in_a_body(self) -> None:
        user = self.made()
        self.client.post(
            f"{SCIM}/Users", content=json.dumps(user_doc("olga@acme.test")), headers=self.headers
        )

        found = self.client.post(
            f"{SCIM}/Users/.search",
            content=json.dumps(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:SearchRequest"],
                    "filter": 'userName eq "maria@acme.test"',
                    "attributes": ["userName"],
                }
            ),
            headers=self.headers,
        )

        assert found.status_code == 200, found.text
        body = found.json()
        assert body["totalResults"] == 1
        assert body["Resources"][0]["id"] == user["id"]
        assert set(body["Resources"][0]) == {"schemas", "id", "userName"}

    def test_a_search_over_everything_answers_users_and_groups(self) -> None:
        self.made()
        self.client.post(
            f"{SCIM}/Groups",
            content=json.dumps({"schemas": [SCHEMA_GROUP], "displayName": "noust-ops"}),
            headers=self.headers,
        )

        both = self.client.post(f"{SCIM}/.search", content=json.dumps({}), headers=self.headers)
        users_only = self.client.post(
            f"{SCIM}/.search",
            content=json.dumps({"filter": 'userName eq "maria@acme.test"'}),
            headers=self.headers,
        )

        assert both.status_code == 200, both.text
        assert both.json()["totalResults"] == 2
        assert {tuple(item["schemas"]) for item in both.json()["Resources"]} == {
            (SCHEMA_USER,),
            (SCHEMA_GROUP,),
        }
        # A filter only users can answer is answered by them, not refused for the groups.
        assert users_only.status_code == 200 and users_only.json()["totalResults"] == 1

    def test_a_search_request_that_is_not_one_is_a_400_in_the_protocols_error(self) -> None:
        for body in ({"startIndex": 0}, {"count": "many"}, {"filter": 12}):
            answer = self.client.post(
                f"{SCIM}/Groups/.search", content=json.dumps(body), headers=self.headers
            )

            assert answer.status_code == 400, body
            assert answer.json()["status"] == "400"


class TestTheAdministration:
    @pytest.fixture(autouse=True)
    def ready(self, sandbox: Path) -> None:
        self.sandbox = sandbox
        self.client = build(sandbox, trusted_proxies=[])
        self.codes = make_account("sofia", "security")
        login = sign_in(self.client, "sofia", self.codes[0])
        self.headers = {CSRF_HEADER_NAME: login["csrf_token"]}

    def elevate(self) -> None:
        response = self.client.post(
            "/api/auth/elevate", json={"code": self.codes[1]}, headers=self.headers
        )
        assert response.status_code == 200, response.text

    def put(self, name: str, body: dict[str, Any]) -> Any:
        return self.client.put(f"/api/auth/scim/sources/{name}", json=body, headers=self.headers)

    def test_a_source_is_added_in_sudo_mode_and_its_token_is_shown_once(self) -> None:
        refused = self.put("entra", {"label": "Entra ID"})
        assert refused.status_code == 403 and refused.json()["error"] == "elevation_required"
        self.elevate()

        added = self.put("entra", {"label": "Entra ID", "default_role": "operator"})

        assert added.status_code == 200, added.text
        body = added.json()
        assert body["token"].startswith("noust_scim_")
        assert body["base_url"].endswith("/api/scim/v2")
        assert body["source"]["default_role"] == "operator" and body["source"]["users"] == 0
        listed = self.client.get("/api/auth/scim/sources")
        assert body["token"] not in listed.text
        assert listed.json()["sources"][0]["token_hint"] == body["token"][-4:]
        # The token works, and is what the provider is given.
        assert (
            self.client.get(
                "/api/scim/v2/Users", headers={"Authorization": f"Bearer {body['token']}"}
            ).status_code
            == 200
        )

    def test_changing_a_setting_keeps_the_token_and_rotating_replaces_it(self) -> None:
        self.elevate()
        first = self.put("entra", {}).json()["token"]

        same = self.put("entra", {"label": "Acme"}).json()
        rotated = self.put("entra", {"rotate_token": True}).json()

        assert same["token"] is None and rotated["token"] not in (None, first)
        old = self.client.get("/api/scim/v2/Users", headers={"Authorization": f"Bearer {first}"})
        assert old.status_code == 401

    def test_a_setting_that_is_refused_writes_nothing(self) -> None:
        self.elevate()

        response = self.put("entra", {"default_team": "ghost"})

        assert response.status_code == 400
        assert self.client.get("/api/auth/scim/sources").json()["sources"] == []

    def test_the_token_never_reaches_the_audit_trail(self) -> None:
        self.elevate()
        token = self.put("entra", {}).json()["token"]

        events = [e for e in read_audit(self.sandbox) if e["action"] == "auth.scim.config"]

        assert events and token not in json.dumps(events)
        assert "added" in json.dumps(events[-1]) and "token" in json.dumps(events[-1])

    def test_the_roster_shows_who_was_provisioned_and_in_what_state(self) -> None:
        self.elevate()
        token = self.put("entra", {}).json()["token"]
        provider = {"Authorization": f"Bearer {token}", **JSON}
        self.client.post("/api/scim/v2/Users", content=json.dumps(user_doc()), headers=provider)

        roster = self.client.get("/api/auth/scim/sources/entra/users").json()

        assert roster["total"] == 1
        assert (
            roster["users"][0]["account"] == "maria" and roster["users"][0]["status"] == "invited"
        )
        assert self.client.get("/api/auth/scim/sources").json()["sources"][0]["users"] == 1

    def test_removing_a_source_ends_its_token_and_keeps_its_accounts(self) -> None:
        self.elevate()
        token = self.put("entra", {}).json()["token"]
        provider = {"Authorization": f"Bearer {token}", **JSON}
        self.client.post("/api/scim/v2/Users", content=json.dumps(user_doc()), headers=provider)

        removed = self.client.delete("/api/auth/scim/sources/entra", headers=self.headers)

        assert removed.status_code == 200 and removed.json()["removed"] == "entra"
        assert self.client.get("/api/scim/v2/Users", headers=provider).status_code == 401
        assert get_token_manager().accounts.find("maria") is not None
        assert (
            self.client.delete("/api/auth/scim/sources/entra", headers=self.headers).status_code
            == 404
        )

    def test_an_operator_does_not_touch_the_sources(self) -> None:
        codes = make_account("olga", "operator")
        client = build(self.sandbox, trusted_proxies=[])
        login = sign_in(client, "olga", codes[0])
        headers = {CSRF_HEADER_NAME: login["csrf_token"]}

        assert client.get("/api/auth/scim/sources").status_code == 403
        assert client.put("/api/auth/scim/sources/x", json={}, headers=headers).status_code == 403
        assert client.delete("/api/auth/scim/sources/x", headers=headers).status_code == 403


def test_the_endpoint_is_not_reachable_by_the_console_catch_all(sandbox: Path) -> None:
    client = TestClient(build(sandbox).app)

    response = client.get("/api/scim/v2/Users")

    assert response.status_code == 401 and "<html" not in response.text.lower()
