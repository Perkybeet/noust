# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/apps/{domain}/deploy-key`` and ``/api/deploy-keys`` (checklist A-02).

The endpoints are a client of the deploy key manager. What is checked here is
the contract: the private half is in no response, importing and removing ask
for sudo mode, a viewer cannot change anything, and a refusal says what to do.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.runner import set_runner
from noust.core.store import App, NoustStore
from noust.managers.source_keys import DeployKeyManager
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app as build_app
from noust.web.server import get_token_manager
from tests.deploy_key_support import DeployKeyRunner, GitScript, private_key_text
from tests.fleet_support import ed25519_line, fingerprint

DOMAIN = "shop.example.com"
SOURCE = "git@github.com:acme/shop.git"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """The store the console reads."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()
        set_runner(None)


@pytest.fixture
def keygen(store: NoustStore) -> DeployKeyRunner:
    """The runner ssh-keygen goes through."""
    runner = DeployKeyRunner()
    set_runner(runner)
    return runner


@pytest.fixture
def seeded(store: NoustStore, keygen: DeployKeyRunner) -> App:
    """An application built from a private repository."""
    return store.create_app(
        App(domain=DOMAIN, app_type="nodejs", source=SOURCE, branch="main", port=3000)
    )


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    """The console."""
    return build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


def sign_in(app: FastAPI, *, elevate: bool) -> TestClient:
    """
    Sign in with the master token.

    Args:
        app: The console.
        elevate: Confirm sudo mode too.

    Returns:
        A client carrying the CSRF header.
    """
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    login = client.post("/api/auth/login", json={"token": token})
    assert login.status_code == 200, login.text
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    if elevate:
        assert client.post("/api/auth/elevate", json={"token": token}).status_code == 200
    return client


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    """A client in sudo mode."""
    return sign_in(app, elevate=True)


@pytest.fixture
def plain(app: FastAPI) -> TestClient:
    """A signed-in client that has not confirmed sudo mode."""
    return sign_in(app, elevate=False)


URL = f"/api/apps/{DOMAIN}/deploy-key"


class TestShow:
    """What an application's repository is reached with."""

    def test_without_a_key_it_says_the_server_wide_key_is_used(
        self, seeded: App, plain: TestClient
    ) -> None:
        response = plain.get(URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["domain"] == DOMAIN
        assert body["repository"] == "github.com/acme/shop"
        assert body["source_is_ssh"] is True
        assert body["key"] is None

    def test_an_unknown_application_is_404(self, seeded: App, plain: TestClient) -> None:
        assert plain.get("/api/apps/nobody.example.com/deploy-key").status_code == 404

    def test_a_domain_that_is_not_one_is_refused(self, seeded: App, plain: TestClient) -> None:
        assert plain.get("/api/apps/not_a_domain/deploy-key").status_code in (400, 404, 422)

    def test_it_needs_a_session(self, seeded: App, app: FastAPI) -> None:
        anonymous = TestClient(app, client=("testclient", 50001))
        assert anonymous.get(URL).status_code in (401, 403)


class TestCreate:
    """Generate or assign."""

    def test_generating_answers_with_the_public_key_only(
        self, seeded: App, plain: TestClient
    ) -> None:
        response = plain.post(URL, json={})
        assert response.status_code == 201, response.text
        key = response.json()["key"]
        assert key["public_key"].startswith("ssh-ed25519 ")
        assert key["fingerprint"].startswith("SHA256:")
        assert key["origin"] == "generated"
        assert key["applications"] == [DOMAIN]
        assert "PRIVATE" not in response.text
        assert "seed=" not in response.text

    def test_a_name_may_be_chosen(self, seeded: App, plain: TestClient) -> None:
        assert plain.post(URL, json={"name": "mine"}).json()["key"]["name"] == "mine"

    def test_a_second_key_for_the_same_repository_is_a_refusal_that_says_why(
        self, seeded: App, plain: TestClient
    ) -> None:
        plain.post(URL, json={})
        response = plain.post(URL, json={"name": "other"})
        assert response.status_code == 400
        assert "already has the deploy key" in response.text

    def test_an_existing_key_is_assigned(
        self, seeded: App, plain: TestClient, store: NoustStore
    ) -> None:
        store.create_app(
            App(domain="blog.example.com", app_type="nodejs", source="git@github.com:acme/blog.git")
        )
        made = plain.post("/api/apps/blog.example.com/deploy-key", json={}).json()["key"]
        response = plain.post(URL, json={"key": made["name"]})
        assert response.status_code == 201, response.text
        assert response.json()["key"]["repositories"] == [
            "github.com/acme/blog",
            "github.com/acme/shop",
        ]

    def test_a_key_that_does_not_exist_cannot_be_assigned(
        self, seeded: App, plain: TestClient
    ) -> None:
        response = plain.post(URL, json={"key": "ghost"})
        assert response.status_code == 400
        assert "no deploy key named ghost" in response.text

    def test_an_https_application_is_warned(
        self, seeded: App, plain: TestClient, store: NoustStore
    ) -> None:
        store.create_app(
            App(domain="web.example.com", app_type="nodejs", source="https://github.com/acme/web")
        )
        body = plain.post("/api/apps/web.example.com/deploy-key", json={}).json()
        assert body["ssh_url"] == "git@github.com:acme/web.git"
        assert body["warnings"]


class TestImport:
    """A private key comes in, and only in sudo mode."""

    def test_it_needs_sudo_mode(self, seeded: App, plain: TestClient) -> None:
        response = plain.post(f"{URL}/import", json={"private_key": private_key_text(2)})
        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_it_is_derived_and_never_echoed(self, seeded: App, admin: TestClient) -> None:
        response = admin.post(f"{URL}/import", json={"private_key": private_key_text(2)})
        assert response.status_code == 201, response.text
        assert response.json()["key"]["origin"] == "imported"
        assert "seed=2" not in response.text

    def test_a_key_with_a_passphrase_is_refused_with_the_fix(
        self, seeded: App, admin: TestClient
    ) -> None:
        response = admin.post(
            f"{URL}/import", json={"private_key": private_key_text(2, marker="PASSPHRASE")}
        )
        assert response.status_code == 400
        assert "passphrase" in response.text
        assert "seed=2" not in response.text

    def test_garbage_is_refused(self, seeded: App, admin: TestClient) -> None:
        response = admin.post(f"{URL}/import", json={"private_key": "hello"})
        assert response.status_code == 400


class TestRotate:
    """Start, test, cancel."""

    def test_start_returns_the_waiting_key(self, seeded: App, plain: TestClient) -> None:
        old = plain.post(URL, json={}).json()["key"]
        response = plain.post(f"{URL}/rotate")
        assert response.status_code == 200, response.text
        key = response.json()["key"]
        assert key["public_key"] == old["public_key"]
        assert key["pending"]["public_key"] != old["public_key"]
        assert plain.post(f"{URL}/rotate").json()["key"]["pending"] == key["pending"]

    def test_rotating_without_a_key_is_a_refusal_that_says_how_to_make_one(
        self, seeded: App, plain: TestClient
    ) -> None:
        response = plain.post(f"{URL}/rotate")
        assert response.status_code == 400
        assert "deploy-key generate" in response.text

    def test_cancel_forgets_the_waiting_key(self, seeded: App, plain: TestClient) -> None:
        plain.post(URL, json={})
        plain.post(f"{URL}/rotate")
        response = plain.delete(f"{URL}/rotate")
        assert response.status_code == 200
        assert response.json()["key"]["pending"] is None

    def test_test_completes_the_rotation(self, seeded: App, plain: TestClient) -> None:
        plain.post(URL, json={})
        plain.post(f"{URL}/rotate")

        def respond(argv, env):
            command = env.get("GIT_SSH_COMMAND", "") + " "
            if "/next_key " in command:
                return 0, f"{'a' * 40}\trefs/heads/main\n", ""
            return 128, "", "Permission denied (publickey)."

        set_runner(GitScript(respond))
        response = plain.post(f"{URL}/test")
        assert response.status_code == 200, response.text
        assert "new key works" in response.json()["message"]
        assert plain.get(URL).json()["key"]["pending"] is None

    def test_a_refused_key_is_an_error_with_git_s_words(
        self, seeded: App, plain: TestClient
    ) -> None:
        plain.post(URL, json={})
        set_runner(
            GitScript(lambda argv, env: (128, "", "git@github.com: Permission denied (publickey)."))
        )
        response = plain.post(f"{URL}/test")
        assert response.status_code >= 400
        assert "credentials were refused" in response.text


class TestDelete:
    """Removing a key is a sudo-mode action."""

    def test_it_needs_sudo_mode(self, seeded: App, plain: TestClient) -> None:
        plain.post(URL, json={})
        assert plain.delete(URL).status_code == 403
        assert plain.get(URL).json()["key"] is not None

    def test_it_removes_the_key(self, seeded: App, plain: TestClient, admin: TestClient) -> None:
        plain.post(URL, json={})
        response = admin.delete(URL)
        assert response.status_code == 200, response.text
        assert response.json()["key"] is None
        assert admin.get("/api/deploy-keys").json()["total"] == 0


class TestServerKey:
    """The public half of the key git signs in with before an application has one of its own."""

    def test_it_is_the_public_line_and_its_fingerprint(
        self, plain: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        line = ed25519_line(77, "root@server")
        monkeypatch.setattr(
            "noust.managers.source_keys.ssh_key_exists", lambda: (True, tmp_path / "id_ed25519")
        )
        monkeypatch.setattr("noust.managers.source_keys.get_public_key", lambda path: line)
        response = plain.get("/api/deploy-keys/server")
        assert response.status_code == 200, response.text
        assert response.json() == {"public_key": line, "fingerprint": fingerprint(line)}
        assert "PRIVATE" not in response.text

    def test_a_server_without_a_key_says_so_with_nothing(
        self, plain: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.managers.source_keys.ssh_key_exists", lambda: (False, None))
        response = plain.get("/api/deploy-keys/server")
        assert response.status_code == 200, response.text
        assert response.json() == {"public_key": None, "fingerprint": None}

    def test_it_needs_a_session(self, app: FastAPI) -> None:
        anonymous = TestClient(app, client=("testclient", 50002))
        assert anonymous.get("/api/deploy-keys/server").status_code in (401, 403)


class TestList:
    """Every key, and the pinned host keys."""

    def test_list(self, seeded: App, plain: TestClient) -> None:
        created = plain.post(URL, json={})
        assert created.status_code == 201, created.text
        made = created.json()["key"]
        body = plain.get("/api/deploy-keys").json()
        assert body["total"] == 1
        assert body["keys"][0]["name"] == made["name"]
        assert "PRIVATE" not in str(body)

    def test_hosts_are_empty_before_anything_is_pinned(
        self, seeded: App, plain: TestClient
    ) -> None:
        assert plain.get("/api/deploy-keys/hosts").json() == {"hosts": [], "total": 0}

    def test_forgetting_a_host_needs_sudo_mode(self, seeded: App, plain: TestClient) -> None:
        assert plain.delete("/api/deploy-keys/hosts/github.com").status_code == 403

    def test_forgetting_a_host_that_was_never_pinned_says_so(
        self, seeded: App, admin: TestClient
    ) -> None:
        response = admin.delete("/api/deploy-keys/hosts/github.com")
        assert response.status_code == 200, response.text
        assert response.json() == {"host": "github.com", "forgotten": False}

    def test_a_host_that_is_an_option_is_refused(self, seeded: App, admin: TestClient) -> None:
        assert admin.delete("/api/deploy-keys/hosts/-oProxyCommand=x").status_code == 400

    def test_a_host_is_forgotten_by_the_name_the_list_gave_it(
        self, seeded: App, admin: TestClient, keygen: DeployKeyRunner
    ) -> None:
        """The list says `[host]:port` for a host on another port; the delete takes it back."""
        manager = DeployKeyManager()
        known_hosts = manager.known_hosts_path()
        known_hosts.parent.mkdir(parents=True, exist_ok=True)
        known_hosts.write_text("[git.example.com]:2222 ssh-ed25519 AAAA\n")
        keygen.script(
            ["ssh-keygen", "-l"], stdout="256 SHA256:abc [git.example.com]:2222 (ED25519)\n"
        )
        (listed,) = admin.get("/api/deploy-keys/hosts").json()["hosts"]
        assert listed["host"] == "[git.example.com]:2222"

        response = admin.delete(f"/api/deploy-keys/hosts/{listed['host']}")

        assert response.status_code == 200, response.text
        assert response.json() == {"host": "[git.example.com]:2222", "forgotten": True}
        assert known_hosts.read_text() == ""
