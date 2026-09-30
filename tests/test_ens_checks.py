# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust ens check``: facts in, findings out, each mapped to its RD 311/2022 measure.

The judging (:func:`evaluate`) is tested on facts built by hand, one check at
a time; the gathering (:func:`gather`) on a real store with the console's
tokens, the hardening checks and the console's exposure replaced, and with one
area failing, which must leave the rest of the report whole.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.ens import checks
from noust.core.ens.checks import ConsoleExposure, Facts, Sources, evaluate, gather, run_check
from noust.core.net import ALL_INTERFACES
from noust.core.store import App, NoustStore

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def by_id(findings: list[checks.Finding]) -> dict[str, checks.Finding]:
    return {finding.id: finding for finding in findings}


def account(name: str, role: str, **extra: Any) -> dict[str, Any]:
    return {
        "username": name,
        "role": role,
        "status": "active",
        "mfa_enabled": True,
        "last_login_at": (NOW - timedelta(days=1)).timestamp(),
        "created_at": (NOW - timedelta(days=200)).timestamp(),
        **extra,
    }


def good_facts() -> Facts:
    """Facts of a server that passes every check."""
    return Facts(
        profile="ens-medium",
        now=NOW,
        policy={
            "idle_minutes": 15,
            "absolute_hours": 8,
            "lockout_threshold": 5,
            "lockout_minutes": 15,
            "password_min_length": 14,
            "token_max_days": 90,
        },
        approvals_enabled=True,
        cli_reason_required=True,
        accounts=[account("sofia", "security"), account("admin1", "admin")],
        tokens=[
            {
                "name": "ci",
                "scope": "deploy",
                "created_at": (NOW - timedelta(days=10)).timestamp(),
                "expires_at": (NOW + timedelta(days=80)).timestamp(),
                "owner_account_id": 2,
                "revoked_at": None,
            }
        ],
        master_token_exists=True,
        audit_verify={"ok": True, "checked": 120, "last_seq": 120, "last_mac": "ab"},
        audit_health={
            "status": "ok",
            "problems": [],
            "sinks": [{"sink_id": "syslog-0"}],
            "failing": False,
        },
        audit_retention_days=365,
        audit_journald=True,
        audit_syslog=1,
        audit_events={
            "last_access_review": (NOW - timedelta(days=20)).isoformat(),
            "last_audit_review": (NOW - timedelta(days=2)).isoformat(),
            "break_glass_30d": 0,
            "lockouts_30d": 0,
        },
        hardening=[
            {"id": "ssh.password_auth", "status": "pass", "reason": "no"},
            {"id": "fw.inactive", "status": "pass", "reason": "ufw on"},
            {"id": "upd.security_pending", "status": "pass", "reason": "none"},
            {"id": "time.unsynced", "status": "pass", "reason": "synced"},
            {"id": "f2b.missing", "status": "pass", "reason": "installed"},
            {"id": "disk.full", "status": "pass", "reason": "fine"},
        ],
        backups=[
            {
                "domain": "shop.example.com",
                "newest": (NOW - timedelta(hours=3)).isoformat(),
                "verified": (NOW - timedelta(days=2)).isoformat(),
                "count": 7,
            }
        ],
        destinations=[{"name": "nas", "backend": "sftp", "encrypted": True}],
        schedules=[{"domain": "shop.example.com", "destinations": ["nas"]}],
        backup_encryption_required=True,
        exposure=ConsoleExposure(
            source="unit",
            host=ALL_INTERFACES,
            port=8443,
            certificate="/etc/pki/panel.pem",
            allow_ip=("10.20.0.0/24",),
        ),
        certificate={
            "path": "/etc/pki/panel.pem",
            "expires": (NOW + timedelta(days=300)).isoformat(),
            "self_signed": False,
            "fingerprint": "AA:BB",
        },
        inventory=[{"domain": "shop.example.com", "complete": True}],
        allowed_sources=["github.com/acme"],
        totp_plaintext=0,
        secrets_sealed=False,
    )


