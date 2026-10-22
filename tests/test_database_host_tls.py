# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A host engine that presents a certificate clients can verify (checklist C-27).

The engine is simulated: a runner that answers the statements and the handshake the manager
sends and keeps the little state they change (the settings, the certificate presented), so what is
pinned is what is checked before the engine is touched, what it is given, that the engine is
*asked what it presents* before anything is called done, what is undone when it presents another
certificate, and that a renewal reaches it without a restart. The same flows run against a real
PostgreSQL and MariaDB, with a client that verifies the authority and the name, in
``tests/integration/engines_run.py`` (``host_tls``).
"""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import NoustStore, get_store
from noust.managers.database import flavours
from noust.managers.database.host_tls import HostTls, leaf_fingerprint
from noust.managers.database.host_tls_records import HostTlsRecords
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.server.host import HostPaths

NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
HOST = "db.example.com"


def pem(seed: str) -> str:
    """
    A certificate as far as the manager can tell: PEM with a body that is its identity.

    Args:
        seed: Makes one certificate different from another.

    Returns:
        The PEM text.
    """
    body = base64.b64encode(seed.encode() * 8).decode()
    return f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n"


def fingerprint_of(seed: str) -> str:
    """
    Args:
        seed: As for :func:`pem`.

    Returns:
        The fingerprint of that certificate's leaf.
    """
    found = leaf_fingerprint(pem(seed))
    assert found is not None
    return found


class SimulatedEngine(FakeRunner):
    """
    A runner that is an engine with TLS, as far as the manager can tell.

    Attributes:
        settings: What ``ALTER SYSTEM`` set (PostgreSQL).
        served: The certificate the engine presents now.
        covers: The names the certificates cover.
        key_matches: Whether the key is the certificate's.
        enddate: What openssl prints as the expiry.
        running: Whether the engine runs.
        ignores: Whether the engine silently presents its old certificate (a reload that
            did nothing).
        statements: Every statement sent to the engine, in order.
        restarts: How many times the engine was restarted.
    """

    def __init__(self, engine: str, include: Path | None = None, *, mariadb: bool = False) -> None:
        """
        Args:
            engine: ``postgresql`` or ``mysql``.
            include: Where MySQL's include file is.
            mariadb: Whether the MySQL server is MariaDB (which is how Noust tells).
        """
        super().__init__()
        self.engine = engine
        self.include = include
        self.settings: dict[str, str] = {"ssl": "on"}
        self.served = pem("snakeoil")
        self.covers = {HOST}
        self.key_matches = True
        self.enddate = "Dec 31 23:59:59 2027 GMT"
        self.running = True
        self.ignores = False
        self.statements: list[str] = []
        self.restarts = 0
        self.only_knows("psql", "mysql", *(["mariadb"] if mariadb else []), "openssl", "systemctl")

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = 120,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        scripted = super().run(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            input=input,
            stdin_path=stdin_path,
            user=user,
            check=check,
            secrets=secrets,
        )
        words = tuple(argv)
        if words[:2] == ("systemctl", "is-active"):
            return CommandResult(
                argv=words,
                exit_code=0 if self.running else 3,
                stdout="active\n" if self.running else "inactive\n",
            )
        if words[:2] == ("systemctl", "restart"):
            self.restarts += 1
            self.load_files()
            return scripted
        if words[:2] == ("openssl", "x509"):
            return self.x509(words)
        if words[:2] == ("openssl", "pkey"):
            return CommandResult(argv=words, exit_code=0, stdout="PUBKEY-A\n")
        if words[:2] == ("openssl", "s_client"):
            return CommandResult(argv=words, exit_code=0, stdout=self.served)
        if input is not None and ("psql" in " ".join(words) or "mysql" in " ".join(words)):
            self.statements.append(input)
            return self.answer(words, input)
        return scripted

    def x509(self, words: tuple[str, ...]) -> CommandResult:
        """
        Answer ``openssl x509`` the way it would for the files the manager names.

        Args:
            words: The command.

        Returns:
            The result.
        """
        if "-enddate" in words:
            return CommandResult(argv=words, exit_code=0, stdout=f"notAfter={self.enddate}\n")
        if "-checkhost" in words:
            name = words[words.index("-checkhost") + 1]
            verdict = "does match" if name in self.covers else "does NOT match"
            return CommandResult(
                argv=words, exit_code=0, stdout=f"Hostname {name} {verdict} certificate\n"
            )
        if "-pubkey" in words:
            return CommandResult(
                argv=words, exit_code=0, stdout="PUBKEY-A\n" if self.key_matches else "PUBKEY-B\n"
            )
        return CommandResult(argv=words, exit_code=0)

    def load_files(self) -> None:
        """Present the certificate the settings point at, as an engine does when it loads them."""
        if self.ignores:
            return
        path: str | None = None
        if self.engine == "postgresql":
            if self.settings.get("ssl") == "on":
                path = self.settings.get("ssl_cert_file")
        elif self.include is not None and self.include.is_file():
            found = re.search(r"ssl_cert = (\S+)", self.include.read_text())
            path = found.group(1) if found else None
        if path and Path(path).is_file():
            self.served = Path(path).read_text()

    def answer(self, words: tuple[str, ...], sql: str) -> CommandResult:
        """
        Answer a statement.

        Args:
            words: The command.
            sql: The statement.

        Returns:
            The result.
        """
        if not self.running:
            return CommandResult(argv=words, exit_code=2, stderr="could not connect")
        out = ""
        for action, name, value in re.findall(
            r"ALTER SYSTEM (SET|RESET) (\w+)(?: = '([^']*)')?;", sql
        ):
            if action == "SET":
                self.settings[name] = value
            else:
                self.settings.pop(name, None)
        if sql.strip().upper().startswith("SHOW SSL"):
            out = self.settings.get("ssl", "off")
        if "pg_reload_conf" in sql or "FLUSH SSL" in sql or "RELOAD TLS" in sql:
            self.load_files()
        return CommandResult(argv=words, exit_code=0, stdout=out + "\n" if out else "")


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A system root with the units and include directories of Debian."""
    for directory in (
        "etc/mysql/mariadb.conf.d",
        "etc/mysql/mysql.conf.d",
        "usr/lib/systemd/system",
    ):
        (tmp_path / directory).mkdir(parents=True)
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    return tmp_path


