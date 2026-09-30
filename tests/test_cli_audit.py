# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The audited CLI (ENS G05) and the ``noust audit`` commands.

Every command that changes something records who ran it (the operating
system identity, through ``sudo``), what it was given (never a secret), why
(``--reason``, required under the ENS profile), and how it ended - and every
file it changed is linked to it by the correlation id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from noust.cli import audit_policy
from noust.cli.app import Context, NoustCommand, NoustGroup, cli, pass_context
from noust.core.audit import Actor, get_log
from noust.core.exceptions import NoustError
from noust.core.fs import get_fs

OPERATOR = Actor(kind="cli", id="1000", name="ana", via="sudo", source="10.9.8.7")


def events(action: str | None = None) -> list[dict[str, Any]]:
    """Every event written so far, oldest first."""
    found = list(reversed(get_log().read(limit=10_000)))
    return [entry for entry in found if action is None or entry["action"] == action]


@pytest.fixture(autouse=True)
def operator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whoever runs the command is ana, through sudo, from 10.9.8.7."""
    monkeypatch.setattr(audit_policy.audit, "cli_actor", lambda: OPERATOR)
    monkeypatch.setattr(audit_policy, "security_profile", lambda: "standard")


@click.group("thing", cls=NoustGroup)
def thing() -> None:
    """A group standing in for any command module."""


@thing.command("change")
@click.argument("name")
@click.option("--password", default=None)
@click.option("--set", "assignments", multiple=True)
@click.option("--target", type=click.Path(path_type=Path), default=None)
@pass_context
def change(
    ctx: Context, name: str, password: str | None, assignments: tuple[str, ...], target: Path | None
) -> None:
    """Change something."""
    if target is not None:
        get_fs().write_text(target, "changed\n")
    click.echo(f"changed {name}")


@thing.command("fail")
def fail() -> None:
    """Fail the way commands do."""
    raise NoustError("the thing could not be done", details="try again")


@thing.command("exit")
@click.pass_context
def exit_three(ctx: click.Context) -> None:
    """Exit with a code."""
    ctx.exit(3)


@thing.command("look", read_only=True)
def look() -> None:
    """Only read."""
    click.echo("looked")


@thing.command("name", read_only=lambda params: params.get("value") is None)
@click.argument("value", required=False)
def name_command(value: str | None) -> None:
    """Show the name, or change it: only reads without an argument."""
    click.echo(f"renamed {value}" if value else "the name")


@thing.command("config-set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Stand in for 'noust config set'."""


def run(*args: str, root: click.Group = thing) -> Any:
    return CliRunner().invoke(root, list(args), obj=Context(), catch_exceptions=False)


class TestAuditedCommands:
    def test_intent_and_outcome_under_one_correlation_id(self) -> None:
        result = run("change", "shop", "--reason", "CHG-1234")
        assert result.exit_code == 0, result.output

        start, end = events("cli.command.start"), events("cli.command")
        assert len(start) == len(end) == 1
        assert start[0]["resource"] == end[0]["resource"] == "change"
        assert start[0]["result"] == "started"
        assert end[0]["result"] == "ok"
        assert start[0]["corr"] == end[0]["corr"]
        assert start[0]["details"]["reason"] == end[0]["details"]["reason"] == "CHG-1234"
        assert start[0]["details"]["arguments"]["name"] == "shop"
        assert end[0]["details"]["exit_code"] == 0
        assert isinstance(end[0]["details"]["duration_ms"], int)

    def test_the_operating_system_identity_is_the_actor(self) -> None:
        run("change", "shop")
        entry = events("cli.command")[0]
        assert entry["actor"] == "cli:ana"
        assert entry["who"] == {
            "kind": "cli",
            "id": "1000",
            "name": "ana",
            "via": "sudo",
            "source": "10.9.8.7",
        }
        assert entry["ip"] == "10.9.8.7"

    def test_secrets_given_as_arguments_are_never_recorded(self) -> None:
        run(
            "change",
            "shop",
            "--password",
            "hunter2-hunter2",
            "--set",
            "API_TOKEN=tok-abcdef-123456",
            "--set",
            "DEBUG=1",
        )
        run("config-set", "notifications.channels.telegram.bot_token", "123456:ABCDEF-secret")
        raw = get_log().path.read_text()
        assert "hunter2" not in raw
        assert "tok-abcdef" not in raw
        assert "ABCDEF-secret" not in raw
        arguments = events("cli.command.start")[0]["details"]["arguments"]
        assert arguments["password"] == "***"
        assert arguments["assignments"] == ["API_TOKEN=***", "DEBUG=1"]

    def test_a_failure_is_recorded_with_its_message(self) -> None:
        with pytest.raises(NoustError):
            run("fail")
        entry = events("cli.command")[0]
        assert entry["result"] == "failure"
        assert entry["details"]["exit_code"] == 1
        assert entry["details"]["error"] == "the thing could not be done"
        assert entry["sev"] == 4

    def test_an_exit_code_is_recorded(self) -> None:
        assert run("exit").exit_code == 3
        entry = events("cli.command")[0]
        assert (entry["result"], entry["details"]["exit_code"]) == ("failure", 3)

    def test_what_the_command_changed_is_linked_to_it(self, tmp_path: Path) -> None:
        target = tmp_path / "etc" / "thing.conf"
        run("change", "shop", "--target", str(target))

        correlation = events("cli.command")[0]["corr"]
        changes = [entry for entry in events("host.fs") if entry["resource"] == str(target)]
        assert changes and changes[0]["corr"] == correlation
        assert changes[0]["details"] == {"op": "write"}

    def test_a_read_only_command_records_nothing(self) -> None:
        assert run("look").output == "looked\n"
        assert events() == []

    def test_a_rehearsal_records_nothing(self) -> None:
        from noust.core.fs import DryRunFileSystem, set_fs

        set_fs(DryRunFileSystem())
        state = Context(dry_run=True, dry_run_active=True)
        result = CliRunner().invoke(thing, ["change", "shop"], obj=state)
        set_fs(None)
        assert result.exit_code == 0
        assert not get_log().path.exists()


