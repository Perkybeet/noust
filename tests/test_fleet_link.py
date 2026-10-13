"""
Tests for servers that dial in to their central: the code, the account, the invitation, the key.

What is pinned: that a link code is parsed as strictly as a join code and never shows
its token; that the account servers dial in as is restricted in sshd (remote forward
of a loopback port, nothing else), checked with ``sshd -T`` and put back when sshd
refuses or applies something else; that an invitation gives a server one loopback port,
never one another holds, and the command carries the central's host key; that a code
answers its own invitation and no other; that the server's key may listen on its port
and no other; that nothing is kept unless the server connects and answers with its token;
and that the central sends nothing to a port whose listener is not the link account's.
"""

from __future__ import annotations

import stat
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.exceptions import NodeError, NodeUnreachableError
from noust.core.store import NoustStore
from noust.fleet.link.central import (
    INVITE_TTL,
    LINK_POLICY_BEGIN,
    NO_OPEN,
    authorized_link_line,
    link_account_policy,
    link_policy_block,
    link_policy_problems,
)
from noust.fleet.link.code import LINK_PREFIX, LinkCode
from noust.fleet.link.listener import (
    LINK_USER,
    link_account_uids,
    listener_uids,
    require_trusted_listener,
)
from noust.fleet.link.records import ACTIVE, INVITED, PORT_FIRST, LinkRecords
from noust.fleet.models import parse_public_key
from tests.fleet_support import HOST_KEY, TOKEN, decoded, ed25519_line, fingerprint
from tests.link_support import (
    LINK_KEY,
    LINK_UID,
    NONCE,
    Central,
    LinkRig,
    build_link_rig,
    refused,
)


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.link.central.central_name", lambda: "nas")
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    monkeypatch.setattr("noust.fleet.nodes.getpass.getuser", lambda: "root")


@pytest.fixture
def rig(tmp_path: Path):
    built = build_link_rig(tmp_path)
    yield built
    NoustStore.reset_instance()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []

    def capture(event: str, **kwargs: Any) -> None:
        recorded.append((event, kwargs))

    monkeypatch.setattr(audit, "record", capture)
    return recorded


def _bare(line: str) -> str:
    return " ".join(line.split()[:2])


class TestLinkCode:
    def make(self, **overrides: Any) -> LinkCode:
        values: dict[str, Any] = {
            "node_name": "web-5",
            "central": "nas",
            "link_key": _bare(LINK_KEY),
            "console_port": 8080,
            "token": TOKEN,
            "noust_version": "3.4.0",
            "nonce": NONCE,
            "token_name": "fleet-nas",
        }
        values.update(overrides)
        return LinkCode(**values)

    def test_it_round_trips(self):
        code = self.make()
        assert LinkCode.decode(code.encode()) == code
        assert code.encode().startswith(LINK_PREFIX)

    def test_the_token_is_never_in_what_prints(self):
        code = self.make()
        assert TOKEN not in repr(code)
        assert TOKEN not in str(code)

    def test_a_join_code_is_not_a_link_code_and_says_so(self):
        with pytest.raises(NodeError) as caught:
            LinkCode.decode("noust-join:v1:abc")
        assert "join code" in caught.value.message

    def test_a_newer_version_asks_to_upgrade(self):
        with pytest.raises(NodeError) as caught:
            LinkCode.decode("noust-link:v2:abc")
        assert "newer Noust" in caught.value.message

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda doc: doc.update(extra="x"),
            lambda doc: doc.update(token="not-a-token"),
            lambda doc: doc.update(nonce="short"),
            lambda doc: doc.update(link_key="ssh-rsa AAAA nope"),
            lambda doc: doc.update(console_port=0),
            lambda doc: doc.update(node_name="Web 5"),
            lambda doc: doc.update(token_name="other"),
            lambda doc: doc.pop("nonce"),
        ],
    )
    def test_every_field_is_checked(self, mutate):
        import base64
        import json

        document = decoded(self.make().encode())
        mutate(document)
        raw = json.dumps(document).encode()
        text = LINK_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")
        with pytest.raises(NodeError):
            LinkCode.decode(text)

    def test_what_is_not_a_code_is_refused_without_repeating_it(self):
        with pytest.raises(NodeError) as caught:
            LinkCode.decode("noust_" + "tok_" + "z" * 40)
        assert "z" * 40 not in caught.value.message + caught.value.details