@pytest.fixture
def live(tmp_path: Path) -> Path:
    """certbot's live directory, holding the lineage of the host."""
    lineage = tmp_path / "letsencrypt" / "live" / HOST
    lineage.mkdir(parents=True)
    (lineage / "fullchain.pem").write_text(pem("first"))
    (lineage / "privkey.pem").write_text(
        "-----BEGIN PRIVATE KEY-----\nQQ==\n-----END PRIVATE KEY-----\n"
    )
    return tmp_path / "letsencrypt" / "live"


def build(
    engine: str, host: Path, live: Path, *, mariadb: bool = False
) -> tuple[HostTls, SimulatedEngine]:
    """
    Build the manager over a simulated engine.

    Args:
        engine: ``postgresql`` or ``mysql``.
        host: The system root.
        live: certbot's live directory.
        mariadb: Whether the MySQL engine is MariaDB.

    Returns:
        The manager and its engine.
    """
    include = (
        host / "etc/mysql" / ("mariadb.conf.d" if mariadb else "mysql.conf.d") / "98-noust-tls.cnf"
    )
    fake = SimulatedEngine(engine, include, mariadb=mariadb)
    set_runner(fake)
    if engine == "postgresql":
        manager: Any = PostgresManager()
    else:
        manager = MySQLManager()
    tls = HostTls(
        manager,
        records=HostTlsRecords(get_store()),
        live_dir=live,
        clock=lambda: NOW,
        sleep=lambda _seconds: None,
    )
    return tls, fake


@pytest.fixture
def postgres(host: Path, live: Path) -> Iterator[tuple[HostTls, SimulatedEngine]]:
    """The manager over a simulated PostgreSQL."""
    pair = build("postgresql", host, live)
    try:
        yield pair
    finally:
        set_runner(None)


@pytest.fixture
def mysql(host: Path, live: Path) -> Iterator[tuple[HostTls, SimulatedEngine]]:
    """The manager over a simulated MySQL."""
    pair = build("mysql", host, live)
    try:
        yield pair
    finally:
        set_runner(None)


def renew(live: Path, seed: str) -> None:
    """Replace the lineage's certificate, as certbot does."""
    (live / HOST / "fullchain.pem").write_text(pem(seed))


# ------------------------------------------------------------------ what is checked first


