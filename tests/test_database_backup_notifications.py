# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a database backup or restore tells the operator.

The words are the notification catalog's (the same title, state and excerpt an
application's backup gets); what changes is the subject, the page the link
opens, and that no application domain is claimed. A job the console queued is
announced by the job manager's subscriber, and this pins that it says the same
thing as a run of the timer.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import State
from noust.managers.database.backup_notifications import (
    compose_database_backup_completed,
    compose_database_backup_failed,
    compose_database_backup_upload_failed,
    compose_database_restore,
    database_job_notification,
    database_subject,
)

CTX = NotificationContext("en", "web-1", "https://console.example.com")
ERROR = "The dump was taken but failed its check\n  Details: pg_restore: error: unexpected EOF"


def job(job_type: str, status: str, **metadata: Any) -> SimpleNamespace:
    return SimpleNamespace(
        type=job_type,
        status=SimpleNamespace(value=status),
        metadata={"kind": "database", "engine": "postgresql", "database": "shop", **metadata},
        error=ERROR if status == "failed" else None,
    )


class TestComposers:
    def test_a_failed_backup_is_about_the_database(self) -> None:
        notification = compose_database_backup_failed("postgresql", "shop", ERROR, CTX)

        assert notification.kind == "backup_failed"
        assert notification.state is State.FAILED
        assert notification.title == "Backup failed"
        assert notification.subject == "postgresql/shop"
        assert notification.domain is None
        assert notification.headline == "Backup failed · postgresql/shop"

    def test_the_tools_own_words_are_the_excerpt(self) -> None:
        notification = compose_database_backup_failed("postgresql", "shop", ERROR, CTX)

        assert notification.excerpt is not None
        assert "pg_restore: error: unexpected EOF" in notification.excerpt.lines

    def test_it_says_nothing_about_an_applications_files(self) -> None:
        for notification in (
            compose_database_backup_failed("postgresql", "shop", ERROR, CTX),
            compose_database_restore(True, "postgresql", "shop", "dump.dump", None, CTX),
            compose_database_restore(False, "postgresql", "shop", "dump.dump", ERROR, CTX),
        ):
            assert "application" not in notification.summary.lower()

    def test_the_link_opens_the_databases_page(self) -> None:
        notification = compose_database_backup_failed("postgresql", "shop", ERROR, CTX)

        assert notification.console_link is not None
        assert (
            notification.console_link.url == "https://console.example.com/databases/postgresql/shop"
        )

    def test_with_no_public_url_there_is_no_link(self) -> None:
        notification = compose_database_backup_failed(
            "postgresql", "shop", ERROR, NotificationContext("en", "web-1", "")
        )

        assert notification.links == ()

    def test_the_server_is_the_first_fact(self) -> None:
        notification = compose_database_backup_failed("postgresql", "shop", ERROR, CTX)

        assert notification.facts[0].key == "server"
        assert notification.facts[0].value == "web-1"

    def test_an_upload_failure_is_a_warning_naming_the_destination(self) -> None:
        notification = compose_database_backup_upload_failed(
            "postgresql", "shop", "nas", "Failed\n  Details: AccessDenied (403)", CTX
        )

        assert notification.kind == "backup_failed"
        assert notification.state is State.WARNING
        assert notification.subject == "postgresql/shop"
        assert ("destination", "nas") in [(f.key, f.value) for f in notification.facts]

    def test_the_heartbeat_is_the_optional_kind(self) -> None:
        notification = compose_database_backup_completed(
            "postgresql",
            "shop",
            "postgresql-shop-1.dump",
            CTX,
            size_bytes=2048,
            destinations=("nas",),
        )

        assert notification.kind == "backup_success"
        assert notification.state is State.OK
        assert notification.subject == "postgresql/shop"
        keys = {f.key for f in notification.facts}
        assert {"backup", "size", "destinations"} <= keys

    def test_a_restore_carries_the_dump_it_loaded(self) -> None:
        done = compose_database_restore(True, "postgresql", "shop_copy", "d.dump", None, CTX)
        failed = compose_database_restore(False, "postgresql", "shop", "d.dump", ERROR, CTX)

        assert (done.kind, done.state, done.subject) == (
            "restore_success",
            State.OK,
            "postgresql/shop_copy",
        )
        assert ("backup", "d.dump") in [(f.key, f.value) for f in done.facts]
        assert (failed.kind, failed.state) == ("restore_failed", State.FAILED)

    def test_the_words_follow_the_operators_language(self) -> None:
        notification = compose_database_backup_failed(
            "postgresql", "shop", ERROR, NotificationContext("es", "web-1", "")
        )

        assert notification.title == "Copia de seguridad fallida"

    def test_the_subject_of_a_database(self) -> None:
        assert database_subject("mysql", "blog") == "mysql/blog"


class TestJobs:
    def test_a_failed_dump_job_is_announced_as_a_failed_backup_of_the_database(self) -> None:
        notification = database_job_notification(job("backup", "failed"), CTX)

        assert notification is not None
        assert notification.kind == "backup_failed"
        assert notification.subject == "postgresql/shop"

    def test_a_finished_restore_job_is_announced_either_way(self) -> None:
        done = database_job_notification(job("restore", "completed", backup_id="d.dump"), CTX)
        failed = database_job_notification(job("restore", "failed", backup_id="d.dump"), CTX)

        assert done is not None and done.kind == "restore_success"
        assert failed is not None and failed.kind == "restore_failed"
        assert ("backup", "d.dump") in [(f.key, f.value) for f in done.facts]

    @pytest.mark.parametrize(
        ("job_type", "status"),
        [
            ("backup", "completed"),
            ("backup", "running"),
            ("restore", "running"),
            ("database", "failed"),
            ("push", "failed"),
        ],
    )
    def test_everything_else_is_not_announced(self, job_type: str, status: str) -> None:
        assert database_job_notification(job(job_type, status), CTX) is None

    def test_a_job_that_names_no_database_is_not_announced(self) -> None:
        nameless = job("backup", "failed")
        nameless.metadata.pop("database")

        assert database_job_notification(nameless, CTX) is None


class TestTheJobManagersAnnouncer:
    """``web/server.py`` hands a database job to this module, and only a database job."""

    def test_a_database_job_is_announced_about_the_database(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web import server

        monkeypatch.setattr(server, "fresh_config", lambda: {})
        monkeypatch.setattr(
            server.NotificationContext, "from_config", classmethod(lambda cls, config: CTX)
        )

        notification = server.deployment_notification(job("backup", "failed"))

        assert notification is not None
        assert notification.subject == "postgresql/shop"
        assert notification.domain is None

    def test_an_applications_backup_job_is_announced_as_it_always_was(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web import server

        monkeypatch.setattr(server, "fresh_config", lambda: {})
        monkeypatch.setattr(
            server.NotificationContext, "from_config", classmethod(lambda cls, config: CTX)
        )
        application = SimpleNamespace(
            type="backup",
            status=SimpleNamespace(value="failed"),
            metadata={"domain": "shop.example.com"},
            error=ERROR,
        )

        notification = server.deployment_notification(application)

        assert notification is not None
        assert notification.subject == "shop.example.com"
        assert notification.domain == "shop.example.com"
