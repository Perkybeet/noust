# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server of Noust DNS on a scripted machine, for the tests of the server's part and of the zone
editor: the real engine over a scripted runner and a temporary directory, a firewall that
remembers what it was asked, and the store and the secrets in the test's own directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.authdns_store import AuthDnsStore
from noust.core.exceptions import SecurityError
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.authdns.engine import KnotEngine
from noust.managers.authdns.server import AuthDnsServers
from noust.managers.port_claims import PortClaims
from noust.managers.server.security_firewall import DirectChange, RuleRequest

SECRET = "ZmFrZWZha2VmYWtlZmFrZWZha2VmYWtlZmFrZWZha2U="
PUBLIC = "203.0.113.10"


@dataclass
class FakeFirewall:
    """The one firewall manager: remembers what it was asked and says what it changed."""

    asked: list[tuple[str, int, str, str]] = field(default_factory=list)
    present: set[tuple[int, str]] = field(default_factory=set)
    refuse: str | None = None

    def allow_direct(self, request: RuleRequest) -> DirectChange:
        self.asked.append(("allow", request.port, request.proto, request.comment))
        if self.refuse == request.proto:
            raise SecurityError("ufw refused the rule")
        key = (request.port, request.proto)
        if key in self.present:
            return DirectChange("ufw", True, False, "it is already allowed")
        self.present.add(key)
        return DirectChange("ufw", True, True)

    def remove_direct(self, request: RuleRequest) -> DirectChange:
        self.asked.append(("remove", request.port, request.proto, request.comment))
        self.present.discard((request.port, request.proto))
        return DirectChange("ufw", True, True)


@dataclass
class World:
    """A server of Noust DNS and the machine it runs on."""

    servers: AuthDnsServers
    engine: KnotEngine
    runner: FakeRunner
    firewall: FakeFirewall
    secrets: SecretStore
    store: NoustStore
    config: Path
    zones_dir: Path

    @property
    def records(self) -> AuthDnsStore:
        return self.servers.records

    def parsed(self) -> dict[str, Any]:
        loaded = yaml.safe_load(self.config.read_text())
        assert isinstance(loaded, dict)
        return loaded

    def commands(self) -> list[list[str]]:
        return [list(call) for call in self.runner.calls]


def make_world(
    tmp_path: Path,
    store: NoustStore,
    secrets: SecretStore,
    runner: FakeRunner,
    fs: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    config = tmp_path / "etc" / "knot.conf"
    zones = tmp_path / "var" / "zones"
    runner.script(["knotd", "--version"], stdout="knotd, Knot DNS 3.4.6\n")
    runner.script(["systemctl", "is-active"], stdout="inactive\n", exit_code=3)
    runner.script(["ss"], stdout="")
    engine = KnotEngine(
        runner, fs, config_path=config, zones_dir=zones, group=None, sleep=lambda _: None
    )
    firewall = FakeFirewall()
    monkeypatch.setattr(
        "noust.managers.port_claims.Config",
        lambda: type("C", (), {"get": lambda self, key, default=None: default})(),
    )
    servers = AuthDnsServers(
        store=store,
        engine=engine,
        secrets=secrets,
        runner=runner,
        firewall=lambda _runner: firewall,  # type: ignore[arg-type,return-value]
        claims=PortClaims(runner=runner, store=store),
        addresses=lambda: [PUBLIC],
    )
    return World(servers, engine, runner, firewall, secrets, store, config, zones)
