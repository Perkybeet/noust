# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the kernel counted for a cgroup: the processes it killed for running out of memory (item 175).

``memory.events`` of a cgroup v2 carries ``oom_kill``, "the number of processes belonging to this
cgroup killed by any kind of OOM killer", so the kernel says which unit it killed something of, which
its log line cannot. ``memory.max`` is the limit and ``memory.peak`` the highest use. Each is a small
file read as it is, with no process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core import cgroup_events

EVENTS = "low 0\nhigh 0\nmax 12\noom 4\noom_kill 3\noom_group_kill 0\n"


def _cgroup(tmp_path: Path, **files: str) -> Path:
    directory = tmp_path / "shop.service"
    directory.mkdir()
    for name, text in files.items():
        (directory / name.replace("_", ".", 1)).write_text(text)
    return directory


class TestTheKillCounter:
    def test_the_counter_of_kills_is_read_from_memory_events(self, tmp_path: Path) -> None:
        directory = _cgroup(tmp_path, memory_events=EVENTS)

        assert cgroup_events.read_oom_kills(directory) == 3

    def test_a_cgroup_that_killed_nothing_says_zero(self, tmp_path: Path) -> None:
        directory = _cgroup(tmp_path, memory_events="low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n")

        assert cgroup_events.read_oom_kills(directory) == 0

    def test_a_cgroup_without_the_file_has_no_answer_which_is_not_zero(
        self, tmp_path: Path
    ) -> None:
        """No file is no cgroup v2 or no memory controller: not the same as nothing killed."""
        assert cgroup_events.read_oom_kills(_cgroup(tmp_path)) is None

    def test_a_directory_that_is_not_there_has_no_answer(self, tmp_path: Path) -> None:
        assert cgroup_events.read_oom_kills(tmp_path / "gone.service") is None

    def test_a_file_without_the_counter_has_no_answer(self, tmp_path: Path) -> None:
        """Kernels older than 4.13 count ``oom`` only."""
        assert cgroup_events.read_oom_kills(_cgroup(tmp_path, memory_events="oom 2\n")) is None

    @pytest.mark.parametrize("text", ["oom_kill many\n", "oom_kill -1\n", "oom_kill\n", "\x00\xff"])
    def test_a_counter_that_is_not_a_number_has_no_answer(self, tmp_path: Path, text: str) -> None:
        assert cgroup_events.read_oom_kills(_cgroup(tmp_path, memory_events=text)) is None


class TestTheLimitAndThePeak:
    def test_a_limit_is_read_in_bytes(self, tmp_path: Path) -> None:
        directory = _cgroup(tmp_path, memory_max="536870912\n")

        assert cgroup_events.read_memory_limit(directory) == 512 * 1024 * 1024

    def test_no_limit_is_none_not_zero(self, tmp_path: Path) -> None:
        """``max`` is how the kernel says nothing limits it but the machine."""
        assert cgroup_events.read_memory_limit(_cgroup(tmp_path, memory_max="max\n")) is None

    def test_the_peak_is_read_in_bytes(self, tmp_path: Path) -> None:
        directory = _cgroup(tmp_path, memory_peak="535822336\n")

        assert cgroup_events.read_memory_peak(directory) == 535822336

    def test_a_kernel_without_memory_peak_has_no_answer(self, tmp_path: Path) -> None:
        assert cgroup_events.read_memory_peak(_cgroup(tmp_path)) is None


class TestWhatAnApplicationDid:
    def test_the_figures_of_one_cgroup_are_read_together(self, tmp_path: Path) -> None:
        directory = _cgroup(
            tmp_path, memory_events=EVENTS, memory_max="536870912\n", memory_peak="535822336\n"
        )

        facts = cgroup_events.read_facts(directory)

        assert facts == cgroup_events.OomFacts(
            kills=3, limit=536870912, peak=535822336, readable=True
        )

    def test_several_cgroups_of_one_application_are_summed_and_the_highest_peak_kept(
        self, tmp_path: Path
    ) -> None:
        first = _cgroup(
            tmp_path, memory_events="oom_kill 1\n", memory_max="max\n", memory_peak="100\n"
        )
        second_dir = tmp_path / "shop2.service"
        second_dir.mkdir()
        (second_dir / "memory.events").write_text("oom_kill 2\n")
        (second_dir / "memory.max").write_text("1000\n")
        (second_dir / "memory.peak").write_text("900\n")

        facts = cgroup_events.combine(
            [cgroup_events.read_facts(first), cgroup_events.read_facts(second_dir)]
        )

        assert facts.kills == 3
        assert facts.peak == 900
        assert facts.limit == 1000
        assert facts.readable is True

    def test_nothing_readable_is_said_as_such(self, tmp_path: Path) -> None:
        facts = cgroup_events.combine([cgroup_events.read_facts(tmp_path / "gone")])

        assert facts.readable is False
        assert facts.kills == 0

    def test_a_cgroup_that_reads_in_part_counts_what_it_can(self, tmp_path: Path) -> None:
        facts = cgroup_events.read_facts(_cgroup(tmp_path, memory_events="oom_kill 2\n"))

        assert (facts.kills, facts.limit, facts.peak, facts.readable) == (2, None, None, True)
