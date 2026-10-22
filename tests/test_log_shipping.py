# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the logs of applications are shipped to (checklist B-73): the shipper.

Pinned here:

- the journal is read from where each destination got to, one cursor each, and a
  destination added later starts from now;
- only application units are shipped, matched by unit (the web unit, an instance of
  its blue/green template, a process's copies), narrowed by the destination's
  applications;
- secrets an application holds never leave in a line;
- the cursor moves only after a destination accepted a batch, so one that was down
  receives what it missed, and one that fails does not stop the others;
- a stack's containers are shipped from their last timestamp.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_process_store, log_drain_store
from noust.core.app_process_store import ProcessRecord
from noust.core.log_drain_store import Drain
from noust.core.redact import Scrubber
from noust.core.runner import CommandResult, FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers import log_shipping
from noust.managers.log_drains import LogDrains
from noust.managers.log_shipping import AppIndex, LogShipper
from noust.managers.log_transports import DeliveryError, LogEvent

SECRET = "hunter2-secret-value"
BASE = ["journalctl", "--no-pager", "-o", "json"]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop-example-com")
    )
    instance.create_app(
        App(domain="blog.example.com", app_type="nodejs", app_path="/srv/blog-example-com")
    )
    instance.create_app(
        App(
            domain="stack.example.com",
            app_type="docker-compose",
            app_path="/srv/stack",
            compose_project="stack",
        )
    )
    app_process_store.put_process(
        "shop.example.com",
        ProcessRecord(name="worker", command="node w.js"),
        updated_by="t",
        store=instance,
    )
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "secrets")


class Recorder:
    """A destination: keeps what it was sent, and refuses while told to."""

    def __init__(self) -> None:
        self.batches: list[list[LogEvent]] = []
        self.refuse: str | None = None
        self.closed = 0

    def send(self, events: Sequence[LogEvent]) -> None:
        if self.refuse:
            raise DeliveryError("loki answered HTTP 503", details=self.refuse)
        self.batches.append(list(events))

    def close(self) -> None:
        self.closed += 1

    @property
    def lines(self) -> list[str]:
        return [event.message for batch in self.batches for event in batch]


def entry(
    cursor: str,
    unit: str | None,
    message: Any,
    *,
    priority: int = 6,
    micros: int = 1_790_000_000_000_000,
) -> str:
    fields: dict[str, Any] = {
        "__CURSOR": cursor,
        "__REALTIME_TIMESTAMP": str(micros),
        "MESSAGE": message,
        "PRIORITY": str(priority),
    }
    if unit is not None:
        fields["_SYSTEMD_UNIT"] = unit
    return json.dumps(fields)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def destination() -> Recorder:
    return Recorder()


@pytest.fixture
def drains(store: NoustStore, secrets: SecretStore) -> LogDrains:
    return LogDrains(store=store, secrets=secrets)


def shipper(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    destination: Recorder | dict[str, Recorder],
) -> LogShipper:
    def make(drain: Drain, secret: str | None) -> Recorder:
        return destination[drain.name] if isinstance(destination, dict) else destination

    return LogShipper(
        store=store,
        runner=runner,
        secrets=secrets,
        transport_for=make,  # type: ignore[arg-type]
        scrubber_for_app=lambda domain: Scrubber([SECRET]),
        host="web-1",
    )


def add_drain(drains: LogDrains, name: str = "loki", **kwargs: Any) -> None:
    drains.save(name, "http", {"url": "https://logs.example.com"}, **kwargs)


# ---------------------------------------------------------------------------
# Which application a unit belongs to
# ---------------------------------------------------------------------------


def test_a_unit_is_matched_to_its_application_and_process(store: NoustStore) -> None:
    apps = [a for a in store.list_apps() if a.app_type != "docker-compose"]
    index = AppIndex(apps, {"shop.example.com": {"worker"}})

    assert index.lookup("shop-example-com.service") == ("shop.example.com", "web")
    assert index.lookup("shop-example-com@blue.service") == ("shop.example.com", "web")
    assert index.lookup("shop-example-com-worker@2.service") == ("shop.example.com", "worker")
    assert index.lookup("wasm-blog-example-com.service") == ("blog.example.com", "web")