class TestReason:
    def test_the_ens_profile_requires_a_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(audit_policy, "security_profile", lambda: "ens-medium")
        result = CliRunner().invoke(thing, ["change", "shop"], obj=Context())
        assert result.exit_code == 2
        assert "--reason" in result.output
        assert "changed" not in result.output
        assert events() == []

    def test_with_a_reason_it_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(audit_policy, "security_profile", lambda: "ens-medium")
        result = run("change", "shop", "--reason", "CHG-1")
        assert result.exit_code == 0
        assert events("cli.command")[0]["details"]["reason"] == "CHG-1"

    def test_read_only_commands_never_need_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(audit_policy, "security_profile", lambda: "ens-medium")
        assert run("look").exit_code == 0

    def test_a_command_that_only_reads_in_one_form_needs_none_in_that_form(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(audit_policy, "security_profile", lambda: "ens-medium")

        shown = run("name")
        refused = CliRunner().invoke(thing, ["name", "web-2"], obj=Context())

        assert shown.exit_code == 0 and shown.output == "the name\n"
        assert refused.exit_code == 2 and "--reason" in refused.output
        assert "renamed" not in refused.output
        assert events() == []

    def test_the_form_that_changes_is_recorded(self) -> None:
        run("name")
        run("name", "web-2")

        recorded = events("cli.command")
        assert [entry["resource"] for entry in recorded] == ["name"]
        assert recorded[0]["details"].get("exit_code") == 0

    def test_the_root_reason_before_the_command_name(self) -> None:
        state = Context(reason="INC-77")
        CliRunner().invoke(thing, ["change", "shop"], obj=state, catch_exceptions=False)
        assert events("cli.command")[0]["details"]["reason"] == "INC-77"

    def test_the_real_root_accepts_it_on_either_side(self) -> None:
        options = {opt for param in cli.params for opt in getattr(param, "opts", [])}
        assert "--reason" in options
        leaf = cli.get_command(click.Context(cli), "delete")
        assert leaf is not None
        assert "--reason" in {opt for param in leaf.params for opt in getattr(param, "opts", [])}


def walk(command: click.Command, path: tuple[str, ...] = ()):
    yield path, command
    if isinstance(command, click.Group):
        ctx = click.Context(command)
        for name in command.list_commands(ctx):
            sub = command.get_command(ctx, name)
            if sub is not None:
                yield from walk(sub, (*path, name))


class TestPolicy:
    def test_every_listed_read_only_command_exists(self) -> None:
        paths = {
            " ".join(path) for path, command in walk(cli) if not isinstance(command, click.Group)
        }
        stale = sorted(audit_policy.READ_ONLY_COMMANDS - paths)
        assert not stale, f"READ_ONLY_COMMANDS names commands that do not exist: {stale}"

    def test_every_command_the_hook_cannot_see_is_read_only(self) -> None:
        """A leaf built outside NoustCommand is invisible to the audit; it may only read."""
        blind = [
            " ".join(path)
            for path, command in walk(cli)
            if not isinstance(command, click.Group)
            and not isinstance(command, NoustCommand)
            and " ".join(path) not in audit_policy.READ_ONLY_COMMANDS
        ]
        assert not blind, f"not audited and not declared read-only: {blind}"

    def test_the_audit_commands_that_only_read_say_so(self) -> None:
        group = cli.get_command(click.Context(cli), "audit")
        assert isinstance(group, click.Group)
        for name in ("list", "show", "verify", "export", "events", "status", "review", "ship"):
            assert getattr(group.commands[name], "read_only", False), name
        assert not getattr(group.commands["prune"], "read_only", False)

    @pytest.mark.parametrize(
        ("path", "params"),
        [
            ("app sandbox status", {}),
            ("user list", {}),
            ("user exception list", {}),
            ("notify telegram-chats", {}),
            ("github installations", {"sync": False}),
        ],
    )
    def test_commands_that_only_look_need_no_reason(
        self, path: str, params: dict[str, Any]
    ) -> None:
        """Production under ens-medium asked for a reason to look at these."""
        command = {" ".join(p): c for p, c in walk(cli)}[path]
        assert audit_policy.is_read_only(command, path, params)

    def test_syncing_installations_is_a_change(self) -> None:
        command = {" ".join(p): c for p, c in walk(cli)}["github installations"]
        assert not audit_policy.is_read_only(command, "github installations", {"sync": True})

    def test_sanitize_masks_by_name_and_by_assignment(self) -> None:
        recorded = audit_policy.sanitize_arguments(
            {
                "token": "abc-secret-value",
                "key": "web.port",
                "value": "8081",
                "env": ("SECRET_KEY=zzzzzzzz", "PORT=3000"),
                "url": "https://user:pa55word-long@git.example/x.git",
                "unset": None,
                "flag": False,
            }
        )
        assert recorded["token"] == "***"
        assert recorded["value"] == "8081"
        assert recorded["env"] == ["SECRET_KEY=***", "PORT=3000"]
        assert "pa55word" not in recorded["url"]
        assert "unset" not in recorded and "flag" not in recorded


def invoke(*args: str) -> Any:
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


def seed(count: int = 3) -> None:
    from noust.core.audit import record

    for index in range(count):
        record("apps.update", actor=Actor(kind="user", name=f"u{index}"), target=f"app:{index}")


class TestAuditCommands:
    def test_list_and_show(self) -> None:
        seed()
        listed = json.loads(invoke("audit", "list", "--json").output)["events"]
        assert [entry["actor"] for entry in listed[:3]] == ["u2", "u1", "u0"]
        table = invoke("audit", "list", "--action", "apps.update").output
        assert "app:2" in table
        shown = json.loads(invoke("audit", "show", str(listed[0]["seq"])).output)
        assert shown["id"] == listed[0]["id"]

    def test_show_an_unknown_event(self) -> None:
        seed(1)
        with pytest.raises(NoustError, match="No audit event"):
            invoke("audit", "show", "999")

    def test_verify_passes_says_its_limit_and_is_recorded(self) -> None:
        seed()
        result = invoke("audit", "verify")
        assert result.exit_code == 0
        assert "The audit chain holds" in result.output
        assert "Root on this machine" in result.output
        assert events("audit.verify")[0]["result"] == "ok"
        assert events("cli.command") == []

    def test_verify_names_the_first_broken_link(self) -> None:
        seed(4)
        path = get_log().path
        lines = path.read_text().splitlines()
        tampered = json.loads(lines[2])
        tampered["actor"] = "nobody"
        lines[2] = json.dumps(tampered)
        path.write_text("\n".join(lines) + "\n")

        result = invoke("audit", "verify", "--json")

        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert payload["broken"]["line"] == 3
        assert "MAC does not match" in payload["broken"]["reason"]

    def test_export_writes_a_private_copy_and_is_recorded(self, tmp_path: Path) -> None:
        seed()
        output = tmp_path / "export.ndjson"
        result = invoke("audit", "export", "--output", str(output))
        assert result.exit_code == 0
        exported = [json.loads(line) for line in output.read_text().splitlines()]
        assert [entry["action"] for entry in exported][:2] == ["audit.chain_start", "apps.update"]
        assert output.stat().st_mode & 0o777 == 0o600
        assert events("audit.export")[0]["details"]["events"] == 4

    def test_export_csv_for_a_period(self) -> None:
        seed()
        result = invoke("audit", "export", "--format", "csv", "--since", "2000-01-01")
        header, *rows = result.output.splitlines()
        assert header.startswith("seq,ts,actor,action")
        assert len(rows) == 4
        assert invoke("audit", "export", "--until", "2000-01-01").output == ""

    def test_review_records_an_attestation_with_the_chain_state(self) -> None:
        seed()
        result = invoke(
            "audit", "review", "--from", "2000-01-01", "--to", "2100-12-31", "--notes", "weekly"
        )
        assert result.exit_code == 0, result.output
        (review,) = events("audit.review")
        assert review["actor"] == "cli:ana"
        assert review["details"]["notes"] == "weekly"
        assert review["details"]["chain_ok"] is True
        assert review["details"]["events_in_period"] == 4
        assert review["resource"] == "2000-01-01..2100-12-31"
        listed = invoke("audit", "review", "--list").output
        assert "weekly" in listed

    def test_review_needs_a_period(self) -> None:
        result = CliRunner().invoke(cli, ["audit", "review"])
        assert result.exit_code == 2
        assert "--from" in result.output

    def test_events_prints_the_catalog(self) -> None:
        assert "| `audit.review` |" in invoke("audit", "events", "--markdown").output

    def test_status(self) -> None:
        seed(1)
        payload = json.loads(invoke("audit", "status", "--json").output)
        assert payload["status"] == "ok"

    def test_prune_is_audited_as_a_change(self) -> None:
        seed(1)
        result = invoke("audit", "prune", "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["audit_files"] == []
        assert events("cli.command")[0]["resource"] == "audit prune"

    def test_ship_with_nowhere_to_ship(self) -> None:
        assert "No destination" in invoke("audit", "ship").output
