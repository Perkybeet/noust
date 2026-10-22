# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app exec`` and ``noust audit recordings``: the command line over the same
managers the console uses.

What is pinned: the command after ``--`` reaches the program as a vector, in the
application's sandbox; the exit status is the command's; a rehearsal runs
nothing; and a recording is printed safely (a program's escape sequences are
not obeyed by the reader's own terminal), says whether it still matches the
digest on record, and reading it is on record.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.core import audit
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import App, NoustStore
from noust.terminal import targets
from noust.terminal.recording import SessionRecorder

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            app_path=str(tmp_path / "shop"),
            status="running",
            layout="releases",
            identity="noust-app-shop",
        )
    )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(targets, "_unit_variables", lambda app: {})


@pytest.fixture
def runner(store: NoustStore) -> Iterator[FakeRunner]:
    fake = FakeRunner()
    set_runner(fake)
    yield fake
    set_runner(None)


def invoke(*args: str) -> Result:
    return CliRunner().invoke(root_cli, list(args))


class TestAppExec:
    def test_the_command_after_the_dashes_is_the_vector_the_program_gets(self, runner):
        runner.script(["rails", "runner"], stdout="42\n")

        result = invoke("app", "exec", DOMAIN, "--", "rails", "runner", "puts 6*7")

        assert result.exit_code == 0, result.output
        assert "42" in result.output
        argv, spec = runner.sandboxed[0]
        assert argv == ("rails", "runner", "puts 6*7")
        assert spec.user == "noust-app-shop" and spec.interactive is False

    def test_what_looks_like_an_option_of_the_command_is_its_own(self, runner):
        result = invoke("app", "exec", DOMAIN, "--", "ls", "-la", "--color=never")

        assert result.exit_code == 0, result.output
        assert runner.sandboxed[0][0] == ("ls", "-la", "--color=never")

    def test_a_shell_line_is_not_interpreted(self, runner):
        runner.script(["echo"], stdout="a; rm -rf /\n")

        result = invoke("app", "exec", DOMAIN, "--", "echo", "a; rm -rf /")

        assert result.exit_code == 0, result.output
        assert runner.sandboxed[0][0] == ("echo", "a; rm -rf /")

    def test_the_exit_status_is_the_commands(self, runner):
        runner.script(["false"], exit_code=3)

        result = invoke("app", "exec", DOMAIN, "--", "false")

        assert result.exit_code == 3

    def test_nothing_to_run_says_how_to_say_it(self, runner):
        result = invoke("app", "exec", DOMAIN)

        assert result.exit_code != 0
        assert "after --" in str(result.exception)

    def test_an_unknown_application_is_not_found(self, runner):
        result = invoke("app", "exec", "nope.example.com", "--", "ls")

        assert result.exit_code != 0
        assert "not found" in str(result.exception).lower()

    def test_the_time_is_bounded(self, runner):
        result = invoke("app", "exec", DOMAIN, "--timeout", "100000000", "--", "ls")

        assert result.exit_code != 0
        assert "terminal.exec.max_seconds" in str(result.exception)

    def test_a_rehearsal_runs_nothing(self, runner):
        result = invoke("--dry-run", "app", "exec", DOMAIN, "--", "rake", "db:drop")

        assert result.exit_code == 0, result.output
        assert "Would run" in result.output and "rake db:drop" in result.output
        assert runner.calls == []

    def test_the_json_form_says_how_it_went(self, runner):
        runner.script(["ls"], stdout="file\n")

        result = invoke("app", "exec", DOMAIN, "--json", "--", "ls")

        last = result.output.strip().splitlines()[-1]
        assert json.loads(last)["exit_code"] == 0
        assert "file" in result.output

    def test_it_is_on_record_like_every_command_that_changes_something(self, runner):
        invoke("app", "exec", DOMAIN, "--reason", "fix the cache", "--", "rake", "cache:clear")

        actions = {event["action"] for event in audit.get_log().read(limit=50)}

        assert {"terminal.exec", "cli.command"} <= actions


