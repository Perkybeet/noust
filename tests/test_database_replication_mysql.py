# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Replicas of the host's MySQL and MariaDB, from the primary's preparation to a promoted replica (C-14).

The engine is a runner that behaves like one for exactly the statements and tools the manager
uses, with real files for Noust's settings, the authority and the dump it holds, so what is pinned
is what Noust writes and in what order: the settings and the account on a primary; the dump, the
load, the statement that points the server at its primary over verified TLS and the settings it
keeps on a replica; what is undone when a step fails; and that the replication password is never an
argument or a setting of any process. MariaDB and MySQL differ in every word for replication, so
both are driven. The real servers, two of them on a network, are
``tests/integration/engines_run.py`` (scenarios ``replication_mariadb`` and ``replication_mysql``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DatabaseBackupError, DatabaseEngineError, ValidationError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore, get_store
from noust.managers.database import flavours
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.replication_mysql import MySqlReplication, derive_server_id
from noust.managers.database.replication_records import PRIMARY, REPLICA, ReplicationRecords
from noust.managers.database.replication_token import parse
from noust.managers.server.host import HostPaths
from tests.test_database_replication import CA, HOST, REPLICA_ADDRESS, Clock, FakeFirewall

START_DUMP = "-- the primary's databases\nCREATE DATABASE `shop`;\n"


class MySql(FakeRunner):
    """
    A runner that is a MySQL or MariaDB server and its client tools, as far as the manager can tell.

    Attributes:
        mariadb: Whether it is MariaDB.
        facts: What the server says about itself.
        users: The accounts, by name, with the hosts they exist for.
        databases: The user databases.
        remote_databases: What the primary's ``SHOW DATABASES`` lists.
        following: What the server follows, once pointed at a primary.
        statements: Every SQL text sent to the local server, in order.
        loaded: The dumps fed to the local client.
        remote_calls: The arguments and environment of each call to the primary.
        tls_problem: What openssl says when the certificate would be refused; None to accept.
        reach_problem: What the client says when the primary cannot be reached; None to succeed.
        dump_problem: What ``mysqldump`` fails with; None to succeed.
        load_problem: What loading the dump fails with; None to succeed.
        io_error: The error the IO thread stops with once started; None for a healthy replica.
        user_problem: What ``CREATE USER`` fails with; None to succeed.
        restart_dies: Whether a restart leaves the server down.
        replicating: Whether this server follows a primary already.
        processes: The binary log dump threads the primary has: ``(host, time, cipher)``.
    """

    def __init__(self, *, mariadb: bool) -> None:
        super().__init__()
        self.mariadb = mariadb
        self.facts: dict[str, Any] = {
            "version": "11.4.5-MariaDB-ubu2404" if mariadb else "8.4.3",
            "server_id": "1",
            "log_bin": "0" if mariadb else "1",
            "port": "3306",
            "bind": "127.0.0.1",
            "datadir": "/var/lib/mysql/",
            "read_only": "0",
            "gtid_mode": "-" if mariadb else "OFF",
            "enforce": "-" if mariadb else "OFF",
            "size": "33554432",
        }
        self.users: dict[str, set[str]] = {}
        self.databases = ["shop"]
        self.remote_databases = ["shop", "blog"]
        self.following: dict[str, str] | None = None
        self.statements: list[str] = []
        self.loaded: list[bytes] = []
        self.remote_calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.tls_problem: str | None = None
        self.reach_problem: str | None = None
        self.dump_problem: str | None = None
        self.load_problem: str | None = None
        self.io_error: str | None = None
        self.user_problem: str | None = None
        self.restart_dies = False
        self.running = True
        self.restarts = 0
        self.processes: list[tuple[str, str, str]] = []
        self.ssl_type = "ANY"
        known = ["mysql", "mysqldump", "systemctl", "openssl", "journalctl"]
        self.only_knows(*known, *(["mariadb"] if mariadb else []))

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
            argv, cwd=cwd, env=env, timeout=timeout, input=input, user=user, check=check
        )
        words = tuple(argv)
        if words[0] == "openssl":
            if self.tls_problem is not None:
                return CommandResult(
                    argv=words, exit_code=1, stderr=f"verify error:{self.tls_problem}\n"
                )
            return CommandResult(argv=words, exit_code=0, stdout="Verify return code: 0 (ok)\n")
        if words[:2] == ("systemctl", "is-active"):
            return CommandResult(
                argv=words,
                exit_code=0 if self.running else 3,
                stdout="active\n" if self.running else "inactive\n",
            )
        if words[:2] == ("systemctl", "stop"):
            self.running = False
            return scripted
        if words[:2] in (("systemctl", "start"), ("systemctl", "restart")):
            if words[1] == "restart":
                self.restarts += 1
                self.running = not self.restart_dies
                if not self.restart_dies:
                    self.apply_include_settings()
            else:
                self.running = True
            return scripted
        if words[0] == "mysql":
            return self.client(words, env, input, stdin_path)
        return scripted

    def apply_include_settings(self) -> None:
        """Make a restart take the settings Noust's files hold, as the server does."""
        for leaf in ("99-noust-replication.cnf", "99-noust-replica.cnf"):
            for path in self.include_dirs:
                file = path / leaf
                if not file.is_file():
                    continue
                for key, value in re.findall(r"^(\w[\w-]*) = (.*)$", file.read_text(), re.M):
                    if key == "log_bin":
                        self.facts["log_bin"] = "1"
                    elif key == "server_id":
                        self.facts["server_id"] = value
                    elif key == "bind-address":
                        self.facts["bind"] = value
                    elif key == "gtid_mode":
                        self.facts["gtid_mode"] = value
                    elif key == "enforce_gtid_consistency":
                        self.facts["enforce"] = value

    include_dirs: list[Path] = []

    def client(
        self,
        words: tuple[str, ...],
        env: Mapping[str, str] | None,
        input: str | None,
        stdin_path: Path | None,
    ) -> CommandResult:
        """
        Answer the ``mysql`` client, local or remote.

        Args:
            words: The command.
            env: Its environment.
            input: What was sent on its standard input.
            stdin_path: A file given as its standard input.

        Returns:
            The result.
        """
        if "-h" in words:
            self.remote_calls.append((words, dict(env or {})))
            if self.reach_problem is not None:
                return CommandResult(argv=words, exit_code=1, stderr=self.reach_problem)
            return CommandResult(
                argv=words, exit_code=0, stdout="\n".join(self.remote_databases) + "\n"
            )
        if not self.running:
            return CommandResult(argv=words, exit_code=2, stderr="Can't connect to server")
        if stdin_path is not None:
            self.loaded.append(stdin_path.read_bytes())
            if self.load_problem is not None:
                return CommandResult(argv=words, exit_code=1, stderr=self.load_problem)
            self.databases = list(self.remote_databases)
            return CommandResult(argv=words, exit_code=0)
        assert input is not None
        self.statements.append(input)
        return self.answer(words, input)

    def answer(self, words: tuple[str, ...], sql: str) -> CommandResult:
        """
        Answer a statement the way the server would.

        Args:
            words: The command.
            sql: The statement sent on standard input.

        Returns:
            The result.
        """
        for host in re.findall(r"CREATE USER '[^']+'@'([^']+)'", sql):
            if self.user_problem is not None:
                return CommandResult(argv=words, exit_code=1, stderr=self.user_problem)
            account = re.search(r"CREATE USER '([^']+)'", sql).group(1)  # type: ignore[union-attr]
            self.users.setdefault(account, set()).add(host)
        for account, host in re.findall(r"DROP USER IF EXISTS '([^']+)'@'([^']+)'", sql):
            self.users.get(account, set()).discard(host)
            if not self.users.get(account):
                self.users.pop(account, None)
        for name in re.findall(r"DROP DATABASE IF EXISTS `([^`]+)`", sql):
            if name in self.databases:
                self.databases.remove(name)
        for value in re.findall(r"SET GLOBAL server_id = (\d+)", sql):
            self.facts["server_id"] = value
        for value in re.findall(r"SET GLOBAL read_only = (\w+)", sql):
            self.facts["read_only"] = "1" if value in ("1", "ON") else "0"
        if "CHANGE MASTER TO" in sql or "CHANGE REPLICATION SOURCE TO" in sql:
            match = re.search(r"(?:MASTER|SOURCE)_HOST = '([^']*)', \w+_PORT = (\d+)", sql)
            assert match
            self.following = {
                "host": match.group(1),
                "port": match.group(2),
                "ssl": "Yes" if re.search(r"_SSL = 1", sql) else "No",
                "verify": "Yes" if "_SSL_VERIFY_SERVER_CERT = 1" in sql else "No",
            }
        if re.search(r"RESET (SLAVE|REPLICA) ALL", sql):
            self.following = None
        out = ""
        if "VERSION()" in sql:
            out = "\t".join(
                [
                    self.facts["version"],
                    self.facts["server_id"],
                    self.facts["log_bin"],
                    self.facts["port"],
                    self.facts["bind"],
                    self.facts["datadir"],
                    self.facts["read_only"],
                    self.facts["gtid_mode"],
                    self.facts["enforce"],
                    self.facts["size"],
                ]
            )
        elif "SELECT schema_name FROM information_schema.schemata" in sql:
            out = "\n".join(["information_schema", "mysql", *self.databases])
        elif "SELECT count(*) FROM mysql.user" in sql:
            account = re.search(r"user = '([^']+)'", sql).group(1)  # type: ignore[union-attr]
            out = str(len(self.users.get(account, ())))
        elif "SELECT host FROM mysql.user" in sql:
            account = re.search(r"user = '([^']+)'", sql).group(1)  # type: ignore[union-attr]
            out = "\n".join(sorted(self.users.get(account, ())))
        elif "FROM information_schema.PROCESSLIST p" in sql:
            out = "\n".join(
                f"7\t{host}:41234\t{seconds}\t{cipher}\t{self.ssl_type}"
                for host, seconds, cipher in self.processes
            )
        elif sql.strip().startswith(("SHOW SLAVE STATUS", "SHOW REPLICA STATUS")):
            out = self.status_text()
        return CommandResult(argv=words, exit_code=0, stdout=out + "\n" if out else "")

    def status_text(self) -> str:
        """
        Returns:
            What ``SHOW SLAVE STATUS`` prints with ``-E``, in this flavour's words.
        """
        if self.following is None:
            return ""
        slave = self.mariadb
        io = "No" if self.io_error else "Yes"
        names = {
            "io": "Slave_IO_Running" if slave else "Replica_IO_Running",
            "sql": "Slave_SQL_Running" if slave else "Replica_SQL_Running",
            "host": "Master_Host" if slave else "Source_Host",
            "port": "Master_Port" if slave else "Source_Port",
            "ssl": "Master_SSL_Allowed" if slave else "Source_SSL_Allowed",
            "verify": "Master_SSL_Verify_Server_Cert" if slave else "Source_SSL_Verify_Server_Cert",
            "behind": "Seconds_Behind_Master" if slave else "Seconds_Behind_Source",
            "state": "Slave_SQL_Running_State" if slave else "Replica_SQL_Running_State",
        }
        lines = ["*************************** 1. row ***************************"]
        lines += [
            f"{names['io']}: {io}",
            f"{names['sql']}: Yes",
            f"{names['host']}: {self.following['host']}",
            f"{names['port']}: {self.following['port']}",
            f"{names['ssl']}: {self.following['ssl']}",
            f"{names['verify']}: {self.following['verify']}",
            f"{names['behind']}: 3",
            f"{names['state']}: Replica has read all relay log; waiting for more updates",
            f"Last_IO_Error: {self.io_error or ''}",
            "Last_SQL_Error: ",
        ]
        return "\n".join(lines)

    def capture_to_file(
        self,
        argv: Sequence[str],
        destination: Path,
        *,
        compress: bool = False,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = 120,
        user: str | None = None,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        super().capture_to_file(
            argv,
            destination,
            compress=compress,
            cwd=cwd,
            env=env,
            timeout=timeout,
            user=user,
            secrets=secrets,
        )
        if argv[0] != "mysqldump":
            return CommandResult(argv=tuple(argv), exit_code=0)
        self.remote_calls.append((tuple(argv), dict(env or {})))
        if self.dump_problem is not None:
            return CommandResult(argv=tuple(argv), exit_code=2, stderr=self.dump_problem)
        destination.write_text(START_DUMP)
        return CommandResult(argv=tuple(argv), exit_code=0)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture(params=["mariadb", "mysql"])
def flavour(request: pytest.FixtureRequest) -> str:
    """Both flavours: they share a flow and differ in every word."""
    return str(request.param)


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime_start())


