# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`noust.core.notifications.composers` and ``context``.

Composition is tested *before* rendering, on fields and states rather than on
text: a test that asserts ``"Trigger: webhook" in body`` is tied to a layout, one
that asserts the notification has a ``trigger`` fact is not. The channel
layouts are pinned by the snapshot tests instead.

What is defended:

- **Each event says what it is**: the right kind (the switch), the right code
  (the fine name), the right state, the right console page.
- **The deduplication rules of the design (D-1, D-2, D-3)**: a title carries no
  fact, a summary repeats neither title nor fact, the commit appears once, and
  a failure's own message is not shown again as a paragraph.
- **Certificates that already expired say so** instead of "expires in -80 days".
- **What another program said stays verbatim** and lands in the excerpt, the
  end of it, with the health gate's journal preferred over its probes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from noust.core.config import Config
from noust.core.exceptions import DeploymentError, NoustError, RolledBackError
from noust.core.notifications.composers import (
    PreviewOf,
    compose_backup_completed,
    compose_backup_failed,
    compose_backup_schedule_missing,
    compose_backup_upload_failed,
    compose_certificate,
    compose_deploy,
    compose_disk,
    compose_disk_recovered,
    compose_node_host_key_changed,
    compose_node_recovered,
    compose_node_unreachable,
    compose_restore,
    compose_test,
    compose_unit_failure,
    compose_unit_recovered,
    format_bytes,
    format_duration,
)
from noust.core.notifications.context import NotificationContext, default_server_name, server_name
from noust.core.notifications.excerpt import normalize
from noust.core.notifications.model import Notification, State
from noust.deployers.deploy_events import DeployEvent, DeployEventKind
from tests.notifications_support import NODE_CRASH, NODE_ERROR

JOURNAL = "\n".join(
    [f"Sep 29 10:45:{i:02d} web-1 npm[48455]: line number {i}" for i in range(10, 30)]
)
EVIDENCE = (
    "The application did not answer the health check.\n\n"
    "Health check attempts 1-15 failed: <urlopen error [Errno 111] Connection refused>\n\n"
    f"Last lines of the journal of shop-example-com:\n{JOURNAL}"
)


@pytest.fixture
def ctx() -> NotificationContext:
    return NotificationContext(
        locale="en", server="web-1", public_url="https://console.example.com"
    )


@pytest.fixture
def ctx_es() -> NotificationContext:
    return NotificationContext(
        locale="es", server="web-1", public_url="https://console.example.com"
    )


def event(kind: DeployEventKind = DeployEventKind.SUCCEEDED, **overrides: object) -> DeployEvent:
    fields: dict = {
        "domain": "shop.example.com",
        "deployment_id": 42,
        "trigger": "webhook",
        "commit": "a1b2c3d",
        "branch": "main",
        "commit_message": "Fix cart total",
        "release_id": "20260929-104449",
        "duration_s": 72.4,
    }
    fields.update(overrides)
    return DeployEvent(kind=kind, **fields)


def facts(notification: Notification) -> dict[str, str]:
    return {fact.key: fact.value for fact in notification.facts}


