# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_replication.py`` that other tests build on."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

REPLICA_ADDRESS = "203.0.113.7"
HOST = "db.example.com"
CA = (
    "-----BEGIN CERTIFICATE-----\n"
    + "MIIBszCCAVmgAwIBAgIUQ2xhdWRlVGVzdENlcnRpZmljYXRlMDAwMDEwCgYIKoZI\n" * 2
    + "-----END CERTIFICATE-----\n"
)


class Clock:
    """A clock a test moves."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class FakeChange:
    """What the firewall answers to a rule."""

    backend: str = "ufw"
    active: bool = True
    changed: bool = True


class FakeFirewall:
    """Records the rules Noust asked for."""

    def __init__(self) -> None:
        self.allowed: list[Any] = []
        self.removed: list[Any] = []

    def allow_direct(self, rule: Any) -> FakeChange:
        self.allowed.append(rule)
        return FakeChange()

    def remove_direct(self, rule: Any) -> FakeChange:
        self.removed.append(rule)
        return FakeChange()
