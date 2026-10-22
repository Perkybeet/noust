# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust fleet cloud``: what the terminal asks, and what it never takes as an argument.

The managers are tested on their own (``tests/test_cloud_*.py``); here they are recording
stand-ins, and what is pinned is the command's part: a token read without echo or from
standard input and never from an argument, a machine that is not created without a yes
(it costs money), a deletion that needs its name, and a rehearsal that calls nothing.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import fleet_cloud
from noust.core.exceptions import NodeError

TOKEN = "hc" + "_" + "c" * 20 + "L5n3"


class FakeAccounts:
    def __init__(self) -> None:
        self.added: list[tuple[Any, ...]] = []
        self.removed: list[tuple[str, bool]] = []

    def add(self, name: str, provider: str, token: str, **kwargs: Any) -> Any:
        self.added.append((name, provider, token, kwargs))
        return SimpleNamespace(to_dict=lambda: {"name": name, "token_hint": f"...{token[-4:]}"})

    def list(self) -> list[Any]:
        return [
            SimpleNamespace(
                to_dict=lambda: {
                    "name": "hz",
                    "provider": "hetzner",
                    "token_hint": "...L5n3",
                    "verified_at": "2026-10-13",
                    "last_used_at": None,
                    "note": "web",
                }
            )
        ]

    def remove(self, name: str, *, actor: str | None = None, force: bool = False) -> None:
        self.removed.append((name, force))


class FakeLibrary:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str, str]] = []

    def save(self, name: str, kind: str, content: str, **kwargs: Any) -> Any:
        self.saved.append((name, kind, content))
        return SimpleNamespace(kind=kind, to_dict=lambda content=True: {"name": name, "kind": kind})


class FakeProvisioner:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.deleted: list[tuple[str, bool]] = []
        self.servers = SimpleNamespace(
            list=lambda: [
                SimpleNamespace(
                    to_dict=lambda: {
                        "node": "web-2",
                        "state": "ready",
                        "provider": "hetzner",
                        "account": "hz",
                        "address": "203.0.113.9",
                        "plan": "cx22",
                        "location": "fsn1",
                        "created_at": "2026-10-13",
                    }
                )
            ]
        )

    def validate(self, request: Any) -> Any:
        return SimpleNamespace(display_name="Hetzner Cloud", kind="hetzner"), []

    def provision(self, request: Any, *, actor: str | None, log: Any) -> Any:
        self.requests.append(request)
        log("Hetzner Cloud created 42", "success")
        return SimpleNamespace(
            version="3.3.1", status="reachable", to_dict=lambda: {"name": request.name}
        )

    def machine(self, name: str, live: bool = False) -> dict[str, Any]:
        return {"node": name, "state": "ready"}

    def delete_machine(self, name: str, *, remove_node: bool, actor: str | None) -> list[str]:
        self.deleted.append((name, remove_node))
        return ["still registered"]


@pytest.fixture
def accounts(monkeypatch: pytest.MonkeyPatch) -> FakeAccounts:
    fake = FakeAccounts()
    monkeypatch.setattr(fleet_cloud, "_accounts", lambda: fake)
    return fake


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch) -> FakeLibrary:
    fake = FakeLibrary()
    monkeypatch.setattr(fleet_cloud, "_library", lambda: fake)
    return fake


@pytest.fixture
def provisioner(monkeypatch: pytest.MonkeyPatch) -> FakeProvisioner:
    fake = FakeProvisioner()
    monkeypatch.setattr(fleet_cloud, "_provisioner", lambda: fake)
    return fake


def run(args: list[str], *, input_text: str | None = None) -> Any:
    return CliRunner().invoke(root_cli, ["fleet", "cloud", *args], input=input_text)


def test_the_providers_are_listed_with_where_their_tokens_are_made() -> None:
    result = run(["providers"])

    assert result.exit_code == 0, result.output
    assert (
        "hetzner" in result.output and "digitalocean" in result.output and "vultr" in result.output
    )
    assert "https://" in result.output


def test_a_token_is_read_from_standard_input_and_is_in_no_output(accounts: FakeAccounts) -> None:
    result = run(
        ["account", "add", "hz", "--provider", "hetzner", "--token-stdin", "--note", "web"],
        input_text=TOKEN + "\n",
    )

    assert result.exit_code == 0, result.output
    name, provider, token, kwargs = accounts.added[0]
    assert (name, provider, token) == ("hz", "hetzner", TOKEN)
    assert kwargs["note"] == "web" and kwargs["verify"] is True
    assert TOKEN not in result.output
    assert "...L5n3" in result.output


def test_without_a_terminal_a_token_must_come_on_standard_input(accounts: FakeAccounts) -> None:
    result = run(["account", "add", "hz", "--provider", "hetzner"], input_text="")

    assert isinstance(result.exception, NodeError)
    assert "--token-stdin" in (result.exception.details or "")
    assert accounts.added == []