def datetime_start() -> Any:
    from datetime import datetime, timezone

    return datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flavour: str) -> Path:
    """A system root with the engine's units and its include directory."""
    unit = "mariadb" if flavour == "mariadb" else "mysql"
    (tmp_path / "usr/lib/systemd/system").mkdir(parents=True)
    (tmp_path / f"usr/lib/systemd/system/{unit}.service").write_text("")
    folder = "mariadb.conf.d" if flavour == "mariadb" else "mysql.conf.d"
    (tmp_path / "etc/mysql" / folder).mkdir(parents=True)
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    return tmp_path / "etc/mysql" / folder


@pytest.fixture
def engine(tmp_path: Path, host: Path, flavour: str) -> Iterator[MySql]:
    """The simulated server, installed as the process-wide runner."""
    fake = MySql(mariadb=flavour == "mariadb")
    fake.include_dirs = [host]
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def firewall() -> FakeFirewall:
    return FakeFirewall()


@pytest.fixture
def replication(
    tmp_path: Path, engine: MySql, clock: Clock, firewall: FakeFirewall
) -> MySqlReplication:
    """The manager over the simulated server, with a clock and a sleep that only move time."""
    manager = MySQLManager()
    saved: dict[str, Path] = {}

    def backup(database: str, *args: Any, **kwargs: Any) -> Any:
        target = tmp_path / "dumps" / f"{database}.sql"
        target.parent.mkdir(exist_ok=True)
        target.write_text(f"-- {database}\n")
        saved[database] = target
        return type("Info", (), {"path": target})()

    restored: list[tuple[str, Path]] = []

    def restore(database: str, path: Path, *args: Any, **kwargs: Any) -> None:
        restored.append((database, path))
        engine.databases.append(database)

    manager.backup = backup  # type: ignore[method-assign]
    manager.restore = restore  # type: ignore[method-assign]
    manager.safety = saved  # type: ignore[attr-defined]
    manager.restored = restored  # type: ignore[attr-defined]
    return MySqlReplication(
        manager,
        records=ReplicationRecords(get_store()),
        secrets=SecretStore(root=tmp_path / "secrets"),
        firewall=lambda: firewall,  # type: ignore[arg-type,return-value]
        clock=clock,
        sleep=clock.sleep,
        state_root=tmp_path / "state",
        scratch_root=tmp_path / "scratch",
        authority_root=tmp_path / "authority",
        disk_free=lambda _path: 10**12,
        local_address=lambda address: address.startswith("10."),
    )


