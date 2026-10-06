# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A database made reachable from the network on purpose, and private again (checklist C-22).

What is pinned: a database is private until someone says otherwise and accepts
every consequence; the engine itself is never reconfigured (a proxy on the
public port forwards to its loopback); the account given out is one made for
this, not an application's; TLS is required where the engine can and asked
about where it cannot; the firewall is told through the one firewall manager;
a step that fails undoes the ones before it; and making it private removes
exactly what making it public made, and not an operator's own rule.
"""

from __future__ import annotations

import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from noust.core import audit
from noust.core.database_exposures import ExposureStore
from noust.core.exceptions import (
    ConfirmationRequired,
    DatabaseEngineError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    SecurityError,
    ServiceError,
    ValidationError,
)
from noust.core.runner import set_runner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.database import public_access
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.public_access import PublicAccess, remote_user_name
from noust.managers.database.service import DatabaseService
from tests.database_public_support import (
    PUBLIC_IP,
    PublicHostRunner,
    fresh_state,
    public_engine,
)

ENGINE = "postgresql"
DATABASE = "appdb"
PASSWORD = "Once-0nly-Generated-9!"


@dataclass
class World:
    """Everything a test touches."""

    access: PublicAccess
    service: DatabaseService
    runner: PublicHostRunner
    state: dict[str, Any]
    engine: type[BaseDatabaseManager]
    units: Path
    store: NoustStore


def events(name: str) -> list[dict[str, Any]]:
    """The audit entries of one event."""
    return [entry for entry in audit.get_log().read(limit=300) if entry["action"] == name]


def build_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ufw: str = "active",
    tls_blocker: str | None = None,
) -> World:
    """
    Build a server with a PostgreSQL, a firewall and systemd, in a temporary root.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper.
        ufw: ``active``, ``inactive`` or ``absent``.
        tls_blocker: Why the engine cannot require TLS; None when it can.

    Returns:
        The world.
    """
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "state" / "noust.db")
    units = tmp_path / "systemd"
    units.mkdir()
    proxy = tmp_path / "systemd-socket-proxyd"
    proxy.write_text("#!/bin/sh\n")
    monkeypatch.setattr(public_access, "SYSTEMD_DIR", units)
    monkeypatch.setattr(public_access, "PROXY_CANDIDATES", (proxy,))
    state = fresh_state()
    engine = public_engine(tmp_path, state, tls_blocker=tls_blocker)
    runner = PublicHostRunner(ufw=ufw)
    set_runner(runner)
    service = DatabaseService(
        resolve=lambda name: engine() if name.lower() in ("postgresql", "pg") else None,
        engines=lambda: [ENGINE],
        store=store,
        secrets=SecretStore(root=tmp_path / "state" / "secrets"),
    )
    access = PublicAccess(service, runner=runner)
    return World(access, service, runner, state, engine, units, store)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """A server whose firewall is active and whose PostgreSQL can require TLS."""
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        built.store.close()
        NoustStore.reset_instance()
        set_runner(None)


def open_it(world: World, **kwargs: Any) -> Any:
    """Make appdb public, confirmed."""
    return world.access.expose(ENGINE, DATABASE, confirm=True, **kwargs)


class TestThePlan:
    """What opening would do, decided before anything is touched."""

    def test_the_defaults(self, world: World) -> None:
        plan = world.access.plan(ENGINE, DATABASE)
        assert plan.port == 5433
        assert plan.bind_address == "0.0.0.0"  # noqa: S104
        assert plan.sources == ("any",)
        assert plan.tls == "required"
        assert plan.profile == "owner"
        assert plan.remote_user == "appdb_remote"
        assert plan.unit == "noust-dbexpose-postgresql-5433"
        assert plan.target == "127.0.0.1:5432"
        assert plan.firewall["backend"] == "ufw" and plan.firewall["active"] is True

    def test_it_changes_nothing(self, world: World) -> None:
        world.access.plan(ENGINE, DATABASE)
        assert world.access.listing() == []
        assert list(world.units.iterdir()) == []
        assert world.state["accounts"] == set()
        assert world.runner.rules == ["ufw allow 22/tcp"]

    def test_the_warnings_say_what_is_accepted(self, world: World) -> None:
        text = " ".join(world.access.plan(ENGINE, DATABASE).warnings)
        assert "port 5433" in text
        assert "try the account's password" in text
        assert "Nothing limits who connects" in text
        assert "owns the database" in text
        assert "noust db public close appdb --engine postgresql" in text

    def test_a_restricted_source_and_a_lower_profile_warn_less(self, world: World) -> None:
        plan = world.access.plan(ENGINE, DATABASE, sources=["203.0.113.0/24"], profile="read_only")
        text = " ".join(plan.warnings)
        assert "Nothing limits who connects" not in text
        assert "owns the database" not in text
        assert plan.sources == ("203.0.113.0/24",)

    def test_no_firewall_is_said(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        built = build_world(tmp_path, monkeypatch, ufw="absent")
        try:
            plan = built.access.plan(ENGINE, DATABASE)
            assert plan.firewall["manages"] is False
            assert any("No firewall that Noust changes" in line for line in plan.warnings)
        finally:
            built.store.close()
            NoustStore.reset_instance()
            set_runner(None)

    def test_an_inactive_firewall_is_said(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = build_world(tmp_path, monkeypatch, ufw="inactive")
        try:
            plan = built.access.plan(ENGINE, DATABASE)
            assert any("installed but not active" in line for line in plan.warnings)
        finally:
            built.store.close()
            NoustStore.reset_instance()
            set_runner(None)

    def test_the_ens_profile_refuses_whatever_is_asked(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.managers.database.public_access.security_profile.database_remote_listen_allowed",
            lambda: False,
        )
        with pytest.raises(ValidationError, match="ENS profile") as caught:
            world.access.expose(ENGINE, DATABASE, confirm=True)
        assert "SSH tunnel" in caught.value.details
        assert world.access.listing() == []

    def test_a_container_is_never_opened(self, world: World) -> None:
        world.engine.instance = type("I", (), {"container": "db"})()  # type: ignore[assignment]
        with pytest.raises(ValidationError, match="compose file"):
            world.access.plan(ENGINE, DATABASE)

    def test_an_engine_that_lets_anyone_in_is_refused(self, world: World) -> None:
        world.state["blockers"] = ["Redis accepts every command without a password."]
        with pytest.raises(ValidationError) as caught:
            world.access.plan(ENGINE, DATABASE)
        assert "without a password" in caught.value.details

    def test_an_engine_that_is_stopped_is_refused(self, world: World) -> None:
        world.engine.state["running"] = False
        with pytest.raises(DatabaseEngineError, match="not running"):
            world.access.plan(ENGINE, DATABASE)

    def test_a_database_that_does_not_exist_is_refused(self, world: World) -> None:
        with pytest.raises(DatabaseNotFoundError):
            world.access.plan(ENGINE, "ghost")

    def test_a_database_cannot_be_opened_twice(self, world: World) -> None:
        open_it(world)
        with pytest.raises(ValidationError, match="already reachable"):
            world.access.plan(ENGINE, DATABASE)

    @pytest.mark.parametrize("port", [0, 80, 1023, 65536, -1, True])
    def test_a_port_outside_the_unprivileged_range_is_refused(
        self, world: World, port: Any
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            world.access.plan(ENGINE, DATABASE, port=port)
        assert caught.value.field == "port"

    def test_a_port_something_else_listens_on_is_refused(self, world: World) -> None:
        world.runner.base_ports[8080] = "nginx"
        with pytest.raises(ValidationError, match="Port 8080 is taken"):
            world.access.plan(ENGINE, DATABASE, port=8080)

    def test_sshs_port_is_taken_even_when_it_is_not_the_default(self, world: World) -> None:
        world.runner.base_ports[2222] = "sshd"
        with pytest.raises(ValidationError, match="Port 2222 is taken"):
            world.access.plan(ENGINE, DATABASE, port=2222)

    def test_the_engines_own_port_is_taken(self, world: World) -> None:
        with pytest.raises(ValidationError, match="Port 5432 is taken") as caught:
            world.access.plan(ENGINE, DATABASE, port=5432)
        assert "Suggestion: 5433" in caught.value.details

    def test_a_port_another_database_holds_is_refused(self, world: World) -> None:
        open_it(world, port=6000)
        world.engine.dbs["second"] = {"owner": "x", "size": None}
        with pytest.raises(ValidationError, match="already the public port of postgresql/appdb"):
            world.access.plan(ENGINE, "second", port=6000)

    def test_the_next_suggestion_skips_what_is_open(self, world: World) -> None:
        open_it(world)
        world.engine.dbs["second"] = {"owner": "x", "size": None}
        assert world.access.plan(ENGINE, "second").port == 5434

    @pytest.mark.parametrize("bind", ["127.0.0.1", "::1", "localhost", "999.1.1.1", "ff02::1"])
    def test_an_address_that_is_not_a_door_is_refused(self, world: World, bind: str) -> None:
        with pytest.raises(ValidationError) as caught:
            world.access.plan(ENGINE, DATABASE, bind_address=bind)
        assert caught.value.field == "bind_address"

    @pytest.mark.parametrize("bind", ["0.0.0.0", "::", "192.0.2.10"])  # noqa: S104
    def test_an_address_to_listen_on_is_accepted(self, world: World, bind: str) -> None:
        assert world.access.plan(ENGINE, DATABASE, bind_address=bind).bind_address == bind

    def test_sources_are_normalised(self, world: World) -> None:
        plan = world.access.plan(
            ENGINE, DATABASE, sources=["203.0.113.7", "198.51.100.0/24", "203.0.113.7/32"]
        )
        assert plan.sources == ("203.0.113.7/32", "198.51.100.0/24")

    @pytest.mark.parametrize("sources", [["any"], ["0.0.0.0/0"], ["::/0"], [], None, ["", " "]])
    def test_anyone_is_one_source(self, world: World, sources: list[str] | None) -> None:
        assert world.access.plan(ENGINE, DATABASE, sources=sources).sources == ("any",)

    @pytest.mark.parametrize("source", ["nope", "10.0.0.0/33", "1.2.3", "10.0.0.1; rm"])
    def test_a_source_that_is_not_an_address_is_refused(self, world: World, source: str) -> None:
        with pytest.raises(ValidationError) as caught:
            world.access.plan(ENGINE, DATABASE, sources=[source])
        assert caught.value.field == "sources"

    def test_too_many_sources_are_refused(self, world: World) -> None:
        many = [f"10.0.{n}.1" for n in range(21)]
        with pytest.raises(ValidationError, match="At most 20"):
            world.access.plan(ENGINE, DATABASE, sources=many)

    def test_an_unknown_profile_is_refused(self, world: World) -> None:
        with pytest.raises(ValidationError, match="Unknown access profile"):
            world.access.plan(ENGINE, DATABASE, profile="root")

    def test_an_unknown_tls_setting_is_refused(self, world: World) -> None:
        with pytest.raises(ValidationError, match="not a TLS setting"):
            world.access.plan(ENGINE, DATABASE, tls="maybe")

    def test_without_the_proxy_binary_it_is_refused(
        self, world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(public_access, "PROXY_CANDIDATES", (tmp_path / "missing",))
        with pytest.raises(ValidationError, match="systemd-socket-proxyd"):
            world.access.plan(ENGINE, DATABASE)

    def test_an_account_left_behind_is_refused_with_the_command_to_drop_it(
        self, world: World
    ) -> None:
        world.state["accounts"].add("appdb_remote")
        with pytest.raises(ValidationError, match="exists already") as caught:
            world.access.plan(ENGINE, DATABASE)
        assert "user-delete appdb_remote" in caught.value.details

    def test_a_unit_that_is_not_ours_is_not_overwritten(self, world: World) -> None:
        (world.units / "noust-dbexpose-postgresql-5433.socket").write_text("[Socket]\n")
        with pytest.raises(ServiceError, match="Noust did not write it"):
            world.access.plan(ENGINE, DATABASE)


class TestTls:
    """Required where the engine can, and asked about where it cannot."""

    def test_an_engine_that_cannot_require_it_says_so_and_how_to_go_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = build_world(tmp_path, monkeypatch, tls_blocker="Redis cannot require TLS")
        try:
            with pytest.raises(ValidationError, match="Redis cannot require TLS") as caught:
                built.access.plan(ENGINE, DATABASE)
            assert "tls=optional" in caught.value.details
            assert caught.value.field == "tls"
        finally:
            built.store.close()
            NoustStore.reset_instance()
            set_runner(None)

    def test_plaintext_has_to_be_asked_for_and_is_warned_about(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = build_world(tmp_path, monkeypatch, tls_blocker="cannot")
        try:
            plan = built.access.plan(ENGINE, DATABASE, tls="optional")
            assert plan.tls == "optional"
            assert any("in clear" in warning for warning in plan.warnings)
        finally:
            built.store.close()
            NoustStore.reset_instance()
            set_runner(None)

    def test_plaintext_can_be_chosen_on_an_engine_that_could_require_it(self, world: World) -> None:
        result = open_it(world, tls="optional")
        assert result.exposure.tls == "optional"
        assert ("set_remote_tls", "appdb_remote", DATABASE, True) not in world.state["calls"]
        assert result.connection["options"] == {}


class TestOpening:
    """The proxy, the account, TLS and the firewall, in that order, recorded."""

    def test_it_needs_the_confirmation_and_changes_nothing_without_it(self, world: World) -> None:
        with pytest.raises(ConfirmationRequired) as caught:
            world.access.expose(ENGINE, DATABASE)
        assert any("port 5433" in warning for warning in caught.value.warnings)
        assert world.access.listing() == []
        assert list(world.units.iterdir()) == []
        assert world.state["accounts"] == set()
        assert world.runner.rules == ["ufw allow 22/tcp"]
        assert ("daemon-reload",) not in [call[1:2] for call in world.runner.calls]

    def test_the_proxy_forwards_to_the_loopback_and_the_engine_is_not_touched(
        self, world: World
    ) -> None:
        open_it(world, port=5433)
        socket = (world.units / "noust-dbexpose-postgresql-5433.socket").read_text()
        service = (world.units / "noust-dbexpose-postgresql-5433.service").read_text()
        assert "Generated by Noust" in socket and "Generated by Noust" in service
        assert "ListenStream=0.0.0.0:5433" in socket
        assert "Service=noust-dbexpose-postgresql-5433.service" in socket
        assert " --connections-max=256 127.0.0.1:5432" in service
        assert "Requires=noust-dbexpose-postgresql-5433.socket postgresql.service" in service
        assert "DynamicUser=yes" in service and "NoNewPrivileges=yes" in service
        assert (
            stat.S_IMODE((world.units / "noust-dbexpose-postgresql-5433.socket").stat().st_mode)
            == 0o644
        )
        calls = [call for call in world.runner.calls if call[0] == "systemctl"]
        assert ("systemctl", "daemon-reload") in calls
        assert ("systemctl", "enable", "--now", "noust-dbexpose-postgresql-5433.socket") in calls
        # Nothing restarted or reloaded the engine.
        assert not any(call[1] in ("restart", "reload") for call in calls)

    def test_an_ipv6_address_is_bracketed(self, world: World) -> None:
        open_it(world, bind_address="::")
        socket = (world.units / "noust-dbexpose-postgresql-5433.socket").read_text()
        assert "ListenStream=[::]:5433" in socket

    def test_the_account_is_made_for_the_database_with_the_chosen_access(
        self, world: World
    ) -> None:
        open_it(world, profile="read_only")
        assert world.state["accounts"] == {"appdb_remote"}
        assert world.state["profiles"]["appdb_remote"] == (DATABASE, "read_only")
        assert world.state["tls"]["appdb_remote"] is True

    def test_the_applications_accounts_are_never_the_ones_given_out(self, world: World) -> None:
        world.state["accounts"].add("app")
        result = open_it(world)
        assert result.connection["username"] == "appdb_remote"
        assert result.connection["username"] != "app"

    def test_the_firewall_is_told_through_the_one_manager(self, world: World) -> None:
        open_it(world, sources=["203.0.113.7", "198.51.100.0/24"])
        added = [rule for rule in world.runner.rules if "5433" in rule]
        assert len(added) == 2
        assert "from 203.0.113.7/32 to any port 5433 proto tcp" in added[0]
        assert "comment 'noust: db postgresql appdb 5433'" in added[0]

    def test_anyone_is_one_rule(self, world: World) -> None:
        open_it(world)
        (rule,) = [rule for rule in world.runner.rules if "5433" in rule]
        assert "from any to any port 5433 proto tcp" in rule

    def test_what_was_done_is_recorded(self, world: World) -> None:
        open_it(world, sources=["203.0.113.7"])
        (record,) = world.access.listing()
        assert record.state == "exposed"
        assert (record.engine, record.db_name, record.port) == (ENGINE, DATABASE, 5433)
        assert record.sources == ("203.0.113.7/32",)
        assert record.remote_user == "appdb_remote"
        assert record.firewall == {
            "backend": "ufw",
            "active": True,
            "rules": [{"source": "203.0.113.7/32", "created": True}],
        }

    def test_the_password_is_shown_once_and_can_be_revealed_again(self, world: World) -> None:
        result = open_it(world)
        assert result.password == PASSWORD
        assert result.connection["password"] == PASSWORD
        assert quote(PASSWORD, safe="") in result.connection["url"]
        revealed = world.access.reveal(ENGINE, DATABASE)
        assert revealed["password"] == PASSWORD
        assert events("db.user.password.show")

    def test_the_connection_says_where_and_how(self, world: World) -> None:
        connection = open_it(world).connection
        assert connection["host"] == PUBLIC_IP
        assert connection["port"] == 5433
        assert connection["options"] == {"sslmode": "require"}
        assert connection["url"].endswith(f"@{PUBLIC_IP}:5433/appdb?sslmode=require")

    def test_a_listener_on_one_address_is_what_the_connection_says(self, world: World) -> None:
        assert open_it(world, bind_address="192.0.2.10").connection["host"] == "192.0.2.10"

    def test_it_is_audited_and_the_audit_has_no_password(self, world: World) -> None:
        open_it(world, sources=["203.0.113.7"])
        (entry,) = events("db.expose")
        assert entry["resource"] == "db:postgresql/appdb"
        assert entry["details"]["port"] == 5433
        assert entry["details"]["tls"] == "required"
        assert entry["details"]["remote_user"] == "appdb_remote"
        assert PASSWORD not in str(entry)
        assert events("server.firewall")

    def test_the_port_is_listening_afterwards(self, world: World) -> None:
        open_it(world)
        assert 5433 in world.access.listening()

    def test_it_says_nothing_is_listening_when_the_socket_did_not_open(self, world: World) -> None:
        # systemd said yes, but nothing holds the port: not a success.
        world.runner.open_sockets = False
        with pytest.raises(ServiceError, match="Nothing listens on port 5433"):
            open_it(world)
        assert world.access.listing() == []
        assert world.state["accounts"] == set()
        assert list(world.units.iterdir()) == []

    def test_a_failed_firewall_does_not_leave_a_door_behind(self, world: World) -> None:
        world.runner.fail[("ufw", "allow")] = "ERROR: bad rule"
        with pytest.raises(SecurityError, match="refused"):
            open_it(world)
        assert world.access.listing() == []
        assert list(world.units.iterdir()) == []
        assert world.state["accounts"] == set()
        assert world.runner.sockets == {}

    def test_a_failed_socket_start_undoes_the_account_and_the_units(self, world: World) -> None:
        world.runner.fail[("systemctl", "enable")] = "Failed to enable"
        with pytest.raises(ServiceError, match="did not start"):
            open_it(world)
        assert world.access.listing() == []
        assert list(world.units.iterdir()) == []
        assert world.state["accounts"] == set()
        assert world.state["tls"] == {}

    def test_a_refusal_of_the_tls_line_undoes_the_account(self, world: World) -> None:
        world.state["tls_fails"] = True
        with pytest.raises(Exception, match="refused the line"):
            open_it(world)
        assert world.access.listing() == []
        assert list(world.units.iterdir()) == []
        assert world.state["accounts"] == set()

    def test_what_cannot_be_undone_stays_on_record_closing(self, world: World) -> None:
        world.runner.fail[("ufw", "allow")] = "ERROR: bad rule"
        world.engine.state["running"] = True
        original = world.engine.drop_user

        def refuse(self: Any, username: str, host: str = "localhost") -> None:
            from noust.core.exceptions import DatabaseUserError

            raise DatabaseUserError("engine refused the drop")

        world.engine.drop_user = refuse  # type: ignore[method-assign]
        try:
            with pytest.raises(SecurityError):
                open_it(world)
        finally:
            world.engine.drop_user = original  # type: ignore[method-assign]
        (record,) = world.access.listing()
        assert record.state == "closing"
        # Finishing it from the record is the operator's `close`.
        outcome = world.access.make_private(ENGINE, DATABASE)
        assert "account" in outcome.removed
        assert world.access.listing() == []

    def test_a_dry_run_changes_nothing_on_disk(self, world: World) -> None:
        # The plan is what a rehearsal shows: it never writes.
        world.access.plan(ENGINE, DATABASE)
        assert list(world.units.iterdir()) == []


class TestTheFirewallThatIsNotThere:
    """No firewall to tell, and a firewall that is not on."""

    def test_without_one_no_rule_is_made_and_none_is_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = build_world(tmp_path, monkeypatch, ufw="absent")
        try:
            open_it(built)
            (record,) = built.access.listing()
            assert record.firewall is not None
            assert record.firewall["rules"] == [{"source": "any", "created": False}]
            assert not [c for c in built.runner.calls if c[0] == "ufw"]
            built.access.make_private(ENGINE, DATABASE)
            assert not [c for c in built.runner.calls if c[:2] == ("ufw", "delete")]
        finally:
            built.store.close()
            NoustStore.reset_instance()
            set_runner(None)

    def test_an_inactive_ufw_still_keeps_the_rule_for_the_day_it_is_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built = build_world(tmp_path, monkeypatch, ufw="inactive")
        try:
            open_it(built)
            (record,) = built.access.listing()
            assert record.firewall is not None and record.firewall["active"] is False
            assert any("5433" in rule for rule in built.runner.rules)
        finally:
            built.store.close()
            NoustStore.reset_instance()
            set_runner(None)

    def test_an_operators_own_identical_rule_is_left_when_it_is_closed(self, world: World) -> None:
        world.runner.rules.append("ufw allow from any to any port 5433 proto tcp")
        open_it(world)
        (record,) = world.access.listing()
        assert record.firewall is not None
        assert record.firewall["rules"] == [{"source": "any", "created": False}]
        world.access.make_private(ENGINE, DATABASE)
        assert "ufw allow from any to any port 5433 proto tcp" in world.runner.rules


class TestMakingItPrivate:
    """Everything making it public made goes, and nothing else."""

    def test_it_removes_the_rule_the_units_the_tls_line_and_the_account(self, world: World) -> None:
        open_it(world, sources=["203.0.113.7"])
        outcome = world.access.make_private(ENGINE, DATABASE)

        assert outcome.removed == ["firewall", "proxy", "tls", "account"]
        assert world.runner.rules == ["ufw allow 22/tcp"]
        assert list(world.units.iterdir()) == []
        assert world.runner.sockets == {}
        assert world.state["accounts"] == set()
        assert world.state["tls"] == {}
        assert world.access.listing() == []
        calls = world.runner.calls
        assert ("systemctl", "disable", "--now", "noust-dbexpose-postgresql-5433.socket") in calls
        assert ("systemctl", "stop", "noust-dbexpose-postgresql-5433.service") in calls

    def test_it_is_audited(self, world: World) -> None:
        open_it(world)
        world.access.make_private(ENGINE, DATABASE)
        (entry,) = events("db.unexpose")
        assert entry["resource"] == "db:postgresql/appdb"
        assert entry["details"]["port"] == 5433

    def test_a_database_that_was_never_public_has_nothing_to_undo(self, world: World) -> None:
        outcome = world.access.make_private(ENGINE, DATABASE)
        assert outcome.already_private is True and outcome.removed == []
        assert events("db.unexpose") == []

    def test_the_port_can_be_opened_again_afterwards(self, world: World) -> None:
        open_it(world, port=5433)
        world.access.make_private(ENGINE, DATABASE)
        again = open_it(world, port=5433)
        assert again.exposure.port == 5433

    def test_an_engine_that_is_stopped_closes_the_door_and_says_what_is_left(
        self, world: World
    ) -> None:
        open_it(world)
        world.engine.state["running"] = False
        with pytest.raises(DatabaseEngineError) as caught:
            world.access.make_private(ENGINE, DATABASE)
        assert "tls: " in caught.value.details and "account: " in caught.value.details
        assert "noust db public close appdb --engine postgresql" in caught.value.details
        # The network side is closed whatever the engine is doing.
        assert world.runner.sockets == {}
        assert list(world.units.iterdir()) == []
        assert world.runner.rules == ["ufw allow 22/tcp"]
        (record,) = world.access.listing()
        assert record.state == "closing"

        world.engine.state["running"] = True
        outcome = world.access.make_private(ENGINE, DATABASE)
        assert "account" in outcome.removed
        assert world.access.listing() == []
        assert world.state["accounts"] == set()

    def test_a_unit_that_is_not_ours_is_not_removed_and_the_rest_still_is(
        self, world: World
    ) -> None:
        open_it(world)
        path = world.units / "noust-dbexpose-postgresql-5433.socket"
        path.write_text("[Socket]\nListenStream=5433\n")
        with pytest.raises(DatabaseEngineError) as caught:
            world.access.make_private(ENGINE, DATABASE)
        assert "Noust did not write it" in caught.value.details
        assert path.exists()
        assert world.state["accounts"] == set()
        assert world.runner.rules == ["ufw allow 22/tcp"]

    def test_nothing_belonging_to_an_application_is_touched(self, world: World) -> None:
        world.state["accounts"].add("app")
        open_it(world)
        world.access.make_private(ENGINE, DATABASE)
        assert "app" in world.state["accounts"]
        assert ("drop_user", "app") not in world.state["calls"]


class TestTheRecord:
    """The row says how far an action got."""

    def test_it_is_written_before_anything_is_changed(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[Any] = []
        original = world.service.create_user

        def spy(*args: Any, **kwargs: Any) -> Any:
            seen.append(ExposureStore(world.store).get(ENGINE, DATABASE))
            return original(*args, **kwargs)

        monkeypatch.setattr(world.service, "create_user", spy)
        open_it(world)
        assert seen[0] is not None and seen[0].state == "opening"

    def test_one_database_and_one_port_at_a_time(self, world: World) -> None:
        store = ExposureStore(world.store)
        kwargs: dict[str, Any] = {
            "engine": ENGINE,
            "db_name": "a",
            "port": 6000,
            "bind_address": "0.0.0.0",  # noqa: S104
            "sources": ("any",),
            "tls": "required",
            "remote_user": "a_remote",
            "profile": "owner",
            "unit": "u",
            "created_by": None,
        }
        store.create(**kwargs)
        with pytest.raises(DatabaseExistsError):
            store.create(**{**kwargs, "port": 6001})
        with pytest.raises(DatabaseExistsError):
            store.create(**{**kwargs, "db_name": "b"})


class TestNames:
    """The account's name fits the engine."""

    def test_the_usual_name(self, world: World) -> None:
        assert remote_user_name(world.engine(), "shop") == "shop_remote"

    def test_a_name_longer_than_the_engine_allows_is_shortened_with_a_hash(
        self, world: World
    ) -> None:
        manager = world.engine()
        manager.MAX_USER_NAME_LENGTH = 32  # type: ignore[misc]
        first = remote_user_name(manager, "a_database_with_a_very_long_name_one")
        second = remote_user_name(manager, "a_database_with_a_very_long_name_two")
        assert len(first) <= 32 and len(second) <= 32
        assert first != second and first.endswith("_remote")

    def test_a_slot_number_becomes_a_name(self, world: World) -> None:
        assert remote_user_name(world.engine(), "0") == "db_0_remote"


class TestTheGuardsAtTheChokepoint:
    """The service refuses what would pull the rug from under a public database."""

    def test_a_public_database_is_not_dropped(self, world: World) -> None:
        from noust.core.exceptions import DatabaseError

        open_it(world)
        with pytest.raises(DatabaseError, match="reachable from the network") as caught:
            world.service.drop(ENGINE, DATABASE, keep_backup=False)
        assert "noust db public close appdb --engine postgresql" in caught.value.details
        assert DATABASE in world.engine.dbs
        assert events("db.drop")[-1]["result"] == "denied"

    def test_it_is_dropped_once_it_is_private(self, world: World) -> None:
        open_it(world)
        world.access.make_private(ENGINE, DATABASE)
        world.service.drop(ENGINE, DATABASE, keep_backup=False)
        assert DATABASE not in world.engine.dbs

    def test_the_account_of_a_public_database_is_not_dropped(self, world: World) -> None:
        from noust.core.exceptions import DatabaseUserError

        open_it(world)
        with pytest.raises(DatabaseUserError, match="account of the public database appdb"):
            world.service.drop_user(ENGINE, "appdb_remote")
        assert "appdb_remote" in world.state["accounts"]
