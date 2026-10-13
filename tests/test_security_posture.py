# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``noust health`` says about the server's own security posture (checklist
L-04, L-25, L-43): the things that are not broken but that an operator who read
the security model would want to know about.

Each finding names the command that fixes it. None of them makes the verdict an
error: a server whose backups leave unencrypted is exposed, not down.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import sealing
from noust.core.config import Config
from noust.core.store import BackupDestinationRecord, NodeRecord, NoustStore, get_store
from noust.core.update_checker import UpdateChecker, VersionCheck
from noust.managers import security_posture
from noust.managers.health import collect_health_report


@pytest.fixture(autouse=True)
def _fresh(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    NoustStore.reset_instance()
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    monkeypatch.setattr(UpdateChecker, "CACHE_FILE", sandbox / "version_check.json")
    monkeypatch.setattr(sealing, "is_sealed", lambda root: False)
    yield
    NoustStore.reset_instance()
    Config.reset_instance()


def destination(name: str, backend: str, *, encrypted: bool) -> None:
    get_store().save_backup_destination(
        BackupDestinationRecord(
            name=name, backend=backend, settings={"path": "b"}, encrypted=encrypted
        )
    )


def node(name: str) -> None:
    get_store().save_node(
        NodeRecord(
            name=name,
            ssh_host=f"{name}.example.com",
            ssh_port=22,
            ssh_user="noust-tunnel",
            host_key=f"{name}.example.com ssh-ed25519 AAAA",
            console_port=8443,
        )
    )


def codes() -> list[str]:
    return [finding.code for finding in security_posture.collect()]


class TestBackupsLeaveEncrypted:
    def test_a_clean_server_has_nothing_to_say(self) -> None:
        assert security_posture.collect() == []

    def test_a_remote_destination_in_clear_is_a_finding_that_names_the_fix(self) -> None:
        destination("nas", "sftp", encrypted=False)
        destination("cloud", "s3", encrypted=False)

        [finding] = security_posture.collect()

        assert finding.code == "backups_unencrypted"
        assert "nas" in finding.summary and "cloud" in finding.summary
        assert finding.fix == "noust backup destination update cloud --encrypt"

    def test_an_encrypted_one_and_a_local_one_are_not(self) -> None:
        destination("nas", "sftp", encrypted=True)
        destination("usb", "local", encrypted=False)

        assert codes() == []


class TestTheCentralsKeys:
    def test_a_central_with_servers_and_unsealed_secrets_is_a_finding(self) -> None:
        node("alpha")

        [finding] = security_posture.collect()

        assert finding.code == "central_unsealed"
        assert "1 server" in finding.summary
        assert finding.fix == "noust central seal"

    def test_a_sealed_central_is_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        node("alpha")
        monkeypatch.setattr(sealing, "is_sealed", lambda root: True)

        assert codes() == []

    def test_a_server_that_manages_nobody_has_no_keys_to_seal(self) -> None:
        assert "central_unsealed" not in codes()


class TestASecurityReleaseIsOut:
    def write(self, **fields: object) -> None:
        from noust import __version__
        from noust.core import update_checker

        check = VersionCheck(
            current=__version__,
            location=update_checker._location(),
            method="pip",
            installable="99.0.0",
            published="99.0.0",
            checked_at=time.time() - 7 * 86400,
            **fields,  # type: ignore[arg-type]
        )
        UpdateChecker._write_cache(check.to_cache())

    def test_the_last_check_is_read_however_old_and_without_asking_the_network(self) -> None:
        self.write(security="98.0.0", advisory="GHSA-abcd-1234-wxyz")

        [finding] = security_posture.collect()

        assert finding.code == "security_update"
        assert "98.0.0" in finding.summary
        assert "GHSA-abcd-1234-wxyz" in finding.summary
        assert finding.fix == UpdateChecker.last_known().update_command  # type: ignore[union-attr]

    def test_a_check_that_found_none_says_nothing(self) -> None:
        self.write()

        assert codes() == []

    def test_no_check_yet_says_nothing(self) -> None:
        assert UpdateChecker.last_known() is None
        assert codes() == []

    def test_turning_the_check_off_silences_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.write(security="98.0.0")
        Config().set("updates.check", False)

        assert codes() == []


@pytest.fixture
def quiet_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every check of the report but the posture answers ok, without looking at the machine."""
    from noust.core import audit
    from noust.core.audit import AuditHealth
    from noust.deployers.helpers import sandbox as build_sandbox
    from noust.managers import health

    monkeypatch.setattr(build_sandbox, "sandbox_warnings", lambda store=None: {})
    monkeypatch.setattr(audit, "health", lambda log=None: AuditHealth(status="ok"))
    for name in (
        "_check_disk_space",
        "_check_applications",
        "_check_certificates",
        "_check_memory",
    ):
        monkeypatch.setattr(health, name, lambda *a, **k: health.HealthCheck("x", "y", "ok"))
    monkeypatch.setattr(
        health,
        "_check_web_servers",
        lambda *a, **k: (health.HealthCheck("n", "y", "ok"), health.HealthCheck("a", "y", "ok")),
    )
    monkeypatch.setattr(health, "_check_updates", lambda: None)


@pytest.mark.usefixtures("quiet_machine")
class TestInTheHealthReport:
    def test_findings_are_warnings_never_issues(self) -> None:
        destination("nas", "sftp", encrypted=False)

        report = collect_health_report(hardening="off")

        assert report.security is not None and report.security.status == "warning"
        assert any("noust backup destination update nas --encrypt" in w for w in report.warnings)
        assert report.issues == []
        assert report.security in report.checks
        assert report.verdict == "warning"

    def test_a_clean_server_reports_none_and_adds_no_warning(self) -> None:
        report = collect_health_report(hardening="off")

        assert report.security is not None and report.security.status == "ok"
        assert not any("Security posture" in w for w in report.warnings)

    def test_a_check_that_cannot_run_is_a_warning_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken() -> list[security_posture.Finding]:
            from noust.core.exceptions import NoustError

            raise NoustError("the store is locked")

        monkeypatch.setattr(security_posture, "collect", broken)

        report = collect_health_report(hardening="off")

        assert report.security is not None
        assert "Could not check" in report.security.value
        assert report.issues == []


class TestTheCentralStatusAdvice:
    def test_a_central_with_servers_and_no_seal_is_told_how(self) -> None:
        from noust.cli.commands.central import seal_advice

        advice = seal_advice({"sealed": False, "nodes": 2})

        assert advice is not None
        assert "2 servers" in advice and "noust central seal" in advice

    @pytest.mark.parametrize(
        "status", [{"sealed": True, "nodes": 2}, {"sealed": False, "nodes": 0}, {}]
    )
    def test_nothing_to_advise_when_sealed_or_when_there_are_no_keys(
        self, status: dict[str, object]
    ) -> None:
        from noust.cli.commands.central import seal_advice

        assert seal_advice(status) is None
