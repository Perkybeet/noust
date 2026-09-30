"""
The console's identity layer over HTTP: accounts, the master token, tokens, sessions.

What these tests pin is what the 3.1 spec (§2.1-§2.3) promises an operator:
a person signs in with their account and a second factor and is answered the
same way whatever was wrong; the master token becomes break-glass once
accounts exist and account recovery only under the ENS profile; tokens belong
to accounts and never exceed them; sessions die of idleness and age; and a
server upgraded from 3.0 keeps letting its operator in.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.store import NoustStore
from noust.web import auth as auth_module
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scrypt cheap; the rules under test do not depend on its cost."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own, forgotten afterwards so no account leaks on."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def build(sandbox: Path, policy: AuthPolicy | None = None, **overrides: Any) -> TestClient:
    """
    Create the console with its state in the sandbox.

    Args:
        sandbox: Per-test directory.
        policy: The sign-in policy to use.
        **overrides: Other security configuration.

    Returns:
        A client for it.
    """
    config = SecurityConfig(
        state_dir=sandbox / "state",
        rate_limit_requests=5000,
        auth_policy=policy or AuthPolicy(),
        **overrides,
    )
    return TestClient(create_app(config), client=("testclient", 50000))


def accounts() -> AccountManager:
    """The account manager the console uses."""
    return get_token_manager().accounts


def make_account(username: str, role: str, *, mfa: bool = True) -> list[str]:
    """
    Create an account, with an authenticator unless told otherwise.

    Args:
        username: Its name.
        role: Its role.
        mfa: Whether to enrol an authenticator.

    Returns:
        Its backup codes, one per sign-in or confirmation the test needs.
    """
    manager = accounts()
    account = manager.create(username, role, password=PASSWORD)
    if not mfa:
        return []
    secret = manager.begin_totp(account.id)
    codes = manager.confirm_totp(account.id, totp.totp_now(secret))
    assert codes is not None
    return codes


def sign_in(client: TestClient, username: str, code: str | None) -> dict[str, Any]:
    """
    Sign in as an account and return the body.

    Args:
        client: The client, whose cookie jar keeps the session.
        username: The account.
        code: A backup code, or None for an account without a second factor.

    Returns:
        The login response.
    """
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD, "totp_code": code}
    )
    assert response.status_code == 200, response.text
    return response.json()


def master_sign_in(client: TestClient) -> tuple[str, str]:
    """
    Sign in with the master token.

    Args:
        client: The client.

    Returns:
        The master token and the CSRF token of the session.
    """
    token = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    return token, response.json()["csrf_token"]


def elevate_master(client: TestClient, token: str, csrf: str) -> None:
    """Confirm a master token session for sudo mode."""
    response = client.post(
        "/api/auth/elevate", json={"token": token}, headers={CSRF_HEADER_NAME: csrf}
    )
    assert response.status_code == 200, response.text


def read_audit(sandbox: Path) -> list[dict[str, Any]]:
    """Every audit entry the console wrote."""
    import json

    path = sandbox / "state" / "web-audit.log"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestAccountSignIn:
    def test_an_account_signs_in_and_its_session_carries_the_role(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "operator")

        body = sign_in(client, "maria", codes[0])

        assert body["account"]["username"] == "maria"
        assert body["mfa_required"] is False
        session = client.get("/api/auth/session").json()
        assert session["authenticated"] is True
        assert session["role"] == "operator"
        assert "apps.deploy" in session["permissions"]
        assert "apps.manage" not in session["permissions"]
        assert session["scope"] == "deploy"

    def test_the_last_sign_in_and_the_failures_since_are_reported(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "viewer")
        sign_in(client, "maria", codes[0])
        client.post("/api/auth/login", json={"username": "maria", "password": "wrong password"})

        body = sign_in(client, "maria", codes[1])

        assert body["previous_login_at"] is not None
        assert body["previous_login_ip"] == "testclient"
        assert body["failures_since"] == 1

    def test_every_refusal_answers_the_same(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "admin")
        attempts = [
            {"username": "nobody", "password": PASSWORD, "totp_code": codes[0]},
            {"username": "maria", "password": "not the password", "totp_code": codes[0]},
            {"username": "maria", "password": PASSWORD, "totp_code": "zzzzz-zzzzz"},
            {"username": "maria", "password": PASSWORD},
        ]
        answers = []
        for attempt in attempts:
            response = client.post("/api/auth/login", json=attempt)
            answers.append((response.status_code, response.json()))

        assert all(answer == answers[0] for answer in answers)
        assert answers[0][0] == 401
        assert answers[0][1]["error"] == "invalid_credentials"
        assert "remaining" not in answers[0][1]["detail"]

    def test_five_failures_lock_the_account_whatever_the_address(self, sandbox: Path) -> None:
        app = build(sandbox).app
        make_account("maria", "viewer")
        for index in range(5):
            client = TestClient(app, client=(f"10.0.0.{index}", 50000))
            client.post("/api/auth/login", json={"username": "maria", "password": "nope"})

        assert accounts().find("maria").is_locked()
        lockouts = [e for e in read_audit(sandbox) if e["action"] == "auth.lockout"]
        assert lockouts

    def test_an_account_without_a_second_factor_may_only_enrol_one(self, sandbox: Path) -> None:
        client = build(sandbox)
        make_account("maria", "admin", mfa=False)
        body = sign_in(client, "maria", None)
        csrf = body["csrf_token"]
        assert body["mfa_required"] is True

        refused = client.get("/api/apps")
        assert refused.status_code == 403
        assert refused.json()["error"] == "mfa_required"

        enrolment = client.post("/api/auth/2fa/enroll", headers={CSRF_HEADER_NAME: csrf})
        assert enrolment.status_code == 200, enrolment.text
        secret = enrolment.json()["secret"]
        assert "maria" in enrolment.json()["uri"]
        confirmed = client.post(
            "/api/auth/2fa/confirm",
            json={"code": totp.totp_now(secret)},
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert len(confirmed.json()["backup_codes"]) == 8
        assert client.get("/api/auth/session").json()["mfa_required"] is False

    def test_an_account_cannot_turn_its_second_factor_off(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "admin")
        csrf = sign_in(client, "maria", codes[0])["csrf_token"]
        client.post(
            "/api/auth/elevate",
            json={"password": PASSWORD, "code": codes[1]},
            headers={CSRF_HEADER_NAME: csrf},
        )

        response = client.post(
            "/api/auth/2fa/disable", json={"code": codes[2]}, headers={CSRF_HEADER_NAME: csrf}
        )

        assert response.status_code == 400
        assert accounts().find("maria").has_mfa

    def test_sudo_mode_for_an_account_asks_for_password_and_code(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "admin")
        csrf = sign_in(client, "maria", codes[0])["csrf_token"]
        headers = {CSRF_HEADER_NAME: csrf}

        only_code = client.post("/api/auth/elevate", json={"code": codes[1]}, headers=headers)
        assert only_code.status_code == 401
        assert only_code.json()["error"] == "invalid_credentials"

        both = client.post(
            "/api/auth/elevate", json={"password": PASSWORD, "code": codes[2]}, headers=headers
        )
        assert both.status_code == 200, both.text

    def test_disabling_an_account_ends_its_session_at_once(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "viewer")
        sign_in(client, "maria", codes[0])
        assert client.get("/api/auth/verify").status_code == 200

        accounts().disable("maria")

        assert client.get("/api/auth/verify").status_code == 401

    def test_a_role_change_applies_to_the_next_request(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "viewer")
        csrf = sign_in(client, "maria", codes[0])["csrf_token"]
        headers = {CSRF_HEADER_NAME: csrf}
        denied = client.post("/api/jobs/update", json={}, headers=headers)
        assert denied.json()["error"] == "permission_denied"

        accounts().set_role("maria", "operator")

        allowed = client.post("/api/jobs/update", json={}, headers=headers)
        assert allowed.status_code == 422, allowed.text

    def test_the_actor_in_the_audit_log_is_the_person(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "viewer")
        csrf = sign_in(client, "maria", codes[0])["csrf_token"]
        client.post("/api/auth/ws-ticket", headers={CSRF_HEADER_NAME: csrf})

        tickets = [e for e in read_audit(sandbox) if e["action"] == "auth.ws_ticket"]
        assert tickets[-1]["actor"] == "maria"


class TestNoticeAndLeaks:
    def test_an_anonymous_caller_learns_nothing_about_the_server(self, sandbox: Path) -> None:
        client = build(sandbox, policy=AuthPolicy(login_label="Production EU"))
        get_token_manager().begin_totp_enrollment()

        body = client.get("/api/auth/session").json()

        assert body["authenticated"] is False
        assert body["version"] == ""
        assert body["totp_enabled"] is False
        assert body["hostname"] == "Production EU"
        assert body["login_label"] == "Production EU"
        assert body["permissions"] == []

    def test_a_wrong_master_token_does_not_count_down(self, sandbox: Path) -> None:
        client = build(sandbox)
        get_token_manager().generate_master_token()

        response = client.post("/api/auth/login", json={"token": "noust_wrong"})

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_token"
        assert "remaining" not in response.json()["detail"]

    def test_the_notice_must_be_accepted_before_anything_else(self, sandbox: Path) -> None:
        policy = AuthPolicy(notice_text="Access is logged. Use it for your job only.")
        client = build(sandbox, policy=policy)
        codes = make_account("maria", "viewer")
        body = sign_in(client, "maria", codes[0])
        csrf = body["csrf_token"]
        assert body["notice_pending"] is True

        refused = client.get("/api/apps")
        assert refused.status_code == 403
        assert refused.json()["error"] == "notice_required"
        session = client.get("/api/auth/session").json()
        assert session["notice"]["text"].startswith("Access is logged")

        accepted = client.post(
            "/api/auth/notice/accept",
            json={"version": session["notice"]["version"]},
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert accepted.status_code == 200, accepted.text
        assert client.get("/api/auth/session").json()["notice"] is None
        assert accounts().find("maria").notice_version == policy.notice_version


class TestMasterToken:
    def test_without_accounts_the_master_token_is_the_whole_console(self, sandbox: Path) -> None:
        client = build(sandbox)
        master_sign_in(client)

        session = client.get("/api/auth/session").json()

        assert session["grant"] == "compat"
        assert session["accounts_exist"] is False
        assert "audit.manage" in session["permissions"]

    def test_once_accounts_exist_it_is_break_glass_and_says_so(self, sandbox: Path) -> None:
        client = build(sandbox)
        make_account("maria", "admin")
        token, csrf = master_sign_in(client)

        session = client.get("/api/auth/session").json()
        assert session["grant"] == "break_glass"
        refused = client.delete("/api/jobs/cleanup", headers={CSRF_HEADER_NAME: csrf})
        assert refused.status_code == 403
        assert any(e["action"] == "auth.break_glass" for e in read_audit(sandbox))

    def test_the_first_account_is_created_with_the_master_token(self, sandbox: Path) -> None:
        client = build(sandbox)
        token, csrf = master_sign_in(client)
        elevate_master(client, token, csrf)

        created = client.post(
            "/api/auth/accounts",
            json={"username": "maria", "role": "admin", "password": PASSWORD},
            headers={CSRF_HEADER_NAME: csrf},
        )

        assert created.status_code == 201, created.text
        assert created.json()["role"] == "admin"
        assert client.get("/api/auth/session").json()["accounts_exist"] is True

    def test_a_bearer_use_is_audited_as_a_warning(self, sandbox: Path) -> None:
        client = build(sandbox)
        token = get_token_manager().generate_master_token()

        response = client.get("/api/auth/verify", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        warnings = [e for e in read_audit(sandbox) if e["action"] == "auth.break_glass"]
        assert warnings and warnings[-1]["result"] == "warning"

    def test_under_the_ens_profile_the_bearer_is_refused(self, sandbox: Path) -> None:
        client = build(sandbox, policy=AuthPolicy(profile="ens-medium"))
        token = get_token_manager().generate_master_token()

        response = client.get("/api/auth/verify", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401
        assert response.json()["error"] == "master_bearer_disabled"

    def test_under_the_ens_profile_its_session_only_recovers_accounts(self, sandbox: Path) -> None:
        client = build(sandbox, policy=AuthPolicy(profile="ens-medium"))
        make_account("maria", "admin")
        master_sign_in(client)

        session = client.get("/api/auth/session").json()
        assert session["grant"] == "recovery"
        assert client.get("/api/apps").status_code == 403
        assert client.get("/api/auth/accounts").status_code == 200


class TestTokens:
    def issue(self, client: TestClient, csrf: str, **body: Any) -> Any:
        return client.post("/api/auth/tokens", json=body, headers={CSRF_HEADER_NAME: csrf})

    def test_a_token_never_holds_more_than_its_owner(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "operator")
        csrf = sign_in(client, "maria", codes[0])["csrf_token"]
        client.post(
            "/api/auth/elevate",
            json={"password": PASSWORD, "code": codes[1]},
            headers={CSRF_HEADER_NAME: csrf},
        )

        asked = self.issue(client, csrf, name="ci", scope="read", permissions=["apps.manage"])
        assert asked.status_code == 400
        issued = self.issue(client, csrf, name="ci", scope="admin")
        assert issued.status_code == 201, issued.text
        assert issued.json()["owner_account_id"] == accounts().find("maria").id
        assert "apps.manage" not in issued.json()["permissions"]

        token = issued.json()["token"]
        bearer = {"Authorization": f"Bearer {token}"}
        assert client.post("/api/apps", json={}, headers=bearer).json()["error"] == (
            "permission_denied"
        )

    def test_a_token_stops_when_its_owner_is_disabled(self, sandbox: Path) -> None:
        client = build(sandbox)
        make_account("maria", "operator")
        maria = accounts().find("maria")
        issued = get_token_manager().create_api_token("ci", "deploy", owner=maria)
        bearer = {"Authorization": f"Bearer {issued['token']}"}
        assert client.get("/api/auth/verify", headers=bearer).status_code == 200

        accounts().disable("maria")

        refused = client.get("/api/auth/verify", headers=bearer)
        assert refused.status_code == 401
        assert refused.json()["error"] == "token_owner_inactive"

    def test_a_token_restricted_to_a_network_is_refused_outside_it(self, sandbox: Path) -> None:
        client = build(sandbox, trusted_proxies=[])
        make_account("maria", "viewer")
        issued = get_token_manager().create_api_token(
            "ci", "read", owner=accounts().find("maria"), allowed_cidrs=["10.1.0.0/16"]
        )
        bearer = {"Authorization": f"Bearer {issued['token']}"}

        refused = client.get("/api/auth/verify", headers=bearer)
        assert refused.status_code == 401
        assert refused.json()["error"] == "token_network"
        inside = TestClient(client.app, client=("10.1.2.3", 50000))
        assert inside.get("/api/auth/verify", headers=bearer).status_code == 200

    def test_the_ens_profile_makes_expiry_mandatory(self, sandbox: Path) -> None:
        build(sandbox, policy=AuthPolicy(profile="ens-medium", token_max_days=90))
        make_account("maria", "operator")
        maria = accounts().find("maria")
        manager = get_token_manager()

        issued = manager.create_api_token("ci", "deploy", owner=maria)
        assert issued["expires_at"] == pytest.approx(time.time() + 90 * 86400, abs=60)
        with pytest.raises(Exception, match="at most 90 days"):
            manager.create_api_token("longer", "deploy", 24 * 365, owner=maria)
        with pytest.raises(Exception, match="belongs to an account"):
            manager.create_api_token("nobody", "deploy")

    def test_a_new_token_is_asked_for_sudo_mode(self, sandbox: Path) -> None:
        client = build(sandbox)
        make_account("maria", "admin")
        issued = get_token_manager().create_api_token("ci", "admin", owner=accounts().find("maria"))
        bearer = {"Authorization": f"Bearer {issued['token']}"}

        response = client.delete("/api/apps/shop.example.com", headers=bearer)

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_the_first_admin_adopts_the_tokens_issued_before(self, sandbox: Path) -> None:
        client = build(sandbox)
        legacy = get_token_manager().create_api_token("old-ci", "admin")
        bearer = {"Authorization": f"Bearer {legacy['token']}"}
        assert client.get("/api/auth/session", headers=bearer).json()["grant"] is None

        make_account("maria", "admin")
        session = client.get("/api/auth/session", headers=bearer).json()

        record = get_token_manager().get_api_token(legacy["id"])
        assert record["owner_account_id"] == accounts().find("maria").id
        assert session["scope"] == "admin"
        assert "security.manage" not in session["permissions"]


class TestSessions:
    def test_an_idle_session_is_over(self, sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = build(sandbox, policy=AuthPolicy(idle_minutes=30))
        codes = make_account("maria", "viewer")
        now = [time.time()]
        monkeypatch.setattr(auth_module, "_now", lambda: now[0])
        sign_in(client, "maria", codes[0])
        now[0] += 29 * 60
        assert client.get("/api/auth/verify").status_code == 200

        now[0] += 31 * 60

        assert client.get("/api/auth/verify").status_code == 401

    def test_a_person_lists_and_revokes_only_their_own_sessions(self, sandbox: Path) -> None:
        maria_client = build(sandbox)
        lucia_client = TestClient(maria_client.app, client=("testclient", 50000))
        maria_codes = make_account("maria", "viewer")
        lucia_codes = make_account("lucia", "viewer")
        csrf = sign_in(maria_client, "maria", maria_codes[0])["csrf_token"]
        sign_in(lucia_client, "lucia", lucia_codes[0])

        listed = maria_client.get("/api/auth/sessions").json()
        assert listed["active_sessions"] == 1
        maria_client.post("/api/auth/sessions/revoke-all", headers={CSRF_HEADER_NAME: csrf})

        assert lucia_client.get("/api/auth/verify").status_code == 200


class TestUpgradeFrom30:
    def test_a_30_session_and_tokens_keep_working(self, sandbox: Path) -> None:
        state = sandbox / "state"
        state.mkdir(mode=0o700)
        db = sqlite3.connect(state / "web-sessions.db")
        db.executescript(
            """
            CREATE TABLE sessions (sid TEXT PRIMARY KEY, csrf_token TEXT NOT NULL,
                client_ip TEXT NOT NULL, issued_at REAL NOT NULL,
                created_at REAL NOT NULL DEFAULT 0, expires_at REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0, elevated_until REAL, family TEXT,
                rotated_to TEXT);
            CREATE TABLE ws_tickets (ticket_hash TEXT PRIMARY KEY, sid TEXT NOT NULL,
                client_ip TEXT NOT NULL, expires_at REAL NOT NULL);
            CREATE TABLE api_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, token_hash TEXT NOT NULL UNIQUE,
                scope TEXT NOT NULL CHECK (scope IN ('read', 'deploy', 'admin', 'fleet')),
                created_at REAL NOT NULL, expires_at REAL, last_used_at REAL, revoked_at REAL);
            """
        )
        now = time.time()
        db.execute(
            "INSERT INTO sessions VALUES ('a' || hex(randomblob(15)), 'csrf', 'testclient', ?, ?, ?, "
            "0, NULL, NULL, NULL)",
            (now, now, now + 3600),
        )
        db.commit()
        db.close()

        client = build(sandbox)
        manager = get_token_manager()
        sid = manager.sessions.list_active()[0]["sid"]
        cookie = manager._encode(sid, "testclient", auth_module.utcnow(), auth_module.utcnow())
        legacy = manager._issue_api_token("ci-3.0", "deploy", None)

        client.cookies.set("wasm_session", cookie)
        session = client.get("/api/auth/session").json()
        assert session["authenticated"] is True
        assert session["grant"] == "compat"
        bearer = {"Authorization": f"Bearer {legacy['token']}"}
        assert client.post("/api/jobs/update", json={}, headers=bearer).status_code != 403
        assert client.post("/api/apps/x.example.com/restart", headers=bearer).status_code == 403

        make_account("maria", "admin")
        assert client.get("/api/auth/verify", headers=bearer).status_code == 200
        assert client.get("/api/auth/session").json()["grant"] == "break_glass"


class TestInvitations:
    def test_an_invited_person_sets_their_own_credentials(self, sandbox: Path) -> None:
        client = build(sandbox)
        token, csrf = master_sign_in(client)
        elevate_master(client, token, csrf)
        issued = client.post(
            "/api/auth/invitations",
            json={"username": "lucia", "role": "auditor"},
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert issued.status_code == 201, issued.text
        code = issued.json()["code"]

        stranger = TestClient(client.app, client=("198.51.100.7", 50000))
        opened = stranger.post("/api/auth/invitations/open", json={"code": code})
        assert opened.status_code == 200, opened.text
        secret = opened.json()["totp_secret"]
        accepted = stranger.post(
            "/api/auth/invitations/accept",
            json={"code": code, "password": PASSWORD, "totp_code": totp.totp_now(secret)},
        )
        assert accepted.status_code == 200, accepted.text
        backup = accepted.json()["backup_codes"]

        body = sign_in(stranger, "lucia", backup[0])
        assert body["account"]["role"] == "auditor"
        again = stranger.post("/api/auth/invitations/open", json={"code": code})
        assert again.status_code == 401
        assert again.json()["error"] == "invalid_invitation"

    def test_account_management_needs_accounts_manage(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "admin")
        csrf = sign_in(client, "maria", codes[0])["csrf_token"]

        listed = client.get("/api/auth/accounts")
        created = client.post(
            "/api/auth/accounts",
            json={"username": "zed", "role": "viewer", "password": PASSWORD},
            headers={CSRF_HEADER_NAME: csrf},
        )

        assert listed.status_code == 403
        assert created.status_code == 403
        assert created.json()["error"] == "permission_denied"
