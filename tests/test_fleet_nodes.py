"""
Tests for the node registry and the client that talks to a node's API.

The rules that matter: nothing is kept unless the node answers with the token;
a join code for another key, or a host key that changed, is refused; the
token travels in a header and nowhere else; removing a node revokes its token
there; a central without two-factor sign-in registers nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from noust.core.exceptions import NodeError, NodeRefusedError, NodeUnreachableError
from noust.core.store import NoustStore
from noust.fleet import client as client_module
from noust.fleet.client import NodeClient, actor_label
from noust.fleet.keys import KNOWN_HOSTS, secret_name
from noust.fleet.keys import TOKEN as TOKEN_LEAF
from noust.fleet.policy import node_registration_blockers
from noust.fleet.status import fleet_status
from tests.fleet_support import (
    HOST_KEY,
    OTHER_TOKEN,
    TOKEN,
    build_fleet,
    ed25519_line,
    join_code,
)


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    monkeypatch.setattr("noust.fleet.nodes.getpass.getuser", lambda: "root")


@pytest.fixture
def fleet(tmp_path: Path):
    built = build_fleet(tmp_path)
    yield built
    NoustStore.reset_instance()


def _leftovers(fleet, name: str = "web-2") -> list[str]:
    found = []
    if fleet.store.get_node(name) is not None:
        found.append("row")
    for leaf in (TOKEN_LEAF, KNOWN_HOSTS):
        if fleet.secrets.read(secret_name(name, leaf)) is not None:
            found.append(leaf)
    if fleet.tunnels.status(name)["open"]:
        found.append("tunnel")
    return found


class TestClient:
    def test_the_token_travels_in_a_header_only(self, fleet, caplog):
        fleet.added()
        fleet.node.requests.clear()
        caplog.set_level(logging.DEBUG)

        fleet.manager.client("web-2").request(
            "GET", "/api/apps", params={"q": "x"}, actor="cli:root@nas"
        )

        request = fleet.node.requests[0]
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["X-Noust-Actor"] == "cli:root@nas"
        assert TOKEN not in str(request.url)
        assert TOKEN not in caplog.text
        assert TOKEN not in repr(fleet.manager.client("web-2"))

    def test_auth_headers(self, fleet):
        fleet.added()
        client = fleet.manager.client("web-2")

        assert client.auth_headers() == {"Authorization": f"Bearer {TOKEN}"}
        assert client.auth_headers("session:ab12", actor_scope="read", elevated=True) == {
            "Authorization": f"Bearer {TOKEN}",
            "X-Noust-Actor": "session:ab12",
            "X-Noust-Actor-Scope": "read",
            "X-Noust-Elevated": "1",
        }
        with pytest.raises(NodeError):
            client.auth_headers("two\nlines")
        with pytest.raises(NodeError):
            client.auth_headers("x", actor_scope="fleet")

    def test_header_names_match_what_the_node_reads(self):
        from noust.web import auth

        assert client_module.ACTOR_HEADER == auth.FLEET_ACTOR_HEADER
        assert client_module.ACTOR_SCOPE_HEADER == auth.FLEET_ACTOR_SCOPE_HEADER
        assert client_module.ELEVATED_HEADER == auth.FLEET_ELEVATED_HEADER
        assert client_module.ACTOR_PATTERN.pattern == auth.FLEET_ACTOR_PATTERN.pattern

    def test_actor_label_reduces_free_text(self):
        assert actor_label("cli:root@nas") == "cli:root@nas"
        assert actor_label("cli:J. Doe/x") == "cli:J.-Doe-x"
        assert actor_label("") == "unknown"

    def test_base_url_is_the_tunnel(self, fleet):
        fleet.added()
        assert fleet.manager.client("web-2").base_url().startswith("http://127.0.0.1:")

    def test_401_and_403_are_refusals(self, fleet):
        fleet.added()
        fleet.node.revoked = True

        with pytest.raises(NodeRefusedError) as caught:
            fleet.manager.client("web-2").get_json("/api/apps")

        assert caught.value.status_code == 401
        assert "Invalid token" in caught.value.details

    def test_connection_errors_are_unreachable(self, fleet):
        fleet.added()

        def broken(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        client = NodeClient(
            "web-2",
            tunnels=fleet.tunnels,
            secrets=fleet.secrets,
            transport=httpx.MockTransport(broken),
        )
        with pytest.raises(NodeUnreachableError) as caught:
            client.get_json("/api/apps")
        assert "Connection refused" in caught.value.details

    def test_error_statuses_carry_the_node_s_words(self, fleet):
        fleet.added()
        fleet.node.responses["/api/apps"] = (500, {"message": "nginx: [emerg] bad"})

        with pytest.raises(NodeError) as caught:
            fleet.manager.client("web-2").get_json("/api/apps")

        assert "nginx: [emerg] bad" in caught.value.details

    @pytest.mark.parametrize("path", ["api/apps", "//evil.example/x", "/api\r\nX: y"])
    def test_only_paths_on_the_node(self, fleet, path):
        fleet.added()
        with pytest.raises(NodeError):
            fleet.manager.client("web-2").request("GET", path)

    def test_no_token_stored(self, fleet):
        with pytest.raises(NodeError):
            NodeClient("web-2", tunnels=fleet.tunnels, secrets=fleet.secrets).auth_headers()


class TestAdd:
    def test_registers_a_reachable_node(self, fleet):
        key = fleet.manager.central_public_key("web-2")

        record = fleet.manager.add(
            "web-2", ssh_target="root@web2.example.com:2222", join_code=join_code(key)
        )

        assert (record.ssh_host, record.ssh_port, record.ssh_user) == (
            "web2.example.com",
            2222,
            "root",
        )
        assert record.host_key == f"noust-node-web-2 {HOST_KEY}"
        assert (record.status, record.version) == ("reachable", "3.0.1")
        assert record.last_seen is not None
        assert fleet.secrets.read(secret_name("web-2", TOKEN_LEAF)) == TOKEN
        assert fleet.node.requests[0].url.path == "/api/system/version"
        assert fleet.node.requests[0].headers["X-Noust-Actor"] == "cli:root@nas"
        # A version check is read-only: it must not ask the node for admin,
        # which the fleet token's actor scope is narrowed to (noust.web.auth
        # fails closed to 'read' when this header is absent).
        assert fleet.node.requests[0].headers["X-Noust-Actor-Scope"] == "read"

    def test_the_ssh_port_defaults_to_the_code_s(self, fleet):
        fleet.added(ssh_port=2200)
        assert fleet.manager.get("web-2").ssh_port == 2200

    def test_blocked_by_policy_writes_nothing(self, fleet):
        fleet.manager._blockers = lambda: ["Enable two-factor sign-in first."]
        key = fleet.manager.central_public_key("web-2")

        with pytest.raises(NodeError) as caught:
            fleet.manager.add("web-2", ssh_target="web2.example.com", join_code=join_code(key))

        assert "two-factor" in caught.value.details
        assert _leftovers(fleet) == []

    def test_a_code_for_another_key_is_refused_before_anything_is_written(self, fleet):
        fleet.manager.central_public_key("web-2")

        with pytest.raises(NodeError) as caught:
            fleet.manager.add(
                "web-2",
                ssh_target="web2.example.com",
                join_code=join_code(ed25519_line(123)),
            )

        assert "another key" in caught.value.message
        assert _leftovers(fleet) == []
        assert fleet.runner.processes == []

    def test_no_key_yet(self, fleet):
        with pytest.raises(NodeError) as caught:
            fleet.manager.add(
                "web-2", ssh_target="web2.example.com", join_code=join_code(ed25519_line(5))
            )
        assert "noust node key web-2" in caught.value.details

    def test_a_user_the_code_does_not_authorize_is_refused(self, fleet):
        key = fleet.manager.central_public_key("web-2")
        with pytest.raises(NodeError):
            fleet.manager.add("web-2", ssh_target="deploy@web2", join_code=join_code(key))
        assert _leftovers(fleet) == []

    def test_a_taken_name_is_refused(self, fleet):
        fleet.added()
        key = fleet.manager.central_public_key("web-2")
        with pytest.raises(NodeError):
            fleet.manager.add("web-2", ssh_target="web2", join_code=join_code(key))

    def test_a_host_key_mismatch_leaves_nothing_behind(self, fleet):
        key = fleet.manager.central_public_key("web-2")
        fleet.runner.script(["ssh"], exit_code=255, stderr="Host key verification failed.")

        with pytest.raises(NodeUnreachableError) as caught:
            fleet.manager.add("web-2", ssh_target="web2", join_code=join_code(key))

        assert "does not match the one pinned" in caught.value.message
        assert caught.value.details == "Host key verification failed."
        assert _leftovers(fleet) == []
        # The key pair predates add() and stays, so the operator can retry.
        assert fleet.keys.public_key("web-2") is not None

    def test_a_refused_token_leaves_nothing_behind(self, fleet):
        key = fleet.manager.central_public_key("web-2")
        fleet.node.token = OTHER_TOKEN

        with pytest.raises(NodeRefusedError):
            fleet.manager.add("web-2", ssh_target="web2", join_code=join_code(key))

        assert _leftovers(fleet) == []
        assert fleet.runner.processes[0].terminated

    def test_an_invalid_code_writes_nothing(self, fleet):
        fleet.manager.central_public_key("web-2")
        with pytest.raises(NodeError):
            fleet.manager.add("web-2", ssh_target="web2", join_code="noust-join:v1:xx")
        assert _leftovers(fleet) == []


class TestPolicy:
    @pytest.fixture
    def state_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        from noust.web import auth

        directory = tmp_path / "state"
        directory.mkdir()
        monkeypatch.setenv(auth.STATE_DIR_ENV, str(directory))
        # A console built by an earlier test leaves its manager installed;
        # the policy would read that console's two-factor state instead.
        monkeypatch.setattr(auth, "_global_token_manager", None)
        return directory

    def test_blocks_without_two_factor(self, state_dir):
        blockers = node_registration_blockers()

        assert len(blockers) == 1
        assert "Two-factor sign-in is not enabled" in blockers[0]
        assert "noust 2fa enroll" in blockers[0]

    def test_allows_with_two_factor(self, state_dir):
        from noust.core import totp
        from noust.web.auth import SecurityConfig, TokenManager

        manager = TokenManager(SecurityConfig())
        secret = manager.begin_totp_enrollment()
        assert manager.confirm_totp_enrollment(totp.totp_now(secret)) is not None

        assert node_registration_blockers() == []

    def test_add_asks_the_real_policy_by_default(self, tmp_path, state_dir):
        from noust.fleet.nodes import NodeManager

        assert NodeManager()._blockers is node_registration_blockers


class TestRemoveAndTest:
    def test_remove_revokes_on_the_node_and_forgets_everything(self, fleet):
        fleet.added()

        warnings = fleet.manager.remove("web-2")

        assert fleet.node.revoked
        revoke = fleet.node.requests[-1]
        assert (revoke.method, revoke.url.path) == ("POST", "/api/auth/fleet/revoke")
        # The node requires admin scope for this write (noust.web.auth
        # required_scope), and there is no lesser scope that revokes a token.
        assert revoke.headers["X-Noust-Actor-Scope"] == "admin"
        assert len(warnings) == 1 and "noust fleet deauthorize --name nas" in warnings[0]
        assert _leftovers(fleet) == []
        assert fleet.keys.public_key("web-2") is None
        assert fleet.runner.processes[0].terminated

    def test_remove_without_revoke_says_the_token_stays(self, fleet):
        fleet.added()

        warnings = fleet.manager.remove("web-2", revoke=False)

        assert not fleet.node.revoked
        assert any("left valid" in warning for warning in warnings)
        assert _leftovers(fleet) == []

    def test_remove_of_an_unreachable_node_still_forgets_it_and_warns(self, fleet):
        fleet.added()
        fleet.tunnels.close("web-2")
        fleet.runner.script(["ssh"], exit_code=255, stderr="Connection timed out")

        warnings = fleet.manager.remove("web-2")

        assert any("could not be reached" in warning for warning in warnings)
        assert _leftovers(fleet) == []

    def test_remove_when_the_node_lacks_the_revoke_endpoint(self, fleet):
        fleet.added()
        fleet.node.responses["/api/auth/fleet/revoke"] = (404, {"error": "not_found"})

        warnings = fleet.manager.remove("web-2")

        assert any("did not revoke" in warning for warning in warnings)

    def test_remove_when_already_revoked_is_quiet_about_it(self, fleet):
        fleet.added()
        fleet.node.revoked = True

        warnings = fleet.manager.remove("web-2")

        assert len(warnings) == 1

    def test_remove_unknown(self, fleet):
        with pytest.raises(NodeError):
            fleet.manager.remove("ghost")

    def test_test_records_each_outcome(self, fleet):
        fleet.added()

        ok = fleet.manager.test("web-2")
        assert ok["reachable"] and ok["version"] == "3.0.1" and ok["error"] is None
        assert isinstance(ok["latency_ms"], float)

        fleet.node.revoked = True
        refused = fleet.manager.test("web-2")
        assert (refused["reachable"], refused["status"]) == (False, "refused")
        assert fleet.manager.get("web-2").status == "refused"

        fleet.node.revoked = False
        fleet.tunnels.close("web-2")
        fleet.runner.script(
            ["ssh"], exit_code=255, stderr="ssh: connect to host: Connection refused"
        )
        down = fleet.manager.test("web-2")
        assert (down["reachable"], down["status"]) == (False, "unreachable")
        assert down["details"] == "ssh: connect to host: Connection refused"
        assert fleet.manager.get("web-2").version == "3.0.1"

    def test_a_revoked_ssh_key_is_recorded_as_refused_too(self, fleet):
        """
        Not only a revoked fleet token (HTTP 401): a revoked SSH key answers
        "Permission denied" opening the tunnel, and the node is recorded
        refused exactly the same way, not merely unreachable.
        """
        fleet.added()
        fleet.tunnels.close("web-2")
        fleet.runner.script(["ssh"], exit_code=255, stderr="Permission denied (publickey).")

        result = fleet.manager.test("web-2")

        assert (result["reachable"], result["status"]) == (False, "refused")
        assert fleet.manager.get("web-2").status == "refused"


class TestFleetStatus:
    def test_summarises_every_node_in_parallel(self, fleet):
        fleet.added("web-2")
        fleet.added("web-3")
        # web-2's tunnel stays open; web-3's is closed and cannot reopen.
        fleet.tunnels.close("web-3")
        fleet.runner.script(
            ["ssh"], exit_code=255, stderr="ssh: connect to host web2: Connection refused"
        )

        summaries = fleet_status(fleet.manager)

        assert [summary["name"] for summary in summaries] == ["web-2", "web-3"]
        up, down = summaries
        assert up["reachable"] and up["status"] == "reachable"
        assert up["apps"] == {"running": 3, "failed": 1, "stopped": 0, "static": 2, "unmanaged": 0}
        assert up["units"] == {"running": 5, "failed": 1, "stopped": 0}
        assert up["certificates_expiring"] == 1
        assert up["warnings"] == []
        assert not down["reachable"] and down["status"] == "unreachable"
        assert "Connection refused" in down["details"]
        assert fleet.manager.get("web-3").status == "unreachable"
        # A status poll has no human actor behind it: read is enough, and is
        # what it must ask for so a fleet token narrowed to it still works.
        status_requests = [r for r in fleet.node.requests if r.url.path.startswith("/api/system")]
        assert status_requests
        assert all(r.headers["X-Noust-Actor-Scope"] == "read" for r in status_requests)

    def test_a_part_that_fails_is_a_warning(self, fleet):
        fleet.added()
        fleet.node.responses["/api/system/machine"] = (503, {"message": "psutil is missing"})

        (summary,) = fleet_status(fleet.manager)

        assert summary["reachable"]
        assert summary["apps"] is None
        assert summary["warnings"] and "Machine snapshot" in summary["warnings"][0]

    def test_no_nodes(self, fleet):
        assert fleet_status(fleet.manager) == []