@pytest.mark.parametrize(
    "unit",
    [
        None,
        "",
        "nginx.service",
        "sshd.service",
        "init.scope",
        "shop-example-com-queue@1.service",
        "shop.service",
    ],
)
def test_what_is_not_an_applications_unit_is_not_matched(
    store: NoustStore, unit: str | None
) -> None:
    index = AppIndex(
        [a for a in store.list_apps() if a.app_type != "docker-compose"],
        {"shop.example.com": {"worker"}},
    )

    assert index.lookup(unit) is None


# ---------------------------------------------------------------------------
# From now on
# ---------------------------------------------------------------------------


def test_a_new_destination_starts_from_now_and_ships_nothing_of_the_past(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    add_drain(drains)
    runner.script([*BASE, "-n", "1"], stdout=entry("s=now", "shop-example-com.service", "old line"))

    report = shipper(store, runner, secrets, destination).cycle()

    assert destination.lines == [] and report.sent == {"loki": 0}
    assert log_drain_store.get_drain("loki", store=store).cursors["journal"] == "s=now"  # type: ignore[union-attr]


def test_a_destination_added_to_an_empty_journal_tries_again_next_time(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    add_drain(drains)
    runner.script([*BASE, "-n", "1"], stdout="")

    shipper(store, runner, secrets, destination).cycle()

    assert "journal" not in log_drain_store.get_drain("loki", store=store).cursors  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Shipping
# ---------------------------------------------------------------------------


def started(drains: LogDrains, store: NoustStore, cursor: str = "c0", **kwargs: Any) -> None:
    add_drain(drains, **kwargs)
    log_drain_store.record_delivery("loki", {"journal": cursor}, 0, store=store)


def read(runner: FakeRunner, cursor: str, *entries: str) -> None:
    runner.script([*BASE, f"--after-cursor={cursor}"], stdout="\n".join(entries))


def test_only_the_lines_of_applications_are_shipped_with_their_process(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store)
    read(
        runner,
        "c0",
        entry("c1", "shop-example-com.service", "listening on 3100"),
        entry("c2", "nginx.service", "GET / 200"),
        entry("c3", "shop-example-com-worker@1.service", "job done"),
        entry("c4", "sshd.service", "Accepted publickey"),
        entry("c5", "shop-example-com@blue.service", "request", priority=3),
    )

    shipper(store, runner, secrets, destination).cycle()

    sent = [event for batch in destination.batches for event in batch]
    assert [(e.app, e.process, e.message, e.priority) for e in sent] == [
        ("shop.example.com", "web", "listening on 3100", 6),
        ("shop.example.com", "worker", "job done", 6),
        ("shop.example.com", "web", "request", 3),
    ]
    assert {e.host for e in sent} == {"web-1"}
    assert sent[0].ts_ns == 1_790_000_000_000_000 * 1000


def test_the_cursor_moves_to_the_last_entry_read_and_the_count_is_kept(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store)
    read(
        runner,
        "c0",
        entry("c1", "shop-example-com.service", "one"),
        entry("c2", "nginx.service", "not ours"),
    )

    shipper(store, runner, secrets, destination).cycle()

    after = log_drain_store.get_drain("loki", store=store)
    assert after is not None
    assert after.cursors["journal"] == "c2", "past the line that was not an application's too"
    assert after.shipped == 1 and after.last_ok_at is not None and after.last_error is None


def test_an_application_secret_never_leaves_in_a_line(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store)
    read(runner, "c0", entry("c1", "shop-example-com.service", f"db password is {SECRET} ok"))

    shipper(store, runner, secrets, destination).cycle()

    assert SECRET not in destination.lines[0]
    assert "db password is" in destination.lines[0]


def test_a_destination_narrowed_to_applications_receives_only_theirs(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store, apps=["blog.example.com"])
    read(
        runner,
        "c0",
        entry("c1", "shop-example-com.service", "shop"),
        entry("c2", "blog-example-com.service", "blog"),
    )

    shipper(store, runner, secrets, destination).cycle()

    assert destination.lines == ["blog"]


def test_bytes_a_unit_wrote_are_decoded_and_empty_lines_are_not_shipped(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store)
    read(
        runner,
        "c0",
        entry("c1", "shop-example-com.service", list("café ok".encode())),
        entry("c2", "shop-example-com.service", ""),
        entry("c3", "shop-example-com.service", "   \n"),
        entry("c4", "shop-example-com.service", None),
        "this is not json",
    )

    shipper(store, runner, secrets, destination).cycle()

    assert destination.lines == ["café ok"]


def test_a_priority_the_journal_does_not_say_is_informational(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store)
    bad = json.loads(entry("c1", "shop-example-com.service", "x"))
    bad["PRIORITY"] = "banana"
    read(runner, "c0", json.dumps(bad))

    shipper(store, runner, secrets, destination).cycle()

    assert destination.batches[0][0].priority == 6


def test_a_destination_that_is_off_is_not_sent_to(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store, enabled=False)
    read(runner, "c0", entry("c1", "shop-example-com.service", "x"))

    report = shipper(store, runner, secrets, destination).cycle()

    assert report.sent == {} and destination.batches == []


# ---------------------------------------------------------------------------
# A destination that does not answer
# ---------------------------------------------------------------------------


def test_a_destination_that_refuses_keeps_its_cursor_and_says_why(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store)
    read(runner, "c0", entry("c1", "shop-example-com.service", "one"))
    destination.refuse = "tenant over its rate limit"

    report = shipper(store, runner, secrets, destination).cycle()

    after = log_drain_store.get_drain("loki", store=store)
    assert after is not None
    assert after.cursors["journal"] == "c0", "what it missed is sent again"
    assert after.last_error is not None and "tenant over its rate limit" in after.last_error
    assert "loki" in report.failed


def test_it_receives_what_it_missed_when_it_is_back(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store)
    read(runner, "c0", entry("c1", "shop-example-com.service", "while it was down"))
    destination.refuse = "no route to host"
    ship = shipper(store, runner, secrets, destination)
    ship.cycle()

    destination.refuse = None
    ship.cycle()

    after = log_drain_store.get_drain("loki", store=store)
    assert destination.lines == ["while it was down"]
    assert after is not None and after.last_error is None and after.cursors["journal"] == "c1"


def test_one_destination_failing_does_not_stop_the_others(
    store: NoustStore, runner: FakeRunner, secrets: SecretStore, drains: LogDrains
) -> None:
    started(drains, store)
    drains.save("axiom-copy", "http", {"url": "https://other.example.com"})
    log_drain_store.record_delivery("axiom-copy", {"journal": "c0"}, 0, store=store)
    read(runner, "c0", entry("c1", "shop-example-com.service", "one"))
    broken, healthy = Recorder(), Recorder()
    broken.refuse = "down"

    report = shipper(store, runner, secrets, {"loki": broken, "axiom-copy": healthy}).cycle()

    assert healthy.lines == ["one"]
    assert set(report.failed) == {"loki"} and report.sent == {"axiom-copy": 1}


def test_a_journal_that_cannot_be_read_is_the_destinations_error_to_retry(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store)
    runner.script([*BASE, "--after-cursor=c0"], exit_code=1, stderr="Failed to open journal")

    report = shipper(store, runner, secrets, destination).cycle()

    assert "journal could not be read" in report.failed["loki"]
    assert log_drain_store.get_drain("loki", store=store).cursors["journal"] == "c0"  # type: ignore[union-attr]


def test_the_transport_is_closed_after_every_cycle(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store)
    read(runner, "c0")

    shipper(store, runner, secrets, destination).cycle()

    assert destination.closed == 1


def test_a_long_outage_is_caught_up_over_several_reads(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(log_shipping, "JOURNAL_ENTRIES", 2)
    started(drains, store)
    read(
        runner,
        "c0",
        entry("c1", "shop-example-com.service", "one"),
        entry("c2", "shop-example-com.service", "two"),
    )
    read(runner, "c2", entry("c3", "shop-example-com.service", "three"))

    shipper(store, runner, secrets, destination).cycle()

    assert destination.lines == ["one", "two", "three"]
    assert log_drain_store.get_drain("loki", store=store).cursors["journal"] == "c3"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# A stack's containers
# ---------------------------------------------------------------------------

PS = ["docker", "ps", "--filter", "label=com.docker.compose.project=stack"]
KEY = "compose:stack.example.com"


class Docker(FakeRunner):
    """``docker ps`` names the containers; ``docker logs`` answers by container id."""

    def __init__(self, logs: dict[str, tuple[str, str]] | None = None) -> None:
        super().__init__()
        self.logs = logs or {}

    def _lookup(
        self, argv: Sequence[str], user: str | None = None, env: Any = None
    ) -> CommandResult:
        result = super()._lookup(argv, user, env)
        if list(argv[:2]) == ["docker", "logs"] and argv[-1] in self.logs:
            out, err = self.logs[argv[-1]]
            return CommandResult(argv=tuple(argv), exit_code=0, stdout=out, stderr=err)
        return result


def stack_up(docker: Docker, *containers: str) -> None:
    docker.script(PS, stdout="\n".join(containers))
    docker.script([*BASE, "--after-cursor=c0"], stdout="")


def test_a_stack_is_followed_from_the_moment_the_destination_was_added(
    store: NoustStore, secrets: SecretStore, drains: LogDrains, destination: Recorder
) -> None:
    docker = Docker()
    started(drains, store)
    stack_up(docker, "aaa111|web|running")

    shipper(store, docker, secrets, destination).cycle()

    assert destination.lines == []
    assert not [call for call in docker.calls if call[:2] == ("docker", "logs")]
    assert int(log_drain_store.get_drain("loki", store=store).cursors[KEY]) > 0  # type: ignore[union-attr]


def test_a_stack_ships_what_its_containers_wrote_after_the_last_line_shipped(
    store: NoustStore, secrets: SecretStore, drains: LogDrains, destination: Recorder
) -> None:
    docker = Docker(
        {
            "aaa111": (
                "2026-09-21T14:13:20.000000000Z already shipped\n"
                f"2026-09-21T14:13:21.5Z listening with {SECRET}\n"
                "no timestamp on this one\n",
                "2026-09-21T14:13:22.000000001Z crashed and came back\n",
            ),
            "bbb222": ("2026-09-21T14:13:21.9Z job done\n", ""),
        }
    )
    started(drains, store)
    log_drain_store.record_delivery("loki", {KEY: "1790000000000000000"}, 0, store=store)
    stack_up(docker, "aaa111|web|running", "bbb222|worker|running")

    shipper(store, docker, secrets, destination).cycle()

    sent = [e for batch in destination.batches for e in batch]
    assert [(e.process, e.unit, e.ts_ns) for e in sent] == [
        ("web", "aaa111", 1_790_000_001_500_000_000),
        ("worker", "bbb222", 1_790_000_001_900_000_000),
        ("web", "aaa111", 1_790_000_002_000_000_001),
    ], "from every container, in the order they were written"
    assert SECRET not in sent[0].message and "listening with" in sent[0].message
    assert {e.app for e in sent} == {"stack.example.com"}
    cursors = log_drain_store.get_drain("loki", store=store).cursors  # type: ignore[union-attr]
    assert cursors[KEY] == "1790000002000000001"


def test_a_container_is_read_between_the_last_line_and_the_start_of_the_cycle(
    store: NoustStore, secrets: SecretStore, drains: LogDrains, destination: Recorder
) -> None:
    docker = Docker()
    started(drains, store)
    log_drain_store.record_delivery("loki", {KEY: "1790000000000000000"}, 0, store=store)
    stack_up(docker, "aaa111|web|running")

    shipper(store, docker, secrets, destination).cycle()

    (call,) = [c for c in docker.calls if c[:2] == ("docker", "logs")]
    assert call[:5] == (
        "docker",
        "logs",
        "--timestamps",
        "--since",
        "2026-09-21T14:13:20.000000000Z",
    )
    assert call[5] == "--until" and call[-1] == "aaa111"
    assert call[6].endswith("Z") and call[6] > call[4], (
        "up to now, so a slow container loses nothing"
    )


def test_a_stack_that_is_down_ships_nothing_and_is_not_an_error(
    store: NoustStore, secrets: SecretStore, drains: LogDrains, destination: Recorder
) -> None:
    docker = Docker()
    started(drains, store)
    log_drain_store.record_delivery("loki", {KEY: "1"}, 0, store=store)
    stack_up(docker)

    report = shipper(store, docker, secrets, destination).cycle()

    assert report.failed == {} and destination.lines == []


def test_docker_not_answering_is_the_stacks_state_not_the_destinations(
    store: NoustStore, secrets: SecretStore, drains: LogDrains, destination: Recorder
) -> None:
    docker = Docker()
    started(drains, store)
    log_drain_store.record_delivery("loki", {KEY: "1"}, 0, store=store)
    docker.script([*BASE, "--after-cursor=c0"], stdout="")
    docker.script(PS, exit_code=1, stderr="Cannot connect to the Docker daemon")

    report = shipper(store, docker, secrets, destination).cycle()

    assert report.failed == {} and destination.lines == []
    assert log_drain_store.get_drain("loki", store=store).last_error is None  # type: ignore[union-attr]


def test_a_container_removed_since_it_was_listed_is_skipped(
    store: NoustStore, secrets: SecretStore, drains: LogDrains, destination: Recorder
) -> None:
    docker = Docker({"bbb222": ("2026-09-21T14:13:21.9Z kept\n", "")})
    started(drains, store)
    log_drain_store.record_delivery("loki", {KEY: "1790000000000000000"}, 0, store=store)
    stack_up(docker, "aaa111|web|running", "bbb222|worker|running")
    docker.script(["docker", "logs"], exit_code=1, stderr="No such container: aaa111")

    shipper(store, docker, secrets, destination).cycle()

    assert destination.lines == ["kept"]


# ---------------------------------------------------------------------------
# The loop and the unit
# ---------------------------------------------------------------------------


def test_the_loop_runs_cycles_until_told_to_stop(
    store: NoustStore,
    runner: FakeRunner,
    secrets: SecretStore,
    drains: LogDrains,
    destination: Recorder,
) -> None:
    started(drains, store)
    read(runner, "c0")
    stop = threading.Event()
    ship = shipper(store, runner, secrets, destination)
    cycles = []
    real = ship.cycle

    def counting() -> Any:
        cycles.append(1)
        if len(cycles) == 3:
            stop.set()
        return real()

    ship.cycle = counting  # type: ignore[method-assign]

    ship.run(stop, interval=0)

    assert len(cycles) == 3


class Services:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.exists = True

    def install_unit(self, name: str, template: str, context: dict[str, Any]) -> Path:
        self.calls.append(("install", name, template, context["exec_start"]))
        return Path(f"/etc/systemd/system/{name}.service")

    def enable(self, name: str) -> bool:
        self.calls.append(("enable", name))
        return True

    def start(self, name: str) -> None:
        self.calls.append(("start", name))

    def inspect_unit(self, name: str, *, serving: bool = True) -> Any:
        return type("Info", (), {"exists": self.exists})()

    def delete_service(self, name: str) -> None:
        self.calls.append(("delete", name))


def test_the_unit_runs_the_shipper_in_the_foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    services = Services()
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    monkeypatch.setattr("noust.core.utils.find_noust_executable", lambda: "/usr/bin/noust")

    state = log_shipping.ensure_service(services=services)

    assert state == "started"
    assert services.calls == [
        ("install", "noust-logship", "logship", "/usr/bin/noust logdrain run"),
        ("enable", "noust-logship"),
        ("start", "noust-logship"),
    ]


def test_a_machine_without_systemd_has_no_unit_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    services = Services()
    monkeypatch.setattr(Path, "is_dir", lambda self: False)

    assert log_shipping.ensure_service(services=services) == "no_systemd"
    assert services.calls == []


def test_the_unit_is_removed_only_when_it_is_there() -> None:
    services = Services()

    assert log_shipping.remove_service(services=services) is True
    assert services.calls == [("delete", "noust-logship")]
    services.calls.clear()
    services.exists = False
    assert log_shipping.remove_service(services=services) is False
    assert services.calls == []


def test_the_unit_state_is_read_from_systemd(runner: FakeRunner) -> None:
    runner.script(
        ["systemctl", "show", "noust-logship.service"],
        stdout="LoadState=loaded\nActiveState=active\n",
    )

    assert log_shipping.service_state(runner=runner) == {
        "installed": True,
        "active": True,
        "state": "active",
    }
    runner.script(
        ["systemctl", "show", "noust-logship.service"],
        stdout="LoadState=not-found\nActiveState=inactive\n",
    )
    assert log_shipping.service_state(runner=runner)["installed"] is False
