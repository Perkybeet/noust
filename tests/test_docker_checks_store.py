# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the last look at the registries found of each image in use, kept in the store.

The summary of the Docker zone says "3 images have a new version" without asking a registry, because
asking is something the operator does on purpose: what it says is what was found the last time, and a row
is replaced whole by the next look. These tests hold what a reader of that table needs: one row per
image, replaced and not duplicated, forgotten when the image is no longer in use, and a row that says it
could not tell is a row and not an absence.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.docker_image_checks import ImageVersionStore, VersionRecord
from noust.core.store import NoustStore


@pytest.fixture
def versions(tmp_path: Path) -> Iterator[ImageVersionStore]:
    NoustStore.reset_instance()
    yield ImageVersionStore(NoustStore(tmp_path / "noust.db"))
    NoustStore.reset_instance()


def record(reference: str = "redis:7-alpine", **changes: object) -> VersionRecord:
    base: dict[str, object] = {
        "reference": reference,
        "state": "newer",
        "local_digest": "sha256:" + "a" * 64,
        "remote_digest": "sha256:" + "b" * 64,
        "current_version": "7.4.1",
        "latest_version": "7.4.2",
        "latest_created": "2026-11-09T10:00:00+00:00",
        "change": "patch",
        "floating": False,
        "method": "imagetools",
        "detail": "",
        "checked_at": "2026-11-12T10:00:00+00:00",
    }
    base.update(changes)
    return VersionRecord(**base)  # type: ignore[arg-type]


def test_a_record_is_read_back_as_it_was_written(versions: ImageVersionStore) -> None:
    versions.save(record())

    assert versions.get("redis:7-alpine") == record()


def test_an_image_nobody_looked_at_has_no_record(versions: ImageVersionStore) -> None:
    assert versions.get("redis:7-alpine") is None
    assert versions.all() == []


def test_the_next_look_replaces_the_row_and_does_not_add_one(versions: ImageVersionStore) -> None:
    versions.save(record(state="newer"))
    versions.save(record(state="current", change="", checked_at="2026-11-13T10:00:00+00:00"))

    found = versions.all()

    assert [(row.reference, row.state, row.checked_at) for row in found] == [
        ("redis:7-alpine", "current", "2026-11-13T10:00:00+00:00")
    ]


def test_a_look_that_could_not_tell_is_kept_with_why(versions: ImageVersionStore) -> None:
    versions.save(
        record(
            state="unknown",
            remote_digest=None,
            latest_version=None,
            change="",
            detail="This Docker cannot ask a registry without downloading the image",
        )
    )

    found = versions.get("redis:7-alpine")

    assert found is not None
    assert found.state == "unknown"
    assert found.remote_digest is None
    assert "without downloading" in found.detail


def test_what_is_no_longer_in_use_is_forgotten_and_what_is_kept_stays(
    versions: ImageVersionStore,
) -> None:
    for reference in ("redis:7-alpine", "nginx:1.27", "postgres:latest"):
        versions.save(record(reference))

    versions.keep_only({"redis:7-alpine", "postgres:latest"})

    assert [row.reference for row in versions.all()] == ["postgres:latest", "redis:7-alpine"]


def test_keeping_nothing_forgets_everything(versions: ImageVersionStore) -> None:
    versions.save(record())

    versions.keep_only(set())

    assert versions.all() == []


def test_a_floating_tag_is_remembered_as_one(versions: ImageVersionStore) -> None:
    versions.save(record("postgres:latest", floating=True, change="major"))

    found = versions.get("postgres:latest")

    assert found is not None
    assert found.floating is True
    assert found.change == "major"


def test_a_state_the_table_does_not_know_is_refused(versions: ImageVersionStore) -> None:
    with pytest.raises(ValueError, match="state"):
        versions.save(record(state="maybe"))


def test_a_change_the_table_does_not_know_is_refused(versions: ImageVersionStore) -> None:
    with pytest.raises(ValueError, match="change"):
        versions.save(record(change="huge"))
