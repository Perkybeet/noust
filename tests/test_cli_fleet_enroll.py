# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust fleet enroll``: what the terminal asks, and what it never takes as an argument.

The enrollment is :class:`noust.fleet.enroll.NodeEnroller` (``tests/test_fleet_enroll.py``);
here it is a recording stand-in, and what is pinned is the command's part: the
fingerprint shown and confirmed before anything is sent, the password read without
echo (or from standard input) and never from an argument, the key read from a
file, the options that become the request, and a rehearsal that connects to nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import fleet_enroll
from noust.core.exceptions import NodeError
from noust.fleet import enroll as enroll_module

FINGERPRINT = "SHA256:" + "k" * 43
PASSWORD = "correct-horse-battery-1234"


class FakeEnroller:
    """Records what the command asks of the enrollment."""

    scans: list[tuple[str, int]] = []
    requests: list[Any] = []

    def scan_host_key(self, host: str, port: int = 22) -> Any:
        type(self).scans.append((host, port))
        return SimpleNamespace(host=host, port=port, fingerprint=FINGERPRINT)

    def enroll(self, request: Any, *, actor: str | None, log: Any) -> Any:
        type(self).requests.append(request)
        log("Connecting", "info")
        log("web-2 is enrolled", "success")
        return SimpleNamespace(
            version="3.3.1",
            status="reachable",
            to_dict=lambda: {"name": request.name, "status": "reachable"},
        )


@pytest.fixture(autouse=True)
def fake_enroller(monkeypatch: pytest.MonkeyPatch) -> type[FakeEnroller]:
    FakeEnroller.scans = []
    FakeEnroller.requests = []
    monkeypatch.setattr(enroll_module, "NodeEnroller", FakeEnroller)
    return FakeEnroller


def run(args: list[str], *, input_text: str | None = None, tty: bool = False) -> Any:
    runner = CliRunner()
    return runner.invoke(root_cli, ["fleet", "enroll", *args], input=input_text)


def test_the_fingerprint_is_shown_and_confirmed_before_the_credential_is_asked(
    fake_enroller: type[FakeEnroller], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_enroll, "_interactive", lambda: True)
    prompts: list[str] = []
    monkeypatch.setattr("click.prompt", lambda text, **kwargs: prompts.append(text) or PASSWORD)

    result = run(["web-2", "--host", "web2.example.com"], input_text="y\n")

    assert result.exit_code == 0, result.output
    assert FINGERPRINT in result.output
    assert "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub" in result.output
    request = fake_enroller.requests[0]
    assert request.host_key_fingerprint == FINGERPRINT
    assert request.credential.password == PASSWORD
    assert prompts == ["Password of root@web2.example.com"]
    assert "Node web-2 added: Noust 3.3.1, reachable" in result.output
    assert "web-2 is enrolled" in result.output


def test_an_unconfirmed_fingerprint_sends_nothing(
    fake_enroller: type[FakeEnroller], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fleet_enroll, "_interactive", lambda: True)

    result = run(["web-2", "--host", "web2.example.com"], input_text="n\n")

    assert result.exit_code != 0
    assert isinstance(result.exception, NodeError)
    assert "was not confirmed" in result.exception.message
    assert fake_enroller.requests == []


def test_without_a_terminal_the_fingerprint_must_come_with_the_command(
    fake_enroller: type[FakeEnroller],
) -> None:
    result = run(["web-2", "--host", "web2.example.com", "--password-stdin"], input_text=PASSWORD)

    assert isinstance(result.exception, NodeError)
    assert "--host-key-fingerprint" in (result.exception.details or "")
    assert fake_enroller.requests == []


def test_a_script_gives_the_fingerprint_and_the_password_on_stdin(
    fake_enroller: type[FakeEnroller],
) -> None:
    result = run(
        [
            "web-2",
            "--host",
            "web2.example.com",
            "--port",
            "2222",
            "--ssh-user",
            "deploy",
            "--access",
            "deploy",
            "--allow-host-access",
            "--no-install",
            "--replace-token",
            "--host-key-fingerprint",
            FINGERPRINT,
            "--password-stdin",
        ],
        input_text=f"{PASSWORD}\n",
    )

    assert result.exit_code == 0, result.output
    request = fake_enroller.requests[0]
    assert (request.name, request.host, request.port, request.ssh_user) == (
        "web-2",
        "web2.example.com",
        2222,
        "deploy",
    )
    assert request.access == "deploy" and request.host_access is True
    assert request.install is False and request.replace_token is True
    assert request.credential.password == PASSWORD
    assert PASSWORD not in result.output


def test_a_key_is_read_from_a_file_and_its_passphrase_asked_without_echo(
    fake_enroller: type[FakeEnroller], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = tmp_path / "id_ed25519"
    key.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("click.prompt", lambda text, **kwargs: "my passphrase")

    result = run(
        [
            "web-2",
            "--host",
            "web2.example.com",
            "--key-file",
            str(key),
            "--ask-passphrase",
            "--host-key-fingerprint",
            FINGERPRINT,
        ]
    )

    assert result.exit_code == 0, result.output
    credential = fake_enroller.requests[0].credential
    assert credential.private_key and "BEGIN OPENSSH" in credential.private_key
    assert credential.passphrase == "my passphrase"
    assert credential.password is None
    assert "my passphrase" not in result.output


def test_an_empty_password_line_is_refused(fake_enroller: type[FakeEnroller]) -> None:
    result = run(
        [
            "web-2",
            "--host",
            "h.example.com",
            "--password-stdin",
            "--host-key-fingerprint",
            FINGERPRINT,
        ],
        input_text="\n",
    )

    assert isinstance(result.exception, NodeError)
    assert fake_enroller.requests == []


def test_a_rehearsal_connects_to_nothing(fake_enroller: type[FakeEnroller]) -> None:
    result = CliRunner().invoke(
        root_cli,
        ["--dry-run", "fleet", "enroll", "web-2", "--host", "web2.example.com", "--access", "read"],
    )

    assert result.exit_code == 0, result.output
    assert "would connect to root@web2.example.com:22" in result.output
    assert fake_enroller.scans == [] and fake_enroller.requests == []


def test_json_output_is_the_node(fake_enroller: type[FakeEnroller]) -> None:
    result = CliRunner().invoke(
        root_cli,
        [
            "--json",
            "fleet",
            "enroll",
            "web-2",
            "--host",
            "web2.example.com",
            "--password-stdin",
            "--host-key-fingerprint",
            FINGERPRINT,
        ],
        input=f"{PASSWORD}\n",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output.strip().splitlines()[-1]) == {
        "name": "web-2",
        "status": "reachable",
    }
    assert "Connecting" not in result.output, "JSON mode prints the JSON and nothing else"