def test_a_certificate_that_does_not_cover_the_name_is_refused_before_the_engine_is_touched(
    postgres: tuple[HostTls, SimulatedEngine],
) -> None:
    tls, engine = postgres
    engine.covers = {"other.example.com"}

    with pytest.raises(ValidationError, match=f"does not cover {HOST}") as caught:
        tls.plan(HOST)

    assert caught.value.field == "hostname"
    assert engine.settings == {"ssl": "on"}


def test_a_key_that_is_not_the_certificates_is_refused(
    postgres: tuple[HostTls, SimulatedEngine],
) -> None:
    tls, engine = postgres
    engine.key_matches = False

    with pytest.raises(ValidationError, match="not the certificate's"):
        tls.plan(HOST)


def test_an_expired_certificate_is_refused_with_the_date(
    postgres: tuple[HostTls, SimulatedEngine],
) -> None:
    tls, engine = postgres
    engine.enddate = "Jan  1 00:00:00 2026 GMT"

    with pytest.raises(ValidationError, match="expired on Jan  1 00:00:00 2026 GMT"):
        tls.plan(HOST)


def test_a_lineage_that_does_not_exist_says_how_to_get_one(
    postgres: tuple[HostTls, SimulatedEngine],
) -> None:
    tls, _engine = postgres

    with pytest.raises(ValidationError, match="does not exist") as caught:
        tls.plan("missing.example.com")

    assert "noust cert create" in str(caught.value.details)


@pytest.mark.parametrize("hostname", ["", "not a host", "a/b", "-x.example.com", "x" * 300])
def test_something_that_is_not_a_host_name_is_refused(
    postgres: tuple[HostTls, SimulatedEngine], hostname: str
) -> None:
    tls, _engine = postgres

    with pytest.raises(ValidationError, match="is not a host name"):
        tls.plan(hostname)


def test_a_source_that_needs_a_name_is_given_one(postgres: tuple[HostTls, SimulatedEngine]) -> None:
    tls, _engine = postgres

    with pytest.raises(ValidationError, match="is named"):
        tls.plan(HOST, source="custom")
    with pytest.raises(ValidationError, match="Unknown certificate source"):
        tls.plan(HOST, source="self-signed")


def test_an_engine_that_is_not_running_is_not_given_a_certificate(
    postgres: tuple[HostTls, SimulatedEngine],
) -> None:
    tls, engine = postgres
    engine.running = False

    with pytest.raises(DatabaseEngineError, match="is not running"):
        tls.plan(HOST)


