"""
Tests for the fact cache and the server summary built on it.

The rule under both: nothing slow runs in a request. A first look at a fact starts
computing it and says "not known yet"; the next look has it, with its age. A probe
that fails is a fact too, with the tool's own words, never an empty box that reads
as "all fine".
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from noust.core.exceptions import NoustError
from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.server.facts import FactCache
from noust.managers.server.summary import (
    build_summary,
    read_system_state,
    register_summary_section,
)
from tests.server_support import (  # noqa: F401 - the fixture registers itself
    make_machine,
    no_package_manager_running,
    platform_for,
)


class Clock:
    """A clock the test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestFactCache:
    def test_a_fact_is_computed_once_while_it_is_fresh(self) -> None:
        cache = FactCache(background=False)
        calls: list[int] = []

        def loader() -> int:
            calls.append(1)
            return 42

        first = cache.get("answer", loader, ttl=60)
        second = cache.get("answer", loader, ttl=60)

        assert first is not None and first.value == 42
        assert second is not None and second.value == 42
        assert len(calls) == 1

    def test_an_old_fact_is_computed_again(self) -> None:
        clock = Clock()
        cache = FactCache(background=False, clock=clock)
        values = iter([1, 2])

        cache.get("n", lambda: next(values), ttl=60)
        clock.now += 61
        fact = cache.get("n", lambda: next(values), ttl=60)

        assert fact is not None and fact.value == 2

    def test_a_fact_says_how_old_it_is(self) -> None:
        clock = Clock()
        cache = FactCache(background=False, clock=clock)
        cache.get("n", lambda: 1, ttl=60)
        clock.now += 10

        fact = cache.peek("n", ttl=60)

        assert fact is not None
        assert fact.age_seconds == 10
        assert fact.fresh is True
        assert fact.checked_at

    def test_a_failure_is_kept_in_the_tools_words_and_the_last_good_answer_stays(self) -> None:
        clock = Clock()
        cache = FactCache(background=False, clock=clock)
        cache.get("n", lambda: "good", ttl=60)
        clock.now += 61

        def failing() -> str:
            raise NoustError("Could not list", output="E: mirror unreachable")

        fact = cache.get("n", failing, ttl=60)

        assert fact is not None
        assert fact.value == "good"
        assert fact.error == "Could not list: E: mirror unreachable"

    def test_a_first_failure_is_a_fact_with_no_value_and_an_error(self) -> None:
        def failing() -> None:
            raise OSError("Permission denied")

        fact = FactCache(background=False).get("n", failing, ttl=60)

        assert fact is not None
        assert fact.value is None
        assert fact.error == "Permission denied"

    def test_a_bug_in_a_loader_is_not_swallowed(self) -> None:
        def buggy() -> None:
            raise AttributeError("no such method")

        with pytest.raises(AttributeError):
            FactCache(background=False).get("n", buggy, ttl=60)

    def test_in_the_background_the_first_look_starts_the_work_and_finds_nothing(self) -> None:
        cache = FactCache(background=True)
        done = threading.Event()

        def loader() -> int:
            done.set()
            return 7

        first = cache.get("n", loader, ttl=60)
        assert first is None
        assert done.wait(2)
        for _ in range(50):
            fact = cache.peek("n", ttl=60)
            if fact is not None:
                break
            time.sleep(0.02)
        assert fact is not None and fact.value == 7

    def test_a_stale_fact_is_returned_while_a_new_one_is_computed(self) -> None:
        clock = Clock()
        cache = FactCache(background=True, clock=clock)
        cache.put("n", "old")
        clock.now += 100
        release = threading.Event()

        def slow() -> str:
            release.wait(2)
            return "new"

        fact = cache.get("n", slow, ttl=60)

        assert fact is not None and fact.value == "old" and fact.fresh is False
        release.set()

    def test_two_callers_share_one_computation(self) -> None:
        cache = FactCache(background=True)
        started = threading.Event()
        release = threading.Event()
        calls: list[int] = []

        def slow() -> int:
            calls.append(1)
            started.set()
            release.wait(2)
            return 1

        cache.get("n", slow, ttl=60)
        assert started.wait(2)
        cache.get("n", slow, ttl=60)
        cache.get("n", slow, ttl=60)
        release.set()
        time.sleep(0.1)

        assert len(calls) == 1

    def test_invalidate_forgets_so_the_next_look_computes(self) -> None:
        cache = FactCache(background=False)
        values = iter([1, 2])
        cache.get("n", lambda: next(values), ttl=60)

        cache.invalidate("n")

        assert cache.peek("n") is None
        fact = cache.get("n", lambda: next(values), ttl=60)
        assert fact is not None and fact.value == 2

    def test_a_job_that_just_refreshed_can_put_its_answer_in(self) -> None:
        cache = FactCache(background=False)

        cache.put("n", "from a job")

        fact = cache.get("n", lambda: "computed", ttl=60)
        assert fact is not None and fact.value == "from a job"


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "summary.db", fs=RecordingFileSystem())
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store):
    """A machine made of fixtures: apt, timedatectl, swap, systemd and two disks."""
    return make_machine(tmp_path, monkeypatch, store)


