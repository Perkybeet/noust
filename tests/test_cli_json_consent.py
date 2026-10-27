# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``--json`` is not an answer.

``noust compliance disable ens-media --json`` turned the framework off with its input closed,
because the command took ``--json`` for "nobody is here to ask, so go ahead". About fifty commands
shared that reading, each in its own ``if not (yes or ctx.json_output)``. A rule held by each caller
has as many holes as there are callers (CONTRIBUTING, rule 4), so it is held where every command
passes: :class:`~noust.cli.app.NoustCommand` refuses a command that changes something, whose
question ``--yes`` (or ``--force``) spares, when it is run with ``--json`` and without that flag.
``--dry-run`` shows what would happen and is the other way to look.

The sweep walks the real command tree, so a command added tomorrow with a ``--yes`` is covered
without anyone remembering to ask. A command may hold the gate itself (``gates_json=True``: it
prints its plan and changes nothing without ``--yes``), and each of those is proved below.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from noust.cli import audit_policy
from noust.cli.app import Context, NoustCommand, cli, consent_options
from noust.core.audit import Actor, get_log
from noust.core.compliance import resolve
from noust.core.config import Config
from noust.core.store import NoustStore, get_store
from tests.test_cli_json import LEAF_COMMANDS

OPERATOR = Actor(kind="cli", id="0", name="root", via="sudo", source=None)

#: Commands with a ``--yes`` that does not spare a question, with why. The sweep fails on any other
#: ``--yes`` it does not recognise, so a new one is looked at once instead of passing unnoticed.
NOT_A_QUESTION: dict[str, str] = {
    "db limits": "its --yes accepts a memory limit below the engine's use; the manager refuses it "
    "otherwise, with or without --json, and no prompt is ever shown",
    "db settings": "its --yes accepts a restart the settings need; the manager refuses without it, "
    "with or without --json, and no prompt is ever shown",
    "server security confirm": "keeping a change asks nothing; the flag is accepted so that a script "
    "can pass -y to every command of the group",
}

#: Commands that hold the gate themselves under ``--json``, with where it is proved: they print their
#: plan and change nothing without the flag (``app migrate``, ``compliance``), or they ask whatever has
#: to be asked whether or not ``--json`` was given and decline when nobody answers (``fleet authorize``
#: and ``fleet link``, which the guided enrollment runs with ``--json`` and without ``--yes``).
GATES_JSON: dict[str, str] = {
    "app migrate": "tests/test_cli_app.py::test_json_without_yes_prints_the_plan_and_changes_nothing",
    "compliance disable": "tests/test_cli_json_consent.py::TestComplianceUnderJson",
    "compliance enable": "tests/test_cli_json_consent.py::TestComplianceUnderJson",
    "compliance set": "tests/test_cli_json_consent.py::TestComplianceUnderJson",
    "fleet authorize": "tests/test_cli_fleet.py::TestAuthorizeCommand::test_a_second_authorize_asks_under_json_too",
    "fleet link": "tests/test_cli_node_link.py::TestServer::test_a_second_link_asks_under_json_too",
}


def _context_for(path: str) -> click.Context:
    """
    Build the context a command has when ``--json`` was typed before its name.

    Args:
        path: The command's path, such as ``compliance disable``.

    Returns:
        A context whose parents are the real groups (the audit policy reads the path), whose
        shared state says ``--json``, and whose parameters are the defaults.
    """
    ctx = click.Context(cli, info_name="noust", obj=Context(json_output=True))
    command: click.Command = cli
    for name in path.split():
        assert isinstance(command, click.Group)
        found = command.get_command(ctx, name)
        assert found is not None
        command = found
        ctx = click.Context(command, parent=ctx, info_name=name)
    ctx.params = {p.name: p.get_default(ctx) for p in command.params if p.name}
    return ctx


def _asks(path: str, command: click.Command) -> bool:
    """
    Report whether a command changes something and has a flag that spares its question.

    Args:
        path: The command's path.
        command: The command.

    Returns:
        True when ``--json`` without that flag would change something unasked.
    """
    if not isinstance(command, NoustCommand) or not command._declares_json_option():
        return False
    if not consent_options(command):
        return False
    if audit_policy.is_read_only(command, path, _context_for(path).params):
        return False
    return True


