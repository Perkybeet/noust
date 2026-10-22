# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
TLS for the engines Noust runs (3.4, C-25).

The machine is :class:`~tests.database_instance_support.DockerHost` with an openssl in
front of it, so the certificate is really asked for, written where the container reads
it, and mounted by the Compose file *on disk*. What is defended:

- **a mode that cannot be had is refused before anything changes**, naming why;
- **turning it on is a restart behind the readiness gate**: when the engine does not
  come up the previous file is back, and nothing half-done is left in the instance's
  directory;
- **the key is never on a command line** and is written readable by the engine's group
  and no one else's;
- **the certificate is the authority's, for the names the instance is reached by**, and
  renewing it is the same restart.
"""

from __future__ import annotations

import stat
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.db_instance_store import READY, UPGRADING, DbInstanceStore
from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.core.fs import RealFileSystem
from noust.core.runner import CommandResult, set_runner
from noust.managers.database import instance_layout as layout
from noust.managers.database import tls_authority
from noust.managers.database.instance_tls import InstanceTls
from noust.managers.database.tls_authority import TlsAuthority
from tests.database_instance_support import DockerHost, World, build_world, close_world

GIB = 1024**3


class UnprivilegedFs(RealFileSystem):
    """The real filesystem without the chown a test is not root to make."""

    def write_text(self, path, content, *, mode=0o644, owner=None):  # type: ignore[no-untyped-def]
        super().write_text(path, content, mode=mode)


class TlsHost(DockerHost):
    """A Docker host that also answers openssl, with certificates that say what they are."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.issued = 0
        self.not_after = datetime.now(timezone.utc) + timedelta(days=825)

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:  # type: ignore[override]
        command = tuple(argv)
        if command[:1] == ("openssl",):
            super().run(argv, **kwargs)  # recorded like every other call
            return self._openssl(command, str(kwargs.get("input") or ""))
        return super().run(argv, **kwargs)

    def _openssl(self, command: tuple[str, ...], stdin: str) -> CommandResult:
        def ok(stdout: str) -> CommandResult:
            return CommandResult(argv=command, exit_code=0, stdout=stdout)

        self.issued += 1
        key = f"-----BEGIN PRIVATE KEY-----\nKEY{self.issued}\n-----END PRIVATE KEY-----\n"
        if command[1:3] == ("req", "-x509"):
            cert = f"-----BEGIN CERTIFICATE-----\nCA{self.issued}\n-----END CERTIFICATE-----\n"
            return ok(key + cert)
        if command[1:3] == ("req", "-new"):
            csr = (
                "-----BEGIN CERTIFICATE REQUEST-----\n"
                f"CSR{self.issued}\n-----END CERTIFICATE REQUEST-----\n"
            )
            return ok(key + csr)
        if command[1:3] == ("x509", "-req"):
            assert "CERTIFICATE REQUEST" in stdin, "the request goes on stdin"
            cert = f"-----BEGIN CERTIFICATE-----\nCERT{self.issued}\n-----END CERTIFICATE-----\n"
            return ok(cert)
        if command[1:3] == ("x509", "-in"):
            return ok(
                "subject=CN = pg16\n"
                "issuer=CN = Noust database CA on testhost\n"
                "notBefore=Oct 13 10:00:00 2026 GMT\n"
                f"notAfter={self.not_after.strftime('%b %d %H:%M:%S %Y')} GMT\n"
                "sha256 Fingerprint=AB:CD:EF\n"
                "X509v3 Subject Alternative Name: \n"
                "    DNS:localhost, IP Address:127.0.0.1, DNS:pg16\n"
            )
        return ok("")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    host = TlsHost(tmp_path)
    set_runner(host)
    built.host = host
    monkeypatch.setattr(tls_authority, "authority_dir", lambda: tmp_path / "etc" / "noust" / "tls")
    built.managed._fs = UnprivilegedFs()
    yield built
    close_world()


@pytest.fixture
def tls(world: World) -> InstanceTls:
    world.managed.create("pg16", "postgresql", version="16", memory="1g")
    return InstanceTls(world.managed, TlsAuthority(fs=UnprivilegedFs()))


def compose_of(name: str) -> dict[str, Any]:
    return yaml.safe_load(layout.compose_path(name).read_text())


def service_of(name: str) -> dict[str, Any]:
    return compose_of(name)["services"]["db"]


