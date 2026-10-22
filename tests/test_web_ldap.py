# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Signing in against a directory over HTTP, and administering the directories (checklist I-22).

What matters here is that a directory is a first factor and nothing more (the account's own
second factor still follows), that every failure of the credentials is the same answer and is
counted, that a directory being down is not a guess, and that no password reaches the audit trail
or an answer.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts.directory import DirectoryManager
from noust.web.auth import CSRF_HEADER_NAME
from noust.web.server import get_token_manager
from tests.ldap_support import FakeDirectory, entry
from tests.test_web_accounts import (  # noqa: F401 - the fixtures of the account tests
    accounts,
    build,
    cheap_hashes,
    make_account,
    read_audit,
    sign_in,
    store,
)

LOGIN = "/api/auth/ldap/login"
SERVICE = "cn=svc,ou=system,dc=acme,dc=test"
USER_FILTER = "(&(objectClass=person)(uid={username}))"


def people() -> list[dict[str, Any]]:
    return [
        entry(
            "uid=maria,ou=people,dc=acme,dc=test",
            uid="maria",
            mail="maria@acme.test",
            displayName="Maria Lopez",
            userPassword="correct horse",
            entryUUID="uuid-maria",
            objectClass="person",
        ),
        entry(
            "uid=olga,ou=people,dc=acme,dc=test",
            uid="olga",
            mail="olga@evil.test",
            userPassword="other secret",
            entryUUID="uuid-olga",
            objectClass="person",
        ),
        entry(SERVICE, cn="svc", userPassword="service password", objectClass="applicationProcess"),
    ]


@pytest.fixture
def server() -> Iterator[FakeDirectory]:
    with FakeDirectory(people()) as directory:
        yield directory


def manager() -> DirectoryManager:
    tokens = get_token_manager()
    return DirectoryManager(tokens.accounts.store, accounts=tokens.accounts)


def settings(server: FakeDirectory, **more: Any) -> dict[str, Any]:
    return {
        "host": "localhost",
        "port": server.port,
        "tls": "none",
        "bind_dn": SERVICE,
        "user_base": "ou=people,dc=acme,dc=test",
        "user_filter": USER_FILTER,
        "label": "Acme directory",
        **more,
    }


class TestSigningIn:
    @pytest.fixture(autouse=True)
    def ready(self, sandbox: Path, server: FakeDirectory, real_config: Any) -> None:
        # A directory on this machine is allowed by naming it, as any internal destination is.
        real_config.set("security.allow_private_hosts", ["localhost"])
        self.sandbox = sandbox
        self.server = server
        self.client = build(sandbox, trusted_proxies=[])
        make_account("root-admin", "admin")
        manager().configure(
            "corp", settings(server, provisioning="auto"), bind_password="service password"
        )

    def post(self, username: str = "maria", password: str | None = None, **more: Any) -> Any:
        return self.client.post(
            LOGIN,
            json={
                "directory": "corp",
                "username": username,
                "password": "correct horse" if password is None else password,
                **more,
            },
        )

    def test_a_new_person_signs_in_and_gets_a_session_fit_only_to_enrol_a_factor(self) -> None:
        response = self.post()

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True and body["account"]["username"] == "maria"
        assert self.client.cookies.get("noust_session")
        assert get_token_manager().accounts.require("maria").status == "active"

    def test_an_account_with_a_second_factor_owes_it_after_the_directory(self) -> None:
        codes = make_account("maria", "operator", person_ref="maria@acme.test")
        manager().link("corp", "maria", "uuid-maria")

        first = self.post().json()

        assert first["success"] is False
        assert first["second_factor"]["challenge"] and not self.client.cookies.get("noust_session")
        finished = self.client.post(
            "/api/auth/login/second-factor",
            json={"challenge": first["second_factor"]["challenge"], "code": codes[0]},
        )
        assert finished.status_code == 200, finished.text
        assert finished.json()["success"] is True
        events = [e for e in read_audit(self.sandbox) if e["action"] == "auth.ldap.login"]
        assert events[-1]["result"] == "success" and "second factor" in json.dumps(events[-1])

    def test_a_wrong_password_and_an_unknown_name_are_the_same_401(self) -> None:
        wrong = self.post(password="not the password")
        unknown = self.post(username="nobody")

        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()
        assert wrong.json()["error"] == "invalid_credentials"

    def test_guessing_is_counted_and_locks_the_address_out(self) -> None:
        for _ in range(5):
            assert self.post(password="guess").status_code == 401

        locked = self.post(password="guess again")

        assert locked.status_code == 429
        # Even the right password is refused while it lasts.
        assert self.post().status_code == 429

    def test_a_person_the_policy_does_not_admit_is_told_so_and_is_not_counted(self) -> None:
        manager().configure("corp", {"allowed_domains": ["acme.test"]})

        for _ in range(8):
            refused = self.post(username="olga", password="other secret")
            assert refused.status_code == 403
            assert refused.json()["error"] == "sign_in_refused"

        assert self.post().status_code == 200

    def test_a_directory_that_is_down_is_a_503_and_is_not_counted(self) -> None:
        manager().configure("corp", {"port": 1})

        for _ in range(8):
            down = self.post()
            assert down.status_code == 503 and down.json()["error"] == "sign_in_unavailable"

        manager().configure("corp", {"port": self.server.port})
        assert self.post().status_code == 200

    def test_a_directory_that_does_not_exist_is_unavailable(self) -> None:
        response = self.client.post(
            LOGIN, json={"directory": "nowhere", "username": "maria", "password": "x"}
        )

        assert response.status_code == 503

    def test_turning_the_password_sign_in_off_does_not_turn_the_directory_off(
        self, real_config: Any
    ) -> None:
        self.post()
        real_config.set("auth.local_login.exempt", ["root-admin"])
        real_config.set("auth.local_login.enabled", False)

        response = self.post()

        assert response.status_code == 200 and response.json()["success"] is True

    def test_a_disabled_account_is_refused(self) -> None:
        self.post()
        get_token_manager().accounts.disable("maria")

        response = self.post()

        assert response.status_code == 403

    def test_the_sign_in_page_is_told_the_directory_and_nothing_else_of_it(self) -> None:
        listed = self.client.get("/api/auth/oidc/providers").json()

        assert listed["providers"] == [
            {"name": "corp", "label": "Acme directory", "protocol": "ldap"}
        ]
        assert "localhost" not in json.dumps(listed) and SERVICE not in json.dumps(listed)

    def test_no_password_and_no_typed_name_reaches_the_audit_trail(self) -> None:
        self.post(username="maria", password="correct horse")
        self.post(username="correct horse", password="x")
        self.post(username="maria", password="the wrong one")

        raw = self.sandbox.joinpath("state", "web-audit.log").read_text()

        for secret in ("correct horse", "the wrong one", "service password"):
            assert secret not in raw
        assert any(e["action"] == "auth.ldap.login" for e in read_audit(self.sandbox))

    def test_the_directory_is_asked_for_a_bind_it_never_makes_empty(self) -> None:
        response = self.post(password="")

        assert response.status_code in (401, 422)
        assert self.server.seen.binds == []

    def test_an_oversized_name_or_password_is_refused_before_it_is_used(self) -> None:
        assert self.post(username="m" * 300).status_code == 422
        assert self.post(password="p" * 2000).status_code == 422


