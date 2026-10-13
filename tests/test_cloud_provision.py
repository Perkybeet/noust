"""
Creating a server at a provider and enrolling it, against a fake provider and a stand-in enroller.

The provider is the real Hetzner adapter over a fake API; the enrollment is a stand-in that
reads the host key out of the user-data the create call carried, as the machine would present
it, so the chain is checked end to end: what is asked, what is waited for, what is trusted,
what is cleaned up, and what is never written anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from noust.core import audit
from noust.core.exceptions import NodeError, ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import NodeRecord, NoustStore
from noust.fleet.cloud.accounts import CloudAccounts
from noust.fleet.cloud.init import KIND_SCRIPT, CloudInitLibrary
from noust.fleet.cloud.provision import ProvisionRequest, ServerProvisioner
from noust.fleet.cloud.servers import CloudServers
from noust.fleet.enroll import EnrollRequest, HostKeyScan
from noust.fleet.models import PublicKey, parse_public_key
from tests.cloud_support import FakeApi
from tests.fleet_support import KeygenRunner

TOKEN = "hc" + "_" + "t" * 20 + "K9m2"


class Clock:
    """A clock that only moves when the code sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class StubEnroller:
    """
    The guided enrollment, as far as provisioning meets it.

    Attributes:
        api: The fake provider, to read the host key out of the create call.
        wrong_scans: How many scans present another key before the right one.
        never_matches: Present another key for ever.
        enroll_error: Raise this from ``enroll``.
        enrolled: The requests ``enroll`` was given.
    """

    api: FakeApi
    store: NoustStore
    wrong_scans: int = 0
    never_matches: bool = False
    enroll_error: NodeError | None = None
    blockers: list[str] = field(default_factory=list)
    scans: list[tuple[str, int]] = field(default_factory=list)
    enrolled: list[EnrollRequest] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def manager(self) -> Any:
        return SimpleNamespace(
            registration_blockers=lambda: list(self.blockers),
            store=self.store,
            remove=self._remove,
        )

    def _remove(self, name: str, *, revoke: bool = True, actor: str | None = None) -> list[str]:
        self.removed.append(name)
        return [f"removed {name}"]

    def _chosen_key(self) -> PublicKey:
        (create,) = self.api.sent("POST", "/servers")
        document = yaml.safe_load(create.body["user_data"])
        return parse_public_key(document["ssh_keys"]["ed25519_public"])

    def scan_host_key(self, host: str, port: int = 22) -> HostKeyScan:
        self.scans.append((host, port))
        if self.never_matches or self.wrong_scans > 0:
            self.wrong_scans = max(0, self.wrong_scans - 1)
            from tests.fleet_support import ed25519_line

            return HostKeyScan(host, port, parse_public_key(ed25519_line(4242)))
        return HostKeyScan(host, port, self._chosen_key())

    def enroll(
        self, request: EnrollRequest, *, actor: str | None = None, log: Any = None
    ) -> NodeRecord:
        self.enrolled.append(request)
        if self.enroll_error is not None:
            raise self.enroll_error
        if log:
            log("Joined", "success")
        return NodeRecord(
            name=request.name,
            ssh_host=request.host,
            ssh_port=request.port,
            ssh_user="noust-tunnel",
            host_key="noust-node-x ssh-ed25519 AAAA",
            console_port=8080,
            status="reachable",
        )


@dataclass
class World:
    store: NoustStore
    api: FakeApi
    accounts: CloudAccounts
    library: CloudInitLibrary
    servers: CloudServers
    enroller: StubEnroller
    clock: Clock
    provisioner: ServerProvisioner
    log: list[tuple[str, str]] = field(default_factory=list)

    def say(self, message: str, level: str = "info") -> None:
        self.log.append((level, message))

    @property
    def log_text(self) -> str:
        return "\n".join(m for _, m in self.log)


def server_json(state: str, ip: str | None = "203.0.113.9") -> dict[str, Any]:
    return {
        "server": {
            "id": 42,
            "name": "web-2",
            "status": state,
            "public_net": {"ipv4": {"ip": ip} if ip else None, "ipv6": None},
        }
    }