class TestListener:
    def test_it_names_the_owner_of_a_listener(self, tmp_path):
        central = Central(tmp_path)
        central.listen(24001, uid=LINK_UID)
        assert listener_uids(24001, central.proc_net) == {LINK_UID}
        assert listener_uids(24002, central.proc_net) == set()

    def test_a_listener_on_every_address_would_answer_too(self, tmp_path):
        central = Central(tmp_path)
        central.listen(24001, uid=1001, host="0.0.0.0")  # noqa: S104 - a socket table entry for the test, not a bind
        assert listener_uids(24001, central.proc_net) == {1001}

    def test_only_the_link_account_s_listener_is_trusted(self, tmp_path):
        central = Central(tmp_path)
        allowed = frozenset({0, LINK_UID})
        central.listen(24001, uid=LINK_UID)
        require_trusted_listener("web-5", 24001, allowed, central.proc_net)

        central.listen(24001, uid=1001)
        with pytest.raises(NodeUnreachableError) as caught:
            require_trusted_listener("web-5", 24001, allowed, central.proc_net)
        assert "Something else listens" in caught.value.message

    def test_a_port_nobody_listens_on_means_the_server_is_not_connected(self, tmp_path):
        central = Central(tmp_path)
        with pytest.raises(NodeUnreachableError) as caught:
            require_trusted_listener("web-5", 24001, frozenset({0}), central.proc_net)
        assert "not connected" in caught.value.message

    def test_the_trusted_accounts_are_root_and_the_link_account(self, tmp_path):
        central = Central(tmp_path)
        assert link_account_uids(central.passwd) == frozenset({0})
        central.add_account(LINK_USER)
        assert link_account_uids(central.passwd) == frozenset({0, LINK_UID})


class TestAccountPolicy:
    def test_the_block_allows_a_remote_forward_and_nothing_else(self):
        block = link_policy_block(LINK_USER, Path("/etc/ssh/noust"))
        lines = {line.strip() for line in block.splitlines()}
        assert block.startswith(f"Match User {LINK_USER}\n")
        assert {
            "AllowTcpForwarding remote",
            "AllowStreamLocalForwarding no",
            "PermitOpen none",
            "PermitListen 127.0.0.1:*",
            "GatewayPorts no",
            "PermitTTY no",
            "X11Forwarding no",
            "AllowAgentForwarding no",
            "PermitUserRC no",
            "ForceCommand /usr/bin/false",
            "AuthenticationMethods publickey",
            "AuthorizedKeysFile /etc/ssh/noust/%u.keys",
        } <= lines

    def test_a_setting_another_block_wins_is_named(self, tmp_path):
        from noust.fleet.authorize import SshdSettings

        settings = SshdSettings(allow_tcp_forwarding="yes", permit_listen=("any",))
        problems = link_policy_problems(settings, Path("/etc/ssh/noust"))
        joined = " ".join(problems)
        assert "AllowTcpForwarding yes" in joined
        assert "PermitListen any" in joined

    def test_the_policy_is_its_own_fragment(self):
        policy = link_account_policy()
        assert policy.dropin_name == "00-noust-link.conf"
        assert policy.begin == LINK_POLICY_BEGIN


