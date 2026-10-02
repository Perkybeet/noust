# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the commands that handle a Compose stack's database copy.

``noust app backup-before-update`` switches the copy an update takes on or off;
``noust backup restore --databases-only`` puts back only the databases a backup
holds. Neither decides anything itself: the setting is
:func:`noust.managers.stack_databases.set_backup_before_update` and the
restore is :meth:`BackupManager.restore_stack_databases`, tested in
``tests/test_stack_databases.py`` and ``tests/test_backup_stack.py``.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import Context
from noust.cli.app import cli as root_cli
from noust.cli.commands import backup as backup_cmd
from noust.core.exceptions import BackupError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.managers.backup_manager import BackupMetadata
from noust.managers.stack_databases import StackDatabase

DOMAIN = "proggest.es"
BACKUP_ID = "proggest-es_20261002_120000"
STACK = StackDatabase("postgres", "postgres", "proggest", "proggest")


def metadata(*, with_stack: bool = True) -> BackupMetadata:
    """A backup of Proggest, with or without the copy of its database."""
    entries = (
        [{"engine": "postgres", "name": "proggest", "stack": STACK.to_entry()}]
        if with_stack
        else []
    )
    return BackupMetadata(
        id=BACKUP_ID,
        domain=DOMAIN,
        app_name="proggest-es",
        created_at=datetime.now().isoformat(),
        size_bytes=4096,
        app_type="docker-compose",
        version="3.2.0",
        description="Pre-update automatic backup",
        includes_env=True,
        includes_node_modules=False,
        includes_databases=with_stack,
        database_backups=entries,
    )


class FakeManager:
    """A BackupManager that records what it is asked."""

    calls: list[tuple[str, dict[str, Any]]] = []
    stored: BackupMetadata | None = None
    failure: Exception | None = None

    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose

    def get_backup(self, backup_id: str) -> BackupMetadata | None:
        return type(self).stored

    def require_restore_confirmed(self, domain: str, **kwargs: Any) -> list[int]:
        return []

    def verify(self, backup_id: str) -> dict[str, Any]:
        type(self).calls.append(("verify", {"backup_id": backup_id}))
        return {"valid": True, "errors": [], "warnings": []}

    def restore(self, **kwargs: Any) -> bool:
        type(self).calls.append(("restore", kwargs))
        return True

    def restore_stack_databases(self, backup_id: str) -> list[StackDatabase]:
        type(self).calls.append(("restore_stack_databases", {"backup_id": backup_id}))
        if type(self).failure is not None:
            raise type(self).failure
        return [STACK]


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeManager]]:
    FakeManager.calls = []
    FakeManager.stored = metadata()
    FakeManager.failure = None
    monkeypatch.setattr(backup_cmd, "BackupManager", FakeManager)
    yield FakeManager
    FakeManager.calls = []


class Invoker:
    """Runs a command with a logger the test can read back."""

    def __init__(self) -> None:
        self.messages = io.StringIO()
        self.state = Context(_logger=Logger(stream=self.messages))
        self.result: Result | None = None

    def invoke(self, argv: list[str], **kwargs: Any) -> Result:
        self.result = CliRunner().invoke(root_cli, argv, obj=self.state, **kwargs)
        return self.result

    @property
    def output(self) -> str:
        return self.messages.getvalue() + (self.result.output if self.result else "")


@pytest.fixture
def cli() -> Invoker:
    return Invoker()


