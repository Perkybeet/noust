# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The command line on another server: contexts, tokens and the commands that call the API (J-12).

The token is a file or a variable and never an argument; a context holds the
way to find one and not the token; and a command that runs remotely says what
the server said: the exit code is the outcome, the probes of a refused health
gate are printed as the server wrote them, and nothing is audited here, because
the server audits the call.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.client import ApiClient, ContextError, ContextStore, RemoteSettings, resolve_target
from tests.client_support import Answer, Wire

TOKEN = "noust_tok_" + "r" * 40
URL = "https://noust.test"


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home of the test's own, with no server in the environment."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in ("NOUST_REMOTE", "NOUST_CONTEXT", "NOUST_TOKEN", "NOUST_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    return tmp_path


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[Wire]:
    """A scripted server behind every client the command line builds."""
    scripted = Wire()
    monkeypatch.setattr(ApiClient, "_network", staticmethod(lambda ca_file: scripted))
    yield scripted


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    """A private file holding a token."""
    path = tmp_path / "token"
    path.write_text(TOKEN + "\n")
    path.chmod(0o600)
    return path


def run(*args: str, code: int = 0, stdin: str | None = None) -> str:
    """
    Run the command line.

    Args:
        *args: The arguments after ``noust``.
        code: The exit code expected.
        stdin: What to pipe in.

    Returns:
        Everything it printed, error included.
    """
    result = CliRunner().invoke(root_cli, list(args), input=stdin)
    output = f"{result.output}{result.exception or ''}"
    assert result.exit_code == code, f"{output}"
    return output


class TestContexts:
    def test_a_context_keeps_the_way_to_a_token_and_never_the_token_in_the_list(
        self, home: Path
    ) -> None:
        run("context", "add", "prod", "--url", URL, "--token", "-", stdin=TOKEN + "\n")

        document = (home / "config" / "noust" / "contexts.json").read_text()
        kept = home / "config" / "noust" / "tokens" / "prod"
        assert TOKEN not in document
        assert kept.read_text().strip() == TOKEN
        assert stat.S_IMODE(kept.stat().st_mode) == 0o600
        assert stat.S_IMODE((home / "config" / "noust" / "contexts.json").stat().st_mode) == 0o600
        assert stat.S_IMODE(kept.parent.stat().st_mode) == 0o700

    def test_a_token_is_never_taken_from_the_argument_list(self) -> None:
        output = run("context", "add", "prod", "--url", URL, "--token", TOKEN, code=1)

        assert "not given on the command line" in output
        assert "--token -" in output

    def test_a_token_can_come_from_a_variable_or_a_file(
        self, monkeypatch: pytest.MonkeyPatch, token_file: Path
    ) -> None:
        monkeypatch.setenv("CI_NOUST", TOKEN)

        run("context", "add", "ci", "--url", URL, "--token-env", "CI_NOUST")
        run("context", "add", "laptop", "--url", URL, "--token-file", str(token_file))

        contexts = {c.name: c for c in ContextStore().list()}
        assert contexts["ci"].token_env == "CI_NOUST" and contexts["ci"].token_file is None
        assert contexts["laptop"].token_file == str(token_file)

    def test_exactly_one_way_to_the_token_is_given(self, token_file: Path) -> None:
        output = run(
            "context",
            "add",
            "x",
            "--url",
            URL,
            "--token-env",
            "A",
            "--token-file",
            str(token_file),
            code=1,
        )

        assert "exactly one" in output

    def test_a_token_file_other_users_can_read_is_refused(self, tmp_path: Path) -> None:
        loose = tmp_path / "loose"
        loose.write_text(TOKEN)
        loose.chmod(0o644)

        output = run("context", "add", "x", "--url", URL, "--token-file", str(loose), code=1)

        assert "can be read by other users" in output
        assert "chmod 600" in output

    def test_the_first_context_is_the_one_in_use_and_another_can_be_chosen(
        self, token_file: Path
    ) -> None:
        run("context", "add", "prod", "--url", URL, "--token-file", str(token_file))
        run(
            "context",
            "add",
            "stage",
            "--url",
            "https://stage.test",
            "--token-file",
            str(token_file),
        )

        assert run("context", "current").strip() == "prod"
        run("context", "use", "stage")
        assert run("context", "current").strip() == "stage"
        listing = json.loads(run("--json", "context", "list"))
        assert listing["current"] == "stage"
        assert [c["name"] for c in listing["contexts"]] == ["prod", "stage"]
        assert TOKEN not in json.dumps(listing)

    def test_removing_a_context_removes_the_token_file_it_kept(self, home: Path) -> None:
        run("context", "add", "prod", "--url", URL, "--token", "-", stdin=TOKEN)
        kept = home / "config" / "noust" / "tokens" / "prod"
        assert kept.exists()

        run("context", "remove", "prod")

        assert not kept.exists() and ContextStore().list() == []

    def test_an_unknown_context_says_which_there_are(self, token_file: Path) -> None:
        run("context", "add", "prod", "--url", URL, "--token-file", str(token_file))

        output = run("context", "use", "nowhere", code=1)

        assert "Saved contexts: prod" in output

    def test_verify_says_who_the_token_is_there(self, token_file: Path, wire: Wire) -> None:
        run("context", "add", "prod", "--url", URL, "--token-file", str(token_file))
        wire.on(
            "GET",
            "/api/auth/session",
            Answer(
                200,
                {
                    "authenticated": True,
                    "hostname": "box",
                    "version": "3.4.0",
                    "role": "operator",
                    "permissions": ["self", "apps.read"],
                },
                {"X-Noust-API-Version": "1"},
            ),
        )

        output = run("context", "verify")

        assert "box runs Noust 3.4.0" in output and "operator" in output
        assert wire.seen[0][2]["authorization"] == f"Bearer {TOKEN}"

    def test_verify_says_so_when_the_token_is_not_accepted(
        self, token_file: Path, wire: Wire
    ) -> None:
        run("context", "add", "prod", "--url", URL, "--token-file", str(token_file))
        wire.on("GET", "/api/auth/session", Answer(200, {"authenticated": False}))

        output = run("context", "verify", code=1)

        assert "did not accept the token" in output