class TestSetup:
    def test_it_creates_the_account_and_restricts_it_in_sshd(self, rig: LinkRig, tmp_path):
        central = Central(tmp_path / "second")  # a bare central
        manager = central.manager(rig.fleet.store)

        result = manager.setup()

        assert result.account_created
        assert result.changed
        assert result.ssh_port == 2222
        assert result.host_key_fingerprint == fingerprint(HOST_KEY)
        useradd = central.runner.calls_to("useradd")[0]
        assert "--no-create-home" in useradd
        assert useradd[useradd.index("--shell") + 1].endswith("nologin")
        assert central.runner.calls_to("usermod") == [("usermod", "--password", "*", LINK_USER)]
        text = central.dropin.read_text()
        assert text.endswith(link_policy_block(LINK_USER, central.keys_dir))
        assert ("sshd", "-t") in central.runner.calls
        assert manager.status().ready

    def test_asking_again_changes_nothing(self, rig: LinkRig):
        before = rig.central.dropin.read_text()
        result = rig.links.setup()
        assert not result.changed
        assert not result.account_created
        assert rig.central.dropin.read_text() == before

    def test_sshd_applying_something_else_puts_the_file_back(self, tmp_path):
        central = Central(tmp_path)
        central.runner.ignore_policy = True
        store = NoustStore(tmp_path / "noust.db")
        try:
            error = refused(lambda: central.manager(store).setup())
        finally:
            NoustStore.reset_instance()
        assert "does not apply" in error.message
        assert not central.dropin.exists()

    def test_a_rehearsal_writes_nothing(self, tmp_path, events):
        central = Central(tmp_path)
        store = NoustStore(tmp_path / "noust.db")
        try:
            result = central.manager(store).setup(dry_run=True)
        finally:
            NoustStore.reset_instance()
        assert result.sshd_policy == str(central.dropin)
        assert not central.dropin.exists()
        assert ("sshd", "-t") not in central.runner.calls
        assert events == []

    def test_an_account_that_can_log_in_is_refused(self, tmp_path):
        central = Central(tmp_path)
        central.add_account(LINK_USER, login="/bin/bash")
        store = NoustStore(tmp_path / "noust.db")
        try:
            error = refused(lambda: central.manager(store).setup())
        finally:
            NoustStore.reset_instance()
        assert "can log in" in error.message

    def test_an_allow_list_that_leaves_the_account_out_is_refused_up_front(self, tmp_path):
        central = Central(tmp_path)
        central.runner.config += "allowusers deploy\n"
        store = NoustStore(tmp_path / "noust.db")
        try:
            error = refused(lambda: central.manager(store).setup())
        finally:
            NoustStore.reset_instance()
        assert "AllowUsers" in error.details
        assert "noust node link setup" in error.details
        assert not central.dropin.exists()

    def test_a_central_without_a_host_key_cannot_take_servers(self, tmp_path):
        central = Central(tmp_path)
        central.host_key_file.unlink()
        store = NoustStore(tmp_path / "noust.db")
        try:
            error = refused(lambda: central.manager(store).setup())
        finally:
            NoustStore.reset_instance()
        assert "host key" in error.message


class TestStatus:
    def test_a_central_that_was_not_set_up_says_what_to_run(self, tmp_path):
        central = Central(tmp_path)
        store = NoustStore(tmp_path / "noust.db")
        try:
            status = central.manager(store).status()
        finally:
            NoustStore.reset_instance()
        assert not status.ready
        assert not status.account
        assert any("noust node link setup" in problem for problem in status.problems)
        assert status.setup_command == "noust node link setup"

    def test_a_prepared_central_is_ready_and_lists_its_links(self, rig: LinkRig):
        assert rig.links.status().ready
        rig.invite("web-5")
        status = rig.links.status()
        assert [row["node"] for row in status.links] == ["web-5"]
        assert status.links[0]["state"] == INVITED
        assert "nonce" not in status.links[0]
        assert status.host_key_fingerprint == fingerprint(HOST_KEY)


