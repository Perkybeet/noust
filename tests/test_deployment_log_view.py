# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading a deployment's log by level, by search and without what must not leave (checklist B-07).

The captured log is stored with the secrets the build knew scrubbed. What is added here is
the way it is read: only warnings and errors, only the lines that hold a word, without its
stamps, and as a copy that is safe to paste (credentials masked by their shape, terminal
control sequences gone). Pinned:

- the level of a line, against the cases the console's classifier is tested against too;
- every combination of filters leaves the log's lines intact and in order;
- no credential shape survives a sanitised copy, however the lines are filtered;
- the API and ``noust app deploy-log`` serve the same view.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.config import REDACTED
from noust.core.exceptions import ValidationError
from noust.core.secret_detection import mask_secret_shapes
from noust.core.store import DeploymentStatus, DeploymentTrigger, NoustStore
from noust.core.teams import ScopedStore
from noust.deployers.logs import LEVELS, line_level, read_deployment_log, view_log
from noust.web.api import deployments as deployments_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers

CASES = json.loads(
    (
        Path(__file__).parent.parent / "panel/src/features/app/deployments/logLevels.cases.json"
    ).read_text(encoding="utf-8")
)["cases"]

DOMAIN = "shop.example.com"

LOG = (
    "[2026-09-25 21:53:39] [1/9] Fetching source code...\n"
    "[2026-09-25 21:53:40] npm install\n"
    "[2026-09-25 21:53:41] npm WARN deprecated left-pad@1.0.0\n"
    "[2026-09-25 21:53:42] connecting to postgres://app:hunter2-password@db:5432/app\n"
    "[2026-09-25 21:53:43] \x1b[31mError: Cannot find module 'express'\x1b[0m\n"
    "[2026-09-25 21:53:44] curl -H 'Authorization: Bearer abcdef0123456789abcdef' https://x\n"
    "[2026-09-25 21:53:45] Failed with result 'exit-code'.\n"
)


@pytest.mark.parametrize("case", CASES, ids=[case["line"][:60] for case in CASES])
def test_a_line_reads_at_the_level_the_console_colours_it(case: dict[str, str]) -> None:
    assert line_level(case["line"]) == case["level"]


def test_the_levels_are_the_ones_the_api_and_the_command_offer() -> None:
    assert LEVELS == ("all", "warn", "error")


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


def test_all_is_the_log_as_it_was() -> None:
    assert view_log(LOG) == LOG


def test_warn_keeps_warnings_and_errors_in_order() -> None:
    kept = view_log(LOG, level="warn").splitlines()

    assert [line.split("] ", 1)[1][:12] for line in kept] == [
        "npm WARN dep",
        "\x1b[31mError: ",
        "Failed with ",
    ]


def test_error_keeps_only_errors() -> None:
    kept = view_log(LOG, level="error").splitlines()

    assert len(kept) == 2 and all("WARN" not in line for line in kept)


def test_a_search_keeps_the_lines_that_hold_the_text_ignoring_case() -> None:
    kept = view_log(LOG, search="NPM").splitlines()

    assert len(kept) == 2


def test_a_level_and_a_search_narrow_each_other() -> None:
    assert view_log(LOG, level="error", search="express").count("\n") == 1
    assert view_log(LOG, level="error", search="left-pad") == ""


def test_timestamps_can_be_left_out() -> None:
    plain = view_log(LOG, timestamps=False)

    assert plain.startswith("[1/9] Fetching source code...\nnpm install\n")
    assert "[2026-" not in plain


def test_a_line_without_a_stamp_keeps_its_text_when_stamps_are_left_out() -> None:
    assert view_log("an ordinary line\n", timestamps=False) == "an ordinary line\n"


def test_a_log_that_does_not_end_in_a_newline_does_not_gain_one() -> None:
    assert view_log("[2026-09-25 21:53:39] a\n[2026-09-25 21:53:40] b").endswith("b")


def test_nothing_matching_is_an_empty_log() -> None:
    assert view_log(LOG, search="not in the log") == ""
    assert view_log("") == ""


def test_a_level_that_does_not_exist_is_refused_naming_the_field() -> None:
    with pytest.raises(ValidationError) as caught:
        view_log(LOG, level="info")

    assert caught.value.field == "level"


# ---------------------------------------------------------------------------
# A copy that is safe to paste
# ---------------------------------------------------------------------------