def prepare(manager: MySqlReplication, name: str = "reports", **overrides: Any) -> dict[str, Any]:
    """
    Prepare a replica with sensible defaults.

    Args:
        manager: The manager.
        name: The replica's name.
        **overrides: Arguments of :meth:`MySqlReplication.prepare` to change.

    Returns:
        Its result.
    """
    arguments: dict[str, Any] = {
        "source": REPLICA_ADDRESS,
        "host": HOST,
        "listen": "*",
        "confirmed": True,
    }
    arguments.update(overrides)
    return manager.prepare(name, **arguments)


def include(host: Path, leaf: str = "99-noust-replication.cnf") -> str:
    """
    Args:
        host: The include directory.
        leaf: The file.

    Returns:
        Its text, or an empty string.
    """
    path = host / leaf
    return path.read_text() if path.is_file() else ""


# ------------------------------------------------------------------ planning the primary


def test_planning_changes_nothing_and_says_what_would_be_done(
    replication: MySqlReplication, engine: MySql, host: Path, flavour: str
) -> None:
    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="*")

    assert plan.problems == [] and plan.restart is True
    assert plan.source == "203.0.113.7/32"
    expected = {"mariadb": ["log_bin", "expire_logs_days", "bind-address"]}.get(
        flavour, ["gtid_mode", "enforce_gtid_consistency", "bind-address"]
    )
    assert list(plan.settings) == expected
    assert plan.settings["bind-address"] == ("0.0.0.0" if flavour == "mariadb" else "*")  # noqa: S104
    assert include(host) == "" and engine.restarts == 0 and engine.users == {}
    assert any("REQUIRE SSL" in step for step in plan.steps)


