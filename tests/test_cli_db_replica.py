# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db replica``: read replicas of the host's PostgreSQL, over TLS (checklist C-14).

The command parses and prints; the manager decides. What is pinned: what the operator types
reaches the manager as the values it knows, a refusal prints its reasons and exits non-zero
without anything having been changed, nothing is restarted, replaced or cut off without being
asked (``--yes``, ``--replace-data``) or after the operator said no, ``--plan`` changes nothing,
the join code goes to a file only root reads when asked for one and is never part of ``--json``
output of a plan, and ``--json`` is JSON alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.audit_policy import is_read_only
from noust.cli.commands import db_replica
from noust.core.exceptions import DatabaseBackupError, DatabaseEngineError, ValidationError
from noust.managers.database.replication import JoinPlan, PreparePlan
from noust.managers.database.replication_token import ReplicaToken, encode

TOKEN = ReplicaToken(
    name="reports",
    host="db.example.com",
    port=5432,
    role="noust_repl_reports",
    password="aB3" * 12,
    slot="noust_reports",
    major=16,
    size=1024,
    requires={
        "max_connections": 100,
        "max_locks_per_transaction": 64,
        "max_prepared_transactions": 0,
        "max_wal_senders": 10,
        "max_worker_processes": 8,
    },
)
CODE = encode(TOKEN)