class TestAPassingServer:
    def test_every_check_passes_or_does_not_apply(self) -> None:
        findings = evaluate(good_facts())

        assert {f.status for f in findings} <= {"ok", "n/a"}, [
            (f.id, f.status, f.summary) for f in findings if f.status not in ("ok", "n/a")
        ]

    def test_every_finding_names_a_measure(self) -> None:
        for finding in evaluate(good_facts()):
            assert finding.measures, finding.id
            assert all("." in measure for measure in finding.measures)

    def test_ids_are_unique_and_stable(self) -> None:
        ids = [finding.id for finding in evaluate(good_facts())]

        assert len(ids) == len(set(ids))
        assert {"ENS-ACC-02", "ENS-LOG-01", "ENS-BAK-01", "ENS-CRY-01", "ENS-FLT-01"} <= set(ids)


class TestFailures:
    def test_the_standard_profile_fails_the_profile_check(self) -> None:
        facts = good_facts()
        facts.profile = "standard"

        finding = by_id(evaluate(facts))["ENS-PRF-01"]

        assert finding.status == "fail" and "ens-medium" in finding.remediation

    def test_an_account_without_mfa_fails(self) -> None:
        facts = good_facts()
        facts.accounts = [*facts.accounts, account("juan", "operator", mfa_enabled=False)]

        finding = by_id(evaluate(facts))["ENS-ACC-02"]

        assert finding.status == "fail" and "juan" in " ".join(finding.evidence)

    def test_incompatible_roles_without_an_exception_fail(self) -> None:
        facts = good_facts()
        facts.sod_conflicts = [
            {
                "person_ref": "maria@example.com",
                "accounts": [
                    {"username": "maria", "role": "admin"},
                    {"username": "maria.sec", "role": "security"},
                ],
                "exception": None,
            }
        ]

        assert by_id(evaluate(facts))["ENS-ACC-01"].status == "fail"

    def test_a_token_without_expiry_or_owner_fails(self) -> None:
        facts = good_facts()
        facts.tokens = [
            {
                "name": "old",
                "scope": "admin",
                "created_at": 0,
                "expires_at": None,
                "owner_account_id": None,
                "revoked_at": None,
            }
        ]

        finding = by_id(evaluate(facts))["ENS-ACC-04"]

        assert finding.status == "fail"
        assert "old: never expires" in finding.evidence
        assert "old: belongs to no account" in finding.evidence

    def test_the_fleet_token_is_not_a_finding(self) -> None:
        facts = good_facts()
        facts.tokens = [
            {"name": "central", "scope": "fleet", "expires_at": None, "revoked_at": None}
        ]

        assert by_id(evaluate(facts))["ENS-ACC-04"].status == "n/a"

    def test_no_access_review_on_record_fails_and_an_old_one_warns(self) -> None:
        facts = good_facts()
        facts.audit_events["last_access_review"] = None
        assert by_id(evaluate(facts))["ENS-ACC-05"].status == "fail"

        facts.audit_events["last_access_review"] = (NOW - timedelta(days=120)).isoformat()
        assert by_id(evaluate(facts))["ENS-ACC-05"].status == "warning"

    def test_a_broken_chain_fails(self) -> None:
        facts = good_facts()
        facts.audit_verify = {"ok": False, "broken": {"file": "a", "line": 3, "reason": "mac"}}

        assert by_id(evaluate(facts))["ENS-LOG-01"].status == "fail"

    def test_an_audit_log_kept_only_here_fails(self) -> None:
        facts = good_facts()
        facts.audit_syslog, facts.audit_journald = 0, False

        assert by_id(evaluate(facts))["ENS-LOG-02"].status == "fail"

    def test_a_degraded_destination_fails(self) -> None:
        facts = good_facts()
        facts.audit_health = {
            **facts.audit_health,
            "sinks": [{"sink_id": "syslog-0", "degraded": True}],
        }

        assert by_id(evaluate(facts))["ENS-LOG-02"].status == "fail"

    def test_a_self_signed_certificate_fails(self) -> None:
        facts = good_facts()
        facts.certificate = {**facts.certificate, "self_signed": True}

        assert by_id(evaluate(facts))["ENS-CRY-01"].status == "fail"

    def test_a_certificate_close_to_expiry_warns(self) -> None:
        facts = good_facts()
        facts.certificate = {**facts.certificate, "expires": (NOW + timedelta(days=10)).isoformat()}

        assert by_id(evaluate(facts))["ENS-CRY-01"].status == "warning"

    def test_cleartext_beyond_loopback_fails(self) -> None:
        facts = good_facts()
        facts.exposure = ConsoleExposure(source="unit", host=ALL_INTERFACES, insecure_http=True)

        assert by_id(evaluate(facts))["ENS-CRY-02"].status == "fail"

    def test_a_console_open_to_everyone_fails_and_the_private_default_warns(self) -> None:
        facts = good_facts()
        facts.exposure = ConsoleExposure(source="central", host=ALL_INTERFACES, certificate="x")
        assert by_id(evaluate(facts))["ENS-NET-01"].status == "fail"

        from noust.central.setup import DEFAULT_ALLOWLIST

        facts.exposure.allow_ip = DEFAULT_ALLOWLIST
        assert by_id(evaluate(facts))["ENS-NET-01"].status == "warning"

    def test_hardening_findings_land_in_their_ens_check(self) -> None:
        facts = good_facts()
        facts.hardening = [
            {"id": "upd.security_pending", "status": "fail", "reason": "12 security updates"},
            {"id": "upd.reboot_required", "status": "warn", "reason": "reboot needed"},
            {"id": "ssh.password_auth", "status": "warn", "reason": "passwords accepted"},
            {"id": "disk.full", "status": "warn", "reason": "/ at 95%"},
        ]

        found = by_id(evaluate(facts))

        assert found["ENS-UPD-01"].status == "fail"
        assert "upd.reboot_required (warn): reboot needed" in found["ENS-UPD-01"].evidence
        assert found["ENS-NET-02"].status == "warning"
        assert found["ENS-HRD-01"].status == "warning"
        assert "disk.full" in " ".join(found["ENS-HRD-01"].evidence)

    def test_an_accepted_risk_is_listed_with_its_end_not_failed(self) -> None:
        facts = good_facts()
        facts.hardening = [
            {
                "id": "fw.inactive",
                "status": "accepted",
                "reason": "no firewall",
                "accepted": {
                    "expires_at": "2026-12-31T00:00:00+00:00",
                    "accepted_by": "sofia",
                    "reason": "provider firewall",
                },
            }
        ]

        finding = by_id(evaluate(facts))["ENS-NET-03"]

        assert finding.status == "ok"
        assert (
            "fw.inactive: accepted until 2026-12-31 by sofia: provider firewall" in finding.evidence
        )

    def test_an_old_or_unverified_backup_warns_and_a_missing_one_fails(self) -> None:
        facts = good_facts()
        facts.backups = [
            {
                "domain": "shop.example.com",
                "newest": (NOW - timedelta(days=3)).isoformat(),
                "verified": None,
            },
        ]
        assert by_id(evaluate(facts))["ENS-BAK-01"].status == "warning"

        facts.backups.append({"domain": "blog.example.com", "newest": None, "verified": None})
        assert by_id(evaluate(facts))["ENS-BAK-01"].status == "fail"

    def test_an_unencrypted_destination_fails(self) -> None:
        facts = good_facts()
        facts.destinations.append({"name": "s3", "backend": "s3", "encrypted": False})

        assert by_id(evaluate(facts))["ENS-BAK-02"].status == "fail"

    def test_builds_as_root_and_root_tunnels_warn(self) -> None:
        facts = good_facts()
        facts.sandbox_warnings = {"shop.example.com": "shop.example.com still builds as root"}
        facts.nodes = [
            {"name": "web-1", "ssh_user": "root", "access_level": "admin", "host_access": False}
        ]

        found = by_id(evaluate(facts))

        assert found["ENS-BLD-01"].status == "warning"
        assert found["ENS-FLT-01"].status == "warning"

    def test_a_forgotten_lockdown_warns(self) -> None:
        facts = good_facts()
        facts.lockdown = {"since": "2026-09-29", "by": "cli:root", "reason": "INC-1"}

        assert by_id(evaluate(facts))["ENS-INC-01"].status == "warning"

    def test_an_area_that_could_not_be_read_is_a_warning_with_the_error(self) -> None:
        facts = good_facts()
        facts.accounts = None
        facts.errors["accounts"] = "OperationalError: database is locked"

        finding = by_id(evaluate(facts))["ENS-ACC-02"]

        assert finding.status == "warning"
        assert finding.evidence == ("OperationalError: database is locked",)