class TestWhatCannotBeHad:
    def test_a_mode_that_is_not_one_is_refused(self, tls: InstanceTls) -> None:
        with pytest.raises(ValidationError):
            tls.plan("pg16", "mandatory")

    def test_redis_serves_it_on_its_one_port_and_not_beside_a_plain_one(self, world: World) -> None:
        world.host.exec_stdout = ""
        world.managed.create("cache", "redis", version="8.0", memory="256m")
        engine = InstanceTls(world.managed, TlsAuthority(fs=UnprivilegedFs()))

        assert engine.plan("cache", "required").blockers == []
        plan = engine.plan("cache", "optional")

        assert plan.blockers and "port of their own" in plan.blockers[0]
        with pytest.raises(ValidationError) as caught:
            engine.set_mode("cache", "optional")
        assert caught.value.field == "mode"

    def test_mongodb_serves_it_to_those_who_ask_but_cannot_refuse_the_rest(
        self, world: World
    ) -> None:
        world.host.exec_stdout = '{"databases": []}'
        world.managed.create("docs", "mongodb", version="8.0", memory="1g")
        engine = InstanceTls(world.managed, TlsAuthority(fs=UnprivilegedFs()))

        assert engine.plan("docs", "optional").blockers == []
        assert "not refuse the rest" in engine.plan("docs", "required").blockers[0]

    def test_an_old_mariadb_cannot_refuse_plaintext_for_the_whole_server(
        self, world: World
    ) -> None:
        world.host.exec_stdout = ""
        world.managed.create("maria", "mariadb", version="10.11", memory="512m")
        engine = InstanceTls(world.managed, TlsAuthority(fs=UnprivilegedFs()))

        assert engine.plan("maria", "optional").blockers == []
        assert "from 11.4" in engine.plan("maria", "required").blockers[0]

    def test_planning_changes_nothing_and_says_what_restarts(
        self, tls: InstanceTls, world: World
    ) -> None:
        before = layout.compose_path("pg16").read_text()

        plan = tls.plan("pg16", "required")

        assert plan.restarts and plan.blockers == [] and "localhost" in plan.names
        assert any("refused" in warning for warning in plan.warnings)
        assert layout.compose_path("pg16").read_text() == before
        assert not layout.tls_directory("pg16").exists()


class TestTurningItOn:
    def test_optional_writes_the_files_and_mounts_them_read_only(
        self, tls: InstanceTls, world: World
    ) -> None:
        status = tls.set_mode("pg16", "optional")

        directory = layout.tls_directory("pg16")
        assert sorted(item.name for item in directory.iterdir()) == [
            "ca.crt",
            "server.crt",
            "server.key",
        ]
        assert "KEY" in (directory / "server.key").read_text()
        service = service_of("pg16")
        assert "./tls:/etc/noust-tls:ro" in service["volumes"]
        assert service["command"][:3] == ["postgres", "-c", "shared_buffers=256MB"]
        assert "ssl=on" in service[
            "command"
        ] and "hba_file=/etc/noust-tls/pg_hba.conf" not in " ".join(service["command"])
        assert status["mode"] == "optional"
        assert DbInstanceStore(world.store).get("pg16").tls_mode == "optional"

    def test_required_refuses_plaintext_from_outside_the_container(self, tls: InstanceTls) -> None:
        tls.set_mode("pg16", "required")

        hba = (layout.tls_directory("pg16") / "pg_hba.conf").read_text()
        assert "hostssl all all all scram-sha-256" in hba
        assert "host all all all reject" in hba
        assert "local all all trust" in hba, "Noust's own client reaches it by the local socket"
        assert "hba_file=/etc/noust-tls/pg_hba.conf" in service_of("pg16")["command"]

    def test_mysql_gets_its_ssl_arguments_and_required_gets_secure_transport(
        self, world: World
    ) -> None:
        world.host.exec_stdout = ""
        world.managed.create("my", "mysql", version="8.4", memory="1g")
        engine = InstanceTls(world.managed, TlsAuthority(fs=UnprivilegedFs()))

        engine.set_mode("my", "required")

        command = service_of("my")["command"]
        assert "--ssl-ca=/etc/noust-tls/ca.crt" in command
        assert "--require-secure-transport=ON" in command

    def test_mongodb_gets_a_combined_pem_and_prefers_tls(self, world: World) -> None:
        world.host.exec_stdout = '{"databases": []}'
        world.managed.create("docs", "mongodb", version="8.0", memory="1g")
        engine = InstanceTls(world.managed, TlsAuthority(fs=UnprivilegedFs()))

        engine.set_mode("docs", "optional")

        pem = (layout.tls_directory("docs") / "server.pem").read_text()
        assert "PRIVATE KEY" in pem and "CERTIFICATE" in pem
        command = service_of("docs")["command"]
        assert command[command.index("--tlsMode") + 1] == "preferTLS"

    def test_the_engine_is_restarted_on_the_new_file(self, tls: InstanceTls, world: World) -> None:
        started = len(world.host.started)

        tls.set_mode("pg16", "optional")

        assert world.host.started[started:] == ["noust-db-pg16"]
        assert (
            world.host.containers["noust-db-pg16"]["Config"]["Cmd"] == service_of("pg16")["command"]
        )

    def test_the_authority_is_made_once_and_its_certificate_is_public(
        self, tls: InstanceTls, world: World, tmp_path: Path
    ) -> None:
        tls.set_mode("pg16", "optional")
        first = (tmp_path / "etc/noust/tls/ca.crt").read_text()
        tls.set_mode("pg16", "required")

        assert (tmp_path / "etc/noust/tls/ca.crt").read_text() == first
        key = tmp_path / "etc/noust/tls/ca.key"
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "etc/noust/tls/ca.crt").stat().st_mode) == 0o644

    def test_no_key_is_on_any_command_line(self, tls: InstanceTls, world: World) -> None:
        tls.set_mode("pg16", "optional")

        for call in world.host.calls:
            for part in call:
                assert "PRIVATE KEY" not in part and "KEY1" not in part, call

    def test_the_certificate_is_signed_for_the_names_it_is_reached_by(
        self, tls: InstanceTls, world: World
    ) -> None:
        tls.set_mode("pg16", "optional")

        signing = next(call for call in world.host.calls if call[:3] == ("openssl", "x509", "-req"))
        assert "-CA" in signing and "-extfile" in signing
        status = tls.status("pg16")
        assert status["certificate"]["names"] == ["localhost", "127.0.0.1", "pg16"]
        assert status["ca"]["path"].endswith("/etc/noust/tls/ca.crt")
        assert (
            status["certificate"]["days_left"] > 800 and status["certificate"]["expiring"] is False
        )


