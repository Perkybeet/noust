# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Linking a server to its machine at a provider, over HTTP and from the terminal (checklist F-26).

The rules are in ``test_cloud_link.py``; what is pinned here is what the surfaces owe: sudo mode for
the call that attaches a server to a machine that can be deleted, the provider's live report in the
answer, and the terminal's commands.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.exceptions import NodeError
from noust.core.runner import set_runner
from noust.web.api import cloud as api
from noust.web.permissions.registry import permission_for_route
from tests.test_web_cloud import (  # noqa: F401
    FakeProvisioner,
    admin,
    client,
    fresh_state,
    managers,
    provider_api,
    sign_in,
)


class LinkingProvisioner(FakeProvisioner):
    """What the routes ask of the provisioner when a server is linked."""

    def __init__(self, servers: Any) -> None:
        super().__init__(servers)
        self.linked: list[tuple[str, str, str, bool]] = []
        self.unlinked: list[str] = []
        self.refuse_link: NodeError | None = None

    def link(
        self, node: str, account: str, provider_id: str, *, force: bool = False, actor: Any = None
    ) -> dict[str, Any]:
        if self.refuse_link is not None:
            raise self.refuse_link
        self.linked.append((node, account, provider_id, force))
        self.servers.record(
            node=node,
            account=account,
            provider="hetzner",
            provider_id=provider_id,
            location=None,
            plan=None,
            image=None,
        )
        return self.machine(node)

    def detach(self, node: str, *, actor: Any = None) -> None:
        self.unlinked.append(node)
        self.servers.forget(node)


@pytest.fixture(autouse=True)
def default_runner_after_a_rehearsal() -> Iterator[None]:
    """--dry-run installs a runner that refuses to change anything; the next test must not get it."""
    yield
    set_runner(None)


@pytest.fixture
def linking(managers: FakeProvisioner, monkeypatch: pytest.MonkeyPatch) -> LinkingProvisioner:  # noqa: F811
    provisioner = LinkingProvisioner(managers.servers)
    monkeypatch.setattr(api, "_provisioner", lambda: provisioner)
    return provisioner


def test_linking_is_a_fleet_managers_and_asks_for_sudo_mode(client: TestClient, linking) -> None:  # noqa: F811
    assert permission_for_route("POST", "/api/fleet/cloud/servers/{node}/link") == "fleet.manage"
    assert permission_for_route("DELETE", "/api/fleet/cloud/servers/{node}/link") == "fleet.manage"
    sign_in(client)

    link = client.post(
        "/api/fleet/cloud/servers/web-2/link", json={"account": "hz", "provider_id": "42"}
    )
    unlink = client.delete("/api/fleet/cloud/servers/web-2/link")

    assert link.status_code == 403 and link.json()["error"] == "elevation_required"
    assert unlink.status_code == 403
    assert linking.linked == [] and linking.unlinked == []


def test_a_server_is_linked_and_the_answer_has_the_providers_view(
    client: TestClient,  # noqa: F811
    linking,
) -> None:
    admin(client)

    response = client.post(
        "/api/fleet/cloud/servers/web-2/link",
        json={"account": "hz", "provider_id": "42", "force": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["node"] == "web-2" and response.json()["provider_id"] == "42"
    assert response.json()["remote"]["state"] == "running"
    assert linking.linked == [("web-2", "hz", "42", True)]


def test_a_refusal_is_a_400_with_its_words(client: TestClient, linking) -> None:  # noqa: F811
    admin(client)
    linking.refuse_link = NodeError("Machine 42 is at 198.51.100.7", details="Probably another.")

    response = client.post(
        "/api/fleet/cloud/servers/web-2/link", json={"account": "hz", "provider_id": "42"}
    )

    assert response.status_code == 400
    assert "198.51.100.7" in response.text


def test_unlinking_forgets_the_record(client: TestClient, linking) -> None:  # noqa: F811
    admin(client)
    client.post("/api/fleet/cloud/servers/web-2/link", json={"account": "hz", "provider_id": "42"})

    response = client.delete("/api/fleet/cloud/servers/web-2/link")

    assert response.status_code == 204
    assert linking.unlinked == ["web-2"]


def test_the_terminal_links_and_unlinks(
    linking,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("noust.cli.commands.fleet_cloud._provisioner", lambda: linking)

    linked = CliRunner().invoke(
        root_cli,
        ["fleet", "cloud", "server", "link", "web-2", "--account", "hz", "--id", "42"],
    )
    unlinked = CliRunner().invoke(root_cli, ["fleet", "cloud", "server", "unlink", "web-2"])

    assert linked.exit_code == 0, linked.output
    assert "web-2 is linked to hetzner machine 42" in linked.output
    assert unlinked.exit_code == 0, unlinked.output
    assert linking.linked == [("web-2", "hz", "42", False)]
    assert linking.unlinked == ["web-2"]


def test_a_rehearsal_links_nothing(
    linking,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("noust.cli.commands.fleet_cloud._provisioner", lambda: linking)

    result = CliRunner().invoke(
        root_cli,
        ["--dry-run", "fleet", "cloud", "server", "link", "web-2", "--account", "hz", "--id", "42"],
    )

    assert result.exit_code == 0, result.output
    assert linking.linked == []