class TestDecidingWhereACommandRuns:
    def settings(self, **kwargs: Any) -> RemoteSettings:
        return RemoteSettings(**kwargs)

    def test_with_nothing_set_a_command_runs_here(self, home: Path) -> None:
        assert resolve_target(self.settings(), store=ContextStore(home / "c")) is None

    def test_an_address_and_a_token_file_are_a_server(self, token_file: Path, home: Path) -> None:
        target = resolve_target(
            self.settings(remote=URL, token_file=str(token_file)), store=ContextStore(home / "c")
        )

        assert target is not None and (target.url, target.token) == (URL, TOKEN)
        assert target.name is None

    def test_local_wins_over_everything(self, token_file: Path, home: Path) -> None:
        store = ContextStore(home / "c")
        store.add("prod", URL, token_file=str(token_file))

        assert resolve_target(self.settings(local=True, remote=URL), store=store) is None

    def test_the_environment_stands_in_for_the_flags(self, home: Path) -> None:
        target = resolve_target(
            self.settings(),
            store=ContextStore(home / "c"),
            environ={"NOUST_REMOTE": URL, "NOUST_TOKEN": TOKEN},
        )

        assert target is not None and target.token == TOKEN

    def test_flags_come_before_the_environment_and_the_context(
        self, token_file: Path, home: Path
    ) -> None:
        store = ContextStore(home / "c")
        store.add("prod", "https://prod.test", token_env="PROD_TOKEN")

        target = resolve_target(
            self.settings(token_file=str(token_file)),
            store=store,
            environ={"NOUST_TOKEN": "other", "PROD_TOKEN": "prod-token"},
        )

        assert target is not None
        assert (target.url, target.token, target.name) == ("https://prod.test", TOKEN, "prod")

    def test_a_server_with_no_token_says_how_to_give_one(self, home: Path) -> None:
        with pytest.raises(ContextError, match="no token") as caught:
            resolve_target(self.settings(remote=URL), store=ContextStore(home / "c"), environ={})

        assert "NOUST_TOKEN_FILE" in caught.value.details

    def test_a_variable_that_is_empty_is_not_a_token(self, home: Path) -> None:
        with pytest.raises(ContextError, match="empty or not set"):
            resolve_target(
                self.settings(remote=URL, token_env="NOPE"),
                store=ContextStore(home / "c"),
                environ={},
            )