class TestTheVerdict:
    def test_exit_codes_follow_the_worst_finding(self) -> None:
        def result(*statuses: str) -> checks.ComplianceCheck:
            return checks.ComplianceCheck(
                profile="ens-medium",
                checked_at="x",
                host="h",
                version="v",
                findings=[
                    checks.Finding(f"X-{i}", "t", s, ("op.exp.2",), "s")
                    for i, s in enumerate(statuses)
                ],
            )

        assert result("ok", "n/a").exit_code == 0
        assert result("ok", "warning").exit_code == 1
        assert result("warning", "fail").exit_code == 2
        assert result("fail").to_dict()["verdict"] == "fail"


class FakeTokens:
    def list_api_tokens(self) -> list[dict[str, Any]]:
        return []

    def current_master_generation(self) -> str | None:
        return "g1"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


class TestGathering:
    def sources(self, store: NoustStore, **overrides: Any) -> Sources:
        values: dict[str, Any] = {
            "store": store,
            "token_manager": FakeTokens(),
            "exposure": lambda: ConsoleExposure(source="unit", host="127.0.0.1"),
            "hardening": lambda: [{"id": "fw.inactive", "status": "fail", "reason": "off"}],
            "clock": lambda: NOW,
        }
        values.update(overrides)
        return Sources(**values)

    def test_a_real_store_is_read_area_by_area(self, store) -> None:
        store.create_app(
            App(domain="shop.example.com", app_type="nodejs", source="x", app_path="/x")
        )

        result, facts = run_check(self.sources(store))

        assert facts.accounts == []
        assert facts.inventory and facts.inventory[0]["domain"] == "shop.example.com"
        assert facts.backups == [
            {"domain": "shop.example.com", "newest": None, "verified": None, "count": 0}
        ]
        assert facts.master_token_exists is True
        assert result.to_dict()["verdict"] == "fail"
        assert by_id(result.findings)["ENS-NET-03"].status == "fail"
        assert "mfa_percent" in result.indicators

    def test_one_failing_area_leaves_the_others(self, store) -> None:
        def broken() -> list[dict[str, Any]]:
            raise OSError("sshd -T: permission denied")

        result, facts = run_check(self.sources(store, hardening=broken))

        assert "hardening" in facts.errors
        assert by_id(result.findings)["ENS-NET-02"].status == "warning"
        assert by_id(result.findings)["ENS-INV-01"].status == "n/a"

    def test_the_certificate_is_read_from_the_file(self, store, tmp_path) -> None:
        pem = tmp_path / "self.pem"
        pem.write_text(SELF_SIGNED_PEM)

        facts = gather(
            self.sources(
                store,
                exposure=lambda: ConsoleExposure(
                    source="unit", host=ALL_INTERFACES, certificate=str(pem)
                ),
            )
        )

        assert facts.certificate is not None
        assert facts.certificate["self_signed"] is True
        assert facts.certificate["expires"]


