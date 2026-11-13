# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The client a credential says it is (items 184 and 185): a name and a version, as the client says them.

What a client says of itself decides nothing and is shown as what it said: it is the ``clientInfo`` of MCP's
``initialize`` or the product of a ``User-Agent``, kept a few to a credential, cut to a size and made of
characters a person can read.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import credential_clients as cc
from noust.core.credential_clients import (
    SOURCE_CLIENT_INFO,
    SOURCE_USER_AGENT,
    CredentialClients,
    client_label,
    parse_user_agent,
)
from noust.core.store import NoustStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def clients(store: NoustStore, clock: Clock) -> CredentialClients:
    return CredentialClients(store, clock=clock)


class TestTheUserAgent:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("curl/8.5.0", ("curl", "8.5.0")),
            ("python-requests/2.31.0", ("python-requests", "2.31.0")),
            ("mcp-client/2.1.0 (cli)", ("mcp-client", "2.1.0")),
            ("Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101", ("Mozilla", "5.0")),
            ("my-pipeline", ("my-pipeline", "")),
            ("  deploy-bot/1.2  ", ("deploy-bot", "1.2")),
        ],
    )
    def test_the_first_product_is_the_client(self, header: str, expected: tuple[str, str]) -> None:
        assert parse_user_agent(header) == expected

    @pytest.mark.parametrize("header", [None, "", "   ", "noust-mcp", "/1.0", "\x00\x01"])
    def test_what_says_nothing_is_not_a_client(self, header: str | None) -> None:
        assert parse_user_agent(header) is None

    def test_the_gateway_of_noust_is_not_a_client(self) -> None:
        # The MCP server calls the API as the caller, with its own user agent: the client is who
        # called the MCP server.
        assert parse_user_agent("noust-mcp") is None
        assert parse_user_agent("noust-mcp/1.0") is None

    def test_it_is_cut_to_a_size_and_to_what_a_person_can_read(self) -> None:
        name, version = parse_user_agent("a" * 500 + "/" + "9" * 500) or ("", "")
        assert len(name) <= cc.MAX_NAME and len(version) <= cc.MAX_VERSION

    def test_a_line_break_does_not_start_another_line(self) -> None:
        found = parse_user_agent("tool/1.0\r\nX-Injected: yes")
        assert found == ("tool", "1.0")

    def test_the_label_says_it_is_what_the_client_said(self) -> None:
        assert client_label("curl", "8.5.0") == "curl 8.5.0"
        assert client_label("curl", "") == "curl"


class TestWhatIsKept:
    def test_a_client_is_kept_with_when_it_was_first_and_last_seen(
        self, clients: CredentialClients, clock: Clock
    ) -> None:
        assert clients.note("ci", SOURCE_USER_AGENT, "curl", "8.5.0") is True
        clock.now += 3600
        assert clients.note("ci", SOURCE_USER_AGENT, "curl", "8.5.0") is True
        (found,) = clients.seen("ci")
        assert (found.name, found.version, found.source) == ("curl", "8.5.0", SOURCE_USER_AGENT)
        assert found.last_seen > found.first_seen

    def test_the_same_client_seen_again_within_minutes_is_not_written_again(
        self, clients: CredentialClients, clock: Clock, store: NoustStore
    ) -> None:
        assert clients.note("ci", SOURCE_USER_AGENT, "curl", "8.5.0") is True
        clock.now += 5
        assert clients.note("ci", SOURCE_USER_AGENT, "curl", "8.5.0") is False

    def test_a_credential_keeps_a_few_clients_and_forgets_the_oldest(
        self, clients: CredentialClients, clock: Clock
    ) -> None:
        for number in range(cc.MAX_PER_CREDENTIAL + 5):
            clock.now += 10_000
            clients.note("ci", SOURCE_USER_AGENT, f"tool-{number}", "1")
        seen = clients.seen("ci", limit=100)
        assert len(seen) == cc.MAX_PER_CREDENTIAL
        assert seen[0].name == f"tool-{cc.MAX_PER_CREDENTIAL + 4}"
        assert "tool-0" not in {item.name for item in seen}

    def test_two_credentials_do_not_share_clients(self, clients: CredentialClients) -> None:
        clients.note("ci", SOURCE_USER_AGENT, "curl", "8")
        assert clients.seen("other") == []

    def test_the_name_of_the_client_in_the_mcp_handshake_is_kept_as_it_says(
        self, clients: CredentialClients
    ) -> None:
        assert clients.note_client_info("ci", {"name": "mcp-client", "version": "2.1.0"})
        (found,) = clients.seen("ci")
        assert (found.source, found.name, found.version) == (
            SOURCE_CLIENT_INFO,
            "mcp-client",
            "2.1.0",
        )

    @pytest.mark.parametrize(
        "info", [None, "x", 3, {}, {"name": ""}, {"name": None}, {"name": "\x00"}, {"version": "1"}]
    )
    def test_a_handshake_with_no_usable_name_keeps_nothing(
        self, clients: CredentialClients, info: object
    ) -> None:
        assert clients.note_client_info("ci", info) is False  # type: ignore[arg-type]
        assert clients.seen("ci") == []

    def test_the_user_agent_of_a_request_is_parsed_and_kept(
        self, clients: CredentialClients
    ) -> None:
        assert clients.note_user_agent("ci", "deploy-bot/1.2 (linux)")
        assert [(c.name, c.version) for c in clients.seen("ci")] == [("deploy-bot", "1.2")]
        assert clients.note_user_agent("ci", "noust-mcp") is False

    def test_a_name_that_is_not_text_a_person_can_read_is_refused(
        self, clients: CredentialClients
    ) -> None:
        assert clients.note("ci", SOURCE_USER_AGENT, "bad\nname", "1") is False
        assert clients.note("ci", "somewhere", "curl", "1") is False
        assert clients.note("", SOURCE_USER_AGENT, "curl", "1") is False


class TestWhichClientToShow:
    def test_what_the_handshake_said_wins_over_the_user_agent(
        self, clients: CredentialClients, clock: Clock
    ) -> None:
        clients.note("ci", SOURCE_USER_AGENT, "node", "22")
        clock.now += 10_000
        clients.note("ci", SOURCE_CLIENT_INFO, "mcp-client", "2.1.0")
        clock.now += 10_000
        clients.note("ci", SOURCE_USER_AGENT, "node", "22")
        current = clients.current("ci")
        assert current is not None
        assert (current.name, current.source) == ("mcp-client", SOURCE_CLIENT_INFO)

    def test_without_a_handshake_it_is_the_newest_user_agent(
        self, clients: CredentialClients, clock: Clock
    ) -> None:
        clients.note("ci", SOURCE_USER_AGENT, "curl", "7")
        clock.now += 10_000
        clients.note("ci", SOURCE_USER_AGENT, "deploy-bot", "1")
        current = clients.current("ci")
        assert current is not None and current.name == "deploy-bot"

    def test_a_credential_nobody_saw_has_none(self, clients: CredentialClients) -> None:
        assert clients.current("ghost") is None

    def test_it_is_shown_as_what_the_client_said(self, clients: CredentialClients) -> None:
        clients.note("ci", SOURCE_CLIENT_INFO, "mcp-client", "2.1.0")
        current = clients.current("ci")
        assert current is not None
        assert current.to_dict() == {
            "name": "mcp-client",
            "version": "2.1.0",
            "source": SOURCE_CLIENT_INFO,
            "label": "mcp-client 2.1.0",
            "declared": True,
            "first_seen": current.first_seen,
            "last_seen": current.last_seen,
        }