class FakeReplication:
    """Stands in for the manager: records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.problems: list[str] = []
        self.restart = True
        self.refuse: Exception | None = None
        self.view: dict[str, Any] = {
            "engine": "postgresql",
            "role": "standalone",
            "replicas": [],
            "follows": None,
            "warnings": [],
        }

    def plan_prepare(self, name: str, **kwargs: Any) -> PreparePlan:
        self.calls.append(("plan_prepare", (name, kwargs)))
        if self.refuse:
            raise self.refuse
        return PreparePlan(
            name=name,
            account=f"noust_repl_{name}",
            slot=f"noust_{name}",
            host=kwargs["host"],
            port=5432,
            source="203.0.113.7/32",
            listen="localhost,10.0.0.5" if self.restart else None,
            restart=self.restart,
            steps=["Create the account", "Create the slot"],
            problems=list(self.problems),
            warnings=["PostgreSQL restarts once."] if self.restart else [],
        )

    def prepare(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("prepare", (name, kwargs)))
        return {"token": CODE, "plan": {"ok": True}, "record": {"name": name}}

    def token(self, name: str) -> str:
        self.calls.append(("token", name))
        if self.refuse:
            raise self.refuse
        return CODE

    def plan_join(self, code: str) -> JoinPlan:
        self.calls.append(("plan_join", code))
        if self.refuse:
            raise self.refuse
        return JoinPlan(
            token=TOKEN,
            data_directory="/var/lib/postgresql/16/main",
            saved_as="/var/lib/postgresql/16/main.noust-before-replica",
            databases=["shop"],
            free_bytes=10**12,
            needed_bytes=1100,
            capacity={"max_connections": 100},
            problems=list(self.problems),
            warnings=["These databases of this server are replaced: shop"],
        )

    def join(self, code: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("join", (code, kwargs)))
        return {
            "name": "reports",
            "primary": "db.example.com:5432",
            "saved_as": None if kwargs["discard_previous"] else "/var/lib/postgresql/16/main.old",
            "state": "streaming",
            "seconds": 40,
        }

    def status(self) -> dict[str, Any]:
        self.calls.append(("status", None))
        return self.view

    def promote(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("promote", kwargs))
        if self.refuse:
            raise self.refuse
        return {"promoted": True, "name": "reports", "note": "db.example.com still holds a slot."}

    def remove(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("remove", (name, kwargs)))
        if self.refuse:
            raise self.refuse
        return {"removed": True, "side": "primary", "steps": ["removed the pg_hba.conf line"]}


@pytest.fixture
def cli_runner() -> CliRunner:
    """A Click test runner."""
    return CliRunner()


@pytest.fixture
def manager(runner, monkeypatch: pytest.MonkeyPatch) -> FakeReplication:
    """The fake manager, in the place every command here builds its own."""
    fake = FakeReplication()
    engines: list[str] = []
    fake.engines = engines  # type: ignore[attr-defined]

    def build(logger: Any, engine: str = "postgresql") -> FakeReplication:
        engines.append(engine)
        return fake

    monkeypatch.setattr(db_replica, "_replication", build)
    return fake


def invoke(cli_runner: CliRunner, *args: str, input: str | None = None) -> Any:
    """Run ``noust db replica ...``."""
    return cli_runner.invoke(cli_app.cli, ["db", "replica", *args], input=input)


def calls(manager: FakeReplication, name: str) -> list[Any]:
    """The arguments of each call of one method."""
    return [arguments for method, arguments in manager.calls if method == name]


PREPARE = ["prepare", "reports", "--from", "203.0.113.7", "--host", "db.example.com"]


# ------------------------------------------------------------------ prepare


def test_what_the_operator_types_reaches_the_manager_as_the_values_it_knows(
    cli_runner: CliRunner, manager: FakeReplication, tmp_path: Path
) -> None:
    authority = tmp_path / "ca.pem"
    authority.write_text("PEM\n")

    result = invoke(
        cli_runner,
        *PREPARE,
        "--listen",
        "10.0.0.5",
        "--ca-file",
        str(authority),
        "--open-firewall",
        "--yes",
    )

    assert result.exit_code == 0, result.output
    name, arguments = calls(manager, "prepare")[0]
    assert name == "reports"
    assert arguments == {
        "source": "203.0.113.7",
        "host": "db.example.com",
        "listen": "10.0.0.5",
        "ca_pem": "PEM\n",
        "open_firewall": True,
        "confirmed": True,
    }


def test_the_join_code_is_printed_once_with_where_to_carry_it(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, *PREPARE, "--yes")

    assert result.exit_code == 0, result.output
    assert CODE in result.output
    assert "holds the replication password" in result.output
    assert "noust db replica join" in result.output


def test_the_join_code_goes_to_a_file_only_root_reads_when_asked(
    cli_runner: CliRunner, manager: FakeReplication, tmp_path: Path
) -> None:
    target = tmp_path / "join.token"

    result = invoke(cli_runner, *PREPARE, "--token-file", str(target), "--yes")

    assert result.exit_code == 0, result.output
    assert CODE not in result.output
    assert target.read_text() == CODE + "\n"
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_planning_changes_nothing_and_says_what_would_be_done(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, *PREPARE, "--plan")

    assert result.exit_code == 0, result.output
    assert "Create the account" in result.output and "PostgreSQL restarts once." in result.output
    assert calls(manager, "prepare") == []
    assert CODE not in result.output


def test_a_plan_with_problems_prints_them_and_exits_non_zero_without_preparing(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    manager.problems = ["PostgreSQL is not serving TLS."]

    result = invoke(cli_runner, *PREPARE, "--yes")

    assert result.exit_code == 1
    assert "! PostgreSQL is not serving TLS." in result.output
    assert calls(manager, "prepare") == []


def test_a_restart_is_asked_for_and_a_no_changes_nothing(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, *PREPARE, input="n\n")

    assert "Restart PostgreSQL now?" in result.output
    assert "Nothing was changed." in result.output
    assert calls(manager, "prepare") == []


def test_no_question_is_asked_when_nothing_is_restarted(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    manager.restart = False

    result = invoke(cli_runner, *PREPARE)

    assert result.exit_code == 0, result.output
    assert len(calls(manager, "prepare")) == 1


def test_a_bad_name_is_refused_with_its_fix(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    manager.refuse = ValidationError("'Bad Name' cannot be the name of a replica", field="name")

    result = invoke(cli_runner, *PREPARE)

    assert result.exit_code == 1
    assert "cannot be the name of a replica" in result.output


def test_prepare_as_json_is_json_alone(cli_runner: CliRunner, manager: FakeReplication) -> None:
    result = invoke(cli_runner, *PREPARE, "--yes", "--json")

    assert json.loads(result.output)["token"] == CODE


def test_an_authority_file_that_does_not_exist_is_refused_by_click(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, *PREPARE, "--ca-file", "/nowhere/ca.pem")

    assert result.exit_code == 2 and calls(manager, "prepare") == []


# ------------------------------------------------------------------ token


def test_the_code_of_a_prepared_replica_can_be_shown_again(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "token", "reports")

    assert CODE in result.output and calls(manager, "token") == ["reports"]


def test_the_code_of_a_replica_that_does_not_exist_is_refused(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    manager.refuse = ValidationError("No replica named nope is prepared on this server")

    result = invoke(cli_runner, "token", "nope")

    assert result.exit_code == 1 and "No replica named nope" in result.output


# ------------------------------------------------------------------ join


def test_the_join_asks_before_it_replaces_the_servers_data(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "join", CODE, input="n\n")

    assert "Replace the PostgreSQL data of this server" in result.output
    assert "Nothing was changed." in result.output
    assert calls(manager, "join") == []


def test_replace_data_is_the_answer_to_that_question(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "join", CODE, "--replace-data", "--discard-previous")

    assert result.exit_code == 0, result.output
    code, arguments = calls(manager, "join")[0]
    assert code == CODE and arguments == {"confirmed": True, "discard_previous": True}
    assert "follows db.example.com:5432 as reports" in result.output


def test_the_old_data_directory_is_named_so_the_operator_can_delete_it(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "join", CODE, "--replace-data")

    assert "/var/lib/postgresql/16/main.old" in result.output


def test_the_code_can_be_read_from_a_file(
    cli_runner: CliRunner, manager: FakeReplication, tmp_path: Path
) -> None:
    source = tmp_path / "join.token"
    source.write_text(CODE + "\n")

    result = invoke(cli_runner, "join", "--token-file", str(source), "--replace-data")

    assert result.exit_code == 0, result.output
    assert calls(manager, "join")[0][0] == CODE


def test_without_a_code_the_join_says_what_to_give(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "join", "--replace-data")

    assert result.exit_code == 1 and "Give the join code" in result.output


def test_planning_a_join_shows_what_is_replaced_and_never_the_password(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "join", CODE, "--plan")

    assert result.exit_code == 0, result.output
    assert "shop" in result.output and "noust-before-replica" in result.output
    assert TOKEN.password not in result.output
    assert calls(manager, "join") == []


def test_planning_a_join_as_json_has_no_password(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "join", CODE, "--plan", "--json")

    data = json.loads(result.output)
    assert data["ok"] is True and data["databases"] == ["shop"]
    assert TOKEN.password not in result.output


def test_a_join_that_cannot_work_exits_non_zero_and_asks_nothing(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    manager.problems = ["The primary runs PostgreSQL 16 and this server runs 15."]

    result = invoke(cli_runner, "join", CODE, "--replace-data")

    assert result.exit_code == 1 and "! The primary runs PostgreSQL 16" in result.output
    assert calls(manager, "join") == []


def test_a_join_that_fails_prints_the_engines_own_words(
    cli_runner: CliRunner, manager: FakeReplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(code: str, **kwargs: Any) -> dict[str, Any]:
        raise DatabaseBackupError(
            "The primary's cluster could not be copied",
            details='FATAL:  password authentication failed for user "noust_repl_reports"',
        )

    monkeypatch.setattr(manager, "join", fail)

    result = invoke(cli_runner, "join", CODE, "--replace-data")

    assert result.exit_code == 1
    assert "password authentication failed" in result.output


# ------------------------------------------------------------------ promote and remove


def test_promoting_asks_and_a_no_changes_nothing(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "promote", input="n\n")

    assert "Promote this replica?" in result.output and calls(manager, "promote") == []


def test_promoting_says_what_the_old_primary_still_holds(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "promote", "--yes")

    assert result.exit_code == 0, result.output
    assert calls(manager, "promote") == [{"confirmed": True}]
    assert "still holds a slot" in result.output


def test_a_promotion_the_manager_refuses_exits_non_zero(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    manager.refuse = ValidationError("This server is not a replica")

    result = invoke(cli_runner, "promote", "--yes")

    assert result.exit_code == 1 and "not a replica" in result.output


def test_removing_asks_and_lists_what_it_did(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    declined = invoke(cli_runner, "remove", "reports", input="n\n")
    assert "Remove the replica reports?" in declined.output and calls(manager, "remove") == []

    result = invoke(cli_runner, "remove", "reports", "--restore-listen", "--yes")

    assert result.exit_code == 0, result.output
    assert calls(manager, "remove")[0] == (
        "reports",
        {"confirmed": True, "restore_listen": True},
    )
    assert "removed the pg_hba.conf line" in result.output


# ------------------------------------------------------------------ status


def test_a_standalone_server_is_told_where_to_start(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "status")

    assert "no replicas and follows no primary" in result.output
    assert "noust db replica prepare" in result.output


def test_a_primary_shows_each_replica_its_tls_and_what_its_slot_holds(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    manager.view = {
        "engine": "postgresql",
        "role": "primary",
        "replicas": [
            {
                "name": "reports",
                "state": "streaming",
                "source": "203.0.113.7/32",
                "tls": True,
                "tls_version": "TLSv1.3",
                "lag_bytes": 2048,
                "retained_bytes": 3 * 1024**3,
            },
            {
                "name": "standby",
                "state": "not connected",
                "source": "203.0.113.8/32",
                "tls": None,
                "tls_version": None,
                "lag_bytes": None,
                "retained_bytes": None,
            },
        ],
        "follows": None,
        "warnings": ["standby is not connected and its slot holds a lot."],
    }

    result = invoke(cli_runner, "status")

    assert "reports: streaming" in result.output and "TLSv1.3" in result.output
    assert "behind 2.0 KiB" in result.output and "log held 3.0 GiB" in result.output
    assert "standby: not connected" in result.output
    assert "its slot holds a lot" in result.output


def test_a_replica_shows_where_it_follows_and_where_its_old_data_is(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    manager.view = {
        "engine": "postgresql",
        "role": "replica",
        "replicas": [],
        "follows": {
            "name": "reports",
            "state": "streaming",
            "primary": "db.example.com:5432",
            "replay_delay_seconds": 1,
            "unreplayed_bytes": 0,
            "sslmode": "verify-full",
            "saved_as": "/var/lib/postgresql/16/main.noust-before-replica",
        },
        "warnings": [],
    }

    result = invoke(cli_runner, "status")

    assert "Follows db.example.com:5432 as reports: streaming" in result.output
    assert "sslmode:       verify-full" in result.output
    assert "main.noust-before-replica" in result.output


def test_the_status_as_json_is_json_alone(cli_runner: CliRunner, manager: FakeReplication) -> None:
    result = invoke(cli_runner, "status", "--json")

    views = json.loads(result.output)
    assert [view["role"] for view in views] == ["standalone", "standalone"]


def test_the_status_of_one_engine_is_one_view(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    result = invoke(cli_runner, "status", "--engine", "mysql", "--json")

    assert json.loads(result.output)["role"] == "standalone"
    assert manager.engines == ["mysql"]  # type: ignore[attr-defined]


def test_every_engine_is_asked_for_when_none_is_named(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    invoke(cli_runner, "status")

    assert manager.engines == ["postgresql", "mysql"]  # type: ignore[attr-defined]


def test_an_engine_that_is_not_installed_is_left_out_unless_it_is_asked_for(
    cli_runner: CliRunner, manager: FakeReplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def absent() -> dict[str, Any]:
        raise DatabaseEngineError("MySQL or MariaDB is not installed on this server")

    monkeypatch.setattr(manager, "status", absent)

    quiet = invoke(cli_runner, "status")
    asked = invoke(cli_runner, "status", "--engine", "mysql")

    assert quiet.exit_code == 0 and "no replicas and follows no primary" in quiet.output
    assert asked.exit_code == 1 and "is not installed" in asked.output


def test_the_engine_a_command_works_on_is_the_one_named(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    invoke(cli_runner, *PREPARE, "--engine", "mysql", "--plan")
    invoke(cli_runner, "token", "reports", "-e", "mysql")
    invoke(cli_runner, "promote", "-e", "mysql", "--yes")
    invoke(cli_runner, "remove", "reports", "-e", "mysql", "--yes")

    assert manager.engines == ["mysql"] * 4  # type: ignore[attr-defined]


def test_a_join_goes_to_the_engine_the_code_is_for(
    cli_runner: CliRunner, manager: FakeReplication
) -> None:
    mysql_code = encode(
        ReplicaToken(
            name="reports",
            host="db.example.com",
            port=3306,
            role="noust_repl_reports",
            password="aB3" * 12,
            slot="",
            major=1104,
            requires={"server_id": 1},
            engine="mysql",
            flavour="mariadb",
        )
    )

    invoke(cli_runner, "join", mysql_code, "--plan")
    invoke(cli_runner, "join", CODE, "--plan")

    assert manager.engines == ["mysql", "postgresql"]  # type: ignore[attr-defined]


def test_reading_the_status_is_a_read_the_audit_policy_does_not_record() -> None:
    group = db_replica.replica

    assert is_read_only(group.commands["status"], "db replica status", {})
    for changing in ("prepare", "join", "promote", "remove", "token"):
        assert not is_read_only(group.commands[changing], f"db replica {changing}", {})