def test_a_server_that_needs_nothing_is_not_restarted(
    replication: MySqlReplication, engine: MySql, flavour: str
) -> None:
    engine.facts["bind"] = "10.0.0.5"
    engine.facts["log_bin"] = "1"
    engine.facts["gtid_mode"] = "ON" if flavour == "mysql" else "-"
    engine.facts["enforce"] = "ON" if flavour == "mysql" else "-"

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST)

    assert plan.problems == [] and plan.settings == {} and plan.restart is False


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"version": "10.3.39-MariaDB"}, "too old"),
        ({"version": "8.0.22"}, "too old"),
    ],
)
def test_a_server_too_old_for_replication_says_so(
    replication: MySqlReplication, engine: MySql, flavour: str, change: dict[str, str], message: str
) -> None:
    old = "10.3.39-MariaDB" if flavour == "mariadb" else "8.0.22"
    engine.facts["version"] = old

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="*")

    assert any(message in problem for problem in plan.problems), plan.problems


def test_mysql_with_binary_logging_turned_off_is_refused_with_the_fix(
    replication: MySqlReplication, engine: MySql, flavour: str
) -> None:
    if flavour == "mariadb":
        pytest.skip("MariaDB's binary log is turned on by Noust's own settings")
    engine.facts["log_bin"] = "0"

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="*")

    assert any("Binary logging is turned off" in problem for problem in plan.problems)


def test_a_certificate_the_replica_would_refuse_is_refused_first_in_openssls_words(
    replication: MySqlReplication, engine: MySql
) -> None:
    engine.tls_problem = "num=18:self-signed certificate"

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="*")

    assert any("self-signed certificate" in problem for problem in plan.problems)
    proof = next(call for call in engine.calls if call[0] == "openssl")
    assert proof[proof.index("-starttls") + 1] == "mysql"


def test_a_server_on_the_loopback_asks_where_to_listen(replication: MySqlReplication) -> None:
    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST)

    assert any("listens on '127.0.0.1' only" in problem for problem in plan.problems)


def test_mariadb_listens_on_one_address_so_a_second_is_refused_with_the_way_out(
    replication: MySqlReplication, engine: MySql, flavour: str
) -> None:
    if flavour != "mariadb":
        pytest.skip("only MariaDB takes one address")

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="10.0.0.5")

    assert any("one address" in problem and "'*'" in problem for problem in plan.problems)


def test_mysql_may_listen_on_a_list_of_addresses(
    replication: MySqlReplication, flavour: str
) -> None:
    if flavour != "mysql":
        pytest.skip("only MySQL takes a list")

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="10.0.0.5")

    assert plan.problems == [] and plan.settings["bind-address"] == "127.0.0.1,10.0.0.5"


def test_a_server_that_is_itself_a_replica_does_not_feed_one(
    replication: MySqlReplication, engine: MySql
) -> None:
    engine.following = {"host": "h", "port": "3306", "ssl": "Yes", "verify": "Yes"}

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="*")

    assert any("itself a replica" in problem for problem in plan.problems)


def test_an_account_noust_did_not_make_is_never_taken_over(
    replication: MySqlReplication, engine: MySql
) -> None:
    engine.users["noust_repl_reports"] = {"1.2.3.4"}

    plan = replication.plan_prepare("reports", source=REPLICA_ADDRESS, host=HOST, listen="*")

    assert any("no record of making it" in problem for problem in plan.problems)


def test_a_network_becomes_an_address_and_mask_and_an_ipv6_network_is_refused(
    replication: MySqlReplication,
) -> None:
    plan = replication.plan_prepare("reports", source="10.1.2.0/24", host=HOST, listen="*")
    assert any("10.1.2.0/255.255.255.0" in step for step in plan.steps)

    with pytest.raises(ValidationError) as caught:
        replication.plan_prepare("reports", source="2001:db8::/64", host=HOST, listen="*")
    assert caught.value.field == "source"


# ------------------------------------------------------------------ preparing the primary


def test_a_restart_has_to_be_confirmed_where_it_happens(
    replication: MySqlReplication, engine: MySql, host: Path
) -> None:
    with pytest.raises(ValidationError, match="restarts") as caught:
        prepare(replication, confirmed=False)

    assert caught.value.field == "confirmed"
    assert include(host) == "" and engine.restarts == 0 and engine.users == {}


def test_preparing_writes_the_settings_restarts_once_and_makes_the_account(
    replication: MySqlReplication,
    engine: MySql,
    host: Path,
    firewall: FakeFirewall,
    flavour: str,
) -> None:
    result = prepare(replication, open_firewall=True, ca_pem=CA)

    text = include(host)
    assert text.startswith("# Generated by Noust")
    assert "bind-address = " in text
    assert ("log_bin = noust-bin" in text) if flavour == "mariadb" else ("gtid_mode = ON" in text)
    assert engine.restarts == 1
    assert engine.users == {"noust_repl_reports": {"203.0.113.7"}}
    create = next(s for s in engine.statements if "CREATE USER" in s)
    assert "REQUIRE SSL" in create and "IDENTIFIED BY" in create
    assert "GRANT REPLICATION SLAVE, REPLICATION CLIENT, RELOAD, SELECT" in create
    assert "SUPER" not in create and "ALL PRIVILEGES" not in create
    assert [(r.port, r.source) for r in firewall.allowed] == [(3306, "203.0.113.7/32")]
    record = replication.records.get(PRIMARY, "reports", "mysql")
    assert record is not None and record.listen_was == "127.0.0.1" and record.slot == ""
    assert record.engine == "mysql"
    assert result["plan"]["ok"] is True


def test_the_code_is_for_mysql_and_holds_the_flavour_the_version_and_the_server_id(
    replication: MySqlReplication, engine: MySql, flavour: str
) -> None:
    code = parse(prepare(replication, ca_pem=CA)["token"])

    assert code.engine == "mysql" and code.flavour == flavour and code.slot == ""
    assert code.major == (1104 if flavour == "mariadb" else 804)
    assert code.requires == {"server_id": 1} and code.ca == CA
    assert code.host == HOST and code.port == 3306 and code.size == 33554432


