"""
The central over HTTP: what the session says about it, and unlocking it.

Sealing covers the secret store (node keys and tokens, integration keys,
backup passwords). The console's own credentials - the signing key, the
master token, the two-factor state, the sessions - live in the web state
directory and are never sealed, so an operator can sign in and confirm sudo
mode on a locked central; that is what lets unlocking ask for both.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noust.central import RoleError
from noust.core import sealing
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.secrets import secrets_dir
from noust.core.store import NoustStore
from noust.web.api.deps import error_response
from noust.web.auth import CSRF_HEADER_NAME
from noust.web.server import create_app, get_token_manager
from tests.test_web_auth import make_config, read_audit

PASSPHRASE = "correct horse battery staple"


@pytest.fixture(autouse=True)
def fresh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """This test's own configuration and store, so its secrets dir is its own."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    NoustStore.reset_instance()
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def central(sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


@pytest.fixture
def sealed() -> Iterator[Path]:
    """A sealed secret store that this process has not unlocked."""
    root = secrets_dir()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    sealing.seal_store(root, PASSPHRASE)
    sealing.lock(root)
    yield root
    sealing.lock(root)


def sign_in(client: TestClient) -> str:
    master = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": master})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return master


def elevate(client: TestClient, master: str) -> None:
    response = client.post("/api/auth/elevate", json={"token": master})
    assert response.status_code == 200, response.text


class TestSession:
    def test_a_signed_in_session_describes_the_central(self, central: TestClient) -> None:
        sign_in(central)

        body = central.get("/api/auth/session").json()

        assert body["central"] == {"role": "server", "sealed": False, "locked": False}

    def test_an_anonymous_caller_is_told_nothing_about_it(self, central: TestClient) -> None:
        assert central.get("/api/auth/session").json()["central"] is None

    def test_a_hub_says_so(self, central: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        Config.reset_instance()
        sign_in(central)

        assert central.get("/api/auth/session").json()["central"]["role"] == "hub"

    def test_sign_in_and_sudo_mode_work_while_locked(
        self, central: TestClient, sealed: Path
    ) -> None:
        elevate(central, sign_in(central))

        body = central.get("/api/auth/session").json()

        assert body["authenticated"] is True
        assert body["elevated_until"]
        assert body["central"] == {"role": "server", "sealed": True, "locked": True}


class TestUnlock:
    def test_it_needs_sudo_mode(self, central: TestClient, sealed: Path) -> None:
        sign_in(central)

        response = central.post("/api/central/unlock", json={"passphrase": PASSPHRASE})

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert not sealing.is_unlocked(sealed)

    def test_it_unlocks_and_is_audited(
        self, central: TestClient, sealed: Path, sandbox: Path
    ) -> None:
        elevate(central, sign_in(central))

        response = central.post("/api/central/unlock", json={"passphrase": PASSPHRASE})

        assert response.status_code == 200, response.text
        assert response.json() == {"role": "server", "sealed": True, "locked": False}
        assert sealing.is_unlocked(sealed)
        entries = [e for e in read_audit(sandbox) if e["action"] == "central.unlock"]
        assert [e["result"] for e in entries] == ["success"]

    def test_a_wrong_passphrase_is_a_403_counted_and_never_logged(
        self, central: TestClient, sealed: Path, sandbox: Path
    ) -> None:
        elevate(central, sign_in(central))

        response = central.post(
            "/api/central/unlock", json={"passphrase": "not the passphrase at all"}
        )

        assert response.status_code == 403
        assert response.json()["error"] == "wrong_passphrase"
        assert not sealing.is_unlocked(sealed)
        audit = read_audit(sandbox)
        assert any(e["action"] == "central.unlock" and e["result"] == "denied" for e in audit)
        assert any(e["action"] == "auth.credential" and e["result"] == "denied" for e in audit)
        log = (sandbox / "state" / "web-audit.log").read_text()
        assert "not the passphrase" not in log and PASSPHRASE not in log

    def test_wrong_passphrases_lock_out_like_credentials(
        self, sandbox: Path, runner: FakeRunner, sealed: Path
    ) -> None:
        client = TestClient(
            create_app(make_config(sandbox, max_failed_attempts=2)),
            client=("testclient", 50000),
        )
        elevate(client, sign_in(client))

        statuses = [
            client.post("/api/central/unlock", json={"passphrase": f"wrong guess {i:04d}"})
            for i in range(3)
        ]

        assert [r.status_code for r in statuses][:2] == [403, 403]
        assert statuses[2].status_code == 429
        # Even the right passphrase waits out the lockout.
        right = client.post("/api/central/unlock", json={"passphrase": PASSPHRASE})
        assert right.status_code == 429
        assert not sealing.is_unlocked(sealed)

    def test_a_store_that_is_not_sealed_is_a_409(self, central: TestClient) -> None:
        elevate(central, sign_in(central))

        response = central.post("/api/central/unlock", json={"passphrase": PASSPHRASE})

        assert response.status_code == 409
        assert "not sealed" in response.json()["detail"]

    def test_a_read_token_cannot(self, central: TestClient, sealed: Path) -> None:
        issued = get_token_manager().create_api_token("reader", "read")

        response = central.post(
            "/api/central/unlock",
            json={"passphrase": PASSPHRASE},
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403
        assert not sealing.is_unlocked(sealed)


class TestErrorContract:
    def test_a_locked_central_is_a_423(self) -> None:
        response = error_response(sealing.SecretsLockedError("locked", details="unlock it"))

        assert response.status_code == 423
        assert b'"central_locked"' in response.body

    def test_a_hub_refusal_is_a_409_with_its_message(self) -> None:
        response = error_response(RoleError("Sites: not available", details="A hub ..."))

        assert response.status_code == 409
        assert b'"hub_role"' in response.body
        assert b"A hub ..." in response.body

    def test_a_seal_error_is_a_409(self) -> None:
        response = error_response(sealing.SealError("damaged header"))

        assert response.status_code == 409