class TestInvite:
    def test_it_gives_the_server_a_port_and_the_command_to_run(self, rig: LinkRig, events):
        invite = rig.invite("web-5", actor="cli:root")

        assert invite.port == PORT_FIRST
        assert invite.host == "central.example.com"
        assert invite.ssh_port == 2222
        record = LinkRecords(rig.fleet.store).get("web-5")
        assert record is not None
        assert (record.state, record.port, record.nonce) == (INVITED, PORT_FIRST, invite.nonce)
        # The command is what the server runs; it carries the pinned key and the invitation.
        assert invite.command == (
            "noust fleet link --name nas --as web-5 --host central.example.com "
            f"--ssh-port 2222 --host-key '{_bare(HOST_KEY)}' --remote-port {PORT_FIRST} "
            f"--invite {invite.nonce}"
        )
        assert [event for event, _ in events] == ["fleet.link.invite"]
        assert events[-1][1]["target"] == "node:web-5"

    def test_each_server_gets_a_port_of_its_own(self, rig: LinkRig):
        first = rig.invite("web-5")
        second = rig.invite("web-6")
        assert first.port != second.port
        assert second.port == PORT_FIRST + 1

    def test_a_port_something_listens_on_is_skipped(self, rig: LinkRig):
        rig.central.listen(PORT_FIRST, uid=1001)
        assert rig.invite("web-5").port == PORT_FIRST + 1

    def test_a_port_can_be_asked_for_unless_it_is_taken_or_privileged(self, rig: LinkRig):
        assert rig.invite("web-5", port=30000).port == 30000
        assert "taken" in refused(lambda: rig.invite("web-6", port=30000)).message
        assert "privileged" in refused(lambda: rig.invite("web-6", port=443)).message

    def test_inviting_again_replaces_an_unanswered_invitation(self, rig: LinkRig):
        first = rig.invite("web-5")
        second = rig.invite("web-5")
        assert second.nonce != first.nonce
        assert second.port == first.port
        assert len(rig.links.records.list()) == 1

    def test_a_central_that_is_not_ready_cannot_invite(self, tmp_path):
        central = Central(tmp_path)
        store = NoustStore(tmp_path / "noust.db")
        try:
            error = refused(lambda: central.manager(store).invite("web-5", host="x.example.com"))
        finally:
            NoustStore.reset_instance()
        assert "not ready" in error.message
        assert "noust node link setup" in error.details

    def test_a_registered_node_cannot_be_invited_again(self, rig: LinkRig):
        rig.fleet.added("web-2")
        assert "already registered" in refused(lambda: rig.invite("web-2")).message

    @pytest.mark.parametrize(
        "host", ["", "a b", "x.example.com; reboot", "x'y", "-oProxyCommand=x"]
    )
    def test_a_host_that_could_not_be_one_is_refused(self, rig: LinkRig, host):
        refused(lambda: rig.invite("web-5", host=host))

    def test_an_address_is_accepted_as_it_is_or_in_brackets(self, rig: LinkRig):
        assert rig.invite("web-5", host="203.0.113.7").host == "203.0.113.7"
        assert rig.invite("web-6", host="[2001:db8::7]").host == "2001:db8::7"

    def test_a_name_that_is_not_a_name_is_refused(self, rig: LinkRig):
        refused(lambda: rig.invite("Web 5"))

    def test_an_invitation_can_be_cancelled_until_it_is_answered(self, rig: LinkRig, events):
        rig.invite("web-5")
        rig.links.cancel("web-5", actor="cli:root")
        assert rig.links.records.get("web-5") is None
        assert events[-1][0] == "fleet.link.cancel"
        assert "no invitation" in refused(lambda: rig.links.cancel("web-5")).message


class TestCheckCode:
    def test_a_code_answers_its_own_invitation(self, rig: LinkRig):
        invite = rig.invite("web-5")
        code, record = rig.links.check_code("web-5", rig.code(invite))
        assert code.node_name == "web-5"
        assert record.port == invite.port

    def test_a_code_for_another_invitation_is_refused(self, rig: LinkRig):
        invite = rig.invite("web-5")
        other = "f" * 32
        error = refused(lambda: rig.links.check_code("web-5", rig.code(invite, nonce=other)))
        assert "another invitation" in error.message

    def test_a_server_that_was_never_invited_is_refused(self, rig: LinkRig):
        invite = rig.invite("web-5")
        error = refused(lambda: rig.links.check_code("web-9", rig.code(invite, node_name="web-9")))
        assert "never invited" in error.message

    def test_a_code_for_another_name_is_refused(self, rig: LinkRig):
        invite = rig.invite("web-5")
        error = refused(lambda: rig.links.check_code("web-5", rig.code(invite, node_name="web-6")))
        assert "web-6" in error.message

    def test_a_code_made_for_another_central_is_refused(self, rig: LinkRig):
        invite = rig.invite("web-5")
        error = refused(lambda: rig.links.check_code("web-5", rig.code(invite, central="other")))
        assert "other" in error.message

    def test_an_expired_invitation_is_refused(self, rig: LinkRig):
        invite = rig.invite("web-5")
        rig.central.now += INVITE_TTL + timedelta(minutes=1)
        error = refused(lambda: rig.links.check_code("web-5", rig.code(invite)))
        assert "expired" in error.message