@pytest.fixture
def world(tmp_path: Path) -> World:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    api = FakeApi(prefix="/v1")
    api.on("GET", "/locations", (200, {"locations": []}))
    api.on("POST", "/ssh_keys", (201, {"ssh_key": {"id": 77}}))
    api.on("DELETE", "/ssh_keys/77", (200, None))
    api.on(
        "POST", "/servers", (201, {**server_json("initializing"), "root_password": "pw-never-read"})
    )
    states = iter(["initializing", "starting", "running"])
    api.on("GET", "/servers/42", lambda call: (200, server_json(next(states, "running"))))
    accounts = CloudAccounts(
        store,
        SecretStore(root=tmp_path / "secrets"),
        provider_options={"opener": api, "sleep": lambda _s: None},
    )
    accounts.add("hz", "hetzner", TOKEN)
    api.calls.clear()
    library = CloudInitLibrary(store)
    servers = CloudServers(store)
    enroller = StubEnroller(api=api, store=store)
    clock = Clock()
    provisioner = ServerProvisioner(
        accounts,
        library,
        enroller,  # type: ignore[arg-type]
        servers,
        runner=KeygenRunner(),
        sleep=clock.sleep,
        clock=clock,
        boot_timeout=60,
        ssh_timeout=60,
        poll=5,
    )
    return World(store, api, accounts, library, servers, enroller, clock, provisioner)


def request(**changes: Any) -> ProvisionRequest:
    values: dict[str, Any] = {
        "name": "web-2",
        "account": "hz",
        "location": "fsn1",
        "plan": "cx22",
        "image": "ubuntu-24.04",
    }
    values.update(changes)
    return ProvisionRequest(**values)


class TestTheWholeWay:
    def test_a_server_is_created_waited_for_enrolled_and_the_one_time_key_forgotten(
        self, world: World
    ):
        node = world.provisioner.provision(
            request(access="deploy"), actor="ana", job_id="j1", log=world.say
        )

        assert node.name == "web-2"
        # The provider was asked for the machine with the one-time key and the cloud-init document.
        (create,) = world.api.sent("POST", "/servers")
        assert create.body["ssh_keys"] == [77]
        assert create.body["server_type"] == "cx22" and create.body["location"] == "fsn1"
        document = yaml.safe_load(create.body["user_data"])
        assert document["ssh_pwauth"] is False
        assert "BEGIN OPENSSH PRIVATE KEY" in document["ssh_keys"]["ed25519_private"]
        # The enrollment trusts the key that was chosen, and logs in with the one-time key.
        (enrolled,) = world.enroller.enrolled
        assert (
            enrolled.host == "203.0.113.9" and enrolled.ssh_user == "root" and enrolled.port == 22
        )
        assert (
            enrolled.host_key_fingerprint
            == parse_public_key(document["ssh_keys"]["ed25519_public"]).fingerprint
        )
        assert enrolled.credential.private_key is not None
        assert enrolled.retire_key is not None and enrolled.retire_key.startswith("ssh-ed25519 ")
        assert enrolled.access == "deploy"
        # The provider-side copy of the login key is deleted, and the machine is on record as ready.
        assert len(world.api.sent("DELETE", "/ssh_keys/77")) == 1
        record = world.servers.get("web-2")
        assert (record.state, record.address, record.provider_id, record.job_id) == (
            "ready",
            "203.0.113.9",
            "42",
            "j1",
        )
        assert record.created_by == "ana"
        assert world.accounts.get("hz").last_used_at is not None

    def test_the_machine_is_on_record_as_soon_as_the_provider_answers(self, world: World):
        seen: list[str] = []
        world.enroller.scan_host_key = lambda host, port=22: (  # type: ignore[method-assign]
            seen.append(world.servers.get("web-2").state)
            or StubEnroller.scan_host_key(world.enroller, host, port)
        )

        world.provisioner.provision(request())

        assert seen == ["booting"]

    def test_the_login_key_is_not_the_host_key(self, world: World):
        world.provisioner.provision(request())

        (create,) = world.api.sent("POST", "/servers")
        (upload,) = world.api.sent("POST", "/ssh_keys")
        host_public = yaml.safe_load(create.body["user_data"])["ssh_keys"]["ed25519_public"]
        assert upload.body["public_key"].split()[1] != host_public.split()[1]
        assert upload.body["name"].startswith("noust-web-2-")

    def test_library_scripts_ride_in_the_document_after_noust_s_own_part(self, world: World):
        world.library.save("tune", KIND_SCRIPT, "#!/bin/sh\nsysctl -w vm.swappiness=10\n")

        world.provisioner.provision(request(scripts=["swap-2g", "tune"]))

        (create,) = world.api.sent("POST", "/servers")
        document = yaml.safe_load(create.body["user_data"])
        assert [c[0] for c in document["runcmd"]] == [
            "/var/lib/noust/cloud-init/01-swap-2g.sh",
            "/var/lib/noust/cloud-init/02-tune.sh",
        ]
        assert document["ssh_keys"]["ed25519_public"]

    def test_extra_provider_options_reach_the_create_call(self, world: World):
        world.api.on("POST", "/servers/42/actions/enable_backup", (201, {"action": {}}))

        world.provisioner.provision(
            request(ssh_keys=["9"], firewalls=["3"], networks=["8"], ipv6=False, backups=True)
        )

        (create,) = world.api.sent("POST", "/servers")
        assert create.body["ssh_keys"] == [77, 9]
        assert create.body["firewalls"] == [{"firewall": 3}]
        assert create.body["networks"] == [8]
        assert create.body["public_net"] == {"enable_ipv4": True, "enable_ipv6": False}
        assert len(world.api.sent("POST", "/servers/42/actions/enable_backup")) == 1

    def test_a_machine_that_answers_with_another_key_first_is_waited_out(self, world: World):
        world.enroller.wrong_scans = 2

        world.provisioner.provision(request(), log=world.say)

        assert len(world.enroller.scans) == 3
        assert world.log_text.count("Another host key answers for now") == 1
        assert len(world.enroller.enrolled) == 1


