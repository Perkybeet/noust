# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_cloud_provision.py`` that other tests build on."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from noust.core.exceptions import NodeError
from noust.core.secrets import SecretStore
from noust.core.store import NodeRecord, NoustStore
from noust.fleet.cloud.accounts import CloudAccounts
from noust.fleet.cloud.init import CloudInitLibrary
from noust.fleet.cloud.provision import ServerProvisioner
from noust.fleet.cloud.servers import CloudServers
from noust.fleet.enroll import EnrollRequest, HostKeyScan
from noust.fleet.models import PublicKey, parse_public_key
from tests.support.cloud_support import FakeApi
from tests.support.fleet_support import KeygenRunner

TOKEN = "hc" + "_" + "t" * 20 + "K9m2"


class Clock:
    """A clock that only moves when the code sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class StubEnroller:
    """
    The guided enrollment, as far as provisioning meets it.

    Attributes:
        api: The fake provider, to read the host key out of the create call.
        wrong_scans: How many scans present another key before the right one.
        never_matches: Present another key for ever.
        enroll_error: Raise this from ``enroll``.
        enrolled: The requests ``enroll`` was given.
    """

    api: FakeApi
    store: NoustStore
    wrong_scans: int = 0
    never_matches: bool = False
    enroll_error: NodeError | None = None
    blockers: list[str] = field(default_factory=list)
    scans: list[tuple[str, int]] = field(default_factory=list)
    enrolled: list[EnrollRequest] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def manager(self) -> Any:
        return SimpleNamespace(
            registration_blockers=lambda: list(self.blockers),
            store=self.store,
            remove=self._remove,
        )

    def _remove(self, name: str, *, revoke: bool = True, actor: str | None = None) -> list[str]:
        self.removed.append(name)
        return [f"removed {name}"]

    def _chosen_key(self) -> PublicKey:
        (create,) = self.api.sent("POST", "/servers")
        document = yaml.safe_load(create.body["user_data"])
        return parse_public_key(document["ssh_keys"]["ed25519_public"])

    def scan_host_key(self, host: str, port: int = 22) -> HostKeyScan:
        self.scans.append((host, port))
        if self.never_matches or self.wrong_scans > 0:
            self.wrong_scans = max(0, self.wrong_scans - 1)
            from tests.support.fleet_support import ed25519_line

            return HostKeyScan(host, port, parse_public_key(ed25519_line(4242)))
        return HostKeyScan(host, port, self._chosen_key())

    def enroll(
        self, request: EnrollRequest, *, actor: str | None = None, log: Any = None
    ) -> NodeRecord:
        self.enrolled.append(request)
        if self.enroll_error is not None:
            raise self.enroll_error
        if log:
            log("Joined", "success")
        return NodeRecord(
            name=request.name,
            ssh_host=request.host,
            ssh_port=request.port,
            ssh_user="noust-tunnel",
            host_key="noust-node-x ssh-ed25519 AAAA",
            console_port=8080,
            status="reachable",
        )


@dataclass
class World:
    store: NoustStore
    api: FakeApi
    accounts: CloudAccounts
    library: CloudInitLibrary
    servers: CloudServers
    enroller: StubEnroller
    clock: Clock
    provisioner: ServerProvisioner
    log: list[tuple[str, str]] = field(default_factory=list)

    def say(self, message: str, level: str = "info") -> None:
        self.log.append((level, message))

    @property
    def log_text(self) -> str:
        return "\n".join(m for _, m in self.log)


def server_json(state: str, ip: str | None = "203.0.113.9") -> dict[str, Any]:
    return {
        "server": {
            "id": 42,
            "name": "web-2",
            "status": state,
            "public_net": {"ipv4": {"ip": ip} if ip else None, "ipv6": None},
        }
    }


@pytest.fixture
def world(tmp_path: Path) -> World:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    api = FakeApi(prefix="/v1")
    api.on("GET", "/locations", (200, {"locations": []}))
    api.on("POST", "/ssh_keys", (201, {"ssh_key": {"id": 77}}))
    api.on("DELETE", "/ssh_keys/77", (200, None))
    api.on(
        "POST", "/servers", (201, {**server_json("initializing"), "root_password": "pw-never-read"})
    )
    states = iter(["initializing", "starting", "running"])
    api.on("GET", "/servers/42", lambda call: (200, server_json(next(states, "running"))))
    accounts = CloudAccounts(
        store,
        SecretStore(root=tmp_path / "secrets"),
        provider_options={"opener": api, "sleep": lambda _s: None},
    )
    accounts.add("hz", "hetzner", TOKEN)
    api.calls.clear()
    library = CloudInitLibrary(store)
    servers = CloudServers(store)
    enroller = StubEnroller(api=api, store=store)
    clock = Clock()
    provisioner = ServerProvisioner(
        accounts,
        library,
        enroller,  # type: ignore[arg-type]
        servers,
        runner=KeygenRunner(),
        sleep=clock.sleep,
        clock=clock,
        boot_timeout=60,
        ssh_timeout=60,
        poll=5,
    )
    return World(store, api, accounts, library, servers, enroller, clock, provisioner)
