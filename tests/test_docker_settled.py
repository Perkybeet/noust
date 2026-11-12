# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A look says a version is new; an update later makes what runs the very image the look found.

What was found is a fact of the moment of the look. Reading it later has to take into account what runs
now, or the console would keep saying "new version" of something that was updated an hour ago, until the
operator looks again. So a record that says ``newer`` is turned ``current`` when the digest the running
image came with is the one the registry had; nothing else is changed, and nothing is asked unless there
is such a record.
"""

from __future__ import annotations

from noust.core.docker import registry
from noust.core.docker_image_checks import VersionRecord
from tests.support.docker_machine import DockerMachine

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
IMAGE = "sha256:" + "1" * 64


def record(**changes: object) -> VersionRecord:
    base: dict[str, object] = {
        "reference": "redis:7",
        "state": "newer",
        "local_digest": A,
        "remote_digest": B,
        "current_version": None,
        "latest_version": None,
        "latest_created": None,
        "change": "patch",
        "floating": False,
        "method": "imagetools",
        "detail": "",
        "checked_at": "2026-11-12T10:00:00+00:00",
    }
    base.update(changes)
    return VersionRecord(**base)  # type: ignore[arg-type]


def test_a_newer_record_whose_image_now_runs_is_current() -> None:
    machine = DockerMachine()
    machine.facts(IMAGE, repo_digests=[f"redis@{B}"])

    settled = registry.settled(machine, [record()], {"redis:7": IMAGE})

    assert [(item.state, item.change, item.local_digest) for item in settled] == [
        ("current", "", B)
    ]


def test_a_newer_record_whose_image_still_runs_is_still_newer() -> None:
    machine = DockerMachine()
    machine.facts(IMAGE, repo_digests=[f"redis@{A}"])

    settled = registry.settled(machine, [record()], {"redis:7": IMAGE})

    assert [item.state for item in settled] == ["newer"]


def test_what_is_not_newer_is_left_as_it_is_and_asks_nothing() -> None:
    machine = DockerMachine()
    current = record(state="current", change="")

    assert registry.settled(machine, [current], {"redis:7": IMAGE}) == [current]
    assert machine.calls == []


def test_a_record_with_no_image_to_look_at_is_left_as_it_is() -> None:
    machine = DockerMachine()

    assert [item.state for item in registry.settled(machine, [record()], {})] == ["newer"]
    assert machine.calls == []


def test_one_question_serves_every_record_that_needs_it() -> None:
    machine = DockerMachine()
    other = "sha256:" + "2" * 64
    machine.facts(IMAGE, repo_digests=[f"redis@{B}"])
    machine.facts(other, repo_digests=[f"nginx@{A}"])

    settled = registry.settled(
        machine,
        [record(), record(reference="nginx:1.27")],
        {"redis:7": IMAGE, "nginx:1.27": other},
    )

    assert [(item.reference, item.state) for item in settled] == [
        ("redis:7", "current"),
        ("nginx:1.27", "newer"),
    ]
    assert len([call for call in machine.calls if call[:2] == ("docker", "inspect")]) == 1
