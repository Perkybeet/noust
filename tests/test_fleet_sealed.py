"""
The fleet on a central whose secrets are sealed.

On a sealed store every file under ``secrets/`` is ciphertext, so what ssh is
handed must be a decrypted private copy, which must not outlive the moment ssh
reads it; a locked central must refuse to dial anything, and say how to
unlock; and a node that refused the fleet token must not be asked again (each
refusal is an audited failure on the node) until the operator tests it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core import sealing
from noust.core.exceptions import NodeRefusedError, NodeUnreachableError
from noust.core.runner import SubprocessRunner
from noust.core.sealing import SecretsLockedError
from noust.core.secrets import SecretStore
from noust.fleet.keys import KNOWN_HOSTS, PRIVATE_KEY, PUBLIC_KEY, secret_name
from noust.fleet.nodes import VERSION_PATH
from tests.fleet_support import build_fleet

PASSPHRASE = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


@pytest.fixture
def runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "run"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(directory))
    return directory


@pytest.fixture
def sealed_fleet(tmp_path: Path, runtime_dir: Path):
    """A fleet over a sealed, unlocked store; locked again afterwards."""
    root = tmp_path / "secrets"
    secrets = SecretStore(root=root, runner=SubprocessRunner())
    root.mkdir(mode=0o700)
    sealing.seal_store(root, PASSPHRASE, runner=SubprocessRunner())
    fleet = build_fleet(tmp_path, secrets=secrets)
    yield fleet
    sealing.lock(root)


def _on_disk(fleet, node: str, leaf: str) -> str:
    return fleet.secrets.path(secret_name(node, leaf)).read_text()


@pytest.mark.allow_subprocess
class TestSealedKeys:
    def test_the_key_pair_is_sealed_at_rest(self, sealed_fleet):
        public = sealed_fleet.manager.central_public_key("web-2")

        assert public.startswith("ssh-ed25519 ")
        for leaf in (PRIVATE_KEY, PUBLIC_KEY):
            assert _on_disk(sealed_fleet, "web-2", leaf).startswith(sealing.SEALED_PREFIX)
        # Read back through the seal, and generated once.
        assert sealed_fleet.manager.central_public_key("web-2") == public
        assert len(sealed_fleet.runner.calls_to("ssh-keygen")) == 1

    def test_keygen_writes_into_the_private_runtime_dir(self, sealed_fleet, runtime_dir: Path):
        sealed_fleet.manager.central_public_key("web-2")

        (call,) = sealed_fleet.runner.calls_to("ssh-keygen")
        written = Path(call[call.index("-f") + 1])
        assert written.is_relative_to(runtime_dir)
        # Nothing in clear is left behind once the pair is sealed.
        assert not written.exists()
        assert not Path(f"{written}.pub").exists()

    def test_a_locked_central_generates_nothing(self, sealed_fleet):
        sealing.lock(sealed_fleet.secrets.root)

        with pytest.raises(SecretsLockedError):
            sealed_fleet.manager.central_public_key("web-2")
        assert sealed_fleet.runner.calls_to("ssh-keygen") == []


@pytest.mark.allow_subprocess
class TestSealedTunnels:
    def test_ssh_gets_decrypted_copies_removed_once_it_is_up(self, sealed_fleet, runtime_dir: Path):
        sealed_fleet.added()

        argv = sealed_fleet.runner.processes[0].argv
        identity = Path(argv[argv.index("-i") + 1])
        known_hosts = Path(
            next(o for o in argv if o.startswith("UserKnownHostsFile=")).split("=", 1)[1]
        )
        for path in (identity, known_hosts):
            assert path.is_relative_to(runtime_dir)
            assert not path.is_relative_to(sealed_fleet.secrets.root)
            # ssh read it when it connected; the plaintext is gone.
            assert not path.exists()
        assert _on_disk(sealed_fleet, "web-2", KNOWN_HOSTS).startswith(sealing.SEALED_PREFIX)

    def test_a_failed_open_removes_the_copies_too(self, sealed_fleet, runtime_dir: Path):
        sealed_fleet.added()
        sealed_fleet.tunnels.close("web-2")
        sealed_fleet.runner.script(["ssh"], exit_code=255, stderr="Connection refused")

        with pytest.raises(NodeUnreachableError):
            sealed_fleet.tunnels.endpoint("web-2")

        leftovers = [p for p in runtime_dir.rglob("*") if p.is_file()]
        assert leftovers == []

    def test_a_locked_central_opens_no_tunnel_and_says_how_to_unlock(self, sealed_fleet):
        sealed_fleet.added()
        sealed_fleet.tunnels.close("web-2")
        started = len(sealed_fleet.runner.processes)
        sealing.lock(sealed_fleet.secrets.root)

        with pytest.raises(SecretsLockedError) as caught:
            sealed_fleet.tunnels.endpoint("web-2")

        assert "locked" in caught.value.message
        assert "noust central unlock" in caught.value.details
        assert len(sealed_fleet.runner.processes) == started
        # Not a failure of the node: no backoff to wait out after unlocking.
        assert sealed_fleet.tunnels.status("web-2")["failures"] == 0

    def test_a_live_tunnel_is_not_handed_out_while_locked(self, sealed_fleet):
        sealed_fleet.added()
        sealing.lock(sealed_fleet.secrets.root)

        with pytest.raises(SecretsLockedError):
            sealed_fleet.tunnels.endpoint("web-2")

    def test_unlocking_lets_the_tunnel_open_again(self, sealed_fleet):
        sealed_fleet.added()
        sealed_fleet.tunnels.close("web-2")
        root = sealed_fleet.secrets.root
        sealing.lock(root)
        with pytest.raises(SecretsLockedError):
            sealed_fleet.tunnels.endpoint("web-2")

        sealing.unlock(root, PASSPHRASE)

        host, _port = sealed_fleet.tunnels.endpoint("web-2")
        assert host == "127.0.0.1"

    def test_unlocking_forgets_the_backoff(self, sealed_fleet):
        sealed_fleet.added()
        sealed_fleet.tunnels.close("web-2")
        sealed_fleet.runner.script(["ssh"], exit_code=255, stderr="Connection refused")
        with pytest.raises(NodeUnreachableError):
            sealed_fleet.tunnels.endpoint("web-2")
        assert sealed_fleet.tunnels.status("web-2")["failures"] == 1

        sealed_fleet.tunnels.on_unlocked()

        assert sealed_fleet.tunnels.status("web-2")["failures"] == 0


class TestRefusedNodesAreLeftAlone:
    """A refused token is not presented again until the operator tests the node."""

    def test_a_refusal_is_recorded_and_the_node_is_not_asked_again(self, tmp_path: Path):
        fleet = build_fleet(tmp_path)
        fleet.added()
        fleet.node.revoked = True
        client = fleet.manager.client("web-2")

        with pytest.raises(NodeRefusedError):
            client.get_json(VERSION_PATH)
        assert fleet.store.get_node("web-2").status == "refused"
        asked = len(fleet.node.requests)

        with pytest.raises(NodeRefusedError) as caught:
            fleet.manager.client("web-2").get_json(VERSION_PATH)

        assert len(fleet.node.requests) == asked
        assert "noust node test web-2" in caught.value.details
        assert caught.value.status_code == 401

    def test_the_headers_are_not_built_for_a_refused_node(self, tmp_path: Path):
        # The proxy builds its own requests from auth_headers(); it stops too.
        fleet = build_fleet(tmp_path)
        fleet.added()
        fleet.store.set_node_status("web-2", "refused")

        with pytest.raises(NodeRefusedError):
            fleet.manager.client("web-2").auth_headers("cli:root")

    def test_node_test_asks_again_and_clears_it(self, tmp_path: Path):
        fleet = build_fleet(tmp_path)
        fleet.added()
        fleet.node.revoked = True
        assert fleet.manager.test("web-2")["status"] == "refused"
        asked = len(fleet.node.requests)

        # Still refused: test() asked the node anyway.
        assert fleet.manager.test("web-2")["status"] == "refused"
        assert len(fleet.node.requests) == asked + 1

        fleet.node.revoked = False
        assert fleet.manager.test("web-2")["status"] == "reachable"
        assert fleet.manager.client("web-2").get_json(VERSION_PATH)["current_version"]

    def test_fleet_status_does_not_ask_a_refused_node(self, tmp_path: Path):
        from noust.fleet.status import fleet_status

        fleet = build_fleet(tmp_path)
        fleet.added()
        fleet.store.set_node_status("web-2", "refused")
        asked = len(fleet.node.requests)

        (summary,) = fleet_status(fleet.manager)

        assert summary["status"] == "refused"
        assert len(fleet.node.requests) == asked

    def test_removing_a_refused_node_does_not_present_the_token(self, tmp_path: Path):
        fleet = build_fleet(tmp_path)
        fleet.added()
        fleet.store.set_node_status("web-2", "refused")
        asked = len(fleet.node.requests)

        warnings = fleet.manager.remove("web-2")

        assert len(fleet.node.requests) == asked
        assert not any("did not revoke" in warning for warning in warnings)
