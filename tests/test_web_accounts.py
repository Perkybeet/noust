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


def make_account(
    username: str, role: str, *, mfa: bool = True, person_ref: str | None = None
) -> list[str]:
    """
    Create an account, with an authenticator unless told otherwise.

    Args:
        username: Its name.
        role: Its role.
        mfa: Whether to enrol an authenticator.
        person_ref: The e-mail of the person it belongs to.

    Returns:
        Its backup codes, one per sign-in or confirmation the test needs.
    """
    manager = accounts()
    account = manager.create(username, role, password=PASSWORD, person_ref=person_ref)
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
        # More attempts than the address takes: this is about the answers, not the lockout.
        client = build(sandbox, max_failed_attempts=50)
        codes = make_account("maria", "admin", person_ref="maria@example.com")
        make_account("pedro", "operator", person_ref="pedro@example.com")
        make_account("pedro.view", "viewer", person_ref="pedro@example.com")
        attempts = [
            {"username": "nobody", "password": PASSWORD, "totp_code": codes[0]},
            {"username": "maria", "password": "not the password", "totp_code": codes[0]},
            {"username": "maria", "password": PASSWORD, "totp_code": "zzzzz-zzzzz"},
            {"username": "nobody"},
            {"username": "maria", "password": "not the password"},
            {"username": "maria@example.com", "password": "not the password"},
            {"username": "nobody@example.com", "password": PASSWORD},
            # One person, two accounts: the e-mail names neither, and says so no differently.
            {"username": "pedro@example.com", "password": PASSWORD},
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

    def test_an_account_without_a_second_factor_is_not_asked_for_one(self, sandbox: Path) -> None:
        client = build(sandbox)
        make_account("maria", "admin", mfa=False)

        body = client.post(
            "/api/auth/login", json={"username": "maria", "password": PASSWORD}
        ).json()

        assert body["success"] is True
        assert body["second_factor"] is None
        assert body["mfa_required"] is True

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


def _ws_code(client: TestClient, url: str, **kwargs: Any) -> int | None:
    from starlette.websockets import WebSocketDisconnect

    try:
        with client.websocket_connect(url, **kwargs):
            return None
    except WebSocketDisconnect as exc:
        return exc.code


class TestWebSocketGates:
    """Security review 3.1, finding 4: a stream is held to what its route declares."""

    def test_a_stream_needs_its_permission(self, sandbox: Path) -> None:
        from noust.web.auth import WS_CLOSE_FORBIDDEN
        from noust.web.websockets.router import WS_SUBPROTOCOL, WS_TOKEN_PREFIX

        client = build(sandbox)
        make_account("maria", "admin")
        issued = get_token_manager().create_api_token(
            "fleet-watcher", "read", owner=accounts().find("maria"), permissions=["fleet.read"]
        )
        protocols = [WS_SUBPROTOCOL, f"{WS_TOKEN_PREFIX}{issued['token']}"]
        for path in ("/ws/events", "/ws/jobs", "/ws/jobs/x1", "/ws/logs/shop.example.com"):
            assert _ws_code(client, path, subprotocols=protocols) == WS_CLOSE_FORBIDDEN, path

    def test_no_stream_before_the_notice_is_accepted(self, sandbox: Path) -> None:
        from noust.web.auth import WS_CLOSE_FORBIDDEN

        client = build(sandbox, policy=AuthPolicy(notice_text="Access is logged."))
        codes = make_account("maria", "viewer")
        sign_in(client, "maria", codes[0])
        assert _ws_code(client, "/ws/events") == WS_CLOSE_FORBIDDEN

    def test_no_stream_before_a_second_factor(self, sandbox: Path) -> None:
        from noust.web.auth import WS_CLOSE_FORBIDDEN

        client = build(sandbox)
        make_account("maria", "viewer", mfa=False)
        sign_in(client, "maria", None)
        assert _ws_code(client, "/ws/events") == WS_CLOSE_FORBIDDEN


class TestTheNoticeGatesStandingPower:
    """Security review 3.1, low: no sudo mode and no token before the notice."""

    POLICY = AuthPolicy(notice_text="Access is logged. Use it for your job only.")

    def test_no_sudo_mode_before_the_notice(self, sandbox: Path) -> None:
        client = build(sandbox, policy=self.POLICY)
        codes = make_account("maria", "admin")
        csrf = sign_in(client, "maria", codes[0])["csrf_token"]
        refused = client.post(
            "/api/auth/elevate",
            json={"password": PASSWORD, "code": codes[1]},
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"] == "notice_required"

    def test_a_token_is_held_to_its_owner_s_notice(self, sandbox: Path) -> None:
        client = build(sandbox, policy=self.POLICY)
        make_account("maria", "admin")
        issued = get_token_manager().create_api_token("ci", "read", owner=accounts().find("maria"))
        bearer = {"Authorization": f"Bearer {issued['token']}"}
        refused = client.get("/api/apps", headers=bearer)
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"] == "notice_required"
        # Its own credential stays reachable, like a session's.
        assert client.get("/api/auth/verify", headers=bearer).status_code == 200


class TestLegacyTokensWithoutAnAdmin:
    """Security review 3.1, low: a 3.0 token nobody adopted is not above every account."""

    def test_an_unowned_admin_token_does_not_govern_security(self, sandbox: Path) -> None:
        build(sandbox)
        manager = get_token_manager()
        legacy = manager._issue_api_token("ci-3.0", "admin", None)
        before = manager.verify_api_token(legacy["token"], "testclient")
        assert before is not None and "audit.manage" in before["permissions"]
        make_account("sam", "security")

        payload = manager.verify_api_token(legacy["token"], "testclient")
        assert payload is not None
        assert "audit.manage" not in payload["permissions"]
        assert "accounts.manage" not in payload["permissions"]
        assert "security.manage" not in payload["permissions"]
        assert "apps.manage" in payload["permissions"]


def password_step(client: TestClient, username: str = "maria") -> dict[str, Any]:
    """
    Pass the first step of the console's sign-in: the name and the password.

    Args:
        client: The client.
        username: The username or e-mail typed.

    Returns:
        The second step the server opened.
    """
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert body["second_factor"] is not None, body
    return dict(body["second_factor"])


def second_step(client: TestClient, challenge: str, code: str) -> Any:
    """Answer the second step with a code."""
    return client.post("/api/auth/login/second-factor", json={"challenge": challenge, "code": code})


class TestTwoStepSignIn:
    """The console asks for the password, then for the second factor the account has."""

    def test_a_right_password_opens_the_second_step_and_nothing_else(self, sandbox: Path) -> None:
        client = build(sandbox)
        make_account("maria", "admin")

        step = password_step(client)

        assert step["challenge"] and step["expires_in"] == 300
        assert step["methods"] == ["totp", "backup_code"]
        assert client.get("/api/auth/session").json()["authenticated"] is False
        # Knowing the password is not a failure, and is not a sign-in yet either.
        assert accounts().find("maria").failures_since_login == 0
        assert accounts().find("maria").last_login_at is None

    def test_the_code_completes_the_sign_in(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "operator")
        step = password_step(client)

        response = second_step(client, step["challenge"], codes[0])

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["account"]["username"] == "maria"
        assert body["mfa_required"] is False
        session = client.get("/api/auth/session").json()
        assert session["authenticated"] is True and session["role"] == "operator"
        entries = [e for e in read_audit(sandbox) if e["action"] == "auth.login"]
        assert entries[-1]["result"] == "success"

    def test_the_step_is_single_use(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "operator")
        step = password_step(client)
        assert second_step(client, step["challenge"], codes[0]).status_code == 200

        again = second_step(build(sandbox), step["challenge"], codes[1])

        assert again.status_code == 401
        assert again.json()["error"] == "sign_in_expired"

    def test_a_wrong_code_is_counted_and_the_step_stays_open(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "operator")
        step = password_step(client)

        wrong = second_step(client, step["challenge"], "zzzzz-zzzzz")

        assert wrong.status_code == 401
        assert wrong.json()["error"] == "invalid_totp"
        assert accounts().find("maria").failures_since_login == 1
        assert second_step(client, step["challenge"], codes[0]).status_code == 200

    def test_wrong_codes_lock_the_account(self, sandbox: Path) -> None:
        # The account's lockout, not the address's: the address takes more here.
        client = build(sandbox, max_failed_attempts=50)
        codes = make_account("maria", "operator")
        step = password_step(client)
        for _ in range(4):
            assert second_step(client, step["challenge"], "zzzzz-zzzzz").status_code == 401

        last = second_step(client, step["challenge"], "zzzzz-zzzzz")

        assert accounts().find("maria").is_locked()
        assert any(e["action"] == "auth.lockout" for e in read_audit(sandbox))
        # The locked account is out, whatever it presents next, and the step with it.
        assert last.status_code == 401
        refused = second_step(client, step["challenge"], codes[0])
        assert refused.status_code == 401
        assert refused.json()["error"] in ("sign_in_expired", "invalid_credentials")

    def test_the_step_expires(self, sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = build(sandbox)
        codes = make_account("maria", "operator")
        monkeypatch.setattr(auth_module, "LOGIN_CHALLENGE_TTL", 0)
        step = password_step(client)

        response = second_step(client, step["challenge"], codes[0])

        assert response.status_code == 401
        assert response.json()["error"] == "sign_in_expired"

    def test_the_step_belongs_to_the_address_it_was_opened_from(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "operator")
        step = password_step(client)
        elsewhere = TestClient(client.app, client=("198.51.100.20", 50000))

        response = second_step(elsewhere, step["challenge"], codes[0])

        assert response.status_code == 401
        assert response.json()["error"] == "sign_in_expired"
        assert second_step(client, step["challenge"], codes[0]).status_code == 200

    def test_a_disabled_account_cannot_finish(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("maria", "operator")
        step = password_step(client)
        accounts().disable("maria", reason="left")

        response = second_step(client, step["challenge"], codes[0])

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_credentials"
        assert client.get("/api/auth/session").json()["authenticated"] is False


class TestSignInByEmail:
    def test_the_email_of_the_person_signs_their_one_account_in(self, sandbox: Path) -> None:
        client = build(sandbox)
        codes = make_account("yago", "admin", person_ref="yago.lopez@example.com")

        step = password_step(client, "Yago.Lopez@example.com")
        response = second_step(client, step["challenge"], codes[0])

        assert response.status_code == 200, response.text
        assert response.json()["account"]["username"] == "yago"


class TestSharedAddressLockout:
    """
    Every operator on an SSH tunnel arrives from 127.0.0.1: one operator's typos must not
    lock the others out, and a remote address is still locked as a whole.
    """

    @staticmethod
    def fail(client: TestClient, username: str, times: int = 5, **headers: str) -> None:
        for _ in range(times):
            response = client.post(
                "/api/auth/login",
                json={"username": username, "password": "not the password"},
                headers=headers,
            )
            assert response.status_code == 401, response.text

    @staticmethod
    def attempt(client: TestClient, username: str, **headers: str) -> Any:
        return client.post(
            "/api/auth/login", json={"username": username, "password": PASSWORD}, headers=headers
        )

    def test_on_loopback_the_limit_is_per_name(self, sandbox: Path) -> None:
        client = TestClient(build(sandbox).app, client=("127.0.0.1", 50000))
        make_account("maria", "admin", mfa=False)

        self.fail(client, "ghost")
        locked = self.attempt(client, "ghost")
        other = self.attempt(client, "maria")

        assert locked.status_code == 429
        assert locked.json()["error"] == "locked_out"
        assert int(locked.headers["Retry-After"]) > 0
        assert other.status_code == 200, other.text
        # The name is folded as the account's is: another case is the same name.
        assert self.attempt(client, "GHOST").status_code == 429

    def test_on_loopback_a_locked_name_does_not_lock_the_cookie(self, sandbox: Path) -> None:
        client = TestClient(build(sandbox).app, client=("127.0.0.1", 50000))
        make_account("maria", "admin", mfa=False)
        assert self.attempt(client, "maria").status_code == 200

        self.fail(client, "ghost")

        assert client.get("/api/auth/sessions").status_code == 200

    def test_on_ipv6_loopback_too(self, sandbox: Path) -> None:
        client = TestClient(build(sandbox).app, client=("::1", 50000))
        make_account("maria", "admin", mfa=False)
        self.fail(client, "ghost")
        assert self.attempt(client, "maria").status_code == 200

    def test_a_remote_address_is_still_locked_as_a_whole(self, sandbox: Path) -> None:
        client = TestClient(build(sandbox).app, client=("203.0.113.9", 50000))
        make_account("maria", "admin", mfa=False)

        self.fail(client, "ghost")
        response = self.attempt(client, "maria")

        assert response.status_code == 429
        assert response.json()["error"] == "locked_out"

    def test_a_trusted_proxy_that_names_nobody_counts_like_loopback(self, sandbox: Path) -> None:
        app = build(sandbox, trusted_proxies=["10.0.0.1"]).app
        client = TestClient(app, client=("10.0.0.1", 50000))
        make_account("maria", "admin", mfa=False)

        self.fail(client, "ghost")

        assert self.attempt(client, "maria").status_code == 200

    def test_behind_a_trusted_proxy_the_forwarded_address_is_locked_whole(
        self, sandbox: Path
    ) -> None:
        app = build(sandbox, trusted_proxies=["10.0.0.1"]).app
        client = TestClient(app, client=("10.0.0.1", 50000))
        make_account("maria", "admin", mfa=False)
        forwarded = {"X-Forwarded-For": "198.51.100.7"}

        self.fail(client, "ghost", **forwarded)

        assert self.attempt(client, "maria", **forwarded).status_code == 429
        assert self.attempt(client, "maria").status_code == 200

    def test_the_per_account_lockout_still_holds_on_loopback(self, sandbox: Path) -> None:
        app = build(sandbox).app
        make_account("maria", "admin", mfa=False)
        # Every attempt from its own port: the account is counted, whoever asks.
        for port in range(5):
            client = TestClient(app, client=("127.0.0.1", 50000 + port))
            client.post("/api/auth/login", json={"username": "maria", "password": "nope"})

        assert accounts().find("maria").is_locked()

    def test_the_second_step_on_loopback_is_counted_under_the_account(self, sandbox: Path) -> None:
        client = TestClient(build(sandbox).app, client=("127.0.0.1", 50000))
        codes = make_account("maria", "operator")
        make_account("pedro", "admin", mfa=False)
        step = password_step(client)
        for _ in range(5):
            second_step(client, step["challenge"], "zzzzz-zzzzz")

        assert self.attempt(client, "pedro").status_code == 200
        assert codes

    def test_the_master_token_on_loopback_has_a_limit_of_its_own(self, sandbox: Path) -> None:
        client = TestClient(build(sandbox).app, client=("127.0.0.1", 50000))
        make_account("maria", "admin", mfa=False)
        get_token_manager().generate_master_token()
        for _ in range(5):
            client.post("/api/auth/login", json={"token": "not the token"})

        assert client.post("/api/auth/login", json={"token": "x"}).status_code == 429
        assert self.attempt(client, "maria").status_code == 200


@pytest.mark.parametrize(
    ("address", "shared"),
    [
        ("127.0.0.1", True),
        ("127.8.0.3", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("10.0.0.1", True),
        ("10.0.0.2", False),
        ("203.0.113.9", False),
        ("testclient", False),
        ("unknown", False),
    ],
)
def test_which_addresses_many_people_share(address: str, shared: bool) -> None:
    config = SecurityConfig(trusted_proxies=["10.0.0.1"])
    assert auth_module.shared_address(address, config) is shared
    key = auth_module.sign_in_lockout_key(address, " Maria ", config)
    assert (key != address) is shared
    if shared:
        assert key.endswith(":account:maria")
