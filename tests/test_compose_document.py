# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Editing the Compose document of a stack that was deployed from one.

A document pasted into the console (or converted from a template) is the stack's source and used
to be writable only when the stack was created. Pinned here: which stacks have such a document and
why the others say where their file is edited; that a change goes through the guard a creation
runs, and through the variables the stack has, before anything is written; that writing rebuilds
nothing and leaves the stack's pending changes saying so; and that applying it updates behind the
health gate with the previous document put back when the stack does not come up.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.store import App, NoustStore
from noust.deployers import compose_document, pending_changes
from noust.deployers.compose_document import (
    check_document,
    read_document,
    replace_document,
    why_not_editable,
)
from noust.recipes import get_recipe
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir
from noust.recipes.installed import record_install

DOMAIN = "stack.example.com"
APP_NAME = "stack-example-com"

DOCUMENT = """\
services:
  web:
    image: nginx:1.27
    ports:
      - "127.0.0.1:8080:80"
    environment:
      - DB_PASSWORD=${DB_PASSWORD:?give the database a password}
"""

NEWER = DOCUMENT.replace("nginx:1.27", "nginx:1.28")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


def make_stack(store: NoustStore, tmp_path: Path, **fields: Any) -> App:
    """A Docker Compose stack deployed from a pasted document, with its .env."""
    directory = recipe_source_dir(APP_NAME, store)
    directory.mkdir(parents=True)
    (directory / COMPOSE_FILE).write_text(DOCUMENT)
    root = tmp_path / "apps" / APP_NAME
    root.mkdir(parents=True)
    (root / ".env").write_text("DB_PASSWORD=hunter2\n")
    values: dict[str, Any] = {
        "domain": DOMAIN,
        "app_type": "docker-compose",
        "source": str(directory),
        "app_path": str(root),
        "port": 8080,
        "layout": "inplace",
    }
    values.update(fields)
    store.create_app(App(**values))
    row = store.get_app(DOMAIN)
    assert row is not None
    return row


@pytest.fixture
def stack(store: NoustStore, tmp_path: Path) -> App:
    return make_stack(store, tmp_path)


def kept(store: NoustStore) -> str:
    return (recipe_source_dir(APP_NAME, store) / COMPOSE_FILE).read_text()


