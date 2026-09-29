"""
Tests for the fleet's validated values and the join code.

Each of these values ends up somewhere that interprets it - a secret's path,
ssh's argv, ``authorized_keys`` - so the refusals matter as much as the
round trips.
"""

from __future__ import annotations

import base64
import json

import pytest

from noust.core.exceptions import NodeError
from noust.fleet.joincode import JOIN_PREFIX, MAX_JOIN_CODE, JoinCode
from noust.fleet.models import (
    parse_public_key,
    parse_ssh_target,
    validate_central_name,
    validate_node_name,
)
from tests.fleet_support import HOST_KEY, TOKEN, decoded, ed25519_line, fingerprint, join_code

CENTRAL_KEY = ed25519_line(7, "noust-central@nas")


class TestNames:
    @pytest.mark.parametrize("name", ["web-2", "a", "db1", "x" * 32])
    def test_valid_names(self, name):
        assert validate_node_name(name) == name
        assert validate_central_name(name) == name

    @pytest.mark.parametrize(
        "name", ["", "-web", "web-", "Web", "web.2", "web/2", "../x", "x" * 33, "a b", "web\n"]
    )
    def test_invalid_names(self, name):
        with pytest.raises(NodeError):
            validate_node_name(name)
        with pytest.raises(NodeError):
            validate_central_name(name)


class TestSSHTarget:
    @pytest.mark.parametrize(
        ("text", "user", "host", "port"),
        [
            ("root@web2.example.com", "root", "web2.example.com", None),
            ("web2.example.com:2222", None, "web2.example.com", 2222),
            ("deploy@10.0.0.2:22", "deploy", "10.0.0.2", 22),
            ("root@[2001:db8::1]:2200", "root", "2001:db8::1", 2200),
            ("2001:db8::1", None, "2001:db8::1", None),
        ],
    )
    def test_parses(self, text, user, host, port):
        target = parse_ssh_target(text)
        assert (target.user, target.host, target.port) == (user, host, port)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "root@",
            "-oProxyCommand=x",
            "root@-oProxyCommand=x",
            "host:0",
            "host:99999",
            "host:ab",
            "Root User@host",
            "[::1",
            "ho st",
        ],
    )
    def test_refuses(self, text):
        with pytest.raises(NodeError):
            parse_ssh_target(text)


class TestPublicKey:
    def test_parses_and_fingerprints_like_ssh_keygen(self):
        key = parse_public_key(CENTRAL_KEY)

        assert key.key_type == "ssh-ed25519"
        assert key.comment == "noust-central@nas"
        assert key.fingerprint == fingerprint(CENTRAL_KEY)
        assert key.line() == CENTRAL_KEY
        assert key.bare == " ".join(CENTRAL_KEY.split()[:2])

    def test_a_comment_with_odd_characters_is_dropped(self):
        key = parse_public_key(ed25519_line(7, 'evil"comment'))
        assert key.comment == ""

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ user",
            'restrict,command="x" ' + CENTRAL_KEY,
            CENTRAL_KEY + "\nssh-ed25519 AAAA second",
            "ssh-ed25519 not-base64!!",
            # Valid base64, but an ecdsa blob wearing an ed25519 label.
            "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x13ecdsa-sha2-nistp256").decode(),
            # The right type inside, a key of the wrong length.
            "ssh-ed25519 "
            + base64.b64encode(
                b"\x00\x00\x00\x0bssh-ed25519" + b"\x00\x00\x00\x10" + b"k" * 16
            ).decode(),
            "ssh-ed25519 " + "A" * 2000,
        ],
    )
    def test_refuses_anything_but_one_clean_ed25519_key(self, line):
        with pytest.raises(NodeError):
            parse_public_key(line)


class TestJoinCode:
    def test_round_trip(self):
        code = join_code(CENTRAL_KEY)

        parsed = JoinCode.decode("  " + code + "\n")

        assert code.startswith(JOIN_PREFIX)
        assert parsed.ssh_host_key == HOST_KEY
        assert (parsed.ssh_user, parsed.ssh_port, parsed.console_port) == ("root", 22, 8080)
        assert parsed.token == TOKEN
        assert parsed.central_key_fp == fingerprint(CENTRAL_KEY)
        assert (parsed.node_name, parsed.token_name, parsed.central) == (
            "web-2",
            "fleet-nas",
            "nas",
        )
        assert parsed.encode() == code

    def test_the_token_never_shows_in_its_repr(self):
        parsed = JoinCode.decode(join_code(CENTRAL_KEY))
        assert TOKEN not in repr(parsed)
        assert TOKEN not in str(parsed)

    def test_optional_fields_may_be_absent(self):
        code = join_code(CENTRAL_KEY, node_name=None, token_name=None, central=None)
        parsed = JoinCode.decode(code)
        assert (parsed.node_name, parsed.token_name, parsed.central) == (None, None, None)
        assert "node_name" not in decoded(code)

    @staticmethod
    def _encode(document: dict) -> str:
        raw = json.dumps(document).encode()
        return JOIN_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def _valid_document(self) -> dict:
        return decoded(join_code(CENTRAL_KEY))

    @pytest.mark.parametrize(
        ("change", "expected"),
        [
            (lambda d: d.pop("token"), "lacks token"),
            (lambda d: d.update(extra="x"), "unknown fields"),
            (lambda d: d.update(token="noust_" + "tok_short"), "not a fleet token"),
            (lambda d: d.update(token="wasm_" + "tok_" + "a" * 40), "not a fleet token"),
            (lambda d: d.update(ssh_port="22"), "Invalid SSH port"),
            (lambda d: d.update(ssh_port=True), "Invalid SSH port"),
            (lambda d: d.update(console_port=70000), "Invalid console port"),
            (lambda d: d.update(ssh_user="root; rm"), "Invalid SSH user"),
            (lambda d: d.update(ssh_host_key_line="ssh-rsa AAAA"), "not an ed25519 key"),
            (lambda d: d.update(noust_version="3.0.0 beta"), "version is malformed"),
            (lambda d: d.update(central_key_fp="MD5:aa"), "fingerprint is malformed"),
            (lambda d: d.update(node_name="Web 2"), "Invalid node name"),
            (lambda d: d.update(token_name="admin"), "token name is malformed"),
        ],
    )
    def test_rejects_each_malformed_field(self, change, expected):
        document = self._valid_document()
        change(document)

        with pytest.raises(NodeError) as caught:
            JoinCode.decode(self._encode(document))

        assert expected in str(caught.value)
        assert TOKEN not in str(caught.value)

    @pytest.mark.parametrize(
        "code",
        [
            "",
            "hello",
            JOIN_PREFIX,
            JOIN_PREFIX + "!!!",
            JOIN_PREFIX + base64.urlsafe_b64encode(b"[1,2]").decode(),
            JOIN_PREFIX + base64.urlsafe_b64encode(b"not json").decode(),
            JOIN_PREFIX + "A" * MAX_JOIN_CODE,
        ],
    )
    def test_rejects_what_is_not_a_code(self, code):
        with pytest.raises(NodeError):
            JoinCode.decode(code)

    def test_a_newer_version_says_to_upgrade(self):
        with pytest.raises(NodeError) as caught:
            JoinCode.decode("noust-join:v2:abcd")
        assert "newer Noust" in caught.value.message