def test_the_token_is_asked_for_without_echo_on_a_terminal(
    accounts: FakeAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_cloud, "_interactive", lambda: True)
    prompts: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "click.prompt", lambda text, **kwargs: prompts.append((text, kwargs["hide_input"])) or TOKEN
    )

    result = run(["account", "add", "hz", "--provider", "hetzner"])

    assert result.exit_code == 0, result.output
    assert prompts == [("API token of hz", True)]
    assert accounts.added[0][2] == TOKEN


def test_a_rehearsal_keeps_no_token(accounts: FakeAccounts) -> None:
    result = CliRunner().invoke(
        root_cli, ["--dry-run", "fleet", "cloud", "account", "add", "hz", "--provider", "hetzner"]
    )

    assert result.exit_code == 0, result.output
    assert accounts.added == []


def test_accounts_are_listed_as_json_without_tokens(accounts: FakeAccounts) -> None:
    result = run(["account", "list", "--json"])

    assert json.loads(result.output)[0]["token_hint"] == "...L5n3"
    assert TOKEN not in result.output


def test_removing_an_account_asks_unless_told_not_to(accounts: FakeAccounts) -> None:
    refused = run(["account", "remove", "hz"], input_text="n\n")
    done = run(["account", "remove", "hz", "--yes", "--force"])

    assert refused.exit_code != 0 and accounts.removed == [("hz", True)]
    assert done.exit_code == 0


def test_a_machine_is_not_created_without_a_yes(provisioner: FakeProvisioner) -> None:
    result = run(
        [
            "server",
            "create",
            "web-2",
            "--account",
            "hz",
            "--location",
            "fsn1",
            "--plan",
            "cx22",
            "--image",
            "ubuntu-24.04",
        ]
    )

    assert isinstance(result.exception, NodeError)
    assert "--yes" in (result.exception.details or "")
    assert provisioner.requests == []


def test_the_options_become_the_request(provisioner: FakeProvisioner) -> None:
    result = run(
        [
            "server",
            "create",
            "web-2",
            "--account",
            "hz",
            "--location",
            "fsn1",
            "--plan",
            "cx22",
            "--image",
            "ubuntu-24.04",
            "--access",
            "deploy",
            "--ssh-key",
            "9",
            "--firewall",
            "3",
            "--network",
            "8",
            "--no-ipv6",
            "--backups",
            "--script",
            "swap-2g",
            "--script",
            "base-packages",
            "--delete-on-failure",
            "--yes",
        ]
    )

    assert result.exit_code == 0, result.output
    request = provisioner.requests[0]
    assert (request.name, request.account, request.location, request.plan, request.image) == (
        "web-2",
        "hz",
        "fsn1",
        "cx22",
        "ubuntu-24.04",
    )
    assert request.access == "deploy" and request.ssh_keys == ["9"]
    assert request.firewalls == ["3"] and request.networks == ["8"]
    assert request.ipv6 is False and request.backups is True
    assert request.scripts == ["swap-2g", "base-packages"]
    assert request.delete_on_failure is True
    assert "Hetzner Cloud created 42" in result.output
    assert "Server web-2 created and added" in result.output


def test_a_rehearsal_creates_nothing(provisioner: FakeProvisioner) -> None:
    result = CliRunner().invoke(
        root_cli,
        [
            "--dry-run",
            "fleet",
            "cloud",
            "server",
            "create",
            "web-2",
            "--account",
            "hz",
            "--location",
            "fsn1",
            "--plan",
            "cx22",
            "--image",
            "ubuntu-24.04",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "would create web-2" in result.output
    assert provisioner.requests == []


def test_a_deletion_needs_the_name_typed_or_a_yes(provisioner: FakeProvisioner) -> None:
    refused = run(["server", "delete", "web-2"])
    done = run(["server", "delete", "web-2", "--yes", "--remove-node"])

    assert isinstance(refused.exception, NodeError)
    assert provisioner.deleted == [("web-2", True)]
    assert "web-2 was deleted at its provider" in done.output
    assert "still registered" in done.output


def test_the_machines_are_listed(provisioner: FakeProvisioner) -> None:
    result = run(["server", "list"])

    assert result.exit_code == 0, result.output
    assert "web-2" in result.output and "203.0.113.9" in result.output


def test_a_script_is_saved_from_standard_input_and_its_kind_guessed(library: FakeLibrary) -> None:
    run(["script", "save", "tune", "--description", "x"], input_text="#!/bin/sh\n:\n")
    run(["script", "save", "pkgs"], input_text="#cloud-config\npackages: [git]\n")

    assert [(n, k) for n, k, _ in library.saved] == [("tune", "script"), ("pkgs", "cloud-config")]