def test_an_instance_has_its_own_certificate(
    postgres: tuple[HostTls, SimulatedEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    tls, _engine = postgres
    monkeypatch.setattr(type(tls.manager), "instance", object(), raising=False)

    with pytest.raises(ValidationError, match="its own certificate"):
        tls.plan(HOST)


# ------------------------------------------------------------------ PostgreSQL


def test_postgresql_is_given_the_copy_reloaded_and_asked_what_it_presents(
    postgres: tuple[HostTls, SimulatedEngine], host: Path, ownership_changes: list[Any]
) -> None:
    tls, engine = postgres

    status = tls.enable(HOST, confirmed=False)

    directory = host / "etc/ssl/noust-db/postgresql"
    assert engine.settings["ssl_cert_file"] == str(directory / "fullchain.pem")
    assert engine.settings["ssl_key_file"] == str(directory / "privkey.pem")
    assert engine.settings["ssl_min_protocol_version"] == "TLSv1.2"
    assert engine.restarts == 0, "PostgreSQL reloads"
    assert (directory / "fullchain.pem").read_text() == pem("first")
    handed = {change.path.name: (change.user, change.mode) for change in ownership_changes}
    assert handed["privkey.pem"] == ("postgres", 0o600)
    assert handed["fullchain.pem"] == ("postgres", 0o644)
    assert status["enabled"] and status["served"] == fingerprint_of("first")
    assert status["warnings"] == []
    record = tls.records.get("postgresql")
    assert record is not None and record.fingerprint == fingerprint_of("first")


def test_ssl_is_turned_on_when_the_engine_had_it_off_and_back_off_afterwards(
    postgres: tuple[HostTls, SimulatedEngine],
) -> None:
    tls, engine = postgres
    engine.settings["ssl"] = "off"

    tls.enable(HOST, confirmed=False)
    assert engine.settings["ssl"] == "on"
    tls.disable(confirmed=False)

    assert "ssl" not in engine.settings, "ALTER SYSTEM RESET ssl: the file's own value is back"


def test_an_engine_that_presents_another_certificate_is_put_back(
    postgres: tuple[HostTls, SimulatedEngine], host: Path
) -> None:
    tls, engine = postgres
    engine.ignores = True

    with pytest.raises(
        DatabaseEngineError, match="does not present the certificate it was given"
    ) as caught:
        tls.enable(HOST, confirmed=False)

    assert "put the engine's settings back" in str(caught.value.details)
    assert "ssl_cert_file" not in engine.settings and "ssl_key_file" not in engine.settings
    assert not (host / "etc/ssl/noust-db/postgresql/privkey.pem").exists()
    assert tls.records.get("postgresql") is None


def test_a_rehearsal_changes_nothing(
    postgres: tuple[HostTls, SimulatedEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    tls, engine = postgres
    monkeypatch.setattr("noust.managers.database.host_tls.is_rehearsal", lambda: True)

    result = tls.enable(HOST, confirmed=False)

    assert result["enabled"] is False
    assert engine.settings == {"ssl": "on"} and tls.records.get("postgresql") is None


def test_the_renewal_hook_makes_certbot_refresh_the_engines(
    postgres: tuple[HostTls, SimulatedEngine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tls, _engine = postgres
    installed: list[bool] = []
    monkeypatch.setattr(
        "noust.managers.database.host_tls.CertManager.setup_auto_renewal",
        lambda self: installed.append(True) or True,
    )

    tls.enable(HOST, confirmed=False)

    assert installed == [True]


def test_the_hook_certbot_runs_after_a_renewal_refreshes_the_engines() -> None:
    from noust.managers.cert_manager import _DEPLOY_HOOK_TEXT

    assert "noust db tls refresh" in _DEPLOY_HOOK_TEXT
    assert _DEPLOY_HOOK_TEXT.rstrip().endswith("|| true")


# ------------------------------------------------------------------ a renewal


def test_a_current_copy_is_only_checked(postgres: tuple[HostTls, SimulatedEngine]) -> None:
    tls, engine = postgres
    tls.enable(HOST, confirmed=False)
    before = len(engine.statements)

    report = tls.refresh()

    assert report["postgresql"]["refreshed"] is False
    assert not any("pg_reload_conf" in s for s in engine.statements[before:])


def test_a_renewed_certificate_is_copied_loaded_and_proved_without_a_restart(
    postgres: tuple[HostTls, SimulatedEngine], live: Path, host: Path
) -> None:
    tls, engine = postgres
    tls.enable(HOST, confirmed=False)
    renew(live, "second")
    assert tls.status()["source_changed"] is True

    report = tls.refresh()

    assert report["postgresql"] == {
        "refreshed": True,
        "fingerprint": fingerprint_of("second"),
        "served": fingerprint_of("second"),
    }
    assert (host / "etc/ssl/noust-db/postgresql/fullchain.pem").read_text() == pem("second")
    assert engine.restarts == 0
    after = tls.status()
    assert after["source_changed"] is False and after["warnings"] == []
    record = tls.records.get("postgresql")
    assert record is not None and record.last_status == "ok"


def test_a_renewal_the_engine_does_not_present_is_recorded_as_a_failure(
    postgres: tuple[HostTls, SimulatedEngine], live: Path
) -> None:
    tls, engine = postgres
    tls.enable(HOST, confirmed=False)
    renew(live, "second")
    engine.ignores = True

    with pytest.raises(DatabaseEngineError, match="does not present"):
        tls.refresh()

    record = tls.records.get("postgresql")
    assert record is not None and record.last_status == "failed"
    assert "Expected" in (record.last_error or "")
    assert any("last refresh failed" in w for w in tls.status()["warnings"])


def test_a_renewal_that_is_not_valid_is_not_copied(
    postgres: tuple[HostTls, SimulatedEngine], live: Path, host: Path
) -> None:
    tls, engine = postgres
    tls.enable(HOST, confirmed=False)
    renew(live, "second")
    engine.covers = set()

    with pytest.raises(DatabaseEngineError, match="does not cover"):
        tls.refresh()

    copied = (host / "etc/ssl/noust-db/postgresql/fullchain.pem").read_text()
    assert copied == pem("first"), "the engine keeps the certificate it was serving"


def test_an_engine_that_is_down_is_skipped_by_the_refresh(
    postgres: tuple[HostTls, SimulatedEngine],
) -> None:
    tls, engine = postgres
    tls.enable(HOST, confirmed=False)
    engine.running = False

    assert "is not running" in tls.refresh()["postgresql"]["skipped"]


def test_nothing_is_refreshed_where_nothing_was_given(
    postgres: tuple[HostTls, SimulatedEngine],
) -> None:
    tls, _engine = postgres

    assert tls.refresh() == {}


def test_the_status_counts_the_days_and_warns_before_the_expiry(
    postgres: tuple[HostTls, SimulatedEngine],
) -> None:
    tls, engine = postgres
    engine.enddate = "Oct 25 12:00:00 2026 GMT"
    tls.enable(HOST, confirmed=False)

    status = tls.status()

    assert status["days_left"] == 8
    assert any("expires in 8 days" in w for w in status["warnings"])


# ------------------------------------------------------------------ MySQL and MariaDB


def test_mysql_restarts_once_and_only_when_confirmed(
    mysql: tuple[HostTls, SimulatedEngine], host: Path
) -> None:
    tls, engine = mysql

    with pytest.raises(ValidationError, match="restarts it") as caught:
        tls.enable(HOST, confirmed=False)
    assert caught.value.field == "confirmed"
    assert engine.restarts == 0 and tls.records.get("mysql") is None

    status = tls.enable(HOST, confirmed=True)

    include = host / "etc/mysql/mysql.conf.d/98-noust-tls.cnf"
    text = include.read_text()
    assert text.startswith("# Generated by Noust")
    assert f"ssl_cert = {host}/etc/ssl/noust-db/mysql/fullchain.pem" in text
    assert f"ssl_key = {host}/etc/ssl/noust-db/mysql/privkey.pem" in text
    assert "tls_version = TLSv1.2,TLSv1.3" in text
    assert engine.restarts == 1
    assert status["served"] == fingerprint_of("first")


def test_mysql_loads_a_renewed_certificate_with_alter_instance_and_mariadb_with_flush_ssl(
    host: Path, live: Path
) -> None:
    for mariadb, statement in ((False, "ALTER INSTANCE RELOAD TLS"), (True, "FLUSH SSL")):
        NoustStore.reset_instance()
        tls, engine = build("mysql", host, live, mariadb=mariadb)
        try:
            tls.enable(HOST, confirmed=True)
            renew(live, f"renewed-{mariadb}")
            restarts = engine.restarts

            tls.refresh()

            assert any(statement in s for s in engine.statements)
            assert engine.restarts == restarts, "no restart for a renewal"
            assert engine.served == pem(f"renewed-{mariadb}")
        finally:
            set_runner(None)


def test_taking_the_certificate_off_mysql_removes_the_include_and_restarts(
    mysql: tuple[HostTls, SimulatedEngine], host: Path
) -> None:
    tls, engine = mysql
    tls.enable(HOST, confirmed=True)

    with pytest.raises(ValidationError, match="restarts it"):
        tls.disable(confirmed=False)
    result = tls.disable(confirmed=True)

    assert result == {"disabled": True}
    assert not (host / "etc/mysql/mysql.conf.d/98-noust-tls.cnf").exists()
    assert engine.restarts == 2
    assert tls.records.get("mysql") is None


def test_an_engine_without_an_include_directory_says_where_one_is_needed(
    host: Path, live: Path
) -> None:
    for directory in ("etc/mysql/mysql.conf.d", "etc/mysql/mariadb.conf.d"):
        (host / directory).rmdir()
    tls, _engine = build("mysql", host, live)
    try:
        with pytest.raises(DatabaseEngineError, match="nowhere to write"):
            tls.enable(HOST, confirmed=True)
    finally:
        set_runner(None)


def test_an_engine_this_cannot_serve_is_refused() -> None:
    from noust.managers.database.redis import RedisManager

    fake = SimulatedEngine("redis")
    set_runner(fake)
    try:
        tls = HostTls(RedisManager(), records=HostTlsRecords(get_store()))
        with pytest.raises(ValidationError, match="cannot be given a certificate"):
            tls.plan(HOST)
    finally:
        set_runner(None)


def test_the_fingerprint_of_a_chain_is_its_leafs(tmp_path: Path) -> None:
    chain = pem("leaf") + pem("issuer")

    assert leaf_fingerprint(chain) == fingerprint_of("leaf")
    assert leaf_fingerprint("not a certificate") is None
    assert leaf_fingerprint("-----BEGIN CERTIFICATE-----\n!!!\n-----END CERTIFICATE-----\n") is None
    assert os.path.exists(tmp_path)
