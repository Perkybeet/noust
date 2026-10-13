# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a node lets a central do, read from the configuration Noust writes (checklist L-49).

The fleet's central promise (docs/SECURITY-MODEL.md, guarantee 3) is that a
compromised central gets no shell on a node, no forwarding to anything but the
console, and no more than the node's own ceiling. The ceiling is tested in
``test_fleet_access.py`` and the host-key pin in ``test_fleet_tunnels.py``. This
file holds the other half: the sshd block and the key line that make "no shell"
true, read the way sshd reads them, by their meaning and not by their text, and
attacked with an account name and a central name that try to add a directive.

An external review of the tunnel has not been done; these are the properties
such a review would try to break.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from noust.core.exceptions import NodeError
from noust.fleet.authorize import (
    FORCED_COMMAND,
    NO_LISTEN,
    TUNNEL_USER,
    authorized_key_line,
    has_tunnel_options,
    tunnel_policy_block,
)
from noust.fleet.models import PublicKey

KEY = PublicKey(
    key_type="ssh-ed25519",
    blob="AAAAC3NzaC1lZDI1NTE5AAAAIPlaceholderPlaceholderPlaceholderPlacehol",
)


def directives(block: str) -> dict[str, list[str]]:
    """Read a ``Match`` block as sshd reads it: keyword (lowercased) to its values."""
    found: dict[str, list[str]] = {}
    for line in block.splitlines()[1:]:
        keyword, _, value = line.strip().partition(" ")
        found.setdefault(keyword.lower(), []).append(value.strip())
    return found


class TestTheSshdBlock:
    @pytest.fixture
    def block(self) -> str:
        return tunnel_policy_block(TUNNEL_USER, 8443)

    def test_it_applies_to_the_tunnel_account_and_to_nothing_else(self, block: str) -> None:
        assert block.splitlines()[0] == f"Match User {TUNNEL_USER}"
        assert [line for line in block.splitlines() if line.lower().startswith("match")] == [
            block.splitlines()[0]
        ]

    def test_there_is_no_shell_no_terminal_and_no_command_of_its_own(self, block: str) -> None:
        found = directives(block)

        assert found["forcecommand"] == [FORCED_COMMAND] == ["/usr/bin/false"]
        assert found["permittty"] == ["no"]
        assert found["permituserrc"] == ["no"]
        assert found["authenticationmethods"] == ["publickey"]

    def test_the_only_forwarding_is_local_and_to_the_console_port(self, block: str) -> None:
        found = directives(block)

        assert found["allowtcpforwarding"] == ["local"]
        assert found["permitopen"] == ["127.0.0.1:8443"]
        assert found["permitlisten"] == ["none"]
        assert found["gatewayports"] == ["no"]

    def test_unix_socket_forwarding_is_off_because_permitlisten_does_not_cover_it(
        self, block: str
    ) -> None:
        # A remote forward to a Unix socket can create one as root at any path
        # when the key is in root's authorized_keys: the 3.0 flaw the account fixes.
        assert directives(block)["allowstreamlocalforwarding"] == ["no"]

    def test_agent_x11_and_tunnel_devices_are_off(self, block: str) -> None:
        found = directives(block)

        assert found["allowagentforwarding"] == ["no"]
        assert found["x11forwarding"] == ["no"]
        assert found["permittunnel"] == ["no"]

    def test_no_directive_is_set_twice_so_a_later_line_cannot_loosen_an_earlier_one(
        self, block: str
    ) -> None:
        repeated = {key for key, values in directives(block).items() if len(values) > 1}

        assert repeated == set()

    def test_nothing_it_grants_is_a_yes_but_a_public_key(self, block: str) -> None:
        granted = {key for key, values in directives(block).items() if values == ["yes"]}

        assert granted == {"pubkeyauthentication"}

    @settings(max_examples=100, deadline=None)
    @given(port=st.integers(min_value=1, max_value=65535))
    def test_whatever_the_console_port_is_it_is_the_one_place_reachable(self, port: int) -> None:
        found = directives(tunnel_policy_block(TUNNEL_USER, port))

        assert found["permitopen"] == [f"127.0.0.1:{port}"]
        assert found["forcecommand"] == ["/usr/bin/false"]

    @pytest.mark.parametrize(
        "user",
        [
            "noust\nPermitRootLogin yes",
            "noust\r\nForceCommand /bin/sh",
            "noust-tunnel *",
            "root,noust",
            "!root",
            "a b",
            "",
            "x" * 64,
        ],
    )
    def test_an_account_name_cannot_add_a_directive_or_widen_the_match(self, user: str) -> None:
        with pytest.raises(Exception, match="Invalid SSH user"):
            tunnel_policy_block(user, 8443)

    @pytest.mark.parametrize("port", [0, -1, 65536, "8443; PermitOpen any", None, True, 8443.0])
    def test_a_console_port_is_a_port_or_it_is_refused(self, port: object) -> None:
        with pytest.raises(NodeError, match="console port"):
            tunnel_policy_block(TUNNEL_USER, port)  # type: ignore[arg-type]

        with pytest.raises(NodeError, match="console port"):
            authorized_key_line("central-1", KEY, port)  # type: ignore[arg-type]


class TestTheKeyLine:
    def test_the_options_are_exactly_those_that_take_everything_away_and_give_back_one(
        self,
    ) -> None:
        line = authorized_key_line("central-1", KEY, 8443)
        options, _, rest = line.partition(" ssh-ed25519 ")

        assert options == (
            f'restrict,port-forwarding,permitopen="127.0.0.1:8443",'
            f'permitlisten="{NO_LISTEN}",command="{FORCED_COMMAND}"'
        )
        assert rest.startswith(KEY.blob)

    def test_it_is_recognised_as_a_line_noust_wrote(self) -> None:
        assert has_tunnel_options(authorized_key_line("central-1", KEY, 8443))

    def test_listening_is_pinned_to_a_port_nothing_uses(self) -> None:
        assert NO_LISTEN == "127.0.0.1:1"

    @pytest.mark.parametrize(
        "name",
        [
            'x"\nrestrict',
            "x\nssh-rsa AAAA evil",
            "x y",
            'x",command="/bin/sh',
            "",
            "../x",
        ],
    )
    def test_a_central_name_cannot_close_the_options_or_start_another_key(self, name: str) -> None:
        with pytest.raises(Exception, match="central"):
            authorized_key_line(name, KEY, 8443)

    def test_one_line_comes_out_for_one_key(self) -> None:
        line = authorized_key_line("central-1", KEY, 8443)

        assert "\n" not in line and "\r" not in line
        assert line.count("ssh-ed25519") == 1