class TestNothingIsSentWhenTheRequestIsWrong:
    @pytest.mark.parametrize(
        ("changes", "error"),
        [
            ({"account": "nope"}, NodeError),
            ({"scripts": ["nope"]}, NodeError),
            ({"name": "Bad Name"}, NodeError),
            ({"access": "root"}, Exception),
            ({"location": ""}, ValidationError),
            ({"ipv4": False, "ipv6": False}, ValidationError),
            ({"firewalls": ["1"], "networks": ["x"]}, None),
        ],
    )
    def test_the_request_is_refused_before_the_provider_hears_of_it(
        self, world: World, changes, error
    ):
        if error is None:
            world.provisioner.validate(request(**changes))
            return

        with pytest.raises(error):
            world.provisioner.provision(request(**changes))

        assert world.api.calls == []

    def test_a_name_already_a_node_is_refused(self, world: World):
        world.store.save_node(
            NodeRecord(
                name="web-2",
                ssh_host="h",
                ssh_port=22,
                ssh_user="u",
                host_key="k",
                console_port=8080,
            )
        )

        with pytest.raises(NodeError) as caught:
            world.provisioner.provision(request())

        assert "already registered" in caught.value.message
        assert world.api.calls == []

    def test_a_name_with_a_machine_on_record_is_refused_until_it_is_deleted(self, world: World):
        world.servers.record(
            node="web-2",
            account="hz",
            provider="hetzner",
            provider_id="1",
            location=None,
            plan=None,
            image=None,
        )

        with pytest.raises(NodeError):
            world.provisioner.provision(request())

        world.servers.update("web-2", state="deleted")
        world.provisioner.validate(request())

    def test_a_central_that_may_not_register_servers_says_why_before_spending_anything(
        self, world: World
    ):
        world.enroller.blockers = ["Turn on two-factor sign-in first."]

        with pytest.raises(NodeError) as caught:
            world.provisioner.provision(request())

        assert "two-factor" in caught.value.details
        assert world.api.calls == []

    def test_a_document_too_large_for_the_provider_is_refused_before_it_is_sent(self, world: World):
        for index in range(3):
            world.library.save(f"big{index}", KIND_SCRIPT, "#!/bin/sh\n" + "# padding line\n" * 700)

        with pytest.raises(ValidationError) as caught:
            world.provisioner.provision(request(scripts=["big0", "big1", "big2"]))

        assert "bytes" in caught.value.message
        assert world.api.calls == []