ASKING = [(path, command) for path, command in LEAF_COMMANDS if _asks(path, command)]


def test_the_sweep_sees_the_commands_that_ask() -> None:
    """The walk is not vacuous: the commands the report names are among the ones it found."""
    paths = {path for path, _ in ASKING}

    assert {"compliance disable", "server security firewall disable", "app adopt"} <= paths
    assert len(paths) > 40


@pytest.mark.parametrize("path", [path for path, _ in ASKING], ids=[path for path, _ in ASKING])
def test_json_without_the_flag_that_spares_the_question_is_refused(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A command that asks before it changes something does not take ``--json`` for a yes.

    Either the chokepoint refuses it before its body runs, or the command declares that it holds
    the gate itself, and then the next tests prove it.
    """
    command = dict(ASKING)[path]
    assert isinstance(command, NoustCommand)
    ran: list[str] = []
    monkeypatch.setattr(command, "callback", lambda *args, **kwargs: ran.append(path))
    ctx = _context_for(path)

    if path in GATES_JSON:
        assert command.gates_json
        return
    assert not command.gates_json

    with pytest.raises(click.UsageError) as refused:
        command.invoke(ctx)

    assert ran == []
    message = refused.value.format_message()
    assert "--json" in message
    assert ("--dry-run" in message) == command.honours_dry_run
    assert any(flag in message for flag in ("--yes", "-y", "--force"))


@pytest.mark.parametrize("path", [path for path, _ in ASKING], ids=[path for path, _ in ASKING])
def test_the_flag_lets_the_command_through(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the flag, ``--json`` runs the command as it always did."""
    command = dict(ASKING)[path]
    assert isinstance(command, NoustCommand)
    ran: list[str] = []
    monkeypatch.setattr(command, "callback", lambda *args, **kwargs: ran.append(path))
    ctx = _context_for(path)
    for name in consent_options(command):
        ctx.params[name] = True
    monkeypatch.setattr(audit_policy, "audited", _no_audit)

    command.invoke(ctx)

    assert ran == [path]


def _no_audit(ctx: click.Context, command: click.Command) -> Any:
    """
    Stand in for the audit hook, which is not what these tests are about.

    Args:
        ctx: The command's context.
        command: The command.

    Returns:
        A context manager that does nothing.
    """
    import contextlib

    return contextlib.nullcontext()


def test_a_dry_run_is_the_other_way_to_look(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--dry-run --json`` is not refused: nothing changes under it."""
    command = dict(ASKING)["server security firewall disable"]
    assert isinstance(command, NoustCommand)
    ran: list[str] = []
    monkeypatch.setattr(command, "callback", lambda *args, **kwargs: ran.append("ran"))
    monkeypatch.setattr(audit_policy, "audited", _no_audit)
    ctx = _context_for("server security firewall disable")
    ctx.ensure_object(Context).dry_run = True

    command.invoke(ctx)

    assert ran == ["ran"]


def test_a_dry_run_is_no_way_to_look_at_a_call_to_another_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``manifest apply`` calls a server's API, which ``--dry-run`` does not rehearse."""
    command = dict(ASKING)["manifest apply"]
    ctx = _context_for("manifest apply")
    ctx.ensure_object(Context).dry_run = True

    assert isinstance(command, NoustCommand)
    assert command.consent_missing(ctx) == "--yes"


def test_a_command_that_only_reads_in_this_form_is_not_refused() -> None:
    """``db move --plan --json`` is a plan: the predicate says it reads, so there is nothing to ask."""
    path = "db move"
    command = dict(LEAF_COMMANDS)[path]
    ctx = _context_for(path)
    ctx.params["plan_only"] = True

    assert isinstance(command, NoustCommand)
    assert command.consent_missing(ctx) is None


def test_every_yes_the_sweep_does_not_recognise_is_accounted_for() -> None:
    """
    Recognising a question is by the flag and its help; the ones left out are written down.

    A ``--yes`` that spares no question (it accepts a warning the manager refuses without) is
    not a gate to hold, but it is not to be missed either: a new one fails here until it is read
    and listed, with the reason, in :data:`NOT_A_QUESTION`.
    """
    unrecognised = {}
    for path, command in LEAF_COMMANDS:
        if not isinstance(command, NoustCommand) or consent_options(command):
            continue
        if not command._declares_json_option():
            continue
        for param in command.params:
            if isinstance(param, click.Option) and {"--yes", "-y"} & set(param.opts):
                unrecognised[path] = param.help

    assert set(unrecognised) == set(NOT_A_QUESTION), unrecognised


def test_the_commands_that_hold_the_gate_themselves_are_the_ones_listed() -> None:
    """Holding the gate is declared, and each such command is proved in this file."""
    declared = {path for path, command in LEAF_COMMANDS if getattr(command, "gates_json", False)}

    assert declared == set(GATES_JSON)


@pytest.mark.parametrize("path", sorted(GATES_JSON), ids=sorted(GATES_JSON))
def test_the_proof_of_each_gate_exists(path: str) -> None:
    """The test a command names as its proof is there, in the file named."""
    file, _, rest = GATES_JSON[path].partition("::")
    text = (Path(__file__).parent.parent / file).read_text(encoding="utf-8")

    assert rest.rsplit("::", 1)[-1] in text


# ---------------------------------------------------------------------------
# The commands that hold the gate themselves
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_policy.audit, "cli_actor", lambda: OPERATOR)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = get_store()
    yield instance
    NoustStore.reset_instance()


def _events(action: str) -> list[dict[str, Any]]:
    return [e for e in get_log().read(limit=1000) if e["action"] == action]


def _run(*args: str) -> Any:
    # CliRunner gives the command an empty, closed input: nobody can answer a question.
    return CliRunner().invoke(cli, list(args))


class TestComplianceUnderJson:
    def test_disabling_a_framework_with_json_and_no_yes_shows_the_plan_and_changes_nothing(
        self, real_config: Config, store: NoustStore
    ) -> None:
        """The report: this turned ENS off on a central with the input closed."""
        assert _run("compliance", "enable", "ens-media", "--yes").exit_code == 0, "setup"
        assert resolve.enabled_ids(real_config) == ["ens-media"]

        result = _run("compliance", "disable", "ens-media", "--json", "--reason", "x")

        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["applied"] is False
        assert body["plan"]["disable"] == ["ens-media"]
        assert "--yes" in body["needs"]
        assert resolve.enabled_ids(real_config) == ["ens-media"]
        assert _events("security.framework.change")[0]["details"]["enabled"] == ["ens-media"]

    def test_the_global_spelling_is_held_too(self, real_config: Config, store: NoustStore) -> None:
        _run("compliance", "enable", "nis2", "--yes")

        result = _run("--json", "compliance", "disable", "nis2")

        assert json.loads(result.output)["applied"] is False
        assert resolve.enabled_ids(real_config) == ["nis2"]

    def test_enabling_with_json_and_no_yes_changes_nothing(
        self, real_config: Config, store: NoustStore
    ) -> None:
        result = _run("compliance", "enable", "iso-27001", "--json")

        body = json.loads(result.output)
        assert body["applied"] is False and body["plan"]["enable"] == ["iso-27001"]
        assert resolve.enabled_ids(real_config) == []
        assert _events("security.framework.change") == []

    def test_setting_a_control_with_json_and_no_yes_changes_nothing(
        self, real_config: Config, store: NoustStore
    ) -> None:
        result = _run("compliance", "set", "session_idle_minutes", "10", "--json")

        body = json.loads(result.output)
        assert body["applied"] is False
        assert real_config.get("auth.session.idle_minutes") != 10
        assert _events("security.control.change") == []

    def test_with_yes_json_applies_as_before(self, real_config: Config, store: NoustStore) -> None:
        result = _run("compliance", "enable", "gdpr-32", "--yes", "--json")

        body = json.loads(result.output)
        assert body["applied"] is True
        assert resolve.enabled_ids(real_config) == ["gdpr-32"]

    def test_a_plan_that_changes_nothing_says_so_in_json(
        self, real_config: Config, store: NoustStore
    ) -> None:
        result = _run("compliance", "disable", "nis2", "--json")

        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["applied"] is False and body["needs"] is None
        assert body["plan"]["disable"] == []

    def test_without_json_the_question_is_still_asked(
        self, real_config: Config, store: NoustStore
    ) -> None:
        _run("compliance", "enable", "nis2", "--yes")

        result = _run("compliance", "disable", "nis2")

        assert result.exit_code != 0
        assert resolve.enabled_ids(real_config) == ["nis2"]