class TestWhenItDoesNotComeUp:
    def test_the_previous_file_is_back_and_nothing_half_done_stays(
        self, tls: InstanceTls, world: World
    ) -> None:
        before = layout.compose_path("pg16").read_text()
        world.host.health["noust-db-pg16"] = ["unhealthy", "healthy"]

        with pytest.raises(DatabaseEngineError) as caught:
            tls.set_mode("pg16", "optional")

        assert "runs as it did before" in caught.value.message
        assert layout.compose_path("pg16").read_text() == before
        assert not layout.tls_directory("pg16").exists()
        row = DbInstanceStore(world.store).get("pg16")
        assert (row.tls_mode, row.state) == ("off", READY)

    def test_a_failure_to_put_it_back_says_where_it_stands(
        self, tls: InstanceTls, world: World
    ) -> None:
        world.host.health["noust-db-pg16"] = ["unhealthy", "unhealthy"]

        with pytest.raises(DatabaseEngineError) as caught:
            tls.set_mode("pg16", "optional")

        assert "could not be put back" in caught.value.message
        assert DbInstanceStore(world.store).get("pg16").state == UPGRADING


class TestTurningItOff:
    def test_the_files_go_and_the_file_is_the_plain_one_again(
        self, tls: InstanceTls, world: World
    ) -> None:
        plain = layout.compose_path("pg16").read_text()
        tls.set_mode("pg16", "required")

        tls.set_mode("pg16", "off")

        assert layout.compose_path("pg16").read_text() == plain
        assert not layout.tls_directory("pg16").exists()
        assert DbInstanceStore(world.store).get("pg16").tls_mode == "off"

    def test_asking_for_the_mode_it_has_changes_nothing(
        self, tls: InstanceTls, world: World
    ) -> None:
        tls.set_mode("pg16", "optional")
        started = len(world.host.started)

        tls.set_mode("pg16", "optional")

        assert len(world.host.started) == started


class TestRenewing:
    def test_a_new_certificate_is_written_and_the_engine_restarted_on_it(
        self, tls: InstanceTls, world: World
    ) -> None:
        tls.set_mode("pg16", "optional")
        before = (layout.tls_directory("pg16") / "server.crt").read_text()
        started = len(world.host.started)

        tls.renew("pg16")

        assert (layout.tls_directory("pg16") / "server.crt").read_text() != before
        assert len(world.host.started) == started + 1

    def test_an_instance_without_tls_has_nothing_to_renew(self, tls: InstanceTls) -> None:
        with pytest.raises(DatabaseEngineError) as caught:
            tls.renew("pg16")

        assert "turn it on first" in caught.value.details.lower()

    def test_an_expiring_certificate_is_said_to_be(self, tls: InstanceTls, world: World) -> None:
        tls.set_mode("pg16", "optional")
        world.host.not_after = datetime.now(timezone.utc) + timedelta(days=12)

        status = tls.status("pg16")

        assert (
            status["certificate"]["expiring"] is True and status["certificate"]["days_left"] <= 12
        )