def test_the_password_fits_what_mysql_accepts_for_a_replication_user(
    replication: MySqlReplication,
) -> None:
    assert len(parse(prepare(replication)["token"]).password) == 32


def test_the_password_is_in_the_secret_store_and_the_statement_on_stdin_and_nowhere_else(
    replication: MySqlReplication, engine: MySql, tmp_path: Path
) -> None:
    code = parse(prepare(replication, ca_pem=CA)["token"])

    assert replication.secrets.read("databases/mysql/replication/reports") == code.password
    assert any(code.password in statement for statement in engine.statements)
    for call in engine.calls:
        assert code.password not in " ".join(call), call
    for env in engine.envs:
        assert code.password not in " ".join((env or {}).values())
    stored = replication.records.get(PRIMARY, "reports", "mysql")
    assert stored is not None and code.password not in str(stored.to_dict())


def test_the_code_can_be_shown_again_with_the_same_content(
    replication: MySqlReplication,
) -> None:
    first = parse(prepare(replication, ca_pem=CA)["token"])

    assert parse(replication.token("reports")) == first


def test_a_failed_account_puts_the_settings_back_and_restarts_again(
    replication: MySqlReplication, engine: MySql, host: Path
) -> None:
    engine.user_problem = "ERROR 1396 (HY000): Operation CREATE USER failed"

    with pytest.raises(DatabaseEngineError, match="refused a statement"):
        prepare(replication)

    assert include(host) == "", "Noust's settings are taken off"
    assert engine.restarts == 2, "and the server restarted again to forget them"
    assert replication.records.get(PRIMARY, "reports", "mysql") is None


