"""
Shared pieces of the fleet tests: keys, tokens, a fake node API, a wired manager.

Token-shaped values are assembled from parts so no scanner mistakes a fixture
for a leaked credential.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from noust.core.runner import CommandResult, FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.fleet.joincode import JoinCode
from noust.fleet.keys import NodeKeys
from noust.fleet.nodes import NodeManager
from noust.fleet.tunnels import TunnelManager

TOKEN = "noust_" + "tok_" + "Q" * 20 + "w" * 23
OTHER_TOKEN = "noust_" + "tok_" + "Z" * 43


def ed25519_line(seed: int, comment: str = "") -> str:
    """
    Build a structurally valid ed25519 public key line.

    Args:
        seed: Varies the key bytes.
        comment: The comment, if any.

    Returns:
        ``ssh-ed25519 AAAA... [comment]``.
    """
    key = hashlib.sha256(str(seed).encode()).digest()
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + key
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}".rstrip()


def fingerprint(line: str) -> str:
    """
    Fingerprint a key line as ssh-keygen -l does.

    Args:
        line: The key line.

    Returns:
        ``SHA256:...``.
    """
    blob = base64.b64decode(line.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


HOST_KEY = ed25519_line(1)


def join_code(central_key: str, **overrides: Any) -> str:
    """
    Build a join code as a node would print it.

    Args:
        central_key: The central key the node authorized.
        **overrides: Fields to change.

    Returns:
        The encoded code.
    """
    values: dict[str, Any] = {
        "ssh_host_key": HOST_KEY,
        "ssh_user": "root",
        "ssh_port": 22,
        "console_port": 8080,
        "token": TOKEN,
        "noust_version": "3.0.0",
        "central_key_fp": fingerprint(central_key),
        "node_name": "web-2",
        "token_name": "fleet-nas",
        "central": "nas",
    }
    values.update(overrides)
    return JoinCode(**values).encode()


class KeygenRunner(FakeRunner):
    """A FakeRunner whose ssh-keygen writes a key pair, as the real one does."""

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:  # type: ignore[override]
        result = super().run(argv, **kwargs)
        if argv and argv[0] == "ssh-keygen" and result.success:
            path = Path(argv[argv.index("-f") + 1])
            comment = argv[argv.index("-C") + 1]
            path.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n")
            Path(f"{path}.pub").write_text(ed25519_line(99, comment) + "\n")
        return result


@dataclass
class FakeNode:
    """
    A node's API, as far as the fleet talks to it.

    Attributes:
        token: The token it accepts.
        requests: Every request it received.
        revoked: Whether its fleet token was revoked.
        version: What it reports.
        responses: Path overrides: status and JSON body.
    """

    token: str = TOKEN
    requests: list[httpx.Request] = field(default_factory=list)
    revoked: bool = False
    version: str = "3.0.1"
    responses: dict[str, tuple[int, Any]] = field(default_factory=dict)

    def handler(self, request: httpx.Request) -> httpx.Response:
        """
        Answer one request.

        Args:
            request: The request.

        Returns:
            The response.
        """
        self.requests.append(request)
        if self.revoked or request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "unauthorized", "message": "Invalid token"})
        path = request.url.path
        if path in self.responses:
            status, body = self.responses[path]
            return httpx.Response(status, json=body)
        if path == "/api/system/version":
            return httpx.Response(200, json={"current_version": self.version, "has_update": False})
        if path == "/api/system/machine":
            return httpx.Response(
                200,
                json={
                    "apps": {"running": 3, "failed": 1, "stopped": 0, "static": 2},
                    "units": {"running": 5, "failed": 1, "stopped": 0},
                },
            )
        if path == "/api/certs":
            return httpx.Response(
                200,
                json={
                    "certificates": [
                        {"domain": "a.example.com", "days_remaining": 5},
                        {"domain": "b.example.com", "days_remaining": 80},
                        {"domain": "c.example.com", "days_remaining": None},
                    ],
                    "total": 3,
                },
            )
        if path == "/api/auth/fleet/revoke" and request.method == "POST":
            self.revoked = True
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404, json={"error": "not_found"})

    def transport(self) -> httpx.MockTransport:
        """Returns: A transport answering with this node."""
        return httpx.MockTransport(self.handler)


class Clock:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        """
        Move time forward.

        Args:
            seconds: How far.
        """
        self.now += seconds


@dataclass
class Fleet:
    """A central wired to fakes, for one test."""

    store: NoustStore
    secrets: SecretStore
    runner: KeygenRunner
    keys: NodeKeys
    tunnels: TunnelManager
    node: FakeNode
    manager: NodeManager
    clock: Clock
    probe_answers: dict[int, bool]

    def added(self, name: str = "web-2", **code: Any) -> None:
        """
        Register a node the normal way: key, then add with a matching code.

        Args:
            name: The node's name.
            **code: Join code overrides.
        """
        key = self.manager.central_public_key(name)
        self.manager.add(name, ssh_target="root@web2.example.com", join_code=join_code(key, **code))


def build_fleet(
    tmp_path: Path,
    *,
    blockers: Callable[[], list[str]] = list,
    secrets: SecretStore | None = None,
) -> Fleet:
    """
    Wire a NodeManager to a fresh store, a secret store, fake ssh and a fake node.

    Args:
        tmp_path: The test's directory.
        blockers: The registration policy (none by default).
        secrets: The secret store; one under ``tmp_path/secrets`` by default.

    Returns:
        The wiring.
    """
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    secrets = secrets or SecretStore(root=tmp_path / "secrets")
    runner = KeygenRunner()
    keys = NodeKeys(secrets, runner)
    clock = Clock()
    probe_answers: dict[int, bool] = {}
    ports = iter(range(50000, 51000))
    tunnels = TunnelManager(
        runner=runner,
        store=store,
        keys=keys,
        clock=clock,
        sleep=lambda seconds: clock.advance(seconds),
        probe=lambda port: probe_answers.get(port, True),
        free_port=lambda: next(ports),
    )
    node = FakeNode()
    manager = NodeManager(
        store=store,
        secrets=secrets,
        runner=runner,
        tunnels=tunnels,
        blockers=blockers,
        transport=node.transport(),
    )
    return Fleet(store, secrets, runner, keys, tunnels, node, manager, clock, probe_answers)


def decoded(code: str) -> dict[str, Any]:
    """
    Decode a join code's JSON without validating it.

    Args:
        code: The code.

    Returns:
        Its document.
    """
    payload = code.split(":", 2)[2]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
