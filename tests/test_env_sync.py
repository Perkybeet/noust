# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bringing a real ``.env`` into an application, and seeing it as one (checklist B-41).

``noust env set --stdin`` only split ``NAME=VALUE`` lines. A developer's ``.env`` has comments,
``export``, quotes and values of several lines; ``env sync --file`` reads that, creates and updates
without deleting (``--prune`` is the explicit way to), skips what the unit sets itself instead of
refusing the file, and the API draws the stored file as text for a viewer and a download, masked
unless asked for in sudo mode.
"""

# The env API harness is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.env_sync import MAX_FILE_BYTES, parse_dotenv, plan_sync, render_view
from tests.test_web_apps_env_secrets import (  # noqa: F401  (pytest resolves fixtures by name)
    app,
    client,
    deployed_env,
    elevate,
    read_audit,
    store,
)

FILE = """\
# The database the app talks to.
export DATABASE_URL="postgres://app:s3cret@db/app"
API_KEY='abc 123'
EMPTY=
MULTI='line one
line two'
PORT=3000
NODE_ENV=development
"""


class TestReadingAFile:
    def test_quotes_export_comments_and_lines_are_read_as_the_apps_own_env_is(self) -> None:
        values, comments, repeated = parse_dotenv(FILE)

        assert values["DATABASE_URL"] == "postgres://app:s3cret@db/app"
        assert values["API_KEY"] == "abc 123"
        assert values["EMPTY"] == ""
        assert values["MULTI"] == "line one\nline two"
        assert comments == {"DATABASE_URL": "The database the app talks to."}
        assert repeated == ()

    def test_the_last_assignment_counts_and_is_reported(self) -> None:
        values, _, repeated = parse_dotenv("A=1\nB=2\nA=3\n")

        assert values == {"A": "3", "B": "2"} and repeated == ("A",)

    def test_windows_line_endings_are_read_too(self) -> None:
        values, _, _ = parse_dotenv("A=1\r\nB=two\r\n")

        assert values == {"A": "1", "B": "two"}

    @pytest.mark.parametrize("text", ["A=\x00", "A=" + "x" * (MAX_FILE_BYTES + 1)])
    def test_what_is_not_a_dotenv_is_refused(self, text: str) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_dotenv(text)

        assert caught.value.field == "content"


class TestThePlan:
    def test_it_creates_and_updates_and_keeps_the_rest(self) -> None:
        plan = plan_sync({"KEPT": "k", "API_KEY": "old"}, "API_KEY=new\nFRESH=1\n")

        assert plan.created == ("FRESH",)
        assert plan.updated == ("API_KEY",)
        assert plan.removed == ()
        assert plan.values == {"KEPT": "k", "API_KEY": "new", "FRESH": "1"}
        assert plan.changed == ("API_KEY", "FRESH")

    def test_a_value_that_is_already_there_is_not_a_change(self) -> None:
        plan = plan_sync({"A": "1"}, "A=1\n")

        assert plan.unchanged == ("A",) and plan.changed == ()

    def test_the_text_a_value_was_made_from_counts_as_the_same(self) -> None:
        plan = plan_sync(
            {"URL": "https://x.example"}, "URL={{app.URL}}\n", expressions={"URL": "{{app.URL}}"}
        )

        assert plan.unchanged == ("URL",)

    def test_pruning_is_the_only_way_a_variable_is_taken_out(self) -> None:
        kept = plan_sync({"A": "1", "B": "2"}, "A=1\n")
        pruned = plan_sync({"A": "1", "B": "2"}, "A=1\n", prune=True)

        assert kept.removed == () and "B" in kept.values
        assert pruned.removed == ("B",) and "B" not in pruned.values

    def test_what_the_unit_or_noust_sets_is_skipped_with_the_reason_not_refused(self) -> None:
        plan = plan_sync({}, "PORT=3000\nNODE_ENV=dev\nNOUST_URL=x\nOK=1\n")

        assert set(plan.skipped) == {"PORT", "NODE_ENV", "NOUST_URL"}
        assert "PORT" not in plan.values and plan.created == ("OK",)

    def test_only_the_comments_a_variable_can_carry_are_kept(self) -> None:
        plan = plan_sync({}, "# short\nA=1\n# " + "x" * 600 + "\nB=2\n")

        assert plan.comments == {"A": "short"}

    def test_the_answer_names_variables_and_never_a_value(self) -> None:
        plan = plan_sync({}, "SECRET=hunter2\n")

        assert "hunter2" not in json.dumps(plan.to_dict())


class TestTheView:
    def test_the_file_is_drawn_like_the_one_noust_writes(self) -> None:
        text = render_view({"B": "two words", "A": "1"}, {"A": "first"})

        assert text.splitlines()[:2] == ["# first", "A=1"]
        assert text.splitlines()[2].startswith("B=")

    def test_what_only_the_build_is_given_is_named_and_has_no_value(self) -> None:
        text = render_view({"A": "1"}, build_only=("NPM_TOKEN",))

        assert "NPM_TOKEN" in text and "NPM_TOKEN=" not in text

    def test_an_application_without_variables_is_an_empty_file(self) -> None:
        assert render_view({}) == ""


def run(*args: str, stdin: str | None = None) -> Any:
    return CliRunner().invoke(root_cli, ["env", *args], input=stdin)


class TestTheCommandLine:
    def deploy(
        self, store: NoustStore, tmp_path: Path, env_text: str = "KEPT=1\nAPI_KEY=old\n"
    ) -> Path:
        return deployed_env(store, tmp_path, env_text=env_text)

    def test_a_file_is_brought_in_without_deleting_anything(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = self.deploy(store, tmp_path)
        source = tmp_path / "dev.env"
        source.write_text(FILE)

        done = run("sync", "example.com", "--file", str(source))

        assert done.exit_code == 0, done.output
        written = EnvManager().read_env_file(directory / ".env")
        assert written["KEPT"] == "1" and written["API_KEY"] == "abc 123"
        assert written["MULTI"] == "line one\nline two"
        assert "PORT" not in written and "NODE_ENV" not in written
        assert "PORT skipped" in done.output

    def test_standard_input_is_a_file_too(self, store: NoustStore, tmp_path: Path) -> None:
        directory = self.deploy(store, tmp_path)

        done = run("sync", "example.com", "--file", "-", stdin="FROM_STDIN=yes\n")

        assert done.exit_code == 0, done.output
        assert EnvManager().read_env_file(directory / ".env")["FROM_STDIN"] == "yes"

    def test_prune_takes_out_what_the_file_lacks(self, store: NoustStore, tmp_path: Path) -> None:
        directory = self.deploy(store, tmp_path)

        done = run("sync", "example.com", "--file", "-", "--prune", stdin="ONLY=1\n")

        assert done.exit_code == 0, done.output
        assert EnvManager().read_env_file(directory / ".env") == {"ONLY": "1"}

    def test_the_global_dry_run_only_says_what_would_change(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = self.deploy(store, tmp_path)

        done = CliRunner().invoke(
            root_cli, ["--dry-run", "env", "sync", "example.com", "--file", "-"], input="NEW=1\n"
        )

        assert done.exit_code == 0, done.output
        assert "Would create" in done.output and "NEW" in done.output
        assert "NEW" not in EnvManager().read_env_file(directory / ".env")

    def test_the_answer_as_json_has_names_only(self, store: NoustStore, tmp_path: Path) -> None:
        self.deploy(store, tmp_path)

        done = run("sync", "example.com", "--file", "-", "--json", stdin="SECRET_KEY=hunter2\n")

        data = json.loads(done.output)
        assert data["created"] == ["SECRET_KEY"] and "hunter2" not in done.output

    def test_a_build_only_audience_keeps_the_values_out_of_the_file(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = self.deploy(store, tmp_path)

        done = run("sync", "example.com", "--file", "-", "--build-only", stdin="NPM_TOKEN=t\n")

        assert done.exit_code == 0, done.output
        assert "NPM_TOKEN" not in EnvManager().read_env_file(directory / ".env")

    def test_a_file_that_is_not_there_is_a_usage_error(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        self.deploy(store, tmp_path)

        done = run("sync", "example.com", "--file", str(tmp_path / "missing.env"))

        assert done.exit_code != 0 and "could not be read" in done.output

    def test_a_value_a_unit_cannot_hold_is_refused_and_nothing_is_written(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = self.deploy(store, tmp_path)

        done = run("sync", "example.com", "--file", "-", stdin="BAD=a\x01b\n")

        assert done.exit_code != 0
        assert EnvManager().read_env_file(directory / ".env") == {"KEPT": "1", "API_KEY": "old"}


class TestOverTheApi:
    def test_the_stored_file_is_text_with_the_secrets_masked(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="# the key\nAPI_KEY=sk-live-hunter2\nMODE=fast\n")

        response = client.get("/api/apps/example.com/env/file")

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["cache-control"] == "no-store"
        assert "sk-live-hunter2" not in response.text
        assert "MODE=fast" in response.text and "# the key" in response.text

    def test_in_clear_it_needs_sudo_mode_and_leaves_a_trail(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="API_KEY=sk-live-hunter2\n")
        denied = client.get("/api/apps/example.com/env/file?unmask=true")
        elevate(client)

        shown = client.get("/api/apps/example.com/env/file?unmask=true&download=true")

        assert denied.status_code == 403 and "hunter2" not in denied.text
        assert "API_KEY=sk-live-hunter2" in shown.text
        assert 'attachment; filename="example-com.env"' in shown.headers["content-disposition"]
        actions = [line["action"] for line in read_audit(tmp_path)]
        assert "apps.env.reveal" in actions
        assert "hunter2" not in json.dumps(read_audit(tmp_path))

    def test_what_only_the_build_is_given_is_named_without_a_value(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="A=1\n")
        elevate(client)
        client.patch("/api/apps/example.com/env", json={"set": {"NPM_TOKEN": "tok"}})
        client.put(
            "/api/apps/example.com/env",
            json={"variables": {"A": "1", "NPM_TOKEN": "tok"}, "audience": {"NPM_TOKEN": "build"}},
        )

        response = client.get("/api/apps/example.com/env/file")

        assert "NPM_TOKEN" in response.text and "tok" not in response.text

    def test_sync_creates_and_updates_and_says_which_without_values(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="KEPT=1\nA=old\n")
        elevate(client)

        response = client.post(
            "/api/apps/example.com/env/sync", json={"content": "A=new\nB=2\nPORT=3000\n"}
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["created"] == ["B"] and data["updated"] == ["A"]
        assert data["skipped"].keys() == {"PORT"} and data["restart_required"] is True
        assert EnvManager().read_env_file(directory / ".env") == {"KEPT": "1", "A": "new", "B": "2"}

    def test_a_dry_run_writes_nothing(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="A=old\n")
        elevate(client)

        response = client.post(
            "/api/apps/example.com/env/sync", json={"content": "A=new\n", "dry_run": True}
        )

        assert response.json()["updated"] == ["A"] and response.json()["restart_required"] is False
        assert EnvManager().read_env_file(directory / ".env") == {"A": "old"}

    def test_it_needs_sudo_mode(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        deployed_env(store, tmp_path, env_text="A=old\n")

        response = client.post("/api/apps/example.com/env/sync", json={"content": "A=new\n"})

        assert response.status_code == 403

    def test_a_value_a_unit_cannot_hold_is_a_422_and_nothing_changes(
        self, client: TestClient, store: NoustStore, tmp_path: Path
    ) -> None:
        directory = deployed_env(store, tmp_path, env_text="A=old\n")
        elevate(client)

        response = client.post("/api/apps/example.com/env/sync", json={"content": "A=a\u0001b\n"})

        assert response.status_code == 422
        assert EnvManager().read_env_file(directory / ".env") == {"A": "old"}
