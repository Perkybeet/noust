# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The monitor tells the operator when the kernel kills an application for running out of memory (item 175).

The kernel counts the kills by cgroup (``oom_kill`` in ``memory.events``). The watch keeps the count
of each application and says it once for every rise: a count that did not move is not news, a cgroup
that was made again (the unit restarted) starts counting from zero and its first kill is, and a
machine that cannot say (no file) leaves what was remembered alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.config import DEFAULT_CONFIG
from noust.core.messages import MESSAGES
from noust.core.notifications.composers_watch import compose_app_oom
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import ALL_KINDS, EVENT_KINDS, State
from noust.monitor.plan import Reason, SamplingPlan, Target
from noust.monitor.watch_oom import OomWatch
from tests.support.monitor_rig import Rig

MIB = 1024 * 1024
STARTED = "Tue 2026-11-10 03:00:00 UTC"
RESTARTED = "Tue 2026-11-10 03:20:00 UTC"


class Machine:
    """The cgroups of some applications, and the plans that point at them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.started: dict[str, str] = {}
        self.removed: set[str] = set()

    def cgroup(
        self, domain: str, *, kills: int | None, limit: str = "max", peak: int | None = None
    ) -> None:
        directory = self.root / f"{domain}.service"
        directory.mkdir(exist_ok=True)
        events = directory / "memory.events"
        if kills is None:
            events.unlink(missing_ok=True)
        else:
            events.write_text(f"low 0\nhigh 0\nmax 0\noom {kills}\noom_kill {kills}\n")
        (directory / "memory.max").write_text(f"{limit}\n")
        if peak is not None:
            (directory / "memory.peak").write_text(f"{peak}\n")
        self.started.setdefault(domain, STARTED)

    def plans(self, services: object) -> dict[str, SamplingPlan]:
        return {
            domain: SamplingPlan(
                domain=domain,
                source="cgroup",
                targets=(
                    Target(
                        name=domain,
                        cgroup=self.root / f"{domain}.service",
                        properties={"ActiveEnterTimestamp": self.started[domain]},
                    ),
                ),
            )
            for domain in sorted(self.started)
            if domain not in self.removed
        }


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    instance = Rig(tmp_path)
    yield instance
    instance.close()


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    root = tmp_path / "cgroups"
    root.mkdir()
    return Machine(root)


def watch(machine: Machine) -> OomWatch:
    return OomWatch(plans=machine.plans)


class TestTheMessage:
    def test_it_says_which_application_how_many_times_and_what_its_limit_was(
        self, rig: Rig
    ) -> None:
        note = compose_app_oom(
            "shop.example.com",
            rig.context,
            count=2,
            limit=512 * MIB,
            peak=511 * MIB,
            since=STARTED,
        )

        assert (note.kind, note.code, note.state) == (
            "app_oom_killed",
            "app.oom_killed",
            State.FAILED,
        )
        assert note.subject == "shop.example.com"
        assert note.domain == "shop.example.com"
        facts = {f.key: f.value for f in note.facts}
        assert facts["oom_kills"] == "2 times"
        assert facts["oom_limit"] == "512.0 MB"
        assert facts["oom_peak"] == "511.0 MB"
        assert facts["oom_since"] == STARTED
        assert note.command is not None
        assert note.command.value == "noust diagnose shop.example.com"

    def test_an_application_with_no_limit_says_so(self, rig: Rig) -> None:
        note = compose_app_oom("shop.example.com", rig.context, count=1, limit=None, peak=None)

        facts = {f.key: f.value for f in note.facts}
        assert facts["oom_kills"] == "1 time"
        assert facts["oom_limit"] == "none"
        assert "oom_peak" not in facts

    @pytest.mark.parametrize("locale", ["en", "es"])
    def test_it_is_said_in_both_languages_with_nothing_left_unfilled(self, locale: str) -> None:
        for key in (
            "title.app.oom_killed",
            "summary.app.oom_killed",
            "fact.oom_kills",
            "fact.oom_limit",
            "fact.oom_peak",
            "fact.oom_since",
            "fact.oom_none",
        ):
            assert MESSAGES[key][locale].strip(), (key, locale)
        context = NotificationContext(locale=locale, server="web-1")  # type: ignore[arg-type]
        note = compose_app_oom("shop.example.com", context, count=3)
        assert "{" not in note.summary and "{" not in note.title

    def test_it_is_an_event_an_operator_can_switch_off(self) -> None:
        assert "app_oom_killed" in EVENT_KINDS and "app_oom_killed" in ALL_KINDS
        assert DEFAULT_CONFIG["notifications"]["events"]["app_oom_killed"] is True


class TestTheWatch:
    def test_the_first_look_only_remembers_what_it_finds(self, rig: Rig, machine: Machine) -> None:
        """Kills from before the monitor looked are not news now."""
        machine.cgroup("shop.example.com", kills=4)

        watch(machine).check(rig.services)

        assert rig.sent == []

    def test_a_rise_is_said_once(self, rig: Rig, machine: Machine) -> None:
        machine.cgroup("shop.example.com", kills=0)
        w = watch(machine)
        w.check(rig.services)

        machine.cgroup("shop.example.com", kills=1, limit=str(512 * MIB), peak=511 * MIB)
        w.check(rig.services)
        w.check(rig.services)

        [note] = rig.sent
        assert note.code == "app.oom_killed"
        facts = {f.key: f.value for f in note.facts}
        assert facts["oom_kills"] == "1 time"
        assert facts["oom_limit"] == "512.0 MB"
        assert facts["oom_since"] == STARTED

    def test_every_rise_is_said_with_how_much_it_rose(self, rig: Rig, machine: Machine) -> None:
        machine.cgroup("shop.example.com", kills=0)
        w = watch(machine)
        w.check(rig.services)

        machine.cgroup("shop.example.com", kills=1)
        w.check(rig.services)
        machine.cgroup("shop.example.com", kills=4)
        w.check(rig.services)

        counts = [{f.key: f.value for f in note.facts}["oom_kills"] for note in rig.sent]
        assert counts == ["1 time", "3 times"]

    def test_a_count_that_did_not_move_says_nothing(self, rig: Rig, machine: Machine) -> None:
        machine.cgroup("shop.example.com", kills=2)
        w = watch(machine)

        for _ in range(5):
            w.check(rig.services)

        assert rig.sent == []

    def test_a_unit_that_restarted_counts_again_from_zero(self, rig: Rig, machine: Machine) -> None:
        machine.cgroup("shop.example.com", kills=3)
        w = watch(machine)
        w.check(rig.services)

        # systemd made its cgroup again: the count is the kills since.
        machine.started["shop.example.com"] = RESTARTED
        machine.cgroup("shop.example.com", kills=1)
        w.check(rig.services)

        [note] = rig.sent
        assert {f.key: f.value for f in note.facts}["oom_kills"] == "1 time"

    def test_a_restart_that_lands_on_the_same_count_is_still_a_new_kill(
        self, rig: Rig, machine: Machine
    ) -> None:
        machine.cgroup("shop.example.com", kills=1)
        w = watch(machine)
        w.check(rig.services)

        machine.started["shop.example.com"] = RESTARTED
        machine.cgroup("shop.example.com", kills=1)
        w.check(rig.services)

        assert rig.codes() == ["app.oom_killed"]

    def test_a_restart_that_killed_nothing_says_nothing(self, rig: Rig, machine: Machine) -> None:
        machine.cgroup("shop.example.com", kills=3)
        w = watch(machine)
        w.check(rig.services)

        machine.started["shop.example.com"] = RESTARTED
        machine.cgroup("shop.example.com", kills=0)
        w.check(rig.services)

        assert rig.sent == []

    def test_not_knowing_is_not_a_change(self, rig: Rig, machine: Machine) -> None:
        machine.cgroup("shop.example.com", kills=2)
        w = watch(machine)
        w.check(rig.services)

        machine.cgroup("shop.example.com", kills=None)  # the file is not readable now
        w.check(rig.services)
        machine.cgroup("shop.example.com", kills=2)
        w.check(rig.services)

        assert rig.sent == []

    def test_each_application_is_judged_by_its_own_count(self, rig: Rig, machine: Machine) -> None:
        machine.cgroup("a.example.com", kills=0)
        machine.cgroup("b.example.com", kills=0)
        w = watch(machine)
        w.check(rig.services)

        machine.cgroup("b.example.com", kills=1)
        w.check(rig.services)

        assert [note.subject for note in rig.sent] == ["b.example.com"]

    def test_an_application_that_is_gone_is_forgotten(self, rig: Rig, machine: Machine) -> None:
        machine.cgroup("gone.example.com", kills=0)
        w = watch(machine)
        w.check(rig.services)
        assert rig.services.state.items("oom") != {}

        machine.removed.add("gone.example.com")
        w.check(rig.services)

        assert rig.services.state.items("oom") == {}

    def test_an_application_with_nothing_to_measure_is_left_alone(
        self, rig: Rig, machine: Machine
    ) -> None:
        def plans(services: object) -> dict[str, SamplingPlan]:
            return {
                "static.example.com": SamplingPlan(
                    domain="static.example.com",
                    source="none",
                    reason=Reason(code="static", message="a static site has no process"),
                )
            }

        OomWatch(plans=plans).check(rig.services)

        assert rig.sent == []

    def test_by_default_it_reads_the_cgroups_of_the_applications_of_the_store(
        self, rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The plans are the metrics collector's: systemd says where each unit's cgroup is."""
        from noust.core.store import App
        from tests.support.sampling_plan import FakeManager, builder, lay_unit

        mount = tmp_path / "cgroup"
        mount.mkdir()
        (mount / "cgroup.controllers").write_text("cpu memory\n")
        rig.store.create_app(App(domain="shop.example.com", app_type="nodejs", port=3000))
        rig.store.create_app(
            App(
                domain="pr-1.example.com",
                app_type="nodejs",
                port=3001,
                preview_parent="shop.example.com",
            )
        )
        group = "/system.slice/shop-example-com.service"
        directory = lay_unit(mount, group)
        manager = FakeManager(
            {"shop.example.com": ["shop-example-com"]},
            {
                "shop-example-com": {
                    "Id": "shop-example-com.service",
                    "LoadState": "loaded",
                    "ActiveState": "active",
                    "SubState": "running",
                    "ControlGroup": group,
                    "MemoryAccounting": "yes",
                    "CPUAccounting": "yes",
                    "ActiveEnterTimestamp": STARTED,
                }
            },
        )
        monkeypatch.setattr(
            "noust.monitor.watch_oom.PlanBuilder", lambda runner: builder(mount, manager)
        )
        (directory / "memory.events").write_text("oom_kill 0\n")
        w = OomWatch()
        w.check(rig.services)

        (directory / "memory.events").write_text("oom_kill 2\n")
        w.check(rig.services)

        [note] = rig.sent
        assert note.subject == "shop.example.com"
        assert {f.key: f.value for f in note.facts}["oom_since"] == STARTED
        assert list(rig.services.state.items("oom")) == ["shop.example.com"]

    def test_the_monitor_runs_it(self) -> None:
        from noust.core.runner import FakeRunner
        from noust.monitor.process_monitor import ProcessMonitor

        monitor = ProcessMonitor(verbose=False, runner=FakeRunner())

        assert "oom" in [watch.name for watch in monitor._watches]
