"""
A central notices by itself: host key changes, outages with nobody looking, expected reboots.

- The tunnel manager tells the operator once when a node presents another SSH
  host key, with both fingerprints and the command that sorts it out.
- The reachability probe asks every node on a timer, through the one
  aggregator, single-flight across processes, only when a round is wanted and
  can reach anything.
- A node asked to reboot through the central is "rebooting", not an outage,
  for a bounded time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import NodeUnreachableError
from noust.core.store import NodeRecord, NoustStore
from noust.fleet import aggregate, expected
from noust.fleet.aggregate import gather, set_aggregator
from noust.fleet.keys import known_hosts_line
from noust.fleet.models import parse_public_key
from noust.fleet.probe import ReachabilityProbe
from tests.fleet_support import HOST_KEY, build_fleet
from tests.fleet_views_support import build_central

PRESENTED = "SHA256:7vA1xXhSvS4wNfAhXk3mZqk4cN0YyHq3y4w4vJmP2cQ"

CHANGED = (
    "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
    "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
    "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
    "The fingerprint for the ED25519 key sent by the remote host is\n"
    f"{PRESENTED}.\n"
    "Please contact your system administrator.\n"
    "Host key verification failed."
)


@pytest.fixture(autouse=True)
def _central_named_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")


@pytest.fixture
def fleet(tmp_path: Path):
    return build_fleet(tmp_path)


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


def _register(fleet: Any, name: str = "web-2") -> NodeRecord:
    return fleet.store.save_node(
        NodeRecord(
            name=name,
            ssh_host="web2.example.com",
            ssh_port=2222,
            ssh_user="noust-tunnel",
            host_key=known_hosts_line(name, HOST_KEY),
            console_port=8080,
        )
    )


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    """Every fleet notification sent, by kind."""
    sent: list[tuple[str, str, dict[str, Any]]] = []
    for kind in ("unreachable", "recovered", "host_key_changed"):
        monkeypatch.setattr(
            f"noust.core.notifications.fleet.notify_node_{kind}",
            lambda node, _kind=kind, **kw: sent.append((_kind, node, kw)),
        )
    return sent


# ---------------------------------------------------------------------------
# A changed host key
# ---------------------------------------------------------------------------


class TestHostKeyChanged:
    def test_the_operator_is_told_once_with_both_fingerprints(self, fleet, told):
        _register(fleet)
        fleet.runner.script(["ssh"], exit_code=255, stderr=CHANGED)

        with pytest.raises(NodeUnreachableError):
            fleet.tunnels.endpoint("web-2")
        for _ in range(3):
            fleet.clock.advance(120)
            with pytest.raises(NodeUnreachableError):
                fleet.tunnels.endpoint("web-2")

        assert [(kind, node) for kind, node, _ in told] == [("host_key_changed", "web-2")]
        details = told[0][2]
        assert details["pinned"] == parse_public_key(HOST_KEY).fingerprint
        assert details["presented"] == PRESENTED
        assert details["address"] == "noust-tunnel@web2.example.com:2222"
        assert details["command"] == "noust node rekey web-2"

    def test_a_new_change_after_a_tunnel_opened_is_told_again(self, fleet, told):
        _register(fleet)
        fleet.runner.script(["ssh"], exit_code=255, stderr=CHANGED)
        with pytest.raises(NodeUnreachableError):
            fleet.tunnels.endpoint("web-2")
        fleet.runner.script(["ssh"], exit_code=0)
        fleet.clock.advance(120)
        fleet.tunnels.endpoint("web-2")
        fleet.runner.processes[-1].die(255, CHANGED)
        fleet.runner.script(["ssh"], exit_code=255, stderr=CHANGED)
        fleet.clock.advance(120)

        with pytest.raises(NodeUnreachableError):
            fleet.tunnels.endpoint("web-2")

        assert [kind for kind, _, _ in told] == ["host_key_changed", "host_key_changed"]

    def test_another_failure_is_not_a_host_key_change(self, fleet, told):
        _register(fleet)
        fleet.runner.script(["ssh"], exit_code=255, stderr="ssh: connect: Connection refused")

        with pytest.raises(NodeUnreachableError):
            fleet.tunnels.endpoint("web-2")

        assert told == []


# ---------------------------------------------------------------------------
# An expected reboot
# ---------------------------------------------------------------------------


def _row(result: Any, name: str) -> dict[str, Any]:
    return next(row for row in result.items if row["node"] == name)


class TestExpectedReboot:
    def test_a_node_asked_to_reboot_is_rebooting_not_an_outage(self, central, told):
        expected.expect_reboot("web-3", due=None, requested_by="alice", store=central.store)
        central.nodes["web-3"].down = True

        gather("servers", manager=central.manager)
        central.fleet.clock.advance(aggregate.UNREACHABLE_GRACE_SECONDS + 1)
        result = gather("servers", manager=central.manager)

        assert told == []
        row = _row(result, "web-3")
        assert row["reachability"] == "rebooting"
        assert row["expected_outage"]["kind"] == "reboot"
        assert row["expected_outage"]["requested_by"] == "alice"
        assert _row(result, "web-2")["expected_outage"] is None

    def test_once_the_expectation_runs_out_it_is_an_outage_again(self, central, told):
        long_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        expected.expect_reboot(
            "web-3", due=None, requested_by="alice", store=central.store, now=long_ago
        )
        central.nodes["web-3"].down = True

        gather("servers", manager=central.manager)
        central.fleet.clock.advance(aggregate.UNREACHABLE_GRACE_SECONDS + 1)
        result = gather("servers", manager=central.manager)

        assert [(kind, node) for kind, node, _ in told] == [("unreachable", "web-3")]
        assert _row(result, "web-3")["reachability"] == "unreachable"
        assert expected.current("web-3", store=central.store) is None

    def test_a_node_back_after_its_reboot_was_due_is_no_longer_expected_to_fail(
        self, central, told
    ):
        due = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        expected.expect_reboot("web-3", due=due, requested_by="alice", store=central.store)

        gather("servers", manager=central.manager)

        assert expected.current("web-3", store=central.store) is None
        assert told == []

    def test_a_node_that_answers_before_its_reboot_keeps_the_expectation(self, central):
        due = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        expected.expect_reboot("web-3", due=due, requested_by="alice", store=central.store)

        gather("servers", manager=central.manager)

        assert expected.current("web-3", store=central.store) is not None

    def test_the_proxy_records_a_reboot_and_forgets_a_cancelled_one(self, central):
        due = (datetime.now(timezone.utc) + timedelta(minutes=10)).replace(microsecond=0)
        body = f'{{"action": "reboot", "scheduled_for": "{due.isoformat()}"}}'.encode()

        assert expected.watches("POST", "/api/server/power/reboot")
        assert not expected.watches("POST", "/api/server/power/shutdown")
        refused = expected.observe(
            "web-3",
            "POST",
            "/api/server/power/reboot",
            409,
            b"{}",
            actor="alice",
            store=central.store,
        )
        assert refused is None
        assert expected.current("web-3", store=central.store) is None

        recorded = expected.observe(
            "web-3",
            "POST",
            "/api/server/power/reboot",
            200,
            body,
            actor="alice",
            store=central.store,
        )
        assert recorded is not None
        assert recorded.due_at == due.isoformat()
        assert (
            recorded.expires_at
            == (due + timedelta(seconds=expected.REBOOT_GRACE_SECONDS)).isoformat()
        )

        expected.observe(
            "web-3",
            "DELETE",
            "/api/server/power/scheduled",
            200,
            b"{}",
            actor="alice",
            store=central.store,
        )
        assert expected.current("web-3", store=central.store) is None

    def test_the_expectation_goes_with_its_node(self, central):
        expected.expect_reboot("web-3", due=None, requested_by="alice", store=central.store)

        central.store.delete_node("web-3")

        assert expected.current("web-3", store=central.store) is None


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


class TestProbe:
    def probe(self, fleet: Any, tmp_path: Path, **options: Any) -> tuple[ReachabilityProbe, list]:
        rounds: list[int] = []
        options.setdefault("daemon", False)
        probe = ReachabilityProbe(
            store=fleet.store,
            lock_path=tmp_path / "probe.lock",
            gather_round=lambda: rounds.append(1),
            **options,
        )
        return probe, rounds

    def test_nothing_is_asked_when_no_console_is_looking(self, fleet, tmp_path):
        _register(fleet)
        probe, rounds = self.probe(fleet, tmp_path, active=lambda: False)

        assert probe.run_once() is False
        assert rounds == []

    def test_nothing_is_asked_without_a_node(self, fleet, tmp_path):
        probe, rounds = self.probe(fleet, tmp_path)

        assert probe.run_once() is False
        assert rounds == []

    def test_a_console_probes_one_round_at_a_time(self, fleet, tmp_path):
        _register(fleet)
        probe, rounds = self.probe(fleet, tmp_path)

        assert probe.run_once() is True
        assert rounds == [1]
        assert not probe.holding

    def test_a_daemon_that_probes_keeps_the_console_from_doubling_it(self, fleet, tmp_path):
        _register(fleet)
        daemon, by_daemon = self.probe(fleet, tmp_path, daemon=True)
        console, by_console = self.probe(fleet, tmp_path)

        assert daemon.run_once() is True
        assert daemon.holding
        assert console.run_once() is False
        assert (by_daemon, by_console) == ([1], [])

        daemon.stop()
        assert console.run_once() is True

    def test_a_locked_central_skips_its_round_without_holding_the_lock(
        self, fleet, tmp_path, monkeypatch
    ):
        from noust.core import sealing

        _register(fleet)
        daemon, rounds = self.probe(fleet, tmp_path, daemon=True)
        assert daemon.run_once() is True
        monkeypatch.setattr(sealing, "is_sealed", lambda root: True)
        monkeypatch.setattr(sealing, "is_unlocked", lambda root: False)

        assert daemon.run_once() is False
        assert not daemon.holding
        assert rounds == [1]

    def test_a_round_asks_every_node_the_servers_view_through_the_aggregator(self, monkeypatch):
        from noust.fleet import probe as probe_module

        asked: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            "noust.fleet.aggregate.gather",
            lambda resource, **kw: asked.append((resource, kw)),
        )

        probe_module._gather_round()

        [(resource, options)] = asked
        assert resource == "servers"
        assert options["refresh"] is True
        assert options["deadline"] is None
        assert options["asker"].scope == "read"
