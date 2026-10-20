# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app compose document`` and ``replace``: read and change the Compose document of a stack
that was deployed from one.

The command is a client of :mod:`noust.deployers.compose_document`; pinned here is what it
presents: the document as it is, what ``--check`` judges and the exit status that goes with it,
that writing says when the stack will run it, and that ``--apply`` is the update.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.store import NoustStore
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir
from noust.web.auth import STATE_DIR_ENV
from tests.test_compose_document import (
    APP_NAME,
    DOCUMENT,
    DOMAIN,
    NEWER,
    Updates,
    make_stack,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))


@pytest.fixture
def updates(monkeypatch: pytest.MonkeyPatch) -> Updates:
    fake = Updates()
    monkeypatch.setattr("noust.deployers.lifecycle.update_app", fake.update_app)
    monkeypatch.setattr("noust.recipes.updates.put_stack_back", fake.put_stack_back)
    return fake


def run(*args: str, code: int = 0, input: str | None = None) -> str:
    result = CliRunner().invoke(root_cli, ["app", "compose", *args], input=input)
    assert result.exit_code == code, f"{result.output} {result.exception!r}"
    return f"{result.output}{result.exception or ''}"


def kept(store: NoustStore) -> str:
    return (recipe_source_dir(APP_NAME, store) / COMPOSE_FILE).read_text()


def write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "new.yml"
    path.write_text(text)
    return str(path)


def test_the_document_is_printed_as_it_is(store: NoustStore, tmp_path: Path) -> None:
    make_stack(store, tmp_path)

    assert run("document", DOMAIN) == DOCUMENT


def test_the_document_as_json_says_where_it_is(store: NoustStore, tmp_path: Path) -> None:
    make_stack(store, tmp_path)

    data = json.loads(run("document", DOMAIN, "--json"))

    assert data["document"] == DOCUMENT
    assert data["path"] == str(recipe_source_dir(APP_NAME, store) / COMPOSE_FILE)


def test_a_stack_from_a_repository_says_where_its_file_is_edited(
    store: NoustStore, tmp_path: Path
) -> None:
    make_stack(store, tmp_path, source="https://github.com/acme/stack.git")

    out = run("document", DOMAIN, code=1)

    assert "no compose document to edit" in out and "https://github.com/acme/stack.git" in out


def test_check_judges_and_writes_nothing(store: NoustStore, tmp_path: Path) -> None:
    make_stack(store, tmp_path)

    out = run("replace", DOMAIN, "--from", write(tmp_path, NEWER), "--check")

    assert "Service web" in out and "nginx:1.28" in out and "Different from the stack's" in out
    assert kept(store) == DOCUMENT


def test_check_exits_with_an_error_for_a_document_that_cannot_be_deployed(
    store: NoustStore, tmp_path: Path
) -> None:
    make_stack(store, tmp_path)

    out = run(
        "replace",
        DOMAIN,
        "--from",
        write(tmp_path, NEWER + "    privileged: true\n"),
        "--check",
        code=1,
    )

    assert "privileged" in out and "cannot be deployed" in out
    assert kept(store) == DOCUMENT


def test_check_names_a_variable_the_stack_lacks_with_the_command_to_set_it(
    store: NoustStore, tmp_path: Path
) -> None:
    make_stack(store, tmp_path)
    document = NEWER + "      - TOKEN=${TOKEN:?a token}\n"

    out = run("replace", DOMAIN, "--from", write(tmp_path, document), "--check", code=1)

    assert f"TOKEN required and not set: noust env set {DOMAIN} TOKEN=<value>" in out


def test_replace_keeps_the_document_and_says_when_the_stack_runs_it(
    store: NoustStore, tmp_path: Path, updates: Updates
) -> None:
    make_stack(store, tmp_path)

    out = run("replace", DOMAIN, "--from", write(tmp_path, NEWER))

    assert kept(store) == NEWER
    assert f"noust update {DOMAIN}" in out and updates.calls == []


def test_replace_reads_standard_input(store: NoustStore, tmp_path: Path) -> None:
    make_stack(store, tmp_path)

    run("replace", DOMAIN, "--from", "-", input=NEWER)

    assert kept(store) == NEWER


def test_apply_is_the_update(store: NoustStore, tmp_path: Path, updates: Updates) -> None:
    make_stack(store, tmp_path)

    out = run("replace", DOMAIN, "--from", write(tmp_path, NEWER), "--apply")

    (call,) = updates.calls
    assert call["domain"] == DOMAIN and "now runs the new document" in out


def test_the_same_document_is_said_to_be_the_stacks(
    store: NoustStore, tmp_path: Path, updates: Updates
) -> None:
    make_stack(store, tmp_path)

    out = run("replace", DOMAIN, "--from", write(tmp_path, DOCUMENT), "--apply")

    assert "already has this document" in out and updates.calls == []


def test_replace_as_json(store: NoustStore, tmp_path: Path, updates: Updates) -> None:
    make_stack(store, tmp_path)

    data: dict[str, Any] = json.loads(
        run("replace", DOMAIN, "--from", write(tmp_path, NEWER), "--apply", "--json")
    )

    assert data["changed"] is True and data["applied"] is True and data["deployment_id"] == 41
    assert data["services"] == ["web"]


def test_a_rehearsal_judges_and_writes_nothing(
    store: NoustStore, tmp_path: Path, updates: Updates
) -> None:
    make_stack(store, tmp_path)

    result = CliRunner().invoke(
        root_cli,
        ["--dry-run", "app", "compose", "replace", DOMAIN, "--from", write(tmp_path, NEWER)],
    )

    assert result.exit_code == 0, result.output
    assert "Would replace the document" in result.output
    assert kept(store) == DOCUMENT and updates.calls == []
