# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_replication_token.py`` that other tests build on."""

from __future__ import annotations

from typing import Any

from noust.managers.database.replication_token import REQUIRED_SETTINGS, ReplicaToken

PASSWORD = "aB3" * 12
CA = (
    "-----BEGIN CERTIFICATE-----\n"
    + "MIIBszCCAVmgAwIBAgIUQ2xhdWRlVGVzdENlcnRpZmljYXRlMDAwMDEwCgYIKoZI\n" * 2
    + "-----END CERTIFICATE-----\n"
)


def token(**overrides: Any) -> ReplicaToken:
    """
    Build a code with sensible values.

    Args:
        **overrides: Fields to change.

    Returns:
        The code.
    """
    fields: dict[str, Any] = {
        "name": "reports",
        "host": "db.example.com",
        "port": 5432,
        "role": "noust_repl_reports",
        "password": PASSWORD,
        "slot": "noust_reports",
        "major": 16,
        "size": 1024,
        "requires": dict.fromkeys(REQUIRED_SETTINGS, 8),
        "ca": CA,
    }
    fields.update(overrides)
    return ReplicaToken(**fields)