class TestRestoringOnlyTheDatabases:
    """``noust backup restore ID --databases-only``."""

    def test_it_restores_the_databases_and_no_file(
        self, cli: Invoker, manager: type[FakeManager]
    ) -> None:
        result = cli.invoke(["backup", "restore", BACKUP_ID, "--databases-only", "--force"])

        assert result.exit_code == 0, cli.output
        assert [name for name, _ in manager.calls] == ["verify", "restore_stack_databases"]
        assert manager.calls[1][1] == {"backup_id": BACKUP_ID}
        assert "postgres/proggest" in cli.output

    def test_the_prompt_says_what_is_replaced_and_that_the_application_stops(
        self, cli: Invoker, manager: type[FakeManager]
    ) -> None:
        result = cli.invoke(["backup", "restore", BACKUP_ID, "--databases-only"], input="n\n")

        assert result.exit_code == 0, cli.output
        assert "postgres/proggest" in result.output
        assert "stopped" in result.output
        assert "files" in result.output
        assert "restore_stack_databases" not in [name for name, _ in manager.calls]

    def test_a_backup_without_a_copy_is_refused_before_asking(
        self, cli: Invoker, manager: type[FakeManager]
    ) -> None:
        manager.stored = metadata(with_stack=False)

        result = cli.invoke(["backup", "restore", BACKUP_ID, "--databases-only", "--force"])

        assert result.exit_code == 1
        assert "no copy of a database" in cli.output
        assert [name for name, _ in manager.calls] == []

    def test_it_cannot_name_another_domain(self, cli: Invoker, manager: type[FakeManager]) -> None:
        result = cli.invoke(
            [
                "backup",
                "restore",
                BACKUP_ID,
                "--databases-only",
                "--target-domain",
                "staging.example.com",
                "--force",
            ]
        )

        assert result.exit_code == 2
        assert "--target-domain" in result.output

    def test_what_the_engine_said_is_shown_verbatim(
        self, cli: Invoker, manager: type[FakeManager]
    ) -> None:
        manager.failure = BackupError(
            "Could not restore postgres/proggest (service postgres)",
            details="Check the database before starting the application.",
            output="pg_restore: error: could not execute query: ERROR: relation exists",
        )

        result = cli.invoke(["backup", "restore", BACKUP_ID, "--databases-only", "--force"])

        assert result.exit_code == 1
        assert "pg_restore: error: could not execute query: ERROR: relation exists" in cli.output


class TestAFullRestoreSaysItReplacesTheDatabase:
    """The prompt of the whole restore names the databases the backup would put back."""

    def test_the_prompt_names_them(self, cli: Invoker, manager: type[FakeManager]) -> None:
        result = cli.invoke(["backup", "restore", BACKUP_ID], input="n\n")

        assert "postgres/proggest" in result.output
        assert "replaced" in result.output

    def test_a_backup_without_a_copy_does_not_mention_databases(
        self, cli: Invoker, manager: type[FakeManager]
    ) -> None:
        manager.stored = metadata(with_stack=False)

        result = cli.invoke(["backup", "restore", BACKUP_ID], input="n\n")

        assert "database" not in result.output.lower()


class TestSwitchingTheCopy:
    """``noust app backup-before-update DOMAIN [on|off]``."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[NoustStore]:
        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "noust.db")
        instance.create_app(
            App(domain=DOMAIN, app_type="docker-compose", app_path=str(tmp_path / "p"))
        )
        yield instance
        NoustStore.reset_instance()

    def test_off_and_on(self, cli: Invoker, store: NoustStore) -> None:
        off = cli.invoke(["app", "backup-before-update", DOMAIN, "off"])
        after_off = store.get_app(DOMAIN)
        on = cli.invoke(["app", "backup-before-update", DOMAIN, "on"])
        after_on = store.get_app(DOMAIN)

        assert off.exit_code == 0 and on.exit_code == 0, cli.output
        assert after_off is not None and after_off.backup_before_update is False
        assert after_on is not None and after_on.backup_before_update is True

    def test_without_a_value_it_shows_the_setting_and_changes_nothing(
        self, cli: Invoker, store: NoustStore
    ) -> None:
        store.set_app_backup_before_update(DOMAIN, False)

        result = cli.invoke(["app", "backup-before-update", DOMAIN])

        assert result.exit_code == 0, cli.output
        assert "off" in cli.output
        app = store.get_app(DOMAIN)
        assert app is not None and app.backup_before_update is False

    def test_json(self, cli: Invoker, store: NoustStore) -> None:
        result = cli.invoke(["app", "backup-before-update", DOMAIN, "off", "--json"])

        assert result.exit_code == 0, cli.output
        assert json.loads(result.output) == {
            "domain": DOMAIN,
            "backup_before_update": False,
            "previous": True,
        }

    def test_a_value_that_is_not_on_or_off_is_a_usage_error(
        self, cli: Invoker, store: NoustStore
    ) -> None:
        result = cli.invoke(["app", "backup-before-update", DOMAIN, "maybe"])

        assert result.exit_code == 2

    def test_an_unknown_application_says_so(self, cli: Invoker, store: NoustStore) -> None:
        result = cli.invoke(["app", "backup-before-update", "nobody.example.com", "off"])

        assert result.exit_code != 0
        assert "nobody.example.com" in str(result.exception) + result.output