def test_a_firewall_rule_made_by_a_failed_preparation_is_closed(
    replication: MySqlReplication, engine: MySql, firewall: FakeFirewall
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise DatabaseEngineError("the secret store is read-only")

    replication.secrets.write = refuse  # type: ignore[method-assign]

    with pytest.raises(DatabaseEngineError, match="read-only"):
        prepare(replication, open_firewall=True)

    assert len(firewall.allowed) == 1 and len(firewall.removed) == 1
    assert engine.users == {}


def test_a_server_that_does_not_come_back_gets_its_settings_taken_off(
    replication: MySqlReplication, engine: MySql, host: Path
) -> None:
    engine.restart_dies = True

    with pytest.raises(DatabaseEngineError):
        prepare(replication)

    assert include(host) == ""
    assert replication.records.get(PRIMARY, "reports", "mysql") is None


def test_a_second_replica_keeps_what_the_first_one_needed(
    replication: MySqlReplication, engine: MySql, host: Path
) -> None:
    prepare(replication)
    first = include(host)
    engine.facts["bind"] = "0.0.0.0"  # noqa: S104

    plan = replication.plan_prepare("standby", source="203.0.113.8", host=HOST, listen="*")

    assert plan.problems == []
    assert "bind-address" in first


# ------------------------------------------------------------------ taking a replica away


def test_removing_needs_a_confirmation_and_changes_nothing_without_one(
    replication: MySqlReplication, engine: MySql
) -> None:
    prepare(replication)

    with pytest.raises(ValidationError, match="cuts its replica off"):
        replication.remove("reports", confirmed=False)

    assert "noust_repl_reports" in engine.users


def test_removing_drops_the_account_the_rule_and_the_password_and_nothing_more(
    replication: MySqlReplication, engine: MySql, firewall: FakeFirewall, host: Path, tmp_path: Path
) -> None:
    prepare(replication, open_firewall=True, ca_pem=CA)
    engine.users["someone_else"] = {"%"}

    result = replication.remove("reports", confirmed=True)

    assert result["removed"] is True
    assert engine.users == {"someone_else": {"%"}}
    assert len(firewall.removed) == 1
    assert replication.secrets.read("databases/mysql/replication/reports") is None
    assert replication.records.get(PRIMARY, "reports", "mysql") is None
    assert not (tmp_path / "state" / "mysql" / "reports").exists()
    assert include(host) != "", "left as it is"
    assert any("--restore-listen" in step for step in result["steps"])


def test_the_settings_come_off_when_asked_and_no_other_replica_needs_them(
    replication: MySqlReplication, engine: MySql, host: Path
) -> None:
    prepare(replication)

    replication.remove("reports", confirmed=True, restore_listen=True)

    assert include(host) == "" and engine.restarts == 2


def test_a_replica_that_was_never_prepared_is_not_removed_from_under_someone(
    replication: MySqlReplication,
) -> None:
    assert replication.remove("reports", confirmed=True)["removed"] is False


# ------------------------------------------------------------------ planning a join


@pytest.fixture
def code(replication: MySqlReplication, engine: MySql, flavour: str, tmp_path: Path) -> str:
    """A join code from a primary like the simulated one, made by another server's manager."""
    from noust.managers.database.replication_token import ReplicaToken, encode

    return encode(
        ReplicaToken(
            name="reports",
            host=HOST,
            port=3306,
            role="noust_repl_reports",
            password="aB3" * 10 + "aB",
            slot="",
            major=1104 if flavour == "mariadb" else 804,
            size=1024,
            requires={"server_id": 1},
            ca=CA,
            engine="mysql",
            flavour=flavour,
        )
    )


def test_planning_a_join_lists_what_it_replaces_and_changes_nothing(
    replication: MySqlReplication, engine: MySql, code: str, host: Path
) -> None:
    plan = replication.plan_join(code)

    assert plan.problems == [] and plan.databases == ["shop"]
    assert plan.capacity["server_id"] == derive_server_id("reports", 1) != 1
    assert any("shop" in warning for warning in plan.warnings)
    assert any("accounts of the primary are not copied" in warning for warning in plan.warnings)
    assert include(host, "99-noust-replica.cnf") == "" and engine.restarts == 0
    assert "password" not in str(plan.to_dict()).lower()


def test_a_server_of_another_flavour_cannot_follow(
    replication: MySqlReplication, engine: MySql, code: str, flavour: str
) -> None:
    other = "mysql" if flavour == "mariadb" else "mariadb"
    wrong = code.replace(
        code[len("noust-replica:v1:") :],
        _with(code, f=other),
    )

    plan = replication.plan_join(wrong)

    assert any("follows the flavour of its primary" in problem for problem in plan.problems)


def _with(code: str, **changes: Any) -> str:
    """
    Args:
        code: A join code.
        **changes: Fields to change, by their short names.

    Returns:
        The payload of a code with them changed.
    """
    import base64
    import json

    body = json.loads(base64.urlsafe_b64decode(code[len("noust-replica:v1:") :]))
    body.update(changes)
    return base64.urlsafe_b64encode(json.dumps(body).encode()).decode()


def test_a_replica_older_than_its_primary_is_refused(
    replication: MySqlReplication, engine: MySql, code: str, flavour: str
) -> None:
    engine.facts["version"] = "10.5.1-MariaDB" if flavour == "mariadb" else "8.0.30"

    plan = replication.plan_join(code)

    assert any("cannot be older" in problem for problem in plan.problems)


def test_a_server_that_already_follows_something_does_not_follow_another(
    replication: MySqlReplication, engine: MySql, code: str
) -> None:
    engine.following = {"host": "h", "port": "3306", "ssl": "Yes", "verify": "Yes"}

    plan = replication.plan_join(code)

    assert any("already follows a primary" in problem for problem in plan.problems)


def test_not_enough_disk_for_the_load_is_refused_before_anything_is_dumped(
    replication: MySqlReplication, engine: MySql, code: str
) -> None:
    replication._disk_free = lambda _path: 100

    with pytest.raises(ValidationError, match="cannot join that primary yet"):
        replication.join(code, confirmed=True)

    assert not any(call[0] == "mysqldump" for call in engine.calls)


def test_a_postgresql_code_is_refused_by_this_engine(
    replication: MySqlReplication,
) -> None:
    from noust.managers.database.replication_token import ReplicaToken, encode
    from tests.test_database_replication_token import token as pg_token

    postgres = encode(pg_token())
    assert isinstance(pg_token(), ReplicaToken)

    with pytest.raises(ValidationError, match="for PostgreSQL"):
        replication.plan_join(postgres)


def test_a_join_has_to_be_confirmed_where_it_happens(
    replication: MySqlReplication, engine: MySql, code: str
) -> None:
    with pytest.raises(ValidationError, match="replaces this server's") as caught:
        replication.join(code, confirmed=False)

    assert caught.value.field == "confirmed"
    assert not any(call[0] == "mysqldump" for call in engine.calls)
    assert engine.databases == ["shop"]


# ------------------------------------------------------------------ joining


def test_joining_dumps_the_primary_over_verified_tls_and_loads_it(
    replication: MySqlReplication,
    engine: MySql,
    code: str,
    host: Path,
    tmp_path: Path,
    flavour: str,
) -> None:
    result = replication.join(code, confirmed=True)

    token = parse(code)
    dump_call, env = next(item for item in engine.remote_calls if item[0][0] == "mysqldump")
    assert dump_call[dump_call.index("-h") + 1] == HOST
    assert dump_call[dump_call.index("-u") + 1] == "noust_repl_reports"
    assert "--databases" in dump_call and dump_call[-2:] == ("shop", "blog")
    assert "--single-transaction" in dump_call and "--add-drop-database" in dump_call
    authority = str(tmp_path / "authority" / "reports" / "ca.pem")
    assert f"--ssl-ca={authority}" in dump_call
    if flavour == "mariadb":
        assert "--ssl-verify-server-cert" in dump_call
        assert "--master-data=1" in dump_call and "--gtid" in dump_call
    else:
        assert "--ssl-mode=VERIFY_IDENTITY" in dump_call
        assert "--set-gtid-purged=ON" in dump_call and "--no-tablespaces" in dump_call
    assert env["MYSQL_PWD"] == token.password
    for words, _env in engine.remote_calls:
        assert token.password not in " ".join(words)
    assert engine.loaded == [START_DUMP.encode()]
    assert (tmp_path / "authority" / "reports" / "ca.pem").read_text() == CA
    assert result["state"] == "streaming" and result["primary"] == f"{HOST}:3306"
    assert not (tmp_path / "scratch" / "reports.sql").exists(), "the primary's data is not kept"


def test_the_server_is_pointed_at_the_primary_over_verified_tls_with_the_password_on_stdin(
    replication: MySqlReplication, engine: MySql, code: str, tmp_path: Path, flavour: str
) -> None:
    replication.join(code, confirmed=True)

    token = parse(code)
    statement = next(
        s for s in engine.statements if "CHANGE MASTER TO" in s or "CHANGE REPLICATION SOURCE" in s
    )
    assert token.password in statement
    authority = str(tmp_path / "authority" / "reports" / "ca.pem")
    if flavour == "mariadb":
        assert "CHANGE MASTER TO MASTER_HOST = 'db.example.com'" in statement
        assert "MASTER_USE_GTID = slave_pos" in statement
        assert "MASTER_SSL_VERIFY_SERVER_CERT = 1" in statement
        assert f"MASTER_SSL_CA = '{authority}'" in statement
        assert "START SLAVE;" in statement
    else:
        assert "CHANGE REPLICATION SOURCE TO SOURCE_HOST = 'db.example.com'" in statement
        assert "SOURCE_AUTO_POSITION = 1" in statement
        assert "SOURCE_SSL_VERIFY_SERVER_CERT = 1" in statement
        assert "START REPLICA;" in statement
    for call in engine.calls:
        assert token.password not in " ".join(call), call
    carrying = [
        call
        for call, env in zip(engine.calls, engine.envs, strict=True)
        if token.password in " ".join((env or {}).values())
    ]
    assert {call[0] for call in carrying} == {"mysql", "mysqldump"}, (
        "only the clients of the primary"
    )
    assert all("-h" in call for call in carrying)


def test_the_replicas_own_databases_are_dumped_first_and_replaced_only_then(
    replication: MySqlReplication, engine: MySql, code: str
) -> None:
    result = replication.join(code, confirmed=True)

    manager = replication.manager
    assert list(manager.safety) == ["shop"]  # type: ignore[attr-defined]
    assert result["saved_as"] == str(manager.safety["shop"])  # type: ignore[attr-defined]
    order = [s for s in engine.statements if "DROP DATABASE" in s or "CHANGE" in s]
    assert "DROP DATABASE IF EXISTS `shop`" in order[0], "dropped after the dump is in hand"
    assert any(call[0] == "mysqldump" for call in engine.calls)


def test_the_server_gets_its_own_id_and_refuses_writes_that_are_not_its_primarys(
    replication: MySqlReplication, engine: MySql, code: str, host: Path, flavour: str
) -> None:
    replication.join(code, confirmed=True)

    text = include(host, "99-noust-replica.cnf")
    assert f"server_id = {derive_server_id('reports', 1)}" in text
    assert engine.facts["read_only"] == "1"
    if flavour == "mysql":
        assert "gtid_mode = ON" in text and "super_read_only = ON" in text
        assert engine.restarts == 1, "GTIDs are turned on with a restart"
        assert any("RESET BINARY LOGS AND GTIDS" in s for s in engine.statements)
    else:
        assert "read_only = 1" in text
        assert engine.restarts == 0
        assert any("RESET SLAVE ALL" in s for s in engine.statements)


def test_the_record_says_where_the_server_follows_and_holds_no_password(
    replication: MySqlReplication, engine: MySql, code: str, tmp_path: Path
) -> None:
    replication.join(code, confirmed=True)

    record = replication.records.get(REPLICA, "reports", "mysql")
    assert record is not None and record.state == "following" and record.engine == "mysql"
    assert record.host == HOST and record.passfile == str(tmp_path / "authority/reports/ca.pem")
    assert parse(code).password not in str(record.to_dict())


def test_a_primary_that_cannot_be_reached_changes_nothing(
    replication: MySqlReplication, engine: MySql, code: str, host: Path, tmp_path: Path
) -> None:
    engine.reach_problem = "ERROR 2026 (HY000): SSL connection error: certificate verify failed"

    with pytest.raises(DatabaseBackupError, match="could not be reached") as caught:
        replication.join(code, confirmed=True)

    assert "certificate verify failed" in str(caught.value.details)
    assert engine.databases == ["shop"] and engine.loaded == []
    assert not any("DROP DATABASE" in s or "CHANGE" in s for s in engine.statements)
    assert include(host, "99-noust-replica.cnf") == "" and engine.restarts == 0
    assert not (tmp_path / "authority" / "reports").exists(), "the authority is not left"
    assert replication.records.records(REPLICA, "mysql") == []


def test_a_dump_that_fails_changes_nothing_and_leaves_no_data_behind(
    replication: MySqlReplication, engine: MySql, code: str, tmp_path: Path
) -> None:
    engine.dump_problem = "mysqldump: Got error: 1045: Access denied for user"

    with pytest.raises(DatabaseBackupError, match="could not be dumped") as caught:
        replication.join(code, confirmed=True)

    assert "Access denied" in str(caught.value.details)
    assert engine.databases == ["shop"] and engine.loaded == []
    assert not (tmp_path / "scratch" / "reports.sql").exists()


def test_a_load_that_fails_puts_the_previous_databases_back(
    replication: MySqlReplication,
    engine: MySql,
    code: str,
    host: Path,
    tmp_path: Path,
    flavour: str,
) -> None:
    engine.load_problem = "ERROR 1064 (42000) at line 9: You have an error in your SQL syntax"

    with pytest.raises(DatabaseBackupError, match="previous databases are back") as caught:
        replication.join(code, confirmed=True)

    assert "error in your SQL syntax" in str(caught.value.details)
    manager = replication.manager
    assert [name for name, _path in manager.restored] == ["shop"]  # type: ignore[attr-defined]
    assert engine.following is None
    assert include(host, "99-noust-replica.cnf") == ""
    assert engine.facts["read_only"] == "0"
    assert not (tmp_path / "scratch" / "reports.sql").exists()
    assert not (tmp_path / "authority" / "reports").exists()
    assert replication.records.records(REPLICA, "mysql") == []
    if flavour == "mysql":
        assert engine.restarts == 2, "restarted to turn GTIDs on, and again to take them off"


def test_a_replica_that_stops_with_an_error_is_put_back_with_the_servers_words(
    replication: MySqlReplication, engine: MySql, code: str
) -> None:
    engine.io_error = "error connecting to master 'noust_repl_reports@db.example.com:3306'"

    with pytest.raises(DatabaseBackupError, match="previous databases are back") as caught:
        replication.join(code, confirmed=True)

    assert "error connecting to" in str(caught.value.details)
    assert engine.following is None
    assert [name for name, _ in replication.manager.restored] == ["shop"]  # type: ignore[attr-defined]


def test_the_safety_dumps_can_be_discarded_once_the_replica_streams(
    replication: MySqlReplication, engine: MySql, code: str
) -> None:
    result = replication.join(code, confirmed=True, discard_previous=True)

    assert result["saved_as"] is None
    assert not replication.manager.safety["shop"].exists()  # type: ignore[attr-defined]


# ------------------------------------------------------------------ promoting


def joined(replication: MySqlReplication, code: str) -> None:
    """Join, so the next steps start from a replica."""
    replication.join(code, confirmed=True)


def test_promoting_needs_a_confirmation_and_says_what_stays_behind(
    replication: MySqlReplication, engine: MySql, code: str
) -> None:
    joined(replication, code)

    with pytest.raises(ValidationError, match="stops this server following") as caught:
        replication.promote(confirmed=False)

    assert "noust_repl_reports" in str(caught.value.details)
    assert engine.following is not None


def test_promoting_applies_what_was_read_stops_following_and_accepts_writes(
    replication: MySqlReplication,
    engine: MySql,
    code: str,
    host: Path,
    tmp_path: Path,
    flavour: str,
) -> None:
    joined(replication, code)

    result = replication.promote(confirmed=True)

    assert result["promoted"] is True and engine.following is None
    assert engine.facts["read_only"] == "0"
    stops = [s for s in engine.statements if "IO_THREAD" in s]
    assert stops, "the IO thread stops first so what was read can be applied"
    text = include(host, "99-noust-replica.cnf")
    assert "read_only" not in text and f"server_id = {derive_server_id('reports', 1)}" in text
    assert not (tmp_path / "authority" / "reports").exists()
    assert replication.records.records(REPLICA, "mysql") == []
    assert "noust db replica remove reports" in result["note"]


def test_a_server_that_follows_nothing_cannot_be_promoted(
    replication: MySqlReplication, engine: MySql
) -> None:
    with pytest.raises(ValidationError, match="not a replica"):
        replication.promote(confirmed=True)


def test_a_replica_noust_did_not_make_is_not_promoted_by_noust(
    replication: MySqlReplication, engine: MySql
) -> None:
    engine.following = {"host": "h", "port": "3306", "ssl": "Yes", "verify": "Yes"}

    with pytest.raises(ValidationError, match="did not set this server up"):
        replication.promote(confirmed=True)


def test_the_record_of_a_primary_still_followed_is_not_forgotten(
    replication: MySqlReplication, engine: MySql, code: str
) -> None:
    joined(replication, code)

    with pytest.raises(ValidationError, match="still follows that primary"):
        replication.remove("reports", confirmed=True)


# ------------------------------------------------------------------ watching


def test_the_primary_reads_each_replicas_connection_from_the_server(
    replication: MySqlReplication, engine: MySql, flavour: str
) -> None:
    prepare(replication)
    engine.processes = [
        ("203.0.113.7", "12", "TLS_AES_256_GCM_SHA384" if flavour == "mysql" else "")
    ]

    view = replication.status()

    assert view["role"] == "primary" and view["warnings"] == []
    entry = view["replicas"][0]
    assert entry["name"] == "reports" and entry["connected"] and entry["state"] == "streaming"
    assert entry["tls"] is True
    assert entry["client"] == "203.0.113.7"
    assert entry["tls_version"] == (
        "TLS_AES_256_GCM_SHA384" if flavour == "mysql" else "required by the account"
    )


def test_a_replica_that_is_not_connected_is_said_so(
    replication: MySqlReplication, engine: MySql
) -> None:
    prepare(replication)

    entry = replication.status()["replicas"][0]

    assert entry["connected"] is False and entry["state"] == "not connected"


def test_a_connection_whose_account_does_not_require_tls_and_that_has_none_is_called_out(
    replication: MySqlReplication, engine: MySql
) -> None:
    prepare(replication)
    engine.ssl_type = ""
    engine.processes = [("203.0.113.7", "12", "")]

    view = replication.status()

    assert view["replicas"][0]["tls"] is False
    assert any("without TLS" in warning for warning in view["warnings"])


def test_a_replica_reads_how_it_follows(
    replication: MySqlReplication, engine: MySql, code: str
) -> None:
    joined(replication, code)

    view = replication.status()

    assert view["role"] == "replica"
    follows = view["follows"]
    assert follows["name"] == "reports" and follows["state"] == "streaming"
    assert follows["primary"] == f"{HOST}:3306" and follows["replay_delay_seconds"] == 3
    assert follows["sslmode"] == "verify-full"


def test_a_replica_that_stopped_with_an_error_says_what_the_server_says(
    replication: MySqlReplication, engine: MySql, code: str
) -> None:
    joined(replication, code)
    engine.io_error = "Got fatal error 1236 from master when reading data from binary log"

    view = replication.status()

    assert view["follows"]["state"] == "stopped"
    assert any("fatal error 1236" in warning for warning in view["warnings"])


def test_a_server_with_nothing_to_do_with_replicas_is_standalone(
    replication: MySqlReplication,
) -> None:
    assert replication.status()["role"] == "standalone"


def test_the_server_id_of_a_replica_is_stable_for_its_name_and_never_its_primarys() -> None:
    assert derive_server_id("reports", 1) == derive_server_id("reports", 1)
    assert derive_server_id("reports", 1) != derive_server_id("standby", 1)
    value = derive_server_id("reports", 1)
    assert derive_server_id("reports", value) == value + 1
    assert all(2 <= derive_server_id(f"r{n}", 1) < 2**31 for n in range(200))


def test_an_older_mysql_empties_its_log_with_the_statement_it_has(
    replication: MySqlReplication, engine: MySql, code: str, flavour: str
) -> None:
    if flavour != "mysql":
        pytest.skip("only MySQL empties its binary log to load a dump")
    engine.facts["version"] = "8.0.40"
    from noust.managers.database.replication_mysql_text import forget_binary_log

    assert forget_binary_log((8, 0, 40)) == "RESET MASTER;"
    assert forget_binary_log((8, 4, 0)) == "RESET BINARY LOGS AND GTIDS;"
    assert forget_binary_log((9, 7, 0)) == "RESET BINARY LOGS AND GTIDS;"
