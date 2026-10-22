# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Values of several lines and comments through the API and the command line (checklist B-51).

``tests/test_env_multiline.py`` pins the parser, the writer and the validators; here, how they
travel: ``GET``, ``PUT`` and ``PATCH /api/apps/{domain}/env`` carry a private key and the comment
above a variable, ``noust env set --file`` reads a key from a file, ``noust env comment`` keeps a
note, and a write that says nothing about comments keeps the ones the file has.
"""

# The API fixtures are imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.store import NoustStore
from noust.deployers.helpers import app_env
from noust.deployers.helpers.env_manager import EnvManager
from tests.test_web_apps_env_secrets import (  # noqa: F401  (pytest resolves fixtures by name)
    app,
    client,
    deployed_env,
    elevate,
    store,
)

PEM = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\nZGVm==\n-----END PRIVATE KEY-----"


def env_file(directory: Path) -> str:
    return (directory / ".env").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


def test_a_private_key_is_saved_through_the_api_and_read_back_whole(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    directory = deployed_env(store, tmp_path, env_text="A=1\n")
    elevate(client)

    saved = client.put("/api/apps/example.com/env", json={"variables": {"A": "1", "TLS_KEY": PEM}})

    assert saved.status_code == 200, saved.text
    assert f"TLS_KEY='{PEM}'" in env_file(directory)
    read = client.get("/api/apps/example.com/env", params={"unmask": True}).json()
    assert read["variables"]["TLS_KEY"] == PEM


def test_a_value_that_would_break_out_of_its_quotes_is_refused(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    directory = deployed_env(store, tmp_path, env_text="A=1\n")
    elevate(client)

    refused = client.put(
        "/api/apps/example.com/env", json={"variables": {"EVIL": "x'\nINJECTED=1\n'"}}
    )

    assert refused.status_code == 422 and "single quote" in refused.text
    assert env_file(directory) == "A=1\n"


def test_the_comment_above_each_variable_is_part_of_the_answer(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="# where the queue lives\nQUEUE=redis\nPLAIN=1\n")

    read = client.get("/api/apps/example.com/env").json()

    assert read["comments"] == {"QUEUE": "where the queue lives"}


def test_a_comment_is_set_with_the_values_and_kept_when_a_later_write_says_nothing_of_it(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    directory = deployed_env(store, tmp_path, env_text="A=1\nB=2\n")
    elevate(client)

    first = client.put(
        "/api/apps/example.com/env",
        json={"variables": {"A": "1", "B": "2"}, "comments": {"B": "the second\nletter"}},
    )
    again = client.put("/api/apps/example.com/env", json={"variables": {"A": "changed", "B": "2"}})

    assert first.status_code == again.status_code == 200
    assert env_file(directory) == "A=changed\n# the second\n# letter\nB=2\n"


def test_an_empty_comment_takes_the_comment_away_and_a_name_left_out_keeps_its_own(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    directory = deployed_env(store, tmp_path, env_text="# a\nA=1\n# b\nB=2\n")
    elevate(client)

    done = client.put(
        "/api/apps/example.com/env", json={"variables": {"A": "1", "B": "2"}, "comments": {"A": ""}}
    )

    assert done.status_code == 200
    assert env_file(directory) == "A=1\n# b\nB=2\n"


def test_a_comment_for_a_variable_that_is_not_in_the_file_is_refused(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    directory = deployed_env(store, tmp_path, env_text="A=1\n")
    elevate(client)

    refused = client.put(
        "/api/apps/example.com/env", json={"variables": {"A": "1"}, "comments": {"GHOST": "who?"}}
    )

    assert refused.status_code == 422 and "GHOST" in refused.text
    assert env_file(directory) == "A=1\n"


def test_a_comment_that_is_not_plain_text_is_refused(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    deployed_env(store, tmp_path, env_text="A=1\n")
    elevate(client)

    refused = client.put(
        "/api/apps/example.com/env",
        json={"variables": {"A": "1"}, "comments": {"A": "bad\rEVIL=1"}},
    )

    assert refused.status_code == 422


def test_the_merge_sets_a_comment_without_touching_a_value(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    directory = deployed_env(store, tmp_path, env_text="A=1\n")
    elevate(client)

    merged = client.patch("/api/apps/example.com/env", json={"comments": {"A": "the first"}})

    assert merged.status_code == 200 and merged.json()["changed"] == []
    assert env_file(directory) == "# the first\nA=1\n"


def test_the_merge_dry_run_checks_a_comment_and_writes_nothing(
    client: TestClient, store: NoustStore, tmp_path: Path
) -> None:
    directory = deployed_env(store, tmp_path, env_text="A=1\n")
    elevate(client)

    refused = client.patch(
        "/api/apps/example.com/env", json={"comments": {"GHOST": "x"}, "dry_run": True}
    )

    assert refused.status_code == 422
    assert env_file(directory) == "A=1\n"


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


@pytest.fixture
def deployed(store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An application whose ``.env`` is in the sandbox, found by the command line as well."""
    directory = deployed_env(store, tmp_path, env_text="A=1\n")
    monkeypatch.setattr(
        "noust.deployers.helpers.app_env.hand_over_file", lambda *args, **kwargs: None
    )
    return directory