class TestFailures:
    def test_a_refused_create_leaves_no_machine_and_no_key_at_the_provider(self, world: World):
        world.api._routes = [
            r for r in world.api._routes if r[0] != "POST" or "servers" not in r[1].pattern
        ]
        world.api.on("POST", "/servers", (422, {"error": {"message": "server type unavailable"}}))

        with pytest.raises(Exception) as caught:
            world.provisioner.provision(request())

        assert "server type unavailable" in (getattr(caught.value, "output", "") or "")
        assert world.servers.list() == []
        assert len(world.api.sent("DELETE", "/ssh_keys/77")) == 1

    def test_a_machine_that_never_presents_the_chosen_key_is_refused_and_kept_on_record(
        self, world: World
    ):
        world.enroller.never_matches = True

        with pytest.raises(NodeError) as caught:
            world.provisioner.provision(request(), log=world.say)

        assert (
            "never presented the host key" in caught.value.details
            or "never presented" in caught.value.message
        )
        assert "billed until it is deleted" in caught.value.details
        assert "noust fleet cloud server delete web-2" in caught.value.details
        assert world.enroller.enrolled == []
        record = world.servers.get("web-2")
        assert record.state == "failed" and "never presented" in (record.error or "")
        assert world.api.sent("DELETE", "/servers/42") == []
        assert len(world.api.sent("DELETE", "/ssh_keys/77")) == 1

    def test_delete_on_failure_removes_the_machine_the_job_made(self, world: World):
        world.enroller.never_matches = True
        world.api.on("DELETE", "/servers/42", (200, {"action": {}}))

        with pytest.raises(NodeError) as caught:
            world.provisioner.provision(request(delete_on_failure=True), log=world.say)

        assert len(world.api.sent("DELETE", "/servers/42")) == 1
        assert world.servers.get("web-2").state == "deleted"
        assert "was deleted" in caught.value.details

    def test_a_failed_enrollment_names_the_machine_and_keeps_the_reason(self, world: World):
        world.enroller.enroll_error = NodeError("The installer failed", details="exit 100")

        with pytest.raises(NodeError) as caught:
            world.provisioner.provision(request())

        assert (
            "web-2 was created but did not join the fleet: The installer failed"
            in caught.value.message
        )
        assert "exit 100" in caught.value.details
        assert world.servers.get("web-2").state == "failed"
        assert len(world.api.sent("DELETE", "/ssh_keys/77")) == 1

    def test_a_machine_that_never_runs_times_out_with_the_providers_word(self, world: World):
        world.api._routes = [
            r for r in world.api._routes if not (r[0] == "GET" and "/servers/42" in r[1].pattern)
        ]
        world.api.on("GET", "/servers/42", (200, server_json("initializing", ip=None)))

        with pytest.raises(NodeError) as caught:
            world.provisioner.provision(request())

        assert "did not run within 60 seconds" in caught.value.message
        assert "initializing" in caught.value.details
        assert world.servers.get("web-2").state == "failed"

    def test_a_deleted_key_that_cannot_be_deleted_is_a_warning_not_a_failure(self, world: World):
        world.api._routes = [
            r for r in world.api._routes if not (r[0] == "DELETE" and "ssh_keys" in r[1].pattern)
        ]
        world.api.on("DELETE", "/ssh_keys/77", (500, {"error": {"message": "boom"}}))

        node = world.provisioner.provision(request(), log=world.say)

        assert node.name == "web-2"
        assert any(
            level == "warning" and "could not be deleted" in message for level, message in world.log
        )

    def test_a_backup_request_the_provider_refuses_after_creation_is_a_failure_with_the_machine_named(
        self, world: World
    ):
        world.api.on(
            "POST", "/servers/42/actions/enable_backup", (422, {"error": {"message": "no backups"}})
        )

        with pytest.raises(NodeError) as caught:
            world.provisioner.provision(request(backups=True))

        assert "created but did not join" in caught.value.message
        assert world.servers.get("web-2").state == "failed"