class TestAuditRecordings:
    def record(self, store: NoustStore) -> SessionRecorder:
        rec = SessionRecorder(
            store.db_path.parent / "terminal-recordings",
            session_id="0123456789abcdef",
            meta={
                "kind": "app",
                "domain": DOMAIN,
                "label": DOMAIN,
                "account": "noust-app-shop",
                "actor": "maria",
            },
            rows=24,
            cols=80,
            max_bytes=100_000,
        )
        rec.output("\x1b[2J\x1b]0;title\x07$ ")
        rec.input("whoami\r")
        rec.output("noust-app-shop\r\n")
        rec.close(reason="closed", exit_code=0)
        return rec

    def test_the_list_says_who_had_a_shell_where_and_how_it_ended(self, store):
        rec = self.record(store)

        result = invoke("audit", "recordings")

        assert result.exit_code == 0, result.output
        for word in (rec.id, "maria", DOMAIN, "noust-app-shop", "closed", "complete"):
            assert word in result.output

    def test_it_can_be_filtered_and_printed_as_json(self, store):
        self.record(store)

        listed = json.loads(invoke("audit", "recordings", "--json", "--app", DOMAIN).output)
        other = json.loads(invoke("audit", "recordings", "--json", "--app", "x.example").output)

        assert listed["recordings"][0]["domain"] == DOMAIN
        assert other["recordings"] == []

    def test_an_empty_list_says_so(self, store):
        assert "No terminal sessions" in invoke("audit", "recordings").output

    def test_a_recording_is_printed_safe_and_with_its_verdict(self, store):
        rec = self.record(store)

        result = invoke("audit", "recording", rec.id)

        assert result.exit_code == 0, result.output
        assert "\x1b" not in result.output  # the program's escape sequences are not obeyed
        assert "intact" in result.output
        assert "> whoami<enter>" in result.output
        assert "noust-app-shop" in result.output

    def test_without_the_input_only_what_was_printed_is_shown(self, store):
        rec = self.record(store)

        result = invoke("audit", "recording", rec.id, "--no-input")

        assert "whoami" not in result.output

    def test_a_changed_recording_is_called_out(self, store):
        rec = self.record(store)
        rec.path.write_text(rec.path.read_text().replace("whoami", "ls -la"), encoding="utf-8")

        result = invoke("audit", "recording", rec.id)

        assert "does NOT match" in result.output

    def test_the_cast_is_given_as_it_is_for_a_player(self, store):
        rec = self.record(store)

        result = invoke("audit", "recording", rec.id, "--format", "cast")

        assert result.output == rec.path.read_text(encoding="utf-8")
        assert "\x1b" not in result.output  # JSON-escaped, so it is safe to print too

    def test_reading_one_is_on_record(self, store):
        rec = self.record(store)
        invoke("audit", "recording", rec.id)

        event = audit.get_log().read(action="terminal.recording.read")[0]

        assert event["resource"] == f"recording:{rec.id}"
        assert event["sensitive"] is True

    def test_one_that_does_not_exist_says_how_to_list_them(self, store):
        result = invoke("audit", "recording", "20260101T000000Z-" + "a" * 16)

        assert result.exit_code != 0
        assert "audit recordings" in str(result.exception)

    def test_an_id_that_is_a_path_is_refused(self, store):
        result = invoke("audit", "recording", "../../etc/passwd")

        assert result.exit_code != 0

    def test_pruning_applies_the_retention_to_recordings_too(self, store):
        rec = SessionRecorder(
            store.db_path.parent / "terminal-recordings",
            session_id="fedcba9876543210",
            meta={"kind": "app", "domain": DOMAIN, "actor": "maria"},
            rows=24,
            cols=80,
            max_bytes=10_000,
            clock=lambda: 1_600_000_000.0,
        )
        rec.close(reason="closed", exit_code=0)

        result = invoke("audit", "prune", "--json")

        assert json.loads(result.output)["records"]["deleted"]["terminal"] == 1
        assert not rec.path.exists()
