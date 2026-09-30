# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust.core.audit.record`` and what every event is made of.

Who acted (:class:`Actor`, including the operating system identity behind a
command), what the event may carry (never a secret), the ``noust.audit``
logger that used to go nowhere (finding H2), and the flag that says the trail
is broken.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from noust.core import audit
from noust.core.audit import Actor, bind, current_correlation_id, get_log, health, record
from noust.core.audit import actor as actor_module
from noust.core.audit.bridge import parse_line
from noust.core.audit.sanitize import clean_details, statement_digest
from noust.core.audit.settings import (
    DOCUMENTATION_ENTERPRISE_ID,
    MIN_RETENTION_DAYS,
    load_settings,
)


class FakeConfig:
    """Answers ``get`` from a nested dict, like :class:`~noust.core.config.Config`."""

    def __init__(self, tree: dict) -> None:
        self.tree = tree

    def get(self, key: str, default=None):
        value = self.tree
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value


def newest() -> dict:
    return get_log().read(limit=1)[0]


class TestActor:
    @pytest.mark.parametrize(
        ("actor", "label"),
        [
            (Actor(kind="master"), "master"),
            (Actor(kind="anonymous", source="1.2.3.4"), "anonymous"),
            (Actor(kind="token", name="ci"), "token:ci"),
            (Actor(kind="cli", name="ana"), "cli:ana"),
            (Actor(kind="system", name="retention"), "system:retention"),
            (Actor(kind="user", id="17", name="maria"), "maria"),
            (Actor(kind="fleet", name="maria", via="fleet-nas"), "fleet-nas on behalf of maria"),
        ],
    )
    def test_labels_are_the_ones_the_activity_page_filters_by(
        self, actor: Actor, label: str
    ) -> None:
        assert actor.label == label

    @pytest.mark.parametrize(
        "label",
        [
            "master",
            "anonymous",
            "token:ci",
            "cli:root",
            "0123456789ab",
            "fleet-nas on behalf of token:ci",
        ],
    )
    def test_a_3_0_label_reads_back_as_itself(self, label: str) -> None:
        assert Actor.from_label(label).label == label

    def test_a_3_0_label_names_the_right_kind(self) -> None:
        assert Actor.from_label("token:ci").kind == "token"
        assert Actor.from_label("0123456789ab").kind == "user"
        behalf = Actor.from_label("fleet-nas on behalf of maria")
        assert (behalf.kind, behalf.name, behalf.via) == ("fleet", "maria", "fleet-nas")

    def test_to_dict_and_back(self) -> None:
        actor = Actor(
            kind="user", id="17", name="maria", role="admin", via="web", source="10.0.0.1"
        )
        assert Actor.from_dict(actor.to_dict()) == actor