class TestNothingSecretIsLeftBehind:
    def test_no_private_key_or_token_is_in_the_log_the_audit_trail_or_the_store(
        self, world: World, tmp_path: Path
    ):
        world.provisioner.provision(request(), log=world.say)

        (create,) = world.api.sent("POST", "/servers")
        host_private = yaml.safe_load(create.body["user_data"])["ssh_keys"]["ed25519_private"]
        enrolled = world.enroller.enrolled[0]
        login_private = enrolled.credential.private_key
        audit_text = Path(audit.get_log().path).read_text()
        database = (tmp_path / "noust.db").read_bytes()
        for secret in (host_private, login_private, TOKEN, "pw-never-read"):
            assert secret
            assert secret not in world.log_text
            assert secret not in audit_text
            assert secret.encode() not in database

    def test_the_audit_trail_records_the_start_and_the_finish(self, world: World):
        world.provisioner.provision(request())

        text = Path(audit.get_log().path).read_text()
        assert "fleet.provision.start" in text
        assert "fleet.provision.finish" in text

    def test_no_key_file_outlives_the_job(self, world: World, tmp_path: Path):
        world.provisioner.provision(request())

        leftovers = [p for p in tmp_path.rglob("*") if p.is_file() and p.name in ("key", "key.pub")]
        assert leftovers == []


class TestTheMachinesAfterwards:
    def test_a_machine_is_described_with_and_without_the_providers_live_view(self, world: World):
        world.provisioner.provision(request())

        plain = world.provisioner.machine("web-2")
        live = world.provisioner.machine("web-2", live=True)

        assert plain["state"] == "ready" and "remote" not in plain
        assert live["remote"]["state"] == "running" and live["remote"]["ipv4"] == "203.0.113.9"

    def test_deleting_a_machine_deletes_it_at_the_provider_and_keeps_the_history(
        self, world: World
    ):
        world.provisioner.provision(request())
        world.api.on("DELETE", "/servers/42", (200, {"action": {}}))

        warnings = world.provisioner.delete_machine("web-2", actor="ana")

        assert len(world.api.sent("DELETE", "/servers/42")) == 1
        assert world.servers.get("web-2").state == "deleted"
        assert warnings == []
        with pytest.raises(NodeError):
            world.provisioner.delete_machine("web-2")

    def test_deleting_can_take_the_server_out_of_the_fleet_too(self, world: World):
        world.provisioner.provision(request())
        world.store.save_node(
            NodeRecord(
                name="web-2",
                ssh_host="h",
                ssh_port=22,
                ssh_user="u",
                host_key="k",
                console_port=8080,
            )
        )
        world.api.on("DELETE", "/servers/42", (200, {"action": {}}))

        world.provisioner.delete_machine("web-2", remove_node=True)

        assert world.enroller.removed == ["web-2"]

    def test_a_machine_the_provider_no_longer_knows_is_taken_as_gone(self, world: World):
        world.provisioner.provision(request())
        world.api.on("DELETE", "/servers/42", (404, {"error": {"message": "not found"}}))

        warnings = world.provisioner.delete_machine("web-2")

        assert world.servers.get("web-2").state == "deleted"
        assert any("no longer knew" in w for w in warnings)

    def test_powering_on_asks_the_provider(self, world: World):
        world.provisioner.provision(request())
        world.api.on("POST", "/servers/42/actions/poweron", (201, {"action": {}}))

        world.provisioner.power_on("web-2")

        assert len(world.api.sent("POST", "/servers/42/actions/poweron")) == 1

    def test_a_machine_this_central_did_not_create_is_unknown(self, world: World):
        with pytest.raises(NodeError):
            world.provisioner.machine("stranger")
