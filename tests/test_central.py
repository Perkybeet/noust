"""
The central: its role, its first start, who may reach it, and unlocking it.

- A hub refuses local deployments with a message, at the one place every CLI
  command is resolved, and keeps the console, tokens and 2FA.
- ``noust central run`` prints the sign-in token on the first start and
  never again: the second start finds the hash and issues nothing.
- The console of a central answers loopback and the private networks by
  default, never "anyone" by omission.
- A running central is unlocked over a 0600 UNIX socket by its own user.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust import central
from noust.central import setup
from noust.central import unlock as unlock_module
from noust.central.unlock import UnlockServer, UnlockUnavailableError, handle_request, send_request
from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.core import sealing
from noust.core.config import Config
from noust.core.exceptions import ConfigError
from noust.core.net import ALL_INTERFACES
from noust.core.runner import FakeRunner

PASSPHRASE = "correct horse battery staple"


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A configuration read from an empty file in the test's directory."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_ALLOW_IP", raising=False)
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make this Noust a hub, the way the container image does."""
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    Config.reset_instance()


@pytest.fixture
def short_dir() -> Iterator[Path]:
    """A directory short enough for a UNIX socket path."""
    directory = Path(tempfile.mkdtemp(prefix="nst", dir="/tmp"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


class TestRole:
    def test_a_noust_is_a_server_by_default(self) -> None:
        assert central.role() == "server"
        assert not central.is_hub()
        central.require_server_role("Applications")

    def test_the_environment_makes_it_a_hub(self, hub: None) -> None:
        assert central.role() == "hub"
        assert central.is_hub()

    def test_a_hub_refuses_local_deployments(self, hub: None) -> None:
        with pytest.raises(central.RoleError, match="Applications: not available"):
            central.require_server_role("Applications")

    def test_an_unknown_role_is_an_error_not_a_guess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "satellite")
        Config.reset_instance()
        with pytest.raises(ConfigError, match=r"central\.role"):
            central.role()

    def test_config_set_validates_the_role(self) -> None:
        with pytest.raises(ConfigError, match=r"central\.role"):
            Config().set("central.role", "satellite")
        Config().set("central.role", "HUB")
        assert Config().get("central.role") == "hub"

    def test_the_payload_the_console_reads(self, hub: None) -> None:
        assert central.central_info() == {"role": "hub", "sealed": False, "locked": False}


class TestHubCommands:
    @pytest.mark.parametrize("argv", [["list"], ["create", "--help"], ["cert", "list"], ["site"]])
    def test_a_hub_refuses_them_with_a_message(
        self, hub: None, argv: list[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(argv) == 1
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "not available on this central, which is a hub" in output

    def test_a_hub_keeps_its_own_commands(
        self, hub: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_WEB_STATE_DIR", str(tmp_path / "console"))
        result = CliRunner().invoke(root_cli, ["2fa", "status"])
        assert result.exit_code == 0, result.output

    def test_a_server_does_not_refuse_them(self) -> None:
        central.refuse_command_on_hub("list")
        central.refuse_command_on_hub("cert")

    def test_every_refused_command_exists(self) -> None:
        from noust.cli.app import COMMAND_MODULES, WEBAPP_COMMANDS

        known = {*COMMAND_MODULES, *WEBAPP_COMMANDS}
        assert set(central.HUB_REFUSED_COMMANDS) <= known
        for kept in ("2fa", "config", "token", "sessions", "web", "central", "notify"):
            assert kept not in central.HUB_REFUSED_COMMANDS


class TestAllowlist:
    def test_the_private_networks_by_default(self) -> None:
        from noust.web.auth import ip_matches

        allowed = setup.allowlist(environ={})
        assert allowed == list(setup.DEFAULT_ALLOWLIST)
        for inside in ("127.0.0.1", "::1", "10.1.2.3", "172.20.0.1", "192.168.1.20", "fd12::1"):
            assert ip_matches(inside, allowed), inside
        for outside in ("8.8.8.8", "172.32.0.1", "2001:db8::1", "100.64.0.1"):
            assert not ip_matches(outside, allowed), outside

    def test_config_yaml_wins_over_the_default(self) -> None:
        Config().set("web.ip_whitelist", ["192.168.7.0/24"])
        assert setup.allowlist(environ={}) == ["192.168.7.0/24"]

    def test_the_environment_wins_over_both(self) -> None:
        Config().set("web.ip_whitelist", ["192.168.7.0/24"])
        env = {"NOUST_ALLOW_IP": "10.0.0.0/8, 192.168.1.0/24  fd00::/8"}
        assert setup.allowlist(environ=env) == ["10.0.0.0/8", "192.168.1.0/24", "fd00::/8"]


class TestTlsPair:
    def test_none_means_self_signed(self) -> None:
        assert setup.operator_tls_pair({}) is None

    def test_both_are_used(self) -> None:
        env = {"NOUST_TLS_CERT": "/data/a.crt", "NOUST_TLS_KEY": "/data/a.key"}
        assert setup.operator_tls_pair(env) == ("/data/a.crt", "/data/a.key")

    def test_one_alone_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="travel together"):
            setup.operator_tls_pair({"NOUST_TLS_CERT": "/data/a.crt"})

    def test_the_fingerprint_is_sha256_of_the_der(self, tmp_path: Path) -> None:
        import base64
        import hashlib

        der = b"not really a certificate, but DER bytes all the same"
        pem = tmp_path / "c.pem"
        body = base64.b64encode(der).decode()
        pem.write_text(f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n")

        fingerprint = setup.certificate_fingerprint(pem)
        assert fingerprint is not None
        assert fingerprint.replace(":", "") == hashlib.sha256(der).hexdigest().upper()
        assert setup.certificate_fingerprint(tmp_path / "missing.pem") is None


class TestDataDir:
    def test_creates_every_directory_owner_only(self, tmp_path: Path) -> None:
        layout = [tmp_path / "data" / "config", tmp_path / "data" / "state"]
        (tmp_path / "data").mkdir()
        setup.ensure_data_dir(layout, root=tmp_path / "data")
        for directory in layout:
            assert directory.is_dir()
            assert directory.stat().st_mode & 0o777 == 0o700

    @pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
    def test_an_unwritable_data_dir_says_how_to_fix_it(self, tmp_path: Path) -> None:
        root = tmp_path / "data"
        root.mkdir(mode=0o500)
        try:
            with pytest.raises(ConfigError, match="not writable") as caught:
                setup.ensure_data_dir([root / "state"], root=root)
            assert "chown" in caught.value.details
        finally:
            root.chmod(0o700)


class TestCountNodes:
    def test_no_store(self, tmp_path: Path) -> None:
        assert setup.count_nodes(tmp_path / "none.db") is None

    def test_no_table_yet(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        sqlite3.connect(db).close()
        assert setup.count_nodes(db) is None

    def test_counts_them(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE nodes (name TEXT)")
        connection.executemany("INSERT INTO nodes VALUES (?)", [("a",), ("b",)])
        connection.commit()
        connection.close()
        assert setup.count_nodes(db) == 2


@pytest.fixture
def central_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_dir: Path) -> dict[str, Any]:
    """
    Everything `noust central run` touches, inside the test's directories,
    with the server itself replaced by a recorder.
    """
    console = tmp_path / "console"
    cert = tmp_path / "tls.crt"
    key = tmp_path / "tls.key"
    cert.write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    key.write_text("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
    monkeypatch.setenv("NOUST_WEB_STATE_DIR", str(console))
    monkeypatch.setenv("NOUST_TLS_CERT", str(cert))
    monkeypatch.setenv("NOUST_TLS_KEY", str(key))
    monkeypatch.setattr(
        setup,
        "data_directories",
        lambda: (tmp_path / "data" / "config", tmp_path / "data" / "state"),
    )
    monkeypatch.setattr(unlock_module, "socket_path", lambda: short_dir / "c.sock")
    served: list[dict[str, Any]] = []

    def fake_run_server(**kwargs: Any) -> None:
        served.append(kwargs)

    monkeypatch.setattr("noust.web.server.run_server", fake_run_server)
    return {"console": console, "served": served, "socket": short_dir / "c.sock"}


class TestRun:
    def test_the_token_is_printed_on_the_first_start_only(
        self, central_env: dict[str, Any]
    ) -> None:
        first = CliRunner().invoke(root_cli, ["central", "run"])
        assert first.exit_code == 0, first.output
        assert "noust_" in first.output
        assert "first start" in first.output

        second = CliRunner().invoke(root_cli, ["central", "run"])
        assert second.exit_code == 0, second.output
        assert "noust_" not in second.output
        assert "printed at its first start" in second.output

        # Only the hash is kept.
        stored = (central_env["console"] / "web-token").read_text()
        assert "noust_" not in stored

    def test_it_serves_tls_on_every_interface_limited_to_private_networks(
        self, central_env: dict[str, Any]
    ) -> None:
        import signal

        before = signal.getsignal(signal.SIGTERM)
        result = CliRunner().invoke(root_cli, ["central", "run"])
        assert result.exit_code == 0, result.output
        # The handler that turns uvicorn's re-raised SIGTERM into a clean exit
        # is only in place while it serves.
        assert signal.getsignal(signal.SIGTERM) is before

        (served,) = central_env["served"]
        config = served["config"]
        assert served["host"] == ALL_INTERFACES
        assert served["port"] == 8443
        assert served["show_token"] is False
        assert config.require_https
        assert config.ip_whitelist == list(setup.DEFAULT_ALLOWLIST)
        # The unlock socket was open while it served, and is gone after.
        assert not central_env["socket"].exists()

    def test_a_dry_run_serves_nothing(self, central_env: dict[str, Any]) -> None:
        result = CliRunner().invoke(root_cli, ["--dry-run", "central", "run"])
        assert result.exit_code == 0, result.output
        assert central_env["served"] == []


class TestUnlockSocket:
    @pytest.fixture
    def sealed_root(self, tmp_path: Path) -> Iterator[Path]:
        root = tmp_path / "secrets"
        # Nothing to encrypt yet, so no openssl: only the header is written.
        sealing.seal_store(root, PASSPHRASE, runner=FakeRunner())
        sealing.lock(root)
        yield root
        sealing.lock(root)

    def test_status_and_unlock(self, sealed_root: Path) -> None:
        assert handle_request(b'{"action": "status"}', sealed_root) == {
            "ok": True,
            "sealed": True,
            "locked": True,
        }
        wrong = handle_request(b'{"action": "unlock", "passphrase": "nope"}', sealed_root)
        assert wrong["ok"] is False and wrong["error"] == "Wrong passphrase"

        right = handle_request(
            f'{{"action": "unlock", "passphrase": "{PASSPHRASE}"}}'.encode(), sealed_root
        )
        assert right == {"ok": True, "sealed": True, "locked": False}

    @pytest.mark.parametrize("raw", [b"", b"not json", b"[1]", b'{"action": "rm"}'])
    def test_anything_else_is_refused(self, sealed_root: Path, raw: bytes) -> None:
        assert handle_request(raw, sealed_root)["ok"] is False

    def test_over_a_real_socket(self, sealed_root: Path, short_dir: Path) -> None:
        path = short_dir / "c.sock"
        server = UnlockServer(sealed_root, path=path)
        server.start()
        try:
            assert path.stat().st_mode & 0o777 == 0o600
            assert send_request({"action": "status"}, path)["locked"] is True
            reply = send_request({"action": "unlock", "passphrase": PASSPHRASE}, path)
            assert reply["ok"] is True
            assert sealing.is_unlocked(sealed_root)
        finally:
            server.stop()
        assert not path.exists()

    def test_no_central_running(self, short_dir: Path) -> None:
        with pytest.raises(UnlockUnavailableError, match="No running central"):
            send_request({"action": "status"}, short_dir / "none.sock")

    def test_the_cli_unlocks_the_running_central_from_stdin(
        self,
        sealed_root: Path,
        short_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = short_dir / "c.sock"
        monkeypatch.setattr(unlock_module, "socket_path", lambda: path)
        monkeypatch.setattr("noust.cli.commands.central.socket_path", lambda: path)
        monkeypatch.setattr("noust.cli.commands.central.secrets_dir", lambda: sealed_root)
        monkeypatch.setattr(
            "noust.cli.commands.central.send_request",
            lambda request: send_request(request, path),
        )
        server = UnlockServer(sealed_root, path=path)
        server.start()
        try:
            wrong = CliRunner().invoke(root_cli, ["central", "unlock"], input="nope\n")
            assert wrong.exit_code != 0
            assert isinstance(wrong.exception, sealing.SealError)
            assert wrong.exception.message == "Wrong passphrase"
            assert not sealing.is_unlocked(sealed_root)

            result = CliRunner().invoke(root_cli, ["central", "unlock"], input=PASSPHRASE + "\n")
            assert result.exit_code == 0, result.output
            assert "Unlocked" in result.output
            assert sealing.is_unlocked(sealed_root)
        finally:
            server.stop()