def invoke(*args: str):
    return CliRunner().invoke(root_cli, list(args))


def test_set_file_reads_a_key_from_a_file_with_the_line_an_editor_adds_taken_off(
    deployed: Path, tmp_path: Path
) -> None:
    key = tmp_path / "key.pem"
    key.write_text(PEM + "\n", encoding="utf-8")

    result = invoke("env", "set", "example.com", "--file", f"TLS_KEY={key}")

    assert result.exit_code == 0, result.output
    assert EnvManager().read_env_file(deployed / ".env")["TLS_KEY"] == PEM
    assert "A=1" in env_file(deployed)


def test_set_file_refuses_a_file_that_is_too_large_or_missing(
    deployed: Path, tmp_path: Path
) -> None:
    big = tmp_path / "big"
    big.write_text("x" * (64 * 1024 + 1))

    assert invoke("env", "set", "example.com", "--file", f"K={big}").exit_code != 0
    assert invoke("env", "set", "example.com", "--file", f"K={tmp_path / 'nothing'}").exit_code != 0
    assert invoke("env", "set", "example.com", "--file", "K").exit_code != 0
    assert env_file(deployed) == "A=1\n"


def test_set_file_refuses_a_key_that_would_end_its_quotes(deployed: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    bad.write_text("a'\nINJECTED=1\n'")

    result = invoke("env", "set", "example.com", "--file", f"K={bad}")

    assert result.exit_code != 0
    assert env_file(deployed) == "A=1\n"


def test_set_comment_keeps_a_note_above_what_it_sets(deployed: Path) -> None:
    result = invoke("env", "set", "example.com", "B=2", "--comment", "the second letter")

    assert result.exit_code == 0, result.output
    assert env_file(deployed) == "A=1\n# the second letter\nB=2\n"


def test_a_plain_set_keeps_the_comments_there_are(deployed: Path) -> None:
    (deployed / ".env").write_text("# about A\nA=1\n")

    result = invoke("env", "set", "example.com", "A=2", "B=3")

    assert result.exit_code == 0, result.output
    assert env_file(deployed) == "# about A\nA=2\nB=3\n"


def test_comment_shows_sets_and_takes_away_a_note(deployed: Path) -> None:
    assert "A has no comment" in invoke("env", "comment", "example.com", "A").output

    set_it = invoke("env", "comment", "example.com", "A", "what A is for")
    shown = invoke("env", "comment", "example.com", "A")
    gone = invoke("env", "comment", "example.com", "A", "")

    assert set_it.exit_code == 0 and "what A is for" in shown.output
    assert gone.exit_code == 0 and "taken away" in gone.output
    assert env_file(deployed) == "A=1\n"


def test_comment_on_a_variable_that_is_not_there_says_so(deployed: Path) -> None:
    result = invoke("env", "comment", "example.com", "GHOST", "x")

    assert result.exit_code != 0 and "no variable GHOST" in str(result.exception)


def test_show_carries_the_comments(deployed: Path) -> None:
    (deployed / ".env").write_text("# about A\nA=1\n")

    shown = invoke("--json", "env", "show", "example.com")

    assert json.loads(shown.output)["comments"] == {"A": "about A"}
    text = invoke("env", "show", "example.com")
    assert "# about A" in text.output


def test_a_comment_is_checked_before_it_is_written(deployed: Path) -> None:
    result = invoke("env", "comment", "example.com", "A", "x" * 600)

    assert result.exit_code != 0
    assert env_file(deployed) == "A=1\n"


def test_the_app_env_chokepoint_keeps_comments_through_a_write_of_values(
    deployed: Path, store: NoustStore
) -> None:
    (deployed / ".env").write_text("# about A\nA=1\n")
    found = app_env.find_app("example.com")
    assert found is not None

    app_env.write_app_env(found, {"A": "9"})

    assert env_file(deployed) == "# about A\nA=9\n"