class TestContext:
    def test_link_is_the_public_url_and_a_path(self, ctx: NotificationContext) -> None:
        link = ctx.link("/apps/shop.example.com")

        assert link is not None
        assert link.rel == "console"
        assert link.url == "https://console.example.com/apps/shop.example.com"
        assert link.label == "Open in the console"

    def test_no_public_url_means_no_link(self) -> None:
        assert NotificationContext(server="web-1").link("/apps") is None

    def test_a_node_path_goes_under_n(self, ctx: NotificationContext) -> None:
        link = ctx.link("/apps", node="web 2")

        assert link is not None
        assert link.url == "https://console.example.com/n/web%202/apps"

    def test_a_central_only_page_never_carries_a_node(self, ctx: NotificationContext) -> None:
        link = ctx.link("/fleet", node="web-2")

        assert link is not None
        assert link.url == "https://console.example.com/fleet"

    def test_a_public_url_that_already_names_the_node_is_used_as_it_is(self) -> None:
        ctx = NotificationContext(server="web-2", public_url="https://central.example.com/n/web-2")

        link = ctx.link("/apps/shop.example.com/deployments/42")

        assert link is not None
        assert (
            link.url == "https://central.example.com/n/web-2/apps/shop.example.com/deployments/42"
        )

    def test_spanish_label(self, ctx_es: NotificationContext) -> None:
        link = ctx_es.link("/apps")

        assert link is not None
        assert link.label == "Abrir en la consola"

    def test_from_config_reads_language_name_and_url(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
        Config.reset_instance()
        try:
            config = Config()
            config.set("notifications.language", "es")
            config.set("server.name", "edge-3")
            config.set("web.public_url", "https://console.example.com/")

            context = NotificationContext.from_config(config)
        finally:
            Config.reset_instance()

        assert context == NotificationContext("es", "edge-3", "https://console.example.com")

    def test_the_server_name_is_validated_where_it_is_set(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from noust.core.exceptions import ConfigError

        monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
        Config.reset_instance()
        try:
            config = Config()
            config.set("server.name", "  web-1  ")
            assert config.get("server.name") == "web-1"
            for bad in ("two\nlines", "x" * 65):
                with pytest.raises(ConfigError, match=r"server\.name"):
                    config.set("server.name", bad)
        finally:
            Config.reset_instance()

    def test_the_server_name_defaults_to_the_short_hostname(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("socket.gethostname", lambda: "web-1.internal.example.com")

        assert default_server_name() == "web-1"

    def test_an_unreadable_hostname_still_names_something(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken() -> str:
            raise OSError("no hostname")

        monkeypatch.setattr("socket.gethostname", broken)

        assert default_server_name() == "server"

    def test_a_configured_name_wins_and_is_bounded(self) -> None:
        class Fake:
            def get(self, key: str, default: object = None) -> object:
                return "x" * 200 if key == "server.name" else default

        assert server_name(Fake()) == "x" * 64  # type: ignore[arg-type]


class TestDeployIdentity:
    @pytest.mark.parametrize(
        ("kind", "kind_switch", "state"),
        [
            (DeployEventKind.STARTED, "deploy_started", State.PROGRESS),
            (DeployEventKind.SUCCEEDED, "deploy_success", State.OK),
            (DeployEventKind.FAILED, "deploy_failed", State.FAILED),
            (DeployEventKind.ROLLED_BACK, "deploy_rolled_back", State.WARNING),
        ],
    )
    def test_outcome_decides_switch_and_state(
        self,
        ctx: NotificationContext,
        kind: DeployEventKind,
        kind_switch: str,
        state: State,
    ) -> None:
        notification = compose_deploy(event(kind), ctx)

        assert notification.kind == kind_switch
        assert notification.state is state

    @pytest.mark.parametrize("operation", ["deploy", "update", "rollback", "activate", "migrate"])
    def test_operation_names_the_code_but_not_the_switch(
        self, ctx: NotificationContext, operation: str
    ) -> None:
        notification = compose_deploy(event(operation=operation), ctx)

        assert notification.code == f"{operation}.succeeded"
        assert notification.kind == "deploy_success"

    def test_each_operation_has_its_own_title(self, ctx: NotificationContext) -> None:
        titles = {
            operation: compose_deploy(event(operation=operation), ctx).title
            for operation in ("deploy", "update", "rollback", "activate", "migrate")
        }

        assert titles == {
            "deploy": "Deployed",
            "update": "Updated",
            "rollback": "Reverted",
            "activate": "Release activated",
            "migrate": "Migrated to releases",
        }

    def test_a_migration_cannot_be_rolled_back_it_failed(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(event(DeployEventKind.ROLLED_BACK, operation="migrate"), ctx)

        assert notification.code == "migrate.failed"
        assert notification.state is State.FAILED

    def test_an_unknown_operation_is_a_deploy(self, ctx: NotificationContext) -> None:
        assert compose_deploy(event(operation="teleport"), ctx).code == "deploy.succeeded"

    def test_the_subject_is_the_application_and_domain_is_kept(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_deploy(event(), ctx)

        assert notification.subject == "shop.example.com"
        assert notification.domain == "shop.example.com"
        assert notification.server == "web-1"
        assert notification.locale == "en"

    def test_the_timestamp_is_the_events(self, ctx: NotificationContext) -> None:
        moment = datetime(2026, 9, 29, 10, 45, 51, tzinfo=timezone.utc)

        assert compose_deploy(event(ts=moment), ctx).ts == moment


class TestDeployFacts:
    def test_the_server_is_first(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(event(), ctx)

        assert [fact.key for fact in notification.facts] == [
            "server",
            "release",
            "commit",
            "trigger",
            "duration",
        ]
        assert notification.facts[0].value == "web-1"

    def test_the_commit_reads_hash_branch_and_subject_once(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(event(), ctx)

        assert facts(notification)["commit"] == "a1b2c3d (main): Fix cart total"

    def test_the_commit_appears_nowhere_else(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(event(), ctx)

        text = " ".join([notification.title, notification.summary, notification.subject])
        assert "a1b2c3d" not in text
        assert sum("a1b2c3d" in fact.value for fact in notification.facts) == 1

    def test_a_long_commit_subject_is_bounded(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(event(commit_message="x" * 500), ctx)

        assert len(facts(notification)["commit"]) < 120

    def test_a_missing_commit_or_branch_drops_only_that_part(
        self, ctx: NotificationContext
    ) -> None:
        assert facts(compose_deploy(event(branch=None, commit_message=None), ctx))["commit"] == (
            "a1b2c3d"
        )
        assert "commit" not in facts(compose_deploy(event(commit=None, branch=None), ctx))

    def test_a_started_event_has_no_duration(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(event(DeployEventKind.STARTED, duration_s=None), ctx)

        assert "duration" not in facts(notification)

    def test_the_trigger_is_named_not_quoted_from_the_enum(
        self, ctx: NotificationContext, ctx_es: NotificationContext
    ) -> None:
        assert facts(compose_deploy(event(trigger="webhook"), ctx))["trigger"] == "Webhook"
        assert facts(compose_deploy(event(trigger="cli"), ctx))["trigger"] == "CLI"
        assert facts(compose_deploy(event(trigger="panel"), ctx))["trigger"] == "Console"
        assert facts(compose_deploy(event(trigger="panel"), ctx_es))["trigger"] == "Consola"

    def test_an_unknown_trigger_is_shown_as_it_is(self, ctx: NotificationContext) -> None:
        assert facts(compose_deploy(event(trigger="scheduler"), ctx))["trigger"] == "scheduler"

    @pytest.mark.parametrize(
        ("seconds", "text"),
        [(0.4, "0 s"), (42, "42 s"), (72.4, "1 min 12 s"), (120, "2 min"), (7500, "2 h 5 min")],
    )
    def test_duration_is_short_and_neutral(self, seconds: float, text: str) -> None:
        assert format_duration(seconds) == text

    def test_a_preview_names_its_parent_and_pull_request(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(event(), ctx, preview=PreviewOf("shop.example.com", 12))

        assert facts(notification)["preview"] == "shop.example.com #12"

    def test_a_preview_without_a_number_names_only_its_parent(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_deploy(event(), ctx, preview=PreviewOf("shop.example.com", None))

        assert facts(notification)["preview"] == "shop.example.com"

    def test_facts_have_translated_labels(self, ctx_es: NotificationContext) -> None:
        labels = [fact.label for fact in compose_deploy(event(), ctx_es).facts]

        assert labels == ["Servidor", "Versión", "Commit", "Iniciado por", "Duración"]


class TestDeployLinks:
    def test_links_to_the_deployment(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(event(), ctx)

        assert [link.url for link in notification.links] == [
            "https://console.example.com/apps/shop.example.com/deployments/42"
        ]

    def test_without_a_history_row_it_links_to_the_application(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_deploy(event(deployment_id=None), ctx)

        assert [link.url for link in notification.links] == [
            "https://console.example.com/apps/shop.example.com"
        ]

    def test_without_a_public_url_there_is_no_link(self) -> None:
        notification = compose_deploy(event(), NotificationContext(server="web-1"))

        assert notification.links == ()


class TestDeployDeduplication:
    @pytest.mark.parametrize("kind", list(DeployEventKind))
    @pytest.mark.parametrize("operation", ["deploy", "update", "rollback", "activate"])
    def test_title_and_summary_repeat_no_fact_and_not_each_other(
        self, ctx: NotificationContext, kind: DeployEventKind, operation: str
    ) -> None:
        notification = compose_deploy(event(kind, operation=operation), ctx)

        assert normalize(notification.title) not in normalize(notification.summary)
        for fact in notification.facts:
            if len(fact.value) > 3:
                assert fact.value not in notification.title
                assert fact.value not in notification.summary

    def test_the_summary_is_one_sentence(self, ctx: NotificationContext) -> None:
        for kind in DeployEventKind:
            summary = compose_deploy(event(kind), ctx).summary
            assert summary.count(". ") == 0, summary
            assert summary.endswith(".")


class TestDeployFailureEvidence:
    def test_the_journal_becomes_the_excerpt(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(
            event(
                DeployEventKind.ROLLED_BACK,
                error_message="Release 2026 did not pass its health check; release 2025 is active again",
                error_output=EVIDENCE,
            ),
            ctx,
        )

        assert notification.excerpt is not None
        assert notification.excerpt.label == "Last lines of the journal of shop-example-com"
        assert notification.excerpt.lines[-1] == "Sep 29 10:45:29 web-1 npm[48455]: line number 29"
        assert len(notification.excerpt.lines) == 12
        assert notification.excerpt.omitted == 8

    def test_a_rollbacks_own_message_is_not_shown_again(self, ctx: NotificationContext) -> None:
        message = "Release 2026 did not pass its health check; release 2025 is active again"

        notification = compose_deploy(
            event(DeployEventKind.ROLLED_BACK, error_message=message, error_output=EVIDENCE),
            ctx,
        )

        assert notification.excerpt is not None
        assert message not in notification.excerpt.lines
        assert "Health check attempts" not in " ".join(notification.excerpt.lines)

    def test_a_failures_message_leads_the_excerpt_when_it_says_something_new(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_deploy(
            event(
                DeployEventKind.FAILED,
                error_message="Release 2026 did not pass its health check",
                error_output=EVIDENCE,
            ),
            ctx,
        )

        assert notification.excerpt is not None
        assert notification.excerpt.lines[0] == "Release 2026 did not pass its health check"

    def test_a_node_crash_keeps_its_error_above_systemds_lines(
        self, ctx: NotificationContext
    ) -> None:
        """The real-machine harness: "Error: broken on purpose" was pushed out."""
        evidence = (
            "The application did not answer the health check.\n\n"
            "Last lines of the journal of shop-example-com:\n" + "\n".join(NODE_CRASH)
        )

        notification = compose_deploy(
            event(
                DeployEventKind.ROLLED_BACK,
                error_message="Release 2026 did not pass its health check; release 2025 is active again",
                error_output=evidence,
            ),
            ctx,
        )

        excerpt = notification.excerpt
        assert excerpt is not None
        assert excerpt.lines[0] == NODE_ERROR
        assert excerpt.pinned == 1
        assert excerpt.lines[-1] == NODE_CRASH[-1]
        assert excerpt.lines.count(NODE_ERROR) == 1

    def test_a_unit_crash_keeps_its_error_too(self, ctx: NotificationContext) -> None:
        notification = compose_unit_failure(
            "failed", "shop-example-com", ctx, result="exit-code", journal="\n".join(NODE_CRASH)
        )

        assert notification.excerpt is not None
        assert notification.excerpt.lines[0] == NODE_ERROR

    def test_without_a_journal_the_tools_output_is_the_excerpt(
        self, ctx: NotificationContext
    ) -> None:
        output = (
            "npm ERR! code ELIFECYCLE\nnpm ERR! errno 1\nnpm ERR! shop@1.4.2 build: `next build`"
        )

        notification = compose_deploy(
            event(
                DeployEventKind.FAILED, error_message="npm run build failed", error_output=output
            ),
            ctx,
        )

        assert notification.excerpt is not None
        assert notification.excerpt.label == "What the system reported"
        assert notification.excerpt.lines == (
            "npm run build failed",
            "npm ERR! code ELIFECYCLE",
            "npm ERR! errno 1",
            "npm ERR! shop@1.4.2 build: `next build`",
        )

    def test_the_failure_as_recorded_is_used_when_the_parts_are_missing(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_deploy(
            event(DeployEventKind.FAILED, error="Build failed\n  Details: tsc: error TS2304"), ctx
        )

        assert notification.excerpt is not None
        assert notification.excerpt.lines == ("Build failed", "tsc: error TS2304")

    def test_success_has_no_excerpt(self, ctx: NotificationContext) -> None:
        assert compose_deploy(event(error_output="stale"), ctx).excerpt is None

    def test_a_failed_deploy_suggests_the_diagnosis(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(event(DeployEventKind.FAILED, error_message="x"), ctx)

        assert notification.command is not None
        assert notification.command.value == "noust diagnose shop.example.com"
        assert notification.command.mono

    def test_the_excerpt_repeats_no_fact_or_command(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(
            event(
                DeployEventKind.FAILED,
                error_message="Failed",
                error_output="noust diagnose shop.example.com\nreal cause",
            ),
            ctx,
        )

        assert notification.excerpt is not None
        assert "noust diagnose shop.example.com" not in notification.excerpt.lines

    def test_the_real_exceptions_split_cleanly(self, ctx: NotificationContext) -> None:
        error = RolledBackError(
            "Release 2026 did not pass its health check; release 2025 is active again",
            details=EVIDENCE,
        )

        notification = compose_deploy(
            event(
                DeployEventKind.ROLLED_BACK,
                error_message=error.message,
                error_output=error.details,
            ),
            ctx,
        )

        assert notification.excerpt is not None
        assert notification.summary.startswith("The new version did not answer")
        assert isinstance(error, DeploymentError)


class TestDeployWithoutAnApplication:
    """A job that failed before it knew which application it was for."""

    def test_it_has_no_subject_no_link_no_diagnosis(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(
            event(DeployEventKind.FAILED, domain="", deployment_id=None, error="boom"), ctx
        )

        assert notification.subject == ""
        assert notification.domain is None
        assert notification.links == ()
        assert notification.command is None
        assert notification.excerpt is not None
        assert notification.excerpt.lines == ("boom",)


class TestRestore:
    def test_a_completed_restore_has_its_own_kind(self, ctx: NotificationContext) -> None:
        notification = compose_restore(
            ok=True, domain="shop.example.com", backup_id="20260928-020000", error=None, ctx=ctx
        )

        assert (notification.kind, notification.code) == ("restore_success", "restore.completed")
        assert notification.state is State.OK
        assert facts(notification)["backup"] == "20260928-020000"
        assert [link.url for link in notification.links] == ["https://console.example.com/backups"]

    def test_a_failed_restore_keeps_the_tools_words(self, ctx: NotificationContext) -> None:
        error = NoustError("Restore failed", "tar: Unexpected EOF in archive\ntar: not recoverable")

        notification = compose_restore(
            ok=False, domain="shop.example.com", backup_id=None, error=str(error), ctx=ctx
        )

        assert (notification.kind, notification.code) == ("restore_failed", "restore.failed")
        assert notification.state is State.FAILED
        assert notification.excerpt is not None
        assert notification.excerpt.lines == (
            "tar: Unexpected EOF in archive",
            "tar: not recoverable",
        )

    def test_the_message_that_repeats_the_title_is_dropped(self, ctx: NotificationContext) -> None:
        notification = compose_restore(
            ok=False, domain="a.example.com", backup_id=None, error="Restore failed", ctx=ctx
        )

        assert notification.excerpt is None

    def test_a_restore_without_a_domain_has_no_subject(self, ctx: NotificationContext) -> None:
        notification = compose_restore(ok=True, domain=None, backup_id=None, error=None, ctx=ctx)

        assert notification.subject == ""
        assert notification.domain is None


class TestBackups:
    def test_a_failed_backup(self, ctx: NotificationContext) -> None:
        notification = compose_backup_failed(
            "shop.example.com",
            "Backup of shop.example.com failed\n  Details: tar: No space left on device",
            ctx,
        )

        assert (notification.kind, notification.code) == ("backup_failed", "backup.failed")
        assert notification.state is State.FAILED
        assert notification.excerpt is not None
        assert notification.excerpt.lines == ("tar: No space left on device",)
        assert "Backup of shop.example.com failed" not in notification.excerpt.lines

    def test_an_upload_failure_is_only_a_warning_because_the_local_copy_stays(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_backup_upload_failed(
            "shop.example.com",
            "s3-eu",
            "Failed to upload the backup\n  Details: rclone: 403 Forbidden",
            ctx,
        )

        assert (notification.kind, notification.code) == ("backup_failed", "backup.upload_failed")
        assert notification.state is State.WARNING
        assert facts(notification)["destination"] == "s3-eu"
        assert notification.excerpt is not None
        assert notification.excerpt.lines == ("rclone: 403 Forbidden",)

    def test_a_missing_schedule_says_what_to_run_and_no_version_jargon(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_backup_schedule_missing("shop.example.com", ctx)

        assert (notification.kind, notification.code) == (
            "backup_failed",
            "backup.schedule_missing",
        )
        assert notification.state is State.WARNING
        assert notification.command is not None
        assert notification.command.value == "noust backup schedule update shop.example.com"
        assert "2.1" not in notification.summary

    def test_a_completed_backup_is_the_optional_heartbeat(self, ctx: NotificationContext) -> None:
        notification = compose_backup_completed(
            "shop.example.com",
            "20260929-020000",
            ctx,
            size_bytes=15 * 1024 * 1024,
            destinations=["s3-eu", "nas"],
        )

        assert (notification.kind, notification.code) == ("backup_success", "backup.completed")
        assert notification.state is State.OK
        assert facts(notification)["size"] == "15.0 MB"
        assert facts(notification)["destinations"] == "s3-eu, nas"


class TestCertificates:
    def test_a_certificate_about_to_expire(self, ctx: NotificationContext) -> None:
        notification = compose_certificate(
            "shop.example.com",
            ["shop.example.com", "www.shop.example.com"],
            "2026-10-06",
            7,
            ctx,
        )

        assert (notification.kind, notification.code) == ("cert_expiring", "cert.expiring")
        assert notification.state is State.WARNING
        assert notification.summary == "Expires in 7 days (6 Oct 2026)."
        assert facts(notification)["covers"] == "shop.example.com, www.shop.example.com"
        assert notification.command is not None
        assert notification.command.value == "noust cert renew shop.example.com"

    def test_one_day_is_singular(self, ctx: NotificationContext) -> None:
        notification = compose_certificate("a.example.com", ["a.example.com"], "2026-10-06", 1, ctx)

        assert notification.summary == "Expires in 1 day (6 Oct 2026)."

    def test_today_says_today(self, ctx: NotificationContext) -> None:
        notification = compose_certificate("a.example.com", ["a.example.com"], "2026-10-06", 0, ctx)

        assert notification.summary == "Expires today (6 Oct 2026)."
        assert notification.state is State.WARNING

    def test_an_expired_certificate_is_a_failure_not_a_negative_number(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_certificate(
            "shop.example.com", ["shop.example.com"], "2026-07-11", -80, ctx
        )

        assert (notification.kind, notification.code) == ("cert_expiring", "cert.expired")
        assert notification.state is State.FAILED
        assert notification.title == "Certificate expired"
        assert notification.summary == "Expired 80 days ago (11 Jul 2026)."
        assert "-80" not in notification.summary

    def test_one_day_ago_is_singular(self, ctx: NotificationContext) -> None:
        notification = compose_certificate(
            "a.example.com", ["a.example.com"], "2026-10-05", -1, ctx
        )

        assert notification.summary == "Expired 1 day ago (5 Oct 2026)."

    def test_spanish_dates_and_plurals(self, ctx_es: NotificationContext) -> None:
        soon = compose_certificate("a.example.com", ["a.example.com"], "2026-10-06", 7, ctx_es)
        gone = compose_certificate("a.example.com", ["a.example.com"], "2026-07-11", -80, ctx_es)

        assert soon.summary == "Caduca en 7 días (6 oct 2026)."
        assert gone.summary == "Caducó hace 80 días (11 jul 2026)."

    def test_the_covered_names_are_omitted_when_they_are_only_the_name(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_certificate("a.example.com", ["a.example.com"], "2026-10-06", 7, ctx)

        assert "covers" not in facts(notification)

    def test_an_unparseable_date_is_shown_as_it_is(self, ctx: NotificationContext) -> None:
        notification = compose_certificate("a.example.com", [], "next week", 7, ctx)

        assert notification.summary == "Expires in 7 days (next week)."

    def test_the_link_is_the_domains_page(self, ctx: NotificationContext) -> None:
        notification = compose_certificate("a.example.com", [], "2026-10-06", 7, ctx)

        assert [link.url for link in notification.links] == ["https://console.example.com/domains"]


class TestUnits:
    def test_a_crash_loop_counts_the_restarts_in_words(self, ctx: NotificationContext) -> None:
        notification = compose_unit_failure(
            "crash_loop",
            "shop-example-com",
            ctx,
            result="exit-code",
            exit_status=1,
            restarts=9,
            restarts_grew=3,
            journal=JOURNAL,
        )

        assert (notification.kind, notification.code) == ("unit_failed", "unit.crash_loop")
        assert notification.state is State.FAILED
        assert notification.subject == "shop-example-com"
        assert notification.summary.startswith("systemd restarted it 3 times")
        assert facts(notification)["restarts"] == "9"
        assert notification.command is not None
        assert notification.command.value == "journalctl -u shop-example-com -n 50"

    def test_what_systemd_reported_is_a_fact_when_there_is_no_journal(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_unit_failure(
            "failed", "shop-example-com", ctx, result="exit-code", exit_status=1
        )

        assert facts(notification)["result"] == "exit-code"
        assert facts(notification)["exit_status"] == "1"

    def test_it_is_left_out_when_the_journal_already_says_it(
        self, ctx: NotificationContext
    ) -> None:
        """Design rule D-7: a fact the excerpt carries is not stated twice."""
        notification = compose_unit_failure(
            "failed", "shop-example-com", ctx, result="exit-code", exit_status=1, journal=JOURNAL
        )

        assert "result" not in facts(notification)
        assert "exit_status" not in facts(notification)

    def test_one_restart_is_singular(self, ctx_es: NotificationContext) -> None:
        notification = compose_unit_failure("crash_loop", "u", ctx_es, restarts=2, restarts_grew=1)

        assert "1 vez" in notification.summary

    def test_the_journal_is_the_excerpt(self, ctx: NotificationContext) -> None:
        notification = compose_unit_failure("failed", "shop-example-com", ctx, journal=JOURNAL)

        assert notification.excerpt is not None
        assert notification.excerpt.label == "Last lines of the journal of shop-example-com"

    def test_no_journal_no_excerpt(self, ctx: NotificationContext) -> None:
        assert compose_unit_failure("failed", "u", ctx).excerpt is None

    @pytest.mark.parametrize("kind", ["failed", "crash_loop", "stopped_on_failure"])
    def test_every_kind_is_a_failure_under_the_unit_switch(
        self, ctx: NotificationContext, kind: str
    ) -> None:
        notification = compose_unit_failure(kind, "u", ctx, restarts_grew=2)

        assert notification.kind == "unit_failed"
        assert notification.code == f"unit.{kind}"
        assert notification.state is State.FAILED

    def test_the_unit_is_not_repeated_as_a_fact_when_it_is_the_subject(
        self, ctx: NotificationContext
    ) -> None:
        assert "unit" not in facts(compose_unit_failure("failed", "u-1", ctx))

    def test_with_an_application_the_unit_becomes_a_fact(self, ctx: NotificationContext) -> None:
        notification = compose_unit_failure(
            "failed", "shop-example-com", ctx, domain="shop.example.com"
        )

        assert notification.subject == "shop.example.com"
        assert facts(notification)["unit"] == "shop-example-com"

    def test_recovery_closes_the_alert_under_the_same_switch(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_unit_recovered("u-1", ctx)

        assert (notification.kind, notification.code) == ("unit_failed", "unit.recovered")
        assert notification.state is State.OK
        assert notification.subject == "u-1"


class TestDisk:
    def test_one_precision_everywhere(self, ctx: NotificationContext) -> None:
        notification = compose_disk(
            "/",
            91.7,
            90.0,
            ctx,
            used_bytes=int(18.34 * 1024**3),
            total_bytes=20 * 1024**3,
            free_bytes=int(1.66 * 1024**3),
        )

        assert (notification.kind, notification.code) == ("disk_threshold", "disk.threshold")
        assert notification.state is State.WARNING
        assert notification.subject == "/"
        assert facts(notification)["used"] == "91.7% (18.3 GB / 20.0 GB)"
        assert facts(notification)["free"] == "1.7 GB"
        assert "90%" in notification.summary
        assert "91.7" not in notification.summary and "92" not in notification.title

    def test_without_sizes_only_the_percentage_is_given(self, ctx: NotificationContext) -> None:
        notification = compose_disk("/var", 95.04, 90.0, ctx)

        assert facts(notification)["used"] == "95.0%"
        assert "free" not in facts(notification)

    def test_recovery(self, ctx: NotificationContext) -> None:
        notification = compose_disk_recovered("/", 71.2, ctx)

        assert (notification.kind, notification.code) == ("disk_threshold", "disk.recovered")
        assert notification.state is State.OK
        assert facts(notification)["used"] == "71.2%"

    def test_links_to_the_server_page(self, ctx: NotificationContext) -> None:
        notification = compose_disk("/", 91.0, 90.0, ctx)

        assert [link.url for link in notification.links] == ["https://console.example.com/server"]

    @pytest.mark.parametrize(
        ("size", "text"),
        [
            (0, "0 B"),
            (900, "900 B"),
            (1536, "1.5 KB"),
            (5 * 1024**2, "5.0 MB"),
            (3 * 1024**4, "3072.0 GB"),
        ],
    )
    def test_bytes(self, size: int, text: str) -> None:
        assert format_bytes(size) == text


class TestTestMessage:
    def test_names_the_channel_with_its_own_capitals(self, ctx: NotificationContext) -> None:
        notification = compose_test("telegram", ctx)

        assert notification.kind == "test"
        assert notification.state is State.INFO
        assert notification.subject == "Telegram"
        assert (
            notification.summary
            == "If you can read this, the Telegram channel is configured correctly."
        )
        assert notification.links == ()

    def test_email_is_spelled_in_the_readers_language(self, ctx_es: NotificationContext) -> None:
        notification = compose_test("email", ctx_es)

        assert notification.subject == "Correo electrónico"
        assert notification.title == "Notificación de prueba"

    def test_carries_the_server(self, ctx: NotificationContext) -> None:
        assert facts(compose_test("slack", ctx)) == {"server": "web-1"}


class TestFleet:
    def test_unreachable(self, ctx: NotificationContext) -> None:
        since = datetime(2026, 9, 29, 10, 30, tzinfo=timezone.utc)

        notification = compose_node_unreachable(
            "web-2",
            ctx,
            reason="ssh: connect to host 10.0.0.2 port 22: Connection timed out",
            address="10.0.0.2",
            since=since,
        )

        assert (notification.kind, notification.code) == ("node_unreachable", "node.unreachable")
        assert notification.state is State.FAILED
        assert notification.subject == "web-2"
        assert facts(notification)["address"] == "10.0.0.2"
        assert facts(notification)["since"] == "2026-09-29 10:30 UTC"
        assert notification.excerpt is not None
        assert notification.excerpt.lines == (
            "ssh: connect to host 10.0.0.2 port 22: Connection timed out",
        )
        assert [link.url for link in notification.links] == ["https://console.example.com/fleet"]

    def test_recovered_says_for_how_long(self, ctx: NotificationContext) -> None:
        notification = compose_node_recovered("web-2", ctx, down_for_s=754)

        assert (notification.kind, notification.code) == ("node_recovered", "node.recovered")
        assert notification.state is State.OK
        assert facts(notification)["downtime"] == "12 min 34 s"
        assert [link.url for link in notification.links] == ["https://console.example.com/n/web-2/"]

    def test_a_changed_host_key_shows_both_fingerprints_and_never_calms_down(
        self, ctx: NotificationContext
    ) -> None:
        notification = compose_node_host_key_changed(
            "web-2",
            ctx,
            address="10.0.0.2",
            pinned="SHA256:aaaa",
            presented="SHA256:bbbb",
            command="noust node trust web-2",
        )

        assert (notification.kind, notification.code) == (
            "node_host_key_changed",
            "node.host_key_changed",
        )
        assert notification.state is State.FAILED
        assert facts(notification)["pinned"] == "SHA256:aaaa"
        assert facts(notification)["presented"] == "SHA256:bbbb"
        assert notification.command is not None
        assert notification.command.value == "noust node trust web-2"

    def test_the_central_names_itself_as_the_server(self, ctx: NotificationContext) -> None:
        assert facts(compose_node_recovered("web-2", ctx))["server"] == "web-1"


class TestHostileText:
    def test_nothing_hostile_survives_into_the_model(self, ctx: NotificationContext) -> None:
        notification = compose_deploy(
            event(
                domain="shop\u202e.example.com",
                branch="feat\x1b[31m/x",
                commit_message="fix\nstuff @everyone",
            ),
            ctx,
        )

        assert notification.subject == "shop.example.com"
        assert "\x1b" not in facts(notification)["commit"]
        assert "\n" not in facts(notification)["commit"]
