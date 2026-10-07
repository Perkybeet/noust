"""
What the console server's modelled machine answers for what leaves the server (3.4).

``scripts/console_server.py`` is the backend the console is developed and tested against, and
the console's pages for a database opened to the network, Noust's own backup, the console
published on a name and a server enrolled from an address meet what the machine answers. What
is defended:

- **Sealed files are really sealed**: the machine's cipher is the real ``openssl enc``, so a
  bundle written in the sandbox is one a check can open, and a wrong passphrase fails.
- **A database opened to the network** finds its port listening (the socket unit it wrote is
  read back), its TLS on, and its ``pg_hba.conf`` inside the modelled host, never this machine's.
- **The console is a service** whose unit Noust rewrites, running, so publishing it has a unit
  to change.
- **An enrolled server** presents a host key that is the same for an address every time, refuses
  the one password it is told to, and hands the node's join code over with that host key.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from noust import __version__
from noust.core.runner import FakeRunner, set_runner
from noust.fleet.models import parse_public_key
from noust.managers.database import flavours
from noust.managers.database.postgres import PostgresManager
from noust.managers.server.host import HostPaths
from noust.managers.service_manager import ServiceManager

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "console_server.py"


@pytest.fixture(scope="module")
def console() -> ModuleType:
    """
    Load the script as a module: its heavy imports are lazy, so this starts nothing.

    Returns:
        The module.
    """
    spec = importlib.util.spec_from_file_location("console_server_exposure_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def machine(
    console: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeRunner]:
    """
    The modelled machine: its runner over a host tree below ``tmp_path``.

    Args:
        console: The script.
        tmp_path: The test's directory, which stands in for ``/``.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The runner, installed as the process-wide one.
    """
    host = SimpleNamespace(root=tmp_path / "host", answer=lambda args: None)
    cluster = host.root / "etc" / "postgresql" / "16" / "main"
    (cluster / "conf.d").mkdir(parents=True)
    (cluster / "postgresql.conf").write_text("include_dir = 'conf.d'\n", encoding="utf-8")
    (cluster / "pg_hba.conf").write_text(console._SERVER_PG_HBA_CONF, encoding="utf-8")
    monkeypatch.setattr(console, "_SERVER_HOST", host)
    monkeypatch.setattr(flavours, "HOST", HostPaths(root=host.root))
    monkeypatch.setattr(ServiceManager, "SYSTEMD_DIR", tmp_path / "systemd")
    (tmp_path / "systemd").mkdir()
    units = {"postgresql": console.Unit(active="active", managed=False)}
    runner = console.make_runner(units, {}, {}, [], tmp_path / "systemd")
    set_runner(runner)
    try:
        yield runner
    finally:
        set_runner(None)


class TestSealedFiles:
    """``openssl enc`` is the machine's own: what it seals it opens."""

    @pytest.mark.allow_subprocess
    @pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl client")
    def test_a_file_sealed_with_a_key_opens_with_it_and_not_with_another(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        plain = tmp_path / "state.tar"
        sealed = tmp_path / "state.tar.enc"
        opened = tmp_path / "state.out"
        plain.write_bytes(b"the store, the keys, the unit files")
        seal = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", "env:KEY"]

        done = machine.run([*seal, "-in", str(plain), "-out", str(sealed)], env={"KEY": "one"})
        assert done.success
        assert sealed.read_bytes() != plain.read_bytes()

        right = machine.run(
            [*seal, "-d", "-in", str(sealed), "-out", str(opened)], env={"KEY": "one"}
        )
        assert right.success
        assert opened.read_bytes() == plain.read_bytes()

        wrong = machine.run(
            [*seal, "-d", "-in", str(sealed), "-out", str(tmp_path / "no")], env={"KEY": "two"}
        )
        assert not wrong.success


class TestDatabaseOpenedToTheNetwork:
    """What public access asks of the engine, the kernel and the files."""

    def test_the_engine_has_tls_on_and_its_hba_file_is_inside_the_modelled_host(
        self, machine: FakeRunner, tmp_path: Path
    ) -> None:
        manager = PostgresManager()

        assert manager.remote_tls_blocker() is None
        manager.set_remote_tls("shop_remote", "shop", True)

        hba = tmp_path / "host" / "etc" / "postgresql" / "16" / "main" / "pg_hba.conf"
        assert "shop_remote" in hba.read_text(encoding="utf-8")
        manager.set_remote_tls("shop_remote", "shop", False)
        assert "shop_remote" not in hba.read_text(encoding="utf-8")

    def test_a_port_a_socket_unit_holds_is_listening(
        self, console: ModuleType, machine: FakeRunner, tmp_path: Path
    ) -> None:
        assert console._exposed_database_listeners() == []
        (tmp_path / "systemd" / "noust-dbexpose-postgresql-5433.socket").write_text(
            "[Socket]\nListenStream=0.0.0.0:5433\n", encoding="utf-8"
        )
        (tmp_path / "systemd" / "something-else.socket").write_text(
            "[Socket]\nListenStream=0.0.0.0:9999\n", encoding="utf-8"
        )

        (line,) = console._exposed_database_listeners()

        assert line.split()[3] == "0.0.0.0:5433"


class TestDestinations:
    """A remote is a directory, and what travels beside an archive is written into it."""

    def test_the_record_beside_an_archive_is_written_from_standard_input(
        self, console: ModuleType, machine: FakeRunner, tmp_path: Path
    ) -> None:
        remotes = tmp_path / "remotes"
        console._destinations_rclone_model(SimpleNamespace(systemd_dir=tmp_path), remotes)

        done = machine.run(
            ["rclone", "rcat", "offsite-sftp:noust/data/piece.tar.gz.json"],
            input='{"sha256": "abc"}',
        )

        assert done.success
        written = remotes / "offsite-sftp" / "noust" / "data" / "piece.tar.gz.json"
        assert written.read_text(encoding="utf-8") == '{"sha256": "abc"}'


class TestConsoleAsAService:
    """Publishing the console rewrites the unit that runs it."""

    def test_the_unit_is_there_running_and_names_the_port_it_listens_on(
        self, console: ModuleType, tmp_path: Path
    ) -> None:
        units: dict[str, Any] = {}

        console.seed_console_service(SimpleNamespace(systemd_dir=tmp_path), units)

        text = (tmp_path / "noust-web.service").read_text(encoding="utf-8")
        assert "ExecStart=/usr/bin/noust web start --host 127.0.0.1 --port 8080" in text
        assert text.startswith("# Generated by")
        assert units["noust-web"].active == "active"


class TestEnrolledServer:
    """A server enrolled from an address, as the central meets it."""

    def test_an_address_presents_the_same_ed25519_key_every_time(self, console: ModuleType) -> None:
        argv = ("ssh-keyscan", "-t", "ed25519", "-T", "10", "-p", "22", "203.0.113.50")

        first = console._enroll_keyscan(argv)
        again = console._enroll_keyscan(argv)
        other = console._enroll_keyscan((*argv[:-1], "203.0.113.51"))

        assert first.stdout == again.stdout
        assert first.stdout != other.stdout
        host, kind, blob = first.stdout.split()
        assert (host, kind) == ("203.0.113.50", "ssh-ed25519")
        assert parse_public_key(f"{kind} {blob}", what="host key").fingerprint.startswith("SHA256:")

    def test_the_address_that_answers_nothing_gives_no_key(self, console: ModuleType) -> None:
        silent = console._enroll_keyscan(("ssh-keyscan", "-p", "22", console.ENROLL_SILENT_HOST))

        assert silent.exit_code == 0
        assert not [line for line in silent.stdout.splitlines() if not line.startswith("#")]

    def test_the_password_it_is_told_to_refuse_is_refused_in_ssh_s_words(
        self, console: ModuleType
    ) -> None:
        argv = ("ssh", "-T", "--", "203.0.113.50", "id -u")

        refused = console._enroll_ssh(argv, {"NOUST_ENROLL_SECRET": console.ENROLL_REFUSED_LOGIN})
        accepted = console._enroll_ssh(argv, {"NOUST_ENROLL_SECRET": "another"})

        assert refused.exit_code == 255
        assert "Permission denied" in refused.stderr
        assert accepted.exit_code == 0

    def test_the_probe_says_root_and_the_version_of_noust(self, console: ModuleType) -> None:
        script = "id -u; if command -v noust >/dev/null 2>&1; then noust --version; fi"

        answer = console._enroll_ssh(("ssh", "--", "203.0.113.50", f"sh -c '{script}'"), {})

        assert answer.stdout.splitlines() == ["0", f"Noust {__version__}"]

    def test_authorizing_without_a_node_to_stand_in_fails_the_way_a_command_does(
        self, console: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(console, "_ENROLL_NODES", [])
        command = "noust fleet authorize --central-key K --name c --access admin --json"

        answer = console._enroll_ssh(("ssh", "--", "203.0.113.50", command), {})

        assert answer.exit_code == 1
        assert "no server of this sandbox can stand in" in answer.stderr