class Updates:
    """Stands in for the update, recording what it was asked and failing on demand."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail: NoustError | None = None
        self.put_back: list[dict[str, Any]] = []

    def update_app(self, domain: str, **kwargs: Any) -> Any:
        self.calls.append({"domain": domain, **kwargs})
        if self.fail is not None:
            raise self.fail
        return type("Outcome", (), {"deployment_id": 41, "warnings": None})()

    def put_stack_back(self, domain: str, app_name: str, snapshot: Any, failure: Any, **kw: Any):
        self.put_back.append({"domain": domain, "snapshot": dict(snapshot), "failure": failure})


@pytest.fixture
def updates(monkeypatch: pytest.MonkeyPatch) -> Updates:
    fake = Updates()
    monkeypatch.setattr("noust.deployers.lifecycle.update_app", fake.update_app)
    monkeypatch.setattr("noust.recipes.updates.put_stack_back", fake.put_stack_back)
    return fake


# ---------------------------------------------------------------------------
# Which stacks have a document
# ---------------------------------------------------------------------------


class TestWhoHasADocument:
    def test_a_stack_deployed_from_a_document_has_one(self, store: NoustStore, stack: App) -> None:
        assert why_not_editable(stack, store) is None

    def test_a_stack_from_a_repository_is_told_to_edit_it_in_git(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        app = make_stack(store, tmp_path, source="https://github.com/acme/stack.git")

        reason = why_not_editable(app, store)

        assert reason is not None and "https://github.com/acme/stack.git" in reason
        assert f"noust app compose set {DOMAIN} --file" in reason

    def test_a_stack_installed_from_a_recipe_is_told_to_update_the_recipe(
        self, store: NoustStore, stack: App
    ) -> None:
        assert record_install(DOMAIN, get_recipe("vaultwarden"), store=store)

        reason = why_not_editable(stack, store)

        assert reason is not None and f"noust recipe update {DOMAIN}" in reason

    def test_an_application_that_is_not_a_stack_has_none(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        app = make_stack(store, tmp_path, app_type="nodejs")

        assert "only a Docker Compose stack" in str(why_not_editable(app, store))

    def test_a_link_to_the_directory_is_the_same_directory(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        """A directory reached through a link to it is the same directory."""
        real = recipe_source_dir(APP_NAME, store)
        alias = tmp_path / "alias"
        alias.symlink_to(real.parent)
        app = make_stack(store, tmp_path, source=str(alias / real.name))

        assert why_not_editable(app, store) is None

    def test_reading_one_that_has_none_says_why(self, store: NoustStore, tmp_path: Path) -> None:
        make_stack(store, tmp_path, source="https://github.com/acme/stack.git")

        with pytest.raises(ValidationError, match="no compose document to edit"):
            read_document(DOMAIN, store=store)


# ---------------------------------------------------------------------------
# Reading and judging
# ---------------------------------------------------------------------------


class TestReadingAndChecking:
    def test_the_document_is_read_with_where_it_is_and_the_read_is_audited(
        self, store: NoustStore, stack: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            compose_document.audit, "record", lambda name, **kw: events.append((name, kw))
        )

        found = read_document(DOMAIN, actor="ana", store=store)

        assert found.text == DOCUMENT
        assert found.path == str(recipe_source_dir(APP_NAME, store) / COMPOSE_FILE)
        assert events == [
            (
                "apps.compose.document",
                {"target": f"app:{DOMAIN}", "details": {"action": "read", "by": "ana"}},
            )
        ]

    def test_a_change_is_seen_and_the_same_text_is_not(self, store: NoustStore, stack: App) -> None:
        assert check_document(DOMAIN, NEWER, store=store).changed is True
        assert check_document(DOMAIN, DOCUMENT, store=store).changed is False
        assert check_document(DOMAIN, DOCUMENT.rstrip("\n"), store=store).changed is False

    def test_what_the_guard_of_a_creation_refuses_is_refused_here(
        self, store: NoustStore, stack: App
    ) -> None:
        hostile = DOCUMENT + "    privileged: true\n"

        check = check_document(DOMAIN, hostile, store=store)

        assert not check.deployable and any("privileged" in r for r in check.analysis.refused)

    def test_a_variable_the_document_requires_and_the_stack_lacks_is_named(
        self, store: NoustStore, stack: App
    ) -> None:
        needs_more = DOCUMENT + "      - API_KEY=${API_KEY:?an API key}\n"

        check = check_document(DOMAIN, needs_more, store=store)

        assert check.missing == ["API_KEY"] and not check.deployable

    def test_text_that_is_not_a_compose_document_is_a_validation_error(
        self, store: NoustStore, stack: App
    ) -> None:
        with pytest.raises(ValidationError):
            check_document(DOMAIN, "services: [", store=store)
        with pytest.raises(ValidationError, match="no services"):
            check_document(DOMAIN, "version: '3'\n", store=store)


# ---------------------------------------------------------------------------
# Replacing
# ---------------------------------------------------------------------------


class TestReplacing:
    def test_the_document_is_kept_and_nothing_is_rebuilt(
        self, store: NoustStore, stack: App, updates: Updates
    ) -> None:
        change = replace_document(DOMAIN, NEWER, actor="ana", store=store)

        assert kept(store) == NEWER
        assert change.changed and not change.applied and change.services == ("web",)
        assert updates.calls == []

    def test_the_audit_names_who_and_what_and_never_the_text(
        self, store: NoustStore, stack: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[dict[str, Any]] = []
        monkeypatch.setattr(
            compose_document.audit, "record", lambda name, **kw: events.append({"name": name, **kw})
        )

        replace_document(DOMAIN, NEWER, actor="ana", store=store)

        (event,) = events
        assert event["name"] == "apps.compose.document"
        assert event["details"]["action"] == "replace" and event["details"]["by"] == "ana"
        assert "nginx" not in str(event)

    def test_the_same_document_changes_and_records_nothing(
        self, store: NoustStore, stack: App, updates: Updates, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[Any] = []
        monkeypatch.setattr(compose_document.audit, "record", lambda *a, **kw: events.append(a))

        change = replace_document(DOMAIN, DOCUMENT, apply=True, store=store)

        assert not change.changed and not change.applied
        assert events == [] and updates.calls == []

    def test_a_refused_document_leaves_the_stack_as_it_was(
        self, store: NoustStore, stack: App
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            replace_document(DOMAIN, NEWER + "    privileged: true\n", store=store)

        assert "privileged" in caught.value.details
        assert kept(store) == DOCUMENT

    def test_a_missing_variable_leaves_the_stack_as_it_was_and_says_the_command(
        self, store: NoustStore, stack: App
    ) -> None:
        with pytest.raises(DeploymentError) as caught:
            replace_document(DOMAIN, NEWER + "      - TOKEN=${TOKEN:?a token}\n", store=store)

        assert "TOKEN" in caught.value.message
        assert f"noust env set {DOMAIN}" in caught.value.details
        assert kept(store) == DOCUMENT

    def test_the_text_is_kept_with_one_trailing_newline(
        self, store: NoustStore, stack: App
    ) -> None:
        replace_document(DOMAIN, NEWER.rstrip("\n"), store=store)

        assert kept(store) == NEWER


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


class TestApplying:
    def test_apply_updates_the_stack_after_writing(
        self, store: NoustStore, stack: App, updates: Updates
    ) -> None:
        change = replace_document(
            DOMAIN, NEWER, apply=True, actor="ana", trigger="cli", job_id="job-1", store=store
        )

        (call,) = updates.calls
        assert call["domain"] == DOMAIN and call["trigger"] == "cli" and call["job_id"] == "job-1"
        assert kept(store) == NEWER
        assert change.applied and change.deployment_id == 41

    def test_when_the_stack_does_not_come_up_the_previous_document_is_put_back(
        self, store: NoustStore, stack: App, updates: Updates
    ) -> None:
        updates.fail = DeploymentError("the gate failed", details="probes")

        with pytest.raises(DeploymentError, match="the gate failed"):
            replace_document(DOMAIN, NEWER, apply=True, store=store)

        (back,) = updates.put_back
        assert back["domain"] == DOMAIN and back["snapshot"] == {COMPOSE_FILE: DOCUMENT}
        assert back["failure"] is updates.fail

    def test_a_document_the_guard_refuses_never_reaches_the_update(
        self, store: NoustStore, stack: App, updates: Updates
    ) -> None:
        with pytest.raises(ValidationError):
            replace_document(DOMAIN, NEWER + "    privileged: true\n", apply=True, store=store)

        assert updates.calls == [] and updates.put_back == []


# ---------------------------------------------------------------------------
# Pending changes
# ---------------------------------------------------------------------------


class TestPendingChanges:
    def test_a_saved_document_the_stack_does_not_run_yet_is_a_pending_rebuild(
        self, store: NoustStore, stack: App
    ) -> None:
        assert pending_changes.record_applied(DOMAIN, 1, store=store)

        replace_document(DOMAIN, NEWER, store=store)
        changes = pending_changes.pending(DOMAIN, store=store)

        (item,) = [i for i in changes.items if i.area == "compose"]
        assert (item.change, item.needs) == ("changed", "rebuild")
        assert "nginx" not in str(changes.to_dict())

    def test_a_stack_from_a_repository_has_no_such_section(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        make_stack(store, tmp_path, source="https://github.com/acme/stack.git")
        assert pending_changes.record_applied(DOMAIN, 1, store=store)

        assert all(
            item.area != "compose" for item in pending_changes.pending(DOMAIN, store=store).items
        )