#: Things that look like credentials, each with the part that must not survive.
CREDENTIALS = [
    ("postgres://app:hunter2-password@db:5432/app", "hunter2-password"),
    ("redis://:s3cr3tpassw0rd@cache:6379", "s3cr3tpassw0rd"),
    ("Authorization: Bearer abcdef0123456789abcdef", "abcdef0123456789abcdef"),
    ("authorization=basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA=="),
    ("token ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789", "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"),
    ("key AKIAIOSFODNN7EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
    ("stripe sk_live_51HabcdefGhijklMnop", "sk_live_51HabcdefGhijklMnop"),
    (
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
        "eyJhbGciOiJIUzI1NiJ9",
    ),
]


@pytest.mark.parametrize(("line", "secret"), CREDENTIALS)
def test_a_sanitised_copy_masks_every_credential_shape(line: str, secret: str) -> None:
    masked = mask_secret_shapes(line)

    assert secret not in masked and REDACTED in masked


def test_a_private_key_block_is_masked_whole() -> None:
    log = (
        "before\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\nabcdef\n"
        "-----END RSA PRIVATE KEY-----\nafter\n"
    )

    masked = mask_secret_shapes(log)

    assert "MIIEpAIBAAKCAQEA" not in masked and "abcdef" not in masked
    assert masked.startswith("before\n") and masked.endswith("\nafter\n")


def test_a_private_key_cut_where_the_tool_stopped_is_masked_to_the_end() -> None:
    masked = mask_secret_shapes("-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq")

    assert "MIIEvQIBADANBgkq" not in masked


def test_ordinary_text_is_left_alone() -> None:
    text = "GET http://host:8080/a@b 200\nclone https://github.com/acme/shop.git\ncommit abc1234\n"

    assert mask_secret_shapes(text) == text


def test_the_sanitised_view_has_no_control_sequence_and_no_credential() -> None:
    copy = view_log(LOG, sanitize=True)

    assert "\x1b" not in copy
    assert "hunter2-password" not in copy and "abcdef0123456789abcdef" not in copy
    assert "postgres://app:" in copy, "the rest of the line is still there to read"
    assert "Cannot find module 'express'" in copy


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("timestamps", [True, False])
@pytest.mark.parametrize("search", [None, "postgres", "bearer"])
def test_no_combination_of_filters_lets_a_credential_through_a_sanitised_copy(
    level: str, timestamps: bool, search: str | None
) -> None:
    copy = view_log(LOG, level=level, search=search, timestamps=timestamps, sanitize=True)

    for secret in ("hunter2-password", "abcdef0123456789abcdef"):
        assert secret not in copy


# ---------------------------------------------------------------------------
# The API and the command
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(deployments_api, "scoped_store", lambda: ScopedStore(store=instance))
    yield instance
    NoustStore.reset_instance()


def deployment(store: NoustStore, *, log: str | None = LOG) -> int:
    from noust.core.store import App

    store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path="/srv/shop"))
    deployment_id = store.record_deployment_start(DOMAIN, DeploymentTrigger.CLI.value)
    if log is not None:
        directory = store.db_path.parent / "deploy-logs" / DOMAIN
        directory.mkdir(parents=True)
        path = directory / f"{deployment_id}.log"
        path.write_text(log, encoding="utf-8")
        store.annotate_deployment(deployment_id, log_path=str(path))
    store.finish_deployment(deployment_id, DeploymentStatus.SUCCESS.value)
    return deployment_id


@pytest.fixture
def client(store: NoustStore) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(deployments_api.router, prefix="/api/deployments")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test"}
    return TestClient(app, raise_server_exceptions=False)


def test_the_api_returns_the_view_it_is_asked_for(client: TestClient, store: NoustStore) -> None:
    deployment_id = deployment(store)

    body: dict[str, Any] = client.get(
        f"/api/deployments/{deployment_id}/log",
        params={"level": "error", "timestamps": "false", "sanitize": "true"},
    ).json()

    assert body["missing_reason"] is None
    assert body["content"].splitlines() == [
        "Error: Cannot find module 'express'",
        "Failed with result 'exit-code'.",
    ]


def test_the_api_without_options_returns_the_log_as_it_was(
    client: TestClient, store: NoustStore
) -> None:
    deployment_id = deployment(store)

    assert client.get(f"/api/deployments/{deployment_id}/log").json()["content"] == LOG


def test_the_api_refuses_a_level_it_does_not_have(client: TestClient, store: NoustStore) -> None:
    deployment_id = deployment(store)

    response = client.get(f"/api/deployments/{deployment_id}/log", params={"level": "info"})

    assert response.status_code == 422


def test_a_log_that_is_missing_says_why_whatever_was_asked(
    client: TestClient, store: NoustStore
) -> None:
    deployment_id = deployment(store, log=None)

    body = client.get(f"/api/deployments/{deployment_id}/log", params={"level": "error"}).json()

    assert body["content"] == "" and "No build log was captured" in body["missing_reason"]


def test_the_command_prints_the_latest_deployments_view(store: NoustStore) -> None:
    deployment(store)

    result = CliRunner().invoke(
        root_cli,
        ["app", "deploy-log", DOMAIN, "--level", "warn", "--no-timestamps", "--sanitize"],
    )

    assert result.exit_code == 0, result.output
    assert "npm WARN deprecated left-pad@1.0.0" in result.output
    assert "[2026-" not in result.output and "\x1b" not in result.output


def test_the_command_can_print_one_deployment_as_json(store: NoustStore) -> None:
    deployment_id = deployment(store)

    result = CliRunner().invoke(
        root_cli, ["--json", "app", "deploy-log", DOMAIN, str(deployment_id), "--search", "express"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["deployment"] == deployment_id and data["content"].count("\n") == 1


def test_the_command_says_when_there_is_nothing_to_read(store: NoustStore) -> None:
    deployment(store, log=None)

    result = CliRunner().invoke(root_cli, ["app", "deploy-log", DOMAIN])

    assert result.exit_code != 0 and "No build log was captured" in str(result.exception)


def test_the_command_refuses_a_deployment_of_another_application(store: NoustStore) -> None:
    deployment_id = deployment(store)

    result = CliRunner().invoke(
        root_cli, ["app", "deploy-log", "other.example.com", str(deployment_id)]
    )

    assert result.exit_code != 0


def test_reading_a_log_directly_applies_the_same_view(store: NoustStore) -> None:
    deployment_id = deployment(store)
    record = store.get_deployment(deployment_id)
    assert record is not None

    read = read_deployment_log(record, level="warn", timestamps=False)

    assert read.content.splitlines()[0] == "npm WARN deprecated left-pad@1.0.0"
