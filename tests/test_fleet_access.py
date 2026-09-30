"""
Tests for a node's access ceiling for its centrals (``noust.fleet.policy``).

What is pinned: the ceiling's default (3.0's behaviour, without host access),
the permission table each level allows - including what no central may ever
do - that the ceiling is read from the node's own store on every call, and
that the store keeps one row, whatever writes it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.fleet.policy import (
    DEFAULT_ACCESS,
    FleetAccess,
    current_access,
    permits,
    set_access,
)

#: Every permission of the plan's shared table (noust.web.permissions).
READS = [
    "apps.read",
    "server.read",
    "databases.read",
    "backups.read",
    "fleet.read",
    "settings.read",
    "audit.read",
]
WRITES = [
    "apps.manage",
    "secrets.reveal",
    "root_equivalent",
    "server.manage",
    "databases.write",
    "databases.manage",
    "fleet.manage",
    "settings.manage",
]
DEPLOYS = ["apps.operate", "apps.deploy", "backups.manage"]
NEVER = ["security.manage", "accounts.manage", "audit.manage"]


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
    yield store
    NoustStore.reset_instance()


class TestPermits:
    @pytest.mark.parametrize("permission", READS)
    def test_every_level_reads(self, permission):
        for level in ("read", "deploy", "admin"):
            assert permits(FleetAccess(level), permission), (level, permission)

    @pytest.mark.parametrize("permission", DEPLOYS)
    def test_deploy_moves_existing_applications(self, permission):
        assert not permits(FleetAccess("read"), permission)
        assert permits(FleetAccess("deploy"), permission)
        assert permits(FleetAccess("admin"), permission)

    @pytest.mark.parametrize("permission", WRITES)
    def test_only_admin_creates_deletes_and_configures(self, permission):
        assert not permits(FleetAccess("read"), permission)
        assert not permits(FleetAccess("deploy"), permission)
        assert permits(FleetAccess("admin"), permission)

    @pytest.mark.parametrize("permission", NEVER)
    def test_no_ceiling_reaches_the_node_s_own_accounts_security_or_audit(self, permission):
        for level in ("read", "deploy", "admin"):
            for host_access in (False, True):
                assert not permits(FleetAccess(level, host_access), permission)

    def test_host_access_needs_admin_and_the_node_s_explicit_yes(self):
        assert not permits(FleetAccess("admin"), "server.host_access")
        assert permits(FleetAccess("admin", True), "server.host_access")
        # host_access alone is not a level: a read or deploy ceiling stays one.
        assert not permits(FleetAccess("read", True), "server.host_access")
        assert not permits(FleetAccess("deploy", True), "server.host_access")
        assert not permits(FleetAccess("read", True), "server.manage")

    def test_a_central_s_own_credential_is_never_withheld(self):
        # /api/auth/fleet/revoke and /api/auth/fleet/self are "self": a central held
        # to read must still be able to revoke its own token when removed.
        for level in ("read", "deploy", "admin"):
            assert permits(FleetAccess(level), "self")

    def test_an_unknown_level_is_refused_on_construction(self):
        with pytest.raises(ValidationError):
            FleetAccess("owner")  # type: ignore[arg-type]


class TestCurrentAccess:
    def test_a_server_that_never_set_one_keeps_3_0_behaviour(self, store):
        assert current_access(store) == DEFAULT_ACCESS == FleetAccess("admin", False)

    def test_set_then_read_without_a_restart(self, store):
        set_access(FleetAccess("read"), actor="cli:root", store=store)
        assert current_access(store) == FleetAccess("read", False)

        set_access(FleetAccess("admin", True), actor="cli:root", store=store)
        assert current_access(store) == FleetAccess("admin", True)
        row = store.get_fleet_access()
        assert row is not None and row["updated_by"] == "cli:root"

    def test_the_store_refuses_a_level_it_does_not_know(self, store):
        with pytest.raises(ValidationError):
            store.set_fleet_access("root", False)

    def test_one_row_whatever_writes_it(self, store):
        for level in ("read", "deploy", "admin", "read"):
            store.set_fleet_access(level, False)
        count = store._get_connection().execute("SELECT COUNT(*) FROM fleet_access").fetchone()
        assert count[0] == 1


class TestPublished:
    def test_round_trip(self):
        access = FleetAccess("deploy", True)
        assert FleetAccess.from_dict(access.to_dict()) == access
        assert access.to_dict() == {"level": "deploy", "host_access": True}

    @pytest.mark.parametrize(
        "body",
        [None, [], {"level": "root"}, {"level": "read", "host_access": "yes"}, {}],
    )
    def test_anything_else_is_refused(self, body):
        with pytest.raises(ValidationError):
            FleetAccess.from_dict(body)

    def test_describe(self):
        assert FleetAccess("admin", True).describe() == "admin; host access on"
        assert FleetAccess("read", True).describe() == "read; host access off"


class TestNodeRecordAccess:
    def test_a_central_records_what_a_node_published(self, store):
        from noust.core.store import NodeRecord

        store.save_node(
            NodeRecord(
                name="web-2",
                ssh_host="web2.example.com",
                ssh_port=22,
                ssh_user="noust-tunnel",
                host_key="noust-node-web-2 ssh-ed25519 AAAA",
                console_port=8080,
            )
        )
        fresh = store.get_node("web-2")
        assert fresh is not None
        assert (fresh.access_level, fresh.host_access, fresh.access_read_at) == (None, None, None)

        assert store.set_node_access("web-2", "read", False)
        read = store.get_node("web-2")
        assert read is not None
        assert (read.access_level, read.host_access) == ("read", False)
        assert read.access_read_at

        # Saving the node again (a status change, a re-add) keeps what was learnt.
        store.save_node(read)
        kept = store.get_node("web-2")
        assert kept is not None and kept.access_level == "read"

        assert not store.set_node_access("missing", "admin", True)


class TestAdmission:
    """The node's admission of a fleet token reads this ceiling (noust.web.auth, B1)."""

    def test_a_read_ceiling_withholds_writes_from_any_central(self, store, monkeypatch):
        auth = pytest.importorskip("noust.web.auth")
        fleet_ceiling = getattr(auth, "fleet_ceiling", None)
        if fleet_ceiling is None:
            pytest.skip("the fleet admission does not read the ceiling yet")
        monkeypatch.setattr("noust.core.store.get_store", lambda *a, **k: store)

        assert fleet_ceiling()("apps.manage")
        set_access(FleetAccess("read"), store=store)
        ceiling = fleet_ceiling()

        assert ceiling("apps.read")
        assert not ceiling("apps.manage")
        assert not ceiling("apps.deploy")
        assert ceiling("self")
