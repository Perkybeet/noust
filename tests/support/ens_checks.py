# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_ens_checks.py`` that other tests build on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from noust.core.ens.checks import ConsoleExposure, Facts
from noust.core.net import ALL_INTERFACES

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


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