class TestTheAdministration:
    @pytest.fixture(autouse=True)
    def ready(self, sandbox: Path, server: FakeDirectory, real_config: Any) -> None:
        real_config.set("security.allow_private_hosts", ["localhost"])
        self.sandbox = sandbox
        self.server = server
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
        return self.client.put(
            f"/api/auth/ldap/directories/{name}", json=body, headers=self.headers
        )

    def test_a_directory_is_added_in_sudo_mode_and_its_password_is_never_shown(self) -> None:
        body = {**settings(self.server), "bind_password": "service password"}

        refused = self.put("corp", body)
        assert refused.status_code == 403 and refused.json()["error"] == "elevation_required"
        self.elevate()
        added = self.put("corp", body)

        assert added.status_code == 200, added.text
        info = added.json()
        assert info["bind_password_set"] is True and info["host"] == "localhost"
        assert "service password" not in added.text
        listed = self.client.get("/api/auth/ldap/directories")
        assert "service password" not in listed.text
        assert [d["name"] for d in listed.json()["directories"]] == ["corp"]

    def test_the_password_never_reaches_the_audit_trail(self) -> None:
        self.elevate()
        self.put("corp", {**settings(self.server), "bind_password": "service password"})

        events = [e for e in read_audit(self.sandbox) if e["action"] == "auth.ldap.config"]

        assert events and "service password" not in json.dumps(events)
        assert "bind_password" in json.dumps(events[-1])

    def test_what_is_wrong_is_a_400_and_writes_nothing(self) -> None:
        self.elevate()

        response = self.put("corp", {**settings(self.server), "user_filter": "(uid=maria)"})

        assert response.status_code == 400 and "{username}" in response.text
        assert self.client.get("/api/auth/ldap/directories").json()["directories"] == []

    def test_a_directory_is_tested_check_by_check(self) -> None:
        self.elevate()
        self.put("corp", {**settings(self.server), "bind_password": "service password"})

        response = self.client.post("/api/auth/ldap/directories/corp/test", headers=self.headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert [c["name"] for c in body["checks"]] == ["connection", "bind", "users"]

    def test_a_failing_test_says_where(self) -> None:
        self.elevate()
        self.put("corp", {**settings(self.server), "bind_password": "wrong"})

        body = self.client.post("/api/auth/ldap/directories/corp/test", headers=self.headers).json()

        assert body["ok"] is False
        assert (
            body["checks"][-1]["name"] == "bind"
            and "refused the bind" in body["checks"][-1]["detail"]
        )

    def test_testing_or_removing_one_that_is_not_there_is_a_404(self) -> None:
        self.elevate()

        assert (
            self.client.post(
                "/api/auth/ldap/directories/nowhere/test", headers=self.headers
            ).status_code
            == 404
        )
        assert (
            self.client.delete(
                "/api/auth/ldap/directories/nowhere", headers=self.headers
            ).status_code
            == 404
        )

    def test_removing_a_directory_forgets_its_password(self) -> None:
        self.elevate()
        self.put("corp", {**settings(self.server), "bind_password": "service password"})

        removed = self.client.delete("/api/auth/ldap/directories/corp", headers=self.headers)

        assert removed.status_code == 200 and removed.json()["removed"] == "corp"
        assert not manager().data.has_secret("corp")

    def test_an_operator_does_not_touch_the_directories(self) -> None:
        codes = make_account("olga", "operator")
        client = build(self.sandbox, trusted_proxies=[])
        login = sign_in(client, "olga", codes[0])
        headers = {CSRF_HEADER_NAME: login["csrf_token"]}

        assert client.get("/api/auth/ldap/directories").status_code == 403
        assert (
            client.put("/api/auth/ldap/directories/x", json={}, headers=headers).status_code == 403
        )
        assert client.delete("/api/auth/ldap/directories/x", headers=headers).status_code == 403
        assert client.post("/api/auth/ldap/directories/x/test", headers=headers).status_code == 403