#: A self-signed ECDSA P-256 certificate for "test", made with openssl once.
SELF_SIGNED_PEM = """-----BEGIN CERTIFICATE-----
MIIBczCCARmgAwIBAgIUDnuj/f1m+pr9jjAwulY4E+KtJWkwCgYIKoZIzj0EAwIw
DzENMAsGA1UEAwwEdGVzdDAeFw0yNjA5MzAwMTAxNDNaFw0zNjA5MjcwMTAxNDNa
MA8xDTALBgNVBAMMBHRlc3QwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAQ5W2hm
JHDkojZd9okEXTKFjXg++rXERC5av3FeTDNcyw/PQF0P2ovnilFKEGN66TEM/o/A
qK/0smy/57ULjtQGo1MwUTAdBgNVHQ4EFgQUKqy1WH053/+Zhu4ddUYEgfZhwyIw
HwYDVR0jBBgwFoAUKqy1WH053/+Zhu4ddUYEgfZhwyIwDwYDVR0TAQH/BAUwAwEB
/zAKBggqhkjOPQQDAgNIADBFAiBGWBKvr5CkXXn8Ub74/bU4TNye7hkCyzhWs+DU
o901ZgIhAIbCaeIcxO/I+oBmU2BmfxA5aKj/8hbuNNKeNzeEqwY5
-----END CERTIFICATE-----
"""