@pytest.mark.usefixtures("no_package_manager_running")
class TestSummary:
    def test_everything_the_overview_and_the_fleet_need_is_in_one_answer(self, machine) -> None:
        summary = build_summary(machine.ctx, wait=True)

        assert summary["hostname"] == "vps-1"
        assert summary["os"]["name"] == "Ubuntu 24.04.5 LTS"
        assert summary["os"]["eol"]["status"] == "ok"
        assert summary["uptime_seconds"] == 90000.5
        assert summary["updates"]["pending"] == 9
        assert summary["updates"]["security"] == 7
        assert summary["updates"]["supported"] is True
        assert summary["reboot"]["required"] is True
        assert summary["stale_services"] == 3
        assert summary["auto_updates"]["enabled"] is True
        assert summary["auto_updates"]["security_only"] is True
        assert summary["disk"]["worst_mount"] == "/data"
        assert summary["disk"]["worst_percent"] == 92.0
        assert summary["time"]["timezone"] == "Atlantic/Canary"
        assert summary["time"]["synchronized"] is False
        assert summary["swap"]["recommended"] is True
        assert summary["power"] == {"scheduled": None}
        assert summary["system"]["state"] == "degraded"
        assert summary["system"]["failed_units"] == ["nginx.service", "backup.service"]
        assert summary["capabilities"]["packages"] == "apt"
        assert summary["capabilities"]["updates"] is True

    def test_it_is_plain_data_the_api_can_serialise(self, machine) -> None:
        import json

        json.dumps(build_summary(machine.ctx, wait=True))

    def test_in_the_console_the_first_look_computes_nothing_slow_and_says_not_known_yet(
        self, machine
    ) -> None:
        machine.ctx.cache = FactCache(background=True)
        release = threading.Event()
        original = machine.runner.run

        def blocked(argv, **kwargs):
            if argv[0] == "apt-get":
                release.wait(2)
            return original(argv, **kwargs)

        machine.runner.run = blocked

        summary = build_summary(machine.ctx)

        assert summary["updates"]["pending"] is None
        assert summary["updates"]["checked_at"] is None
        assert summary["updates"]["error"] is None
        # Cheap things are there at once.
        assert summary["hostname"] == "vps-1"
        assert summary["power"] == {"scheduled": None}
        release.set()

    def test_a_probe_that_fails_is_reported_with_its_words_not_left_blank(self, machine) -> None:
        machine.runner.script(
            ["apt-get", "-s"], stderr="E: The package lists are broken", exit_code=100
        )

        summary = build_summary(machine.ctx, wait=True)

        assert summary["updates"]["pending"] is None
        assert "package lists are broken" in summary["updates"]["error"]
        # The rest of the summary is unaffected by one failing probe.
        assert summary["disk"]["worst_mount"] == "/data"

    def test_a_system_where_updates_are_not_managed_says_why(self, machine, tmp_path: Path) -> None:
        machine.ctx._platform = platform_for("none")
        machine.ctx._built.clear()

        summary = build_summary(machine.ctx, wait=True)

        assert summary["updates"]["supported"] is False
        assert (
            "cannot" in summary["updates"]["reason"]
            or "none of them" in summary["updates"]["reason"]
        )
        assert summary["capabilities"]["updates"] is False

    def test_a_scheduled_reboot_is_in_the_summary(self, machine) -> None:
        machine.host.shutdown_scheduled.write_text("USEC=1790716408000000\nMODE=reboot\n")
        machine.runner.script(["findmnt", "--verify"], stdout="Success\n")
        machine.ctx.power.schedule("reboot", minutes=5, actor="yago", force=True)

        summary = build_summary(machine.ctx, wait=True)

        assert summary["power"]["scheduled"]["requested_by"] == "yago"

    def test_other_areas_add_their_sections_and_a_failing_one_does_not_break_the_rest(
        self, machine
    ) -> None:
        register_summary_section("hardening", lambda ctx: {"critical": 1, "warn": 4})
        register_summary_section("broken", lambda ctx: (_ for _ in ()).throw(OSError("no db")))
        try:
            summary = build_summary(machine.ctx, wait=True)
        finally:
            from noust.managers.server import summary as module

            module._extra_sections.pop("hardening", None)
            module._extra_sections.pop("broken", None)

        assert summary["hardening"] == {"critical": 1, "warn": 4}
        assert summary["broken"] == {"error": "no db"}
        assert summary["updates"]["pending"] == 9

    def test_the_system_state_and_its_failed_units(self) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "is-system-running"], stdout="running\n")

        assert read_system_state(runner) == {"state": "running", "failed": []}
