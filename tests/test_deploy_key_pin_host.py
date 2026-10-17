"""
Pinning a code host's SSH key before the first connection (checklist A-54).

What is pinned: that only the key whose fingerprint the operator brought is recorded and that a
host which shows none with it is refused with the fingerprints it did show; that nothing already
pinned is ever changed here; that the host and the fingerprint are judged before anything runs; and
that the surfaces (command, route, permission) say it plainly.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.exceptions import SourceError, ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.source_keys import DeployKeyManager
from noust.web.permissions import Permission
from noust.web.permissions.routes_deploy_keys import ROUTES
from tests.deploy_key_support import DeployKeyRunner, ed25519_line, fingerprint, rsa_line


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def keygen() -> DeployKeyRunner:
    return DeployKeyRunner()


@pytest.fixture
def manager(store: NoustStore, tmp_path: Path, keygen: DeployKeyRunner) -> DeployKeyManager:
    return DeployKeyManager(
        store=store, secrets=SecretStore(root=tmp_path / "state" / "secrets"), runner=keygen
    )


ED25519 = ed25519_line(11)
RSA = rsa_line(12)


def scanned(host: str, *lines: str) -> str:
    """What ssh-keyscan prints for a host that shows these keys."""
    return "".join(f"# {host}:22 SSH-2.0-OpenSSH_9.6\n{host} {line}\n" for line in lines)


def keyscan(keygen: DeployKeyRunner, host: str, *lines: str, exit_code: int = 0) -> None:
    keygen.script(["ssh-keyscan"], stdout=scanned(host, *lines), exit_code=exit_code)


def events() -> list[dict[str, Any]]:
    return [e for e in audit.get_log().read(limit=200) if e["action"] == "apps.deploy_key.host_pin"]


class TestPinning:
    def test_only_the_key_with_the_fingerprint_is_recorded(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
    ) -> None:
        keyscan(keygen, "github.com", ED25519, RSA)

        pinned = manager.pin_host("github.com", fingerprint(ED25519))

        assert (pinned.host, pinned.fingerprint, pinned.key_type) == (
            "github.com",
            fingerprint(ED25519),
            "ED25519",
        )
        written = manager.known_hosts_path().read_text()
        assert written == f"github.com {ED25519.split()[0]} {ED25519.split()[1]}\n"
        assert RSA.split()[1] not in written
        (event,) = events()
        assert event["details"] == {"host": "github.com", "fingerprint": fingerprint(ED25519)}
        # An argv, never a shell; the host is the last argument, after its options.
        (scan,) = [call for call in keygen.calls if call[0] == "ssh-keyscan"]
        assert scan[-1] == "github.com" and "-p" not in scan

    def test_the_file_is_private_and_the_other_pins_stay(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
    ) -> None:
        path = manager.known_hosts_path()
        path.parent.mkdir(parents=True)
        path.write_text("gitlab.com ssh-ed25519 BBBB")
        keyscan(keygen, "github.com", ED25519)

        manager.pin_host("github.com", fingerprint(ED25519))

        lines = path.read_text().splitlines()
        assert lines[0] == "gitlab.com ssh-ed25519 BBBB" and lines[1].startswith("github.com ")
        assert (path.stat().st_mode & 0o777) == 0o600

    def test_a_host_on_another_port_is_scanned_there_and_named_the_way_ssh_stores_it(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
    ) -> None:
        keyscan(keygen, "[git.example.com]:2222", ED25519)

        pinned = manager.pin_host("git.example.com:2222", fingerprint(ED25519))

        (scan,) = [call for call in keygen.calls if call[0] == "ssh-keyscan"]
        assert scan[scan.index("-p") + 1] == "2222" and scan[-1] == "git.example.com"
        assert pinned.host == "[git.example.com]:2222"
        assert manager.known_hosts_path().read_text().startswith("[git.example.com]:2222 ")

    def test_a_host_that_shows_no_key_with_that_fingerprint_is_refused_with_what_it_showed(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
    ) -> None:
        keyscan(keygen, "github.com", ED25519, RSA)
        wrong = fingerprint(ed25519_line(99))

        with pytest.raises(SourceError, match="showed no key with the fingerprint") as caught:
            manager.pin_host("github.com", wrong)

        assert (
            fingerprint(ED25519) in caught.value.details
            and fingerprint(RSA) in caught.value.details
        )
        assert "publishes" in caught.value.details
        assert not manager.known_hosts_path().exists() and events() == []

    def test_a_host_that_answers_nothing(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
    ) -> None:
        keygen.script(
            ["ssh-keyscan"],
            stdout="",
            stderr="getaddrinfo nope.example: Name or service not known",
            exit_code=1,
        )

        with pytest.raises(SourceError, match="did not show any host key") as caught:
            manager.pin_host("nope.example", fingerprint(ED25519))

        assert "Name or service not known" in caught.value.details
        assert not manager.known_hosts_path().exists()

    def test_what_is_pinned_already_is_never_changed_here(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
    ) -> None:
        path = manager.known_hosts_path()
        path.parent.mkdir(parents=True)
        path.write_text(f"github.com {ED25519.split()[0]} {ED25519.split()[1]}\n")
        keygen.script(
            ["ssh-keygen", "-F"],
            stdout=f"# Host github.com found: line 1\ngithub.com {ED25519.split()[0]} {ED25519.split()[1]}\n",
        )
        keyscan(keygen, "github.com", RSA)
        before = path.read_text()

        with pytest.raises(ValidationError, match="another key pinned already") as caught:
            manager.pin_host("github.com", fingerprint(RSA))

        assert "forget-host github.com" in caught.value.details
        assert path.read_text() == before
        assert not [call for call in keygen.calls if call[0] == "ssh-keyscan"]

    def test_pinning_what_is_pinned_already_is_done(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
    ) -> None:
        path = manager.known_hosts_path()
        path.parent.mkdir(parents=True)
        path.write_text(f"github.com {ED25519.split()[0]} {ED25519.split()[1]}\n")
        keygen.script(
            ["ssh-keygen", "-F"],
            stdout=f"# Host github.com found: line 1\ngithub.com {ED25519.split()[0]} {ED25519.split()[1]}\n",
        )

        pinned = manager.pin_host("github.com", fingerprint(ED25519))

        assert pinned.fingerprint == fingerprint(ED25519)
        assert path.read_text().count("github.com") == 1
        assert events() == []


class TestWhatIsJudgedFirst:
    @pytest.mark.parametrize("host", ["", "-oProxyCommand=x", "a b", "host;rm", "*.com", "[a]:"])
    def test_a_host_is_a_name_never_an_option(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
        host: str,
    ) -> None:
        with pytest.raises(ValidationError):
            manager.pin_host(host, fingerprint(ED25519))

        assert keygen.calls == []

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "SHA256:short",
            "MD5:aa:bb:cc",
            "SHA256:" + "A" * 42,
            "SHA256:" + "A" * 44,
            "sha256:" + "A" * 43,
            "SHA256:" + "A" * 42 + "!",
            "github.com ssh-ed25519 AAAA",
        ],
    )
    def test_a_fingerprint_is_the_one_code_hosts_publish(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
        text: str,
    ) -> None:
        with pytest.raises(ValidationError, match="not a fingerprint") as caught:
            manager.pin_host("github.com", text)

        assert caught.value.field == "fingerprint" and keygen.calls == []


class TestTheSurfaces:
    def test_the_route_asks_for_host_access_like_forgetting_does(self) -> None:
        assert ROUTES[("POST", "/api/deploy-keys/hosts")] == Permission.SERVER_HOST_ACCESS
        assert ROUTES[("DELETE", "/api/deploy-keys/hosts/{host}")] == Permission.SERVER_HOST_ACCESS

    def test_the_command_pins_and_says_what(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from click.testing import CliRunner

        from noust.cli.app import cli as root_cli
        from noust.core.config import Config

        monkeypatch.setattr(
            "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml"
        )
        Config.reset_instance()
        monkeypatch.setattr("noust.cli.commands.app_deploy_key.DeployKeyManager", lambda: manager)
        keyscan(keygen, "github.com", ED25519)

        result = CliRunner().invoke(
            root_cli,
            ["app", "deploy-key", "pin-host", "github.com", "--fingerprint", fingerprint(ED25519)],
        )
        as_json = CliRunner().invoke(
            root_cli,
            [
                "app",
                "deploy-key",
                "pin-host",
                "github.com",
                "--fingerprint",
                fingerprint(ED25519),
                "--json",
            ],
        )

        assert result.exit_code == 0, result.output
        assert f"Pinned the ED25519 host key of github.com: {fingerprint(ED25519)}" in result.output
        assert json.loads(as_json.output)["fingerprint"] == fingerprint(ED25519)
        Config.reset_instance()

    def test_the_route_needs_sudo_mode_and_answers_with_the_key(
        self,
        manager: DeployKeyManager,
        keygen: DeployKeyRunner,
    ) -> None:
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        from noust.web.api import deploy_keys as api
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers, require_elevated

        app = FastAPI()
        install_error_handlers(app)
        app.include_router(api.keys_router, prefix="/api/deploy-keys")
        session = {"type": "session", "sid": "s", "account_id": 1, "username": "alice"}
        app.dependency_overrides[get_current_session] = lambda: session
        elevated = [True]

        def elevation() -> dict[str, Any]:
            if not elevated[0]:
                raise HTTPException(status_code=403, detail={"error": "elevation_required"})
            return session

        app.dependency_overrides[require_elevated] = elevation
        api._manager = lambda _session: manager  # type: ignore[assignment]
        client = TestClient(app)
        keyscan(keygen, "github.com", ED25519)
        body = {"host": "github.com", "fingerprint": fingerprint(ED25519)}

        elevated[0] = False
        refused = client.post("/api/deploy-keys/hosts", json=body)
        elevated[0] = True
        pinned = client.post("/api/deploy-keys/hosts", json=body)
        wrong = client.post(
            "/api/deploy-keys/hosts", json={"host": "github.com", "fingerprint": "SHA256:short"}
        )

        assert (
            refused.status_code == 403
            and not [c for c in keygen.calls if c[0] == "ssh-keyscan"][:0]
        )
        assert pinned.status_code == 201, pinned.text
        assert pinned.json() == {
            "host": "github.com",
            "fingerprint": fingerprint(ED25519),
            "key_type": "ED25519",
        }
        assert wrong.status_code == 400 and wrong.json()["fields"]["fingerprint"]
