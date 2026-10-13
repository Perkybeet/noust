# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``noust stream``.

The manager decides (``tests/test_stream_exposure.py``). Pinned here is the translation:
``--to`` read as a port or an address and a port, the flags that become the request, a
plan that opens nothing, a rehearsal that says it would, a removal that asks first, and a
refusal that is exit code 1 with the operation's own words.
"""

from __future__ import annotations

import json
from typing import Any

import click
import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.cli.commands import stream as stream_cli
from noust.core.exceptions import PrerequisiteError, ValidationError
from noust.core.logger import Logger
from noust.core.stream_store import StreamExposure
from noust.managers.stream_exposure import StreamPlan, StreamResult, StreamSupport

EXPOSURE = StreamExposure(
    name="minecraft",
    protocol="tcp",
    listen_port=25565,
    target_host="127.0.0.1",
    target_port=25565,
    sources=("203.0.113.0/24",),
    state="open",
)
SUPPORT = StreamSupport(True, "dynamic", "installable")


class Fake:
    """A manager that records what it was asked."""

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    rehearsal = False
    refusal: Exception | None = None

    def plan(self, *args: Any, **kwargs: Any) -> StreamPlan:
        self.calls.append(("plan", args, kwargs))
        return StreamPlan(
            exposure=EXPOSURE,
            config="server {}\n",
            file=__import__("pathlib").Path("/etc/nginx/noust-stream/minecraft.conf"),
            loader="write",
            support=SUPPORT,
            warnings=("Nothing listens on 127.0.0.1:25565 yet",),
        )

    def open(self, *args: Any, **kwargs: Any) -> StreamResult:
        self.calls.append(("open", args, kwargs))
        if self.refusal is not None:
            raise self.refusal
        return StreamResult(
            EXPOSURE, ("a warning",), "ufw now allows 1 rule(s) for it", self.rehearsal
        )

    def close(self, *args: Any, **kwargs: Any) -> StreamResult:
        self.calls.append(("close", args, kwargs))
        return StreamResult(EXPOSURE, rehearsal=self.rehearsal)

    def all(self) -> list[StreamExposure]:
        return [EXPOSURE]

    def support(self) -> StreamSupport:
        return self.support_answer

    def repair(self) -> list[str]:
        self.calls.append(("repair", (), {}))
        return ["half"]

    directory = __import__("pathlib").Path("/etc/nginx/noust-stream")
    support_answer = SUPPORT


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> Fake:
    fake = Fake()
    fake.calls = []
    fake.refusal = None
    fake.rehearsal = False
    monkeypatch.setattr(stream_cli, "StreamExposures", lambda: fake)
    return fake


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def invoke(args: list[str], input_text: str | None = None) -> Result:
    return CliRunner().invoke(root_cli, args, input=input_text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("25565", ("127.0.0.1", 25565)),
        ("10.0.0.5:25565", ("10.0.0.5", 25565)),
        ("[::1]:25565", ("::1", 25565)),
    ],
)
def test_the_target_is_a_port_or_an_address_and_a_port(
    text: str, expected: tuple[str, int]
) -> None:
    assert stream_cli.parse_target(text) == expected


@pytest.mark.parametrize("text", ["db.internal:25565", "nonsense", "10.0.0.5:port", ""])
def test_a_target_that_is_neither_is_a_usage_error(text: str) -> None:
    with pytest.raises(click.UsageError):
        stream_cli.parse_target(text)


def test_the_flags_become_the_request(manager: Fake, log: list[str]) -> None:
    result = invoke(
        [
            "stream",
            "add",
            "minecraft",
            "--protocol",
            "udp",
            "--port",
            "19132",
            "--to",
            "10.0.0.5:19133",
            "--allow",
            "203.0.113.0/24",
            "--allow",
            "198.51.100.7",
            "--timeout",
            "600",
        ]
    )

    assert result.exit_code == 0, result.output
    name, args, kwargs = manager.calls[-1]
    assert (name, args) == ("open", ("minecraft",))
    assert kwargs["protocol"] == "udp"
    assert kwargs["listen_port"] == 19132
    assert (kwargs["target_host"], kwargs["target_port"]) == ("10.0.0.5", 19133)
    assert kwargs["sources"] == ("203.0.113.0/24", "198.51.100.7")
    assert kwargs["timeout_seconds"] == 600
    assert kwargs["app_domain"] is None
    assert kwargs["actor"]
    text = " ".join(log)
    assert "Published UDP 19132" in text and "a warning" in text and "ufw now allows" in text


def test_an_application_without_a_target_leaves_the_port_to_the_manager(
    manager: Fake, log: list[str]
) -> None:
    result = invoke(["stream", "add", "minecraft", "--port", "25565", "--app", "shop.example.com"])

    assert result.exit_code == 0, result.output
    _, _, kwargs = manager.calls[-1]
    assert kwargs["app_domain"] == "shop.example.com" and kwargs["target_port"] is None


def test_a_plan_opens_nothing_and_says_what_would_happen(manager: Fake, log: list[str]) -> None:
    result = invoke(["stream", "add", "minecraft", "--port", "25565", "--to", "25565", "--plan"])

    assert result.exit_code == 0, result.output
    assert [call[0] for call in manager.calls] == ["plan"]
    text = " ".join(log)
    assert "Nothing listens on 127.0.0.1:25565 yet" in text
    assert "minecraft.conf" in text


def test_the_plan_can_be_json(manager: Fake) -> None:
    result = invoke(
        ["stream", "add", "minecraft", "--port", "25565", "--to", "25565", "--plan", "--json"]
    )

    body = json.loads(result.output)
    assert body["loader"] == "write" and body["exposure"]["name"] == "minecraft"


def test_a_rehearsal_says_it_would(manager: Fake, log: list[str]) -> None:
    manager.rehearsal = True

    result = invoke(["--dry-run", "stream", "add", "minecraft", "--port", "25565", "--to", "25565"])

    assert result.exit_code == 0, result.output
    assert any("Would publish TCP 25565" in line for line in log)


def test_a_refusal_is_exit_code_one_with_the_operations_words(
    manager: Fake, log: list[str]
) -> None:
    manager.refusal = ValidationError(
        "Port 443 is the web server's", details="Choose another port."
    )

    assert main(["stream", "add", "web", "--port", "443", "--to", "8080"]) == 1
    assert any("Port 443 is the web server's" in line for line in log)


def test_a_server_that_cannot_publish_is_told_how_to_fix_it(manager: Fake, log: list[str]) -> None:
    manager.refusal = PrerequisiteError(
        "This nginx was built without the stream module", details="apt install libnginx-mod-stream"
    )

    assert main(["stream", "add", "mc", "--port", "25565", "--to", "25565"]) == 1
    assert any("libnginx-mod-stream" in line for line in log)


def test_the_list_says_who_may_reach_each_port(manager: Fake) -> None:
    result = invoke(["stream", "list", "--json"])

    (row,) = json.loads(result.output)["exposures"]
    assert row["sources"] == ["203.0.113.0/24"] and row["state"] == "open"


def test_the_check_says_what_is_missing(manager: Fake, log: list[str]) -> None:
    manager.support_answer = StreamSupport(
        False,
        "missing",
        "manual",
        "This nginx was built without the stream module",
        "apt install x",
    )

    result = invoke(["stream", "check"])

    assert result.exit_code == 0, result.output
    assert any("built without the stream module" in line for line in log)
    manager.support_answer = SUPPORT


def test_removing_asks_unless_told_not_to(manager: Fake, log: list[str]) -> None:
    declined = invoke(["stream", "remove", "minecraft"], input_text="n\n")
    assert declined.exit_code == 0
    assert manager.calls == []

    confirmed = invoke(["stream", "remove", "minecraft", "--yes"])
    assert confirmed.exit_code == 0, confirmed.output
    assert [call[0] for call in manager.calls] == ["close"]
    assert any("Closed TCP 25565" in line for line in log)


def test_a_rehearsal_removal_does_not_ask(manager: Fake, log: list[str]) -> None:
    manager.rehearsal = True

    result = invoke(["--dry-run", "stream", "remove", "minecraft"])

    assert result.exit_code == 0, result.output
    assert any("Would close TCP 25565" in line for line in log)


def test_repair_names_what_it_finished(manager: Fake, log: list[str]) -> None:
    result = invoke(["stream", "repair"])

    assert result.exit_code == 0, result.output
    assert any("Put right: half" in line for line in log)