class TestAddLinked:
    def connect(self, rig: LinkRig, invite) -> None:
        """What the server's own ssh does once its key is installed: listen on its port."""
        rig.central.listen(invite.port, uid=LINK_UID)

    def test_a_server_that_connects_is_registered(self, rig: LinkRig, events):
        invite = rig.invite("web-5")
        self.connect(rig, invite)

        node = rig.manager.add_linked("web-5", link_code=rig.code(invite), wait=0)

        assert (node.name, node.status, node.version) == ("web-5", "reachable", "3.0.1")
        assert (node.ssh_host, node.ssh_port, node.ssh_user) == (
            "127.0.0.1",
            invite.port,
            LINK_USER,
        )
        assert node.console_port == 8080
        assert LinkRecords(rig.fleet.store).get("web-5").state == ACTIVE
        # Its token is a secret like any node's, and the central talked to it with the token.
        assert rig.fleet.node.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
        assert [event for event, _ in events][-1] == "fleet.link.accept"
        assert TOKEN not in str(events)

    def test_its_key_may_listen_on_its_port_and_nowhere_else(self, rig: LinkRig):
        invite = rig.invite("web-5")
        self.connect(rig, invite)

        rig.manager.add_linked("web-5", link_code=rig.code(invite), wait=0)

        path = rig.central.keys_file
        assert (
            path.read_text()
            == authorized_link_line("web-5", parse_public_key(LINK_KEY), invite.port) + "\n"
        )
        line = path.read_text()
        assert f'permitlisten="127.0.0.1:{invite.port}"' in line
        assert f'permitopen="{NO_OPEN}"' in line
        assert line.startswith("restrict,port-forwarding,")
        assert 'command="/usr/bin/false"' in line
        # root's, readable by sshd as the account.
        assert stat.S_IMODE(path.stat().st_mode) == 0o644

    def test_another_server_s_key_cannot_take_its_port(self, rig: LinkRig):
        first = rig.invite("web-5")
        second = rig.invite("web-6")
        self.connect(rig, first)
        self.connect(rig, second)
        rig.manager.add_linked("web-5", link_code=rig.code(first), wait=0)
        rig.manager.add_linked(
            "web-6",
            link_code=rig.code(second, link_key=_bare(ed25519_line(42))),
            wait=0,
        )

        lines = rig.central.keys_file.read_text().splitlines()
        assert [line.rsplit(" ", 1)[1] for line in lines] == [
            "noust-link:web-5",
            "noust-link:web-6",
        ]
        assert f'permitlisten="127.0.0.1:{first.port}"' in lines[0]
        assert f'permitlisten="127.0.0.1:{second.port}"' in lines[1]

    def test_a_server_that_does_not_connect_leaves_nothing_and_can_try_again(self, rig: LinkRig):
        invite = rig.invite("web-5")

        error = refused(lambda: rig.manager.add_linked("web-5", link_code=rig.code(invite), wait=0))

        assert isinstance(error, NodeUnreachableError)
        assert "did not connect" in error.message
        assert rig.central.keys_file.read_text() == ""
        assert rig.fleet.store.get_node("web-5") is None
        assert LinkRecords(rig.fleet.store).get("web-5").state == INVITED
        # The invitation is still answerable: the same code works once the server is dialling.
        self.connect(rig, invite)
        node = rig.manager.add_linked("web-5", link_code=rig.code(invite), wait=0)
        assert node.status == "reachable"

    def test_a_listener_that_is_not_the_link_account_s_gets_nothing(self, rig: LinkRig):
        invite = rig.invite("web-5")
        rig.central.listen(invite.port, uid=1001)

        error = refused(lambda: rig.manager.add_linked("web-5", link_code=rig.code(invite), wait=0))

        assert "did not connect" in error.message
        # No request, so no token, reached whatever listens there.
        assert rig.fleet.node.requests == []
        assert rig.fleet.store.get_node("web-5") is None

    def test_a_server_that_refuses_the_token_is_undone(self, rig: LinkRig):
        invite = rig.invite("web-5")
        self.connect(rig, invite)
        rig.fleet.node.token = "noust_" + "tok_" + "Y" * 43

        with pytest.raises(NodeError):
            rig.manager.add_linked("web-5", link_code=rig.code(invite), wait=0)

        assert rig.central.keys_file.read_text() == ""
        assert rig.fleet.store.get_node("web-5") is None
        assert LinkRecords(rig.fleet.store).get("web-5").state == INVITED

    def test_a_central_without_two_factor_registers_nothing(self, tmp_path):
        built = build_link_rig(tmp_path, blockers=lambda: ["Two-factor sign-in is not enabled."])
        try:
            invite = built.invite("web-5")
            built.central.listen(invite.port)
            error = refused(
                lambda: built.manager.add_linked("web-5", link_code=built.code(invite), wait=0)
            )
            assert "may not register" in error.message
            assert not built.central.keys_file.exists()
        finally:
            NoustStore.reset_instance()

    def test_a_name_registered_since_the_invitation_is_refused(self, rig: LinkRig):
        invite = rig.invite("web-5")
        rig.fleet.added("web-5")
        error = refused(lambda: rig.manager.add_linked("web-5", link_code=rig.code(invite), wait=0))
        assert "already registered" in error.message

    def test_removing_it_takes_its_key_out_and_says_how_to_unlink(self, rig: LinkRig):
        invite = rig.invite("web-5")
        self.connect(rig, invite)
        rig.manager.add_linked("web-5", link_code=rig.code(invite), wait=0)

        warnings = rig.manager.remove("web-5", revoke=False)

        assert rig.central.keys_file.read_text() == ""
        assert rig.fleet.store.get_node("web-5") is None
        assert LinkRecords(rig.fleet.store).get("web-5") is None
        assert any("noust fleet unlink --name nas" in line for line in warnings)

    def test_a_server_that_dials_in_has_no_ssh_key_to_rotate(self, rig: LinkRig):
        invite = rig.invite("web-5")
        self.connect(rig, invite)
        rig.manager.add_linked("web-5", link_code=rig.code(invite), wait=0)

        for call in (
            lambda: rig.manager.rekey_command("web-5"),
            lambda: rig.manager.migrate_tunnel_command("web-5"),
        ):
            error = refused(call)
            assert "dials in" in error.message
            assert "noust fleet unlink" in error.details


