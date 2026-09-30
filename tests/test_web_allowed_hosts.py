# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``web.allowed_hosts`` is enforced (it was dead configuration before 3.1).

The check compares the ``Host`` header of every request and WebSocket
handshake with the configured names. Loopback and the hosts of the console's
own public URLs are always allowed; an empty list allows any host, as every
earlier version did. Refusing is a 400 in the API's error contract that says
which host was refused and how to allow it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from noust.core.config import Config
from noust.web.auth import SecurityConfig
from noust.web.server import create_app, host_is_allowed, request_host


@pytest.fixture
def config_file(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """
    Point the configuration at a sandbox file the tests write.

    Args:
        sandbox: Isolated filesystem root.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The configuration file's path; it does not exist yet.
    """
    path = sandbox / "etc" / "noust" / "config.yaml"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


def configure(path: Path, **web: object) -> None:
    """
    Write the ``web`` section and reload the configuration.

    Args:
        path: The configuration file.
        **web: Settings of the section.
    """
    path.write_text(yaml.safe_dump({"web": web}), encoding="utf-8")
    Config.reset_instance()


def client_for(sandbox: Path) -> TestClient:
    """
    Build a client for a console whose state lives in the sandbox.

    Args:
        sandbox: Isolated filesystem root.

    Returns:
        The client; it sends ``Host: testserver`` unless a request says otherwise.
    """
    config = SecurityConfig(state_dir=sandbox / "state", rate_limit_requests=5000)
    return TestClient(create_app(config), client=("testclient", 50000))


# -- the pure decision ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "host"),
    [
        ("Console.Example.com", "console.example.com"),
        ("console.example.com:8443", "console.example.com"),
        ("console.example.com.", "console.example.com"),
        ("[::1]:8080", "::1"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("127.0.0.1:8080", "127.0.0.1"),
        ("", ""),
        (None, ""),
        ("[broken", ""),
    ],
)
def test_the_host_is_read_out_of_the_header(header: str | None, host: str) -> None:
    assert request_host(header) == host


def test_an_empty_list_allows_any_host() -> None:
    assert host_is_allowed("anything.example.org", []) is True
    assert host_is_allowed(None, []) is True


def test_a_listed_name_is_allowed_with_or_without_a_port_and_in_any_case() -> None:
    allowed = ["console.example.com"]

    assert host_is_allowed("console.example.com", allowed)
    assert host_is_allowed("CONSOLE.example.com:8443", allowed)
    assert not host_is_allowed("other.example.com", allowed)
    assert not host_is_allowed("console.example.com.evil.net", allowed)
    assert not host_is_allowed("evilconsole.example.com", allowed)


def test_a_wildcard_allows_subdomains_and_not_the_bare_domain() -> None:
    allowed = ["*.example.com"]

    assert host_is_allowed("a.example.com", allowed)
    assert host_is_allowed("a.b.example.com", allowed)
    assert not host_is_allowed("example.com", allowed)
    assert not host_is_allowed("notexample.com", allowed)
    assert not host_is_allowed("a.example.com.evil.net", allowed)


@pytest.mark.parametrize(
    "header",
    ["localhost", "localhost:8080", "127.0.0.1:8080", "127.5.5.5", "[::1]:8080", "api.localhost"],
)
def test_loopback_is_always_allowed(header: str) -> None:
    assert host_is_allowed(header, ["console.example.com"])


def test_the_public_hosts_are_always_allowed() -> None:
    assert host_is_allowed("hooks.example.com", ["console.example.com"], ["hooks.example.com"])


def test_no_host_is_refused_once_a_list_exists() -> None:
    assert not host_is_allowed(None, ["console.example.com"])
    assert not host_is_allowed("", ["console.example.com"])


def test_an_address_in_the_list_is_matched_as_written() -> None:
    assert host_is_allowed("10.0.0.5:8080", ["10.0.0.5"])
    assert not host_is_allowed("10.0.0.6", ["10.0.0.5"])


# -- the middleware ------------------------------------------------------------------


def test_without_a_list_every_host_is_answered(sandbox: Path, config_file: Path) -> None:
    client = client_for(sandbox)

    assert client.get("/health", headers={"Host": "anything.example.org"}).status_code == 200


def test_a_host_outside_the_list_gets_a_400_that_says_how_to_allow_it(
    sandbox: Path, config_file: Path
) -> None:
    configure(config_file, allowed_hosts=["console.example.com"])
    client = client_for(sandbox)

    response = client.get("/health", headers={"Host": "attacker.example.net"})

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "host_not_allowed"
    assert "attacker.example.net" in body["detail"]
    assert "web.allowed_hosts" in body["hint"]


def test_a_listed_host_is_answered(sandbox: Path, config_file: Path) -> None:
    configure(config_file, allowed_hosts=["console.example.com"])
    client = client_for(sandbox)

    assert client.get("/health", headers={"Host": "console.example.com"}).status_code == 200
    assert client.get("/health", headers={"Host": "console.example.com:8443"}).status_code == 200


def test_loopback_is_answered_whatever_the_list_says(sandbox: Path, config_file: Path) -> None:
    configure(config_file, allowed_hosts=["console.example.com"])
    client = client_for(sandbox)

    for host in ("127.0.0.1:8080", "localhost:8080", "[::1]:8080"):
        assert client.get("/health", headers={"Host": host}).status_code == 200, host


def test_the_public_url_is_answered_without_being_listed(sandbox: Path, config_file: Path) -> None:
    configure(
        config_file,
        allowed_hosts=["other.example.com"],
        public_url="https://console.example.com",
        hooks_url="https://hooks.example.com/hooks",
    )
    client = client_for(sandbox)

    assert client.get("/health", headers={"Host": "console.example.com"}).status_code == 200
    assert client.get("/health", headers={"Host": "hooks.example.com"}).status_code == 200
    assert client.get("/health", headers={"Host": "elsewhere.example.com"}).status_code == 400


def test_the_hooks_are_refused_for_a_stranger_too(sandbox: Path, config_file: Path) -> None:
    configure(config_file, allowed_hosts=["console.example.com"])
    client = client_for(sandbox)

    response = client.post(
        "/hooks/deploy/app.example.com", content=b"{}", headers={"Host": "stranger.example.net"}
    )

    assert response.status_code == 400


def test_a_websocket_handshake_for_a_stranger_is_refused(sandbox: Path, config_file: Path) -> None:
    configure(config_file, allowed_hosts=["console.example.com"])
    client = client_for(sandbox)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/events", headers={"Host": "stranger.example.net"}):
            pass


def test_a_refusal_is_audited(sandbox: Path, config_file: Path) -> None:
    configure(config_file, allowed_hosts=["console.example.com"])
    client = client_for(sandbox)

    client.get("/health", headers={"Host": "stranger.example.net"})

    lines = (sandbox / "state" / "web-audit.log").read_text().splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    assert any(
        event["result"] == "denied" and event["detail"] == "Host not allowed" for event in events
    )


def test_a_change_saved_to_the_configuration_applies_to_the_running_console(
    sandbox: Path, config_file: Path
) -> None:
    client = client_for(sandbox)
    assert client.get("/health", headers={"Host": "a.example.net"}).status_code == 200

    Config().set("web.allowed_hosts", ["console.example.com"])

    assert client.get("/health", headers={"Host": "a.example.net"}).status_code == 400