class TestOperatingSystemIdentity:
    def test_the_login_uid_names_who_ran_sudo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The kernel keeps the login user through sudo: that is who is on record."""
        loginuid = tmp_path / "loginuid"
        loginuid.write_text("1000")
        monkeypatch.setattr(actor_module, "LOGINUID_PATH", loginuid)
        monkeypatch.setattr(actor_module, "_user_name", lambda uid: "ana" if uid == 1000 else "?")

        actor = audit.cli_actor(
            {"SUDO_USER": "ana", "SSH_CONNECTION": "10.9.8.7 51000 10.0.0.2 22"}
        )

        assert (actor.kind, actor.id, actor.name) == ("cli", "1000", "ana")
        assert actor.via == "sudo"
        assert actor.source == "10.9.8.7"

    def test_sudo_user_when_the_kernel_has_no_login_uid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loginuid = tmp_path / "loginuid"
        loginuid.write_text("4294967295")
        monkeypatch.setattr(actor_module, "LOGINUID_PATH", loginuid)

        actor = audit.cli_actor({"SUDO_USER": "ana", "SUDO_UID": "1000"})

        assert (actor.name, actor.id, actor.via) == ("ana", "1000", "sudo")

    def test_a_systemd_unit_with_no_login_is_the_system(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(actor_module, "LOGINUID_PATH", tmp_path / "missing")
        monkeypatch.setattr(actor_module, "_tty", lambda: None)

        actor = audit.cli_actor({"INVOCATION_ID": "0123456789abcdef0123"})

        assert (actor.kind, actor.name) == ("system", "systemd")


class TestRecord:
    def test_record_writes_one_chained_event(self) -> None:
        record(
            "apps.delete",
            actor=Actor(kind="user", name="maria", role="admin", source="10.0.0.5"),
            target="app:shop.example.com",
            outcome="denied",
            details={"reason": "retired"},
            correlation_id="abc",
        )
        entry = newest()
        assert entry["action"] == "apps.delete"
        assert entry["result"] == "denied"
        assert entry["sev"] == 4
        assert entry["who"]["role"] == "admin"
        assert entry["ip"] == "10.0.0.5"
        assert entry["corr"] == "abc"
        assert entry["details"] == {"reason": "retired"}
        assert get_log().verify().ok

    def test_without_an_actor_the_bound_one_is_used(self) -> None:
        with bind(actor=Actor(kind="cli", name="ana")) as correlation:
            assert current_correlation_id() == correlation
            record("apps.restart")
        entry = newest()
        assert entry["actor"] == "cli:ana"
        assert entry["corr"] == correlation
        assert current_correlation_id() is None

    def test_without_anything_bound_it_is_the_system(self) -> None:
        record("retention.prune")
        assert newest()["who"] == {"kind": "system"}

    def test_secrets_never_reach_the_log(self) -> None:
        record(
            "config.change",
            details={
                "key": "notifications.channels.slack.webhook_url",
                "password": "hunter2-hunter2",
                "nested": {"api_key": "sk-live-abcdef123456"},
                "url": "postgres://app:s3cr3t-pass@db:5432/app",
            },
        )
        raw = get_log().path.read_text()
        assert "hunter2" not in raw
        assert "sk-live" not in raw
        assert "s3cr3t-pass" not in raw
        details = newest()["details"]
        assert details["key"] == "notifications.channels.slack.webhook_url"
        assert details["password"] == "***"

    def test_details_are_bounded(self) -> None:
        cleaned = clean_details({"blob": "x" * 10_000, "items": list(range(500))})
        assert len(cleaned["blob"]) < 2100
        assert len(cleaned["items"]) == 101

    def test_a_statement_is_recorded_as_a_digest(self) -> None:
        digest = statement_digest("ALTER USER app WITH PASSWORD 'hunter2-hunter2'")
        assert digest["length"] == 46
        assert len(digest["sha256"]) == 64
        assert "hunter2" not in digest["head"]
        assert digest["head"].startswith("ALTER USER app WITH PASSWORD")


class TestNoustAuditLogger:
    """Finding H2: ``logging.getLogger("noust.audit")`` had no handler and no level."""

    def test_a_line_becomes_a_catalog_event(self) -> None:
        logging.getLogger("noust.audit").info(
            "drop_database engine=%s database=%s session=%s", "postgresql", "shop", "master"
        )
        entry = newest()
        assert entry["action"] == "db.drop"
        assert entry["actor"] == "master"
        assert entry["resource"] == "shop"
        assert entry["details"] == {"engine": "postgresql", "database": "shop"}

    def test_a_statement_is_digested_and_a_read_is_a_sensitive_read(self) -> None:
        logging.getLogger("noust.audit").info(
            "query engine=%s database=%s mode=%s session=%s statement=%r",
            "postgresql",
            "shop",
            "read",
            "token:ci",
            "SELECT * FROM users WHERE token = 'abc'",
        )
        entry = newest()
        assert entry["action"] == "db.query.read"
        assert entry["sensitive"] is True
        assert entry["details"]["statement"]["length"] == 39
        assert "statement=" not in get_log().path.read_text()

    def test_an_unmapped_line_is_kept_whole(self) -> None:
        logging.getLogger("noust.audit").info("frobnicate thing=1 session=%s", "0123456789ab")
        entry = newest()
        assert entry["action"] == "audit.legacy"
        assert entry["details"]["line"] == "frobnicate thing=1 session=0123456789ab"
        assert entry["actor"] == "0123456789ab"

    def test_pairs_with_quoted_values(self) -> None:
        verb, pairs = parse_line("query a=1 statement='SELECT \\'x\\'' b=two")
        assert verb == "query"
        assert pairs == {"a": "1", "statement": "SELECT 'x'", "b": "two"}

    def test_other_listeners_still_see_the_line(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="noust.audit"):
            logging.getLogger("noust.audit").info("delete_cron_job name=x session=master")
        assert "delete_cron_job name=x" in caplog.text
        assert newest()["action"] == "cron.delete"


class TestSettings:
    def test_defaults(self) -> None:
        settings = load_settings(FakeConfig({}))
        assert settings.retention_days == 365
        assert settings.enterprise_id == DOCUMENTATION_ENTERPRISE_ID
        assert settings.host_activity == "mutations"
        assert not settings.problems

    def test_a_bad_value_falls_back_and_says_so(self) -> None:
        settings = load_settings(
            FakeConfig(
                {"audit": {"retention_days": 7, "host_activity": "verbose", "rotate_mb": "x"}}
            )
        )
        assert settings.retention_days == MIN_RETENTION_DAYS
        assert settings.host_activity == "mutations"
        assert settings.rotate_mb == 64
        assert len(settings.problems) == 3

    def test_syslog_destinations_are_parsed(self) -> None:
        settings = load_settings(
            FakeConfig(
                {
                    "audit": {
                        "enterprise_id": 99999,
                        "syslog": [
                            {
                                "transport": "tls",
                                "address": "siem.example:6514",
                                "ca": "/etc/ca.pem",
                            },
                            {"transport": "udp", "address": "[::1]"},
                            {"transport": "unix"},
                            {"transport": "carrier-pigeon", "address": "x"},
                        ],
                    }
                }
            )
        )
        tls, udp, unix = settings.syslog
        assert (tls.host, tls.port, tls.ca) == ("siem.example", 6514, "/etc/ca.pem")
        assert (udp.host, udp.port) == ("::1", 514)
        assert unix.path == "/dev/log"
        assert tls.sink_id == "syslog-tls-siem.example_6514"
        assert any(
            "carrier-pigeon" not in problem and "transport" in problem
            for problem in settings.problems
        )

    def test_the_documentation_enterprise_number_is_flagged_when_shipping(self) -> None:
        settings = load_settings(
            FakeConfig({"audit": {"syslog": [{"transport": "udp", "address": "siem:514"}]}})
        )
        assert any("Private Enterprise Number" in problem for problem in settings.problems)


class TestHealth:
    def test_a_working_trail_is_ok(self) -> None:
        record("apps.update", actor=Actor.system())
        report = health()
        assert report.status == "ok", report.problems
        assert report.total_bytes > 0

    def test_a_readable_key_is_an_error(self) -> None:
        record("apps.update", actor=Actor.system())
        get_log().key_path.chmod(0o644)
        report = health()
        assert report.status == "error"
        assert "chmod 600" in report.problems[0]


class TestConsolePrincipals:
    """The console builds actors from its principals (noust.web.permissions.principal)."""

    def test_an_account_is_a_user(self) -> None:
        actor = Actor(kind="account", id="17", name="maria", role="admin")  # type: ignore[arg-type]
        assert actor.kind == "user"
        assert actor.label == "maria"

    def test_a_token_named_with_its_prefix_is_not_prefixed_twice(self) -> None:
        assert Actor(kind="token", id="ci", name="token:ci").label == "token:ci"

    def test_a_fleet_channel_names_the_token(self) -> None:
        actor = Actor(kind="fleet", id="fleet-nas", name="token:ci", via="fleet:fleet-nas")
        assert actor.label == "fleet-nas on behalf of token:ci"


class TestRequestCorrelation:
    def test_a_plain_request_id_header_is_kept(self) -> None:
        from noust.core.audit.context import request_correlation_id

        scope = {"headers": [(b"x-noust-request-id", b"central-7f3c19aa")]}
        assert request_correlation_id(scope) == "central-7f3c19aa"

    def test_anything_else_gets_a_fresh_id(self) -> None:
        from noust.core.audit.context import request_correlation_id

        forged = {"headers": [(b"x-noust-request-id", b"x\nfake line")]}
        assert request_correlation_id(forged) != "x\nfake line"
        assert len(request_correlation_id({"headers": []})) == 16