class TestTunnelEndpoint:
    def linked(self, rig: LinkRig) -> int:
        invite = rig.invite("web-5")
        rig.central.listen(invite.port, uid=LINK_UID)
        rig.manager.add_linked("web-5", link_code=rig.code(invite), wait=0)
        return invite.port

    def test_the_endpoint_is_the_port_the_link_leaves(self, rig: LinkRig):
        port = self.linked(rig)
        assert rig.tunnels.endpoint("web-5") == ("127.0.0.1", port)
        assert rig.fleet.runner.calls_to("ssh") == []

    def test_status_says_it_is_a_link_and_whether_it_is_up(self, rig: LinkRig):
        port = self.linked(rig)
        status = rig.tunnels.status("web-5")
        assert (status["open"], status["local_port"], status["transport"]) == (True, port, "link")

        rig.central.hang_up(port)
        status = rig.tunnels.status("web-5")
        assert (status["open"], status["local_port"]) == (False, None)

    def test_a_link_that_dropped_is_unreachable_with_what_to_check(self, rig: LinkRig, caplog):
        port = self.linked(rig)
        rig.central.hang_up(port)

        with pytest.raises(NodeUnreachableError) as caught:
            rig.tunnels.endpoint("web-5")

        assert "not connected" in caught.value.message
        assert "systemctl status noust-link" in caught.value.details
        assert rig.tunnels.status("web-5")["last_error"]

    def test_an_account_that_took_the_port_is_never_sent_the_token(self, rig: LinkRig):
        port = self.linked(rig)
        rig.central.hang_up(port)
        rig.central.listen(port, uid=1001)
        rig.fleet.node.requests.clear()

        with pytest.raises(NodeUnreachableError) as caught:
            rig.manager.client("web-5").get_json("/api/system/version")

        assert "Something else listens" in caught.value.message
        assert rig.fleet.node.requests == []

    def test_a_node_reached_over_ssh_is_not_affected(self, rig: LinkRig):
        rig.fleet.added("web-2")
        assert rig.fleet.tunnels.status("web-2")["open"] in (True, False)
        assert "transport" not in rig.fleet.tunnels.status("web-2")