class TestCommandsOnAnotherServer:
    def remote(self, token_file: Path, *args: str, code: int = 0) -> str:
        return run("--remote", URL, "--token-file", str(token_file), *args, code=code)

    def test_list_prints_what_the_server_answered(self, token_file: Path, wire: Wire) -> None:
        wire.on(
            "GET",
            "/api/apps",
            Answer(
                200,
                {
                    "apps": [
                        {
                            "domain": "shop.test",
                            "app_type": "nodejs",
                            "status": "running",
                            "team": "acme",
                            "project": "web",
                            "environment": "production",
                        }
                    ],
                    "total": 1,
                },
            ),
        )

        output = self.remote(token_file, "list")

        assert "shop.test" in output and "acme/web/production" in output
        assert "running on https://noust.test" in output

    def test_json_is_the_servers_answer_untouched(self, token_file: Path, wire: Wire) -> None:
        wire.on("GET", "/api/apps", Answer(200, {"apps": [], "total": 0}))

        output = self.remote(token_file, "--json", "list")

        assert json.loads(output) == {"apps": [], "total": 0}

    def test_a_command_with_no_remote_form_says_where_to_run_it(self, token_file: Path) -> None:
        output = self.remote(token_file, "setup", code=2)

        assert "no form on another server" in output
        assert "update" in output and "--local" in output

    def test_context_is_local_even_with_a_server_in_play(self, token_file: Path) -> None:
        output = self.remote(token_file, "context", "list")

        assert "No saved servers" in output

    def test_a_refusal_keeps_the_servers_words(self, token_file: Path, wire: Wire) -> None:
        wire.on(
            "POST",
            "/api/apps/shop.test/restart",
            Answer(
                403,
                {
                    "error": "permission_denied",
                    "detail": "This needs the 'apps.operate' permission, which viewer does not hold",
                    "hint": "Ask a security officer for an account with a role that holds it.",
                },
            ),
        )

        output = self.remote(token_file, "restart", "shop.test", code=1)

        assert "apps.operate" in output
        assert "Ask a security officer" in output

    def test_env_lists_names_and_never_a_value(self, token_file: Path, wire: Wire) -> None:
        wire.on(
            "GET",
            "/api/apps/shop.test/env",
            Answer(
                200,
                {
                    "domain": "shop.test",
                    "variables": {
                        "DATABASE_URL": "postgres://u:hunter2@db/x",
                        "API_KEY": "sk-live-1",
                    },
                    "unmasked": False,
                },
            ),
        )

        output = self.remote(token_file, "env", "list", "shop.test")
        as_json = self.remote(token_file, "--json", "env", "list", "shop.test")

        assert "API_KEY" in output and "DATABASE_URL" in output
        assert "hunter2" not in output + as_json and "sk-live" not in output + as_json
        assert json.loads(as_json) == {"domain": "shop.test", "names": ["API_KEY", "DATABASE_URL"]}

    def test_nothing_is_audited_on_this_machine(
        self, token_file: Path, wire: Wire, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[str] = []
        monkeypatch.setattr("noust.core.audit.record", lambda *a, **k: recorded.append(a[0]))
        wire.on("POST", "/api/apps/shop.test/restart", Answer(200, {"message": "Restarted"}))

        self.remote(token_file, "restart", "shop.test")

        assert recorded == []

    def test_the_api_command_calls_any_route(self, token_file: Path, wire: Wire) -> None:
        wire.on("POST", "/api/jobs/update", Answer(202, {"job": {"id": "j1"}}))

        output = self.remote(
            token_file, "--json", "api", "POST", "/jobs/update", "-d", '{"domain": "shop.test"}'
        )

        assert json.loads(output) == {"job": {"id": "j1"}}
        assert wire.seen[0][3] == {"domain": "shop.test"}

    def test_the_api_command_refuses_a_body_that_is_not_json(self, token_file: Path) -> None:
        output = self.remote(token_file, "api", "POST", "/jobs/update", "-d", "{nope", code=2)

        assert "not JSON" in output


class TestFollowingAJob:
    def queued(self, wire: Wire, job_id: str = "j1") -> None:
        wire.on(
            "POST",
            "/api/jobs/update",
            Answer(
                202, {"message": "Update job created", "job": {"id": job_id, "state": "queued"}}
            ),
        )

    def remote(self, token_file: Path, *args: str, code: int = 0) -> str:
        return run("--remote", URL, "--token-file", str(token_file), *args, code=code)

    def test_a_successful_update_prints_the_log_and_exits_zero(
        self, token_file: Path, wire: Wire
    ) -> None:
        self.queued(wire)
        wire.on(
            "GET",
            "/api/jobs/j1",
            Answer(200, {"id": "j1", "state": "running"}),
            Answer(200, {"id": "j1", "state": "succeeded", "deployment_id": 7}),
        )
        wire.on(
            "GET",
            "/api/jobs/j1/log?tail=20000",
            Answer(200, {"content": "building\nactivating", "truncated": False}),
            Answer(200, {"content": "building\nactivating\nhealthy", "truncated": False}),
        )
        wire.on(
            "GET",
            "/api/deployments/7",
            Answer(
                200,
                {"id": 7, "status": "success", "git_commit": "abcdef0123456789", "duration_s": 41},
            ),
        )

        output = self.remote(token_file, "update", "shop.test")

        assert output.count("building") == 1  # the log is printed as it grows, not again
        assert "activating" in output and "healthy" in output
        assert "Update of shop.test succeeded" in output
        assert "commit abcdef012345" in output

    def test_a_refused_activation_prints_the_probes_verbatim_and_exits_one(
        self, token_file: Path, wire: Wire
    ) -> None:
        self.queued(wire)
        probes = "probe http://127.0.0.1:3000/ -> connection refused\njournal: app crashed on start"
        wire.on(
            "GET",
            "/api/jobs/j1",
            Answer(200, {"id": "j1", "state": "failed", "error": probes}),
        )
        wire.on(
            "GET", "/api/jobs/j1/log?tail=20000", Answer(200, {"content": "x", "truncated": False})
        )

        output = self.remote(token_file, "update", "shop.test", code=1)

        assert "Update of shop.test failed" in output
        assert "connection refused" in output and "app crashed on start" in output

    def test_nothing_new_is_the_servers_own_refusal(self, token_file: Path, wire: Wire) -> None:
        wire.on(
            "POST",
            "/api/jobs/update",
            Answer(
                409,
                {
                    "error": "nothing_new",
                    "detail": "The branch has nothing new",
                    "hint": "Use force to rebuild.",
                },
            ),
        )

        output = self.remote(token_file, "update", "shop.test", code=1)

        assert "nothing new" in output and "Use force" in output

    def test_force_is_sent(self, token_file: Path, wire: Wire) -> None:
        self.queued(wire)
        wire.on("GET", "/api/jobs/j1", Answer(200, {"id": "j1", "state": "succeeded"}))
        wire.on(
            "GET", "/api/jobs/j1/log?tail=20000", Answer(200, {"content": "", "truncated": False})
        )

        self.remote(token_file, "update", "shop.test", "--force")

        assert wire.seen[0][3] == {"domain": "shop.test", "force": True}

    def test_no_wait_returns_at_once_with_the_job(self, token_file: Path, wire: Wire) -> None:
        self.queued(wire, "j9")

        output = self.remote(token_file, "update", "shop.test", "--no-wait")

        assert "j9" in output and "noust jobs wait j9" in output
        assert len(wire.seen) == 1

    def test_running_when_the_wait_runs_out_exits_four(
        self, token_file: Path, wire: Wire, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.queued(wire)
        wire.on("GET", "/api/jobs/j1", Answer(200, {"id": "j1", "state": "running"}))
        wire.on(
            "GET", "/api/jobs/j1/log?tail=20000", Answer(200, {"content": "", "truncated": False})
        )
        ticks = iter(range(0, 10_000, 100))
        monkeypatch.setattr("time.monotonic", lambda: next(ticks))

        output = self.remote(token_file, "update", "shop.test", "--timeout", "150", code=4)

        assert "still running" in output and "noust jobs wait j1" in output

    def test_jobs_wait_follows_a_job_that_was_left(self, token_file: Path, wire: Wire) -> None:
        wire.on("GET", "/api/jobs/j2", Answer(200, {"id": "j2", "state": "cancelled"}))
        wire.on(
            "GET", "/api/jobs/j2/log?tail=20000", Answer(200, {"content": "", "truncated": False})
        )

        output = self.remote(token_file, "jobs", "wait", "j2", code=1)

        assert "cancelled" in output

    def test_rollback_sends_what_it_was_asked(self, token_file: Path, wire: Wire) -> None:
        wire.on(
            "POST",
            "/api/jobs/rollback",
            Answer(
                202, {"message": "Rollback job created", "job": {"id": "r1", "state": "queued"}}
            ),
        )

        self.remote(token_file, "rollback", "shop.test", "--no-wait", "--schema-changed-ok")

        assert wire.seen[0][3] == {
            "domain": "shop.test",
            "backup_id": None,
            "schema_changed_ok": True,
        }


def test_a_local_command_is_untouched_when_no_server_is_in_play(home: Path) -> None:
    output = run("access", "permissions")

    assert "apps.deploy" in output


def test_an_environment_that_sets_a_server_makes_the_commands_remote(
    monkeypatch: pytest.MonkeyPatch, token_file: Path, wire: Wire
) -> None:
    monkeypatch.setenv("NOUST_REMOTE", URL)
    monkeypatch.setenv("NOUST_TOKEN_FILE", str(token_file))
    wire.on("GET", "/api/apps", Answer(200, {"apps": [], "total": 0}))

    output = run("list")

    assert "No applications" in output
    assert os.environ["NOUST_REMOTE"] == URL
