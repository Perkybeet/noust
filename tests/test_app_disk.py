# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application takes of the disk, and the limit its deploys are held to (checklist B-63).

The server's storage analysis has no row per application, so a release tree that keeps growing or
an uploads folder nobody counts is invisible until the whole disk is full. These tests pin the
per-application breakdown (the active release, the ones a prune would remove, ``shared/``, the
repository cache, and the backups apart), the limit Noust holds a deploy to and the things it must
not do with it: never stop going back to an earlier release, never claim a kernel quota it did not
set, never take a measurement as evidence when a tree could not be measured.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import app_disk_store
from noust.core.exceptions import NoustError, ValidationError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, NoustStore, get_store
from noust.managers import app_disk

DOMAIN = "shop.example.com"
MB = 1024 * 1024


class Disk(FakeRunner):
    """``du`` and ``findmnt`` answering from a real tree."""

    def __init__(self) -> None:
        super().__init__()
        self.mount = "/ ext4 rw,relatime"
        self.slow: set[str] = set()

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv
        if "du" in args:
            target = Path(args[-1])
            if str(target) in self.slow:
                return replace(result, exit_code=1, stderr="timed out")
            total = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
            return replace(result, stdout=f"{total}\t{target}\n")
        if args[:1] == ("findmnt",):
            if not self.mount:
                return replace(result, exit_code=1)
            return replace(result, stdout=self.mount + "\n")
        return result

    def measured(self) -> list[str]:
        return [call[-1] for call in self.calls if "du" in call]


def write(path: Path, megabytes: float) -> None:
    """Make a sparse file of that size: the fake ``du`` reads sizes, and no disk is used."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(int(megabytes * MB))


@pytest.fixture
def store() -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    yield get_store()
    NoustStore.reset_instance()


@pytest.fixture
def disk() -> Iterator[Disk]:
    fake = Disk()
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def releases_app(tmp_path: Path, store: NoustStore) -> App:
    """An application on the release layout: three releases, uploads and a repository cache."""
    root = tmp_path / "apps" / "shop-example-com"
    for release, size in (
        ("20260901-100000-aaaaaaa", 60),
        ("20260910-100000-bbbbbbb", 60),
        ("20260920-100000-ccccccc", 100),
    ):
        write(root / "releases" / release / "app.bin", size)
    (root / "current").symlink_to(Path("releases") / "20260920-100000-ccccccc")
    write(root / "shared" / "uploads" / "photo.jpg", 140)
    write(root / "repo" / "pack.bin", 40)
    return store.create_app(
        App(
            domain=DOMAIN, app_type="nodejs", layout="releases", keep_releases=2, app_path=str(root)
        )
    )


@pytest.fixture
def inplace_app(tmp_path: Path, store: NoustStore) -> App:
    root = tmp_path / "apps" / "legacy-example-com"
    write(root / "site.bin", 80)
    write(root / "uploads" / "a.bin", 20)
    return store.create_app(
        App(domain="legacy.example.com", app_type="nodejs", layout="inplace", app_path=str(root))
    )


# -- The breakdown ------------------------------------------------------------------------------


def test_a_release_application_is_told_apart_into_what_serves_and_what_could_go(
    releases_app: App, disk: Disk
) -> None:
    usage = app_disk.measure(releases_app)

    parts = {part.name: part for part in usage.parts}
    assert parts["current"].bytes == 100 * MB
    assert parts["releases"].bytes == 120 * MB  # the two it does not run
    # It keeps two releases: the active one and one more, so the oldest is what a prune takes.
    assert parts["releases"].reclaimable_bytes == 60 * MB
    assert parts["shared"].bytes == 140 * MB and parts["repo"].bytes == 40 * MB
    assert usage.total_bytes == 400 * MB
    assert [part.name for part in usage.parts] == ["shared", "releases", "current", "repo"]
    assert usage.errors == []


def test_an_application_built_in_place_is_one_tree(inplace_app: App, disk: Disk) -> None:
    usage = app_disk.measure(inplace_app)

    assert [(part.name, part.bytes) for part in usage.parts] == [("tree", 100 * MB)]


def test_the_backups_are_told_apart_and_do_not_count_against_the_limit(
    releases_app: App, disk: Disk, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.managers.backup_manager import BackupManager

    monkeypatch.setattr(
        BackupManager,
        "get_storage_usage",
        lambda self: {"by_app": {"shop-example-com": {"count": 3, "size_bytes": 9 * MB}}},
    )

    usage = app_disk.measure(releases_app)

    assert usage.backups_bytes == 9 * MB
    assert usage.total_bytes == 400 * MB


def test_a_tree_that_could_not_be_measured_is_named_and_not_counted_as_empty(
    releases_app: App, disk: Disk
) -> None:
    disk.slow.add(str(Path(releases_app.app_path) / "shared"))

    usage = app_disk.measure(releases_app)

    assert any("shared" in problem for problem in usage.errors)
    assert "shared" not in {part.name for part in usage.parts}


def test_what_is_not_there_is_not_measured(inplace_app: App, disk: Disk) -> None:
    import shutil

    shutil.rmtree(inplace_app.app_path)

    usage = app_disk.measure(inplace_app)

    assert usage.parts == [] and usage.errors == []


# -- The limit --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("used_mb", "state"),
    [(100, "ok"), (899, "ok"), (900, "near"), (999, "near"), (1000, "over"), (4000, "over")],
)
def test_how_an_application_stands_against_its_limit(used_mb: int, state: str) -> None:
    verdict = app_disk.verdict_of(used_mb * MB, 1000)

    assert verdict is not None and verdict.state == state
    assert verdict.percent == round(used_mb * 100 / 1000, 1)


def test_no_limit_has_no_verdict() -> None:
    assert app_disk.verdict_of(10 * MB, None) is None


@pytest.mark.parametrize("value", [0, 1, 63, -5, app_disk.MAX_LIMIT_MB + 1])
def test_a_limit_a_release_would_not_fit_in_is_refused(value: int) -> None:
    with pytest.raises(ValidationError, match="between 64 MB"):
        app_disk.validate_limit(value)


def test_a_limit_is_a_whole_number() -> None:
    with pytest.raises(ValidationError, match="whole number"):
        app_disk.validate_limit(True)
    with pytest.raises(ValidationError, match="whole number"):
        app_disk.validate_limit(1.5)  # type: ignore[arg-type]
    assert app_disk.validate_limit(None) is None and app_disk.validate_limit(64) == 64


def test_setting_the_limit_keeps_the_measurement_and_removing_it_keeps_it_too(
    releases_app: App, disk: Disk, store: NoustStore
) -> None:
    app_disk.measure_and_record(releases_app)

    app_disk.set_disk_limit(releases_app, 512, actor="ana")
    kept = app_disk_store.get_record(DOMAIN, store=store)
    assert kept is not None and kept.soft_mb == 512 and kept.usage is not None
    assert kept.usage["total_bytes"] == 400 * MB and kept.changed_by == "ana"

    app_disk.set_disk_limit(releases_app, None, actor="ana")
    after = app_disk_store.get_record(DOMAIN, store=store)
    assert after is not None and after.soft_mb is None and after.usage is not None


def test_a_row_with_neither_a_limit_nor_a_measurement_is_not_kept(
    releases_app: App, store: NoustStore
) -> None:
    app_disk.set_disk_limit(releases_app, 512, actor="ana")
    app_disk.set_disk_limit(releases_app, None, actor="ana")

    assert app_disk_store.get_record(DOMAIN, store=store) is None


def test_a_measurement_keeps_the_limit(releases_app: App, disk: Disk, store: NoustStore) -> None:
    app_disk.set_disk_limit(releases_app, 512, actor="ana")

    app_disk.measure_and_record(releases_app)

    record = app_disk_store.get_record(DOMAIN, store=store)
    assert record is not None and record.soft_mb == 512 and record.measured_at


def test_the_record_goes_with_the_application(releases_app: App, store: NoustStore) -> None:
    app_disk.set_disk_limit(releases_app, 512, actor="ana")

    store.delete_app(DOMAIN)

    assert app_disk_store.get_record(DOMAIN, store=store) is None


def test_the_status_shows_what_was_kept_and_measures_nothing(releases_app: App, disk: Disk) -> None:
    app_disk.set_disk_limit(releases_app, 128, actor="ana")
    app_disk.measure_and_record(releases_app)
    disk.calls.clear()

    shown = app_disk.status(releases_app)

    assert disk.measured() == []
    assert shown.soft_mb == 128 and shown.usage is not None
    assert shown.verdict is not None and shown.verdict.state == "over"
    assert shown.to_dict()["verdict"]["limit_bytes"] == 128 * MB


def test_an_application_never_measured_shows_no_usage_and_no_verdict(
    releases_app: App, disk: Disk
) -> None:
    app_disk.set_disk_limit(releases_app, 128, actor="ana")

    shown = app_disk.status(releases_app)

    assert shown.usage is None and shown.verdict is None and shown.soft_mb == 128


# -- The kernel's quota -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mount", "expected"),
    [
        ("/ ext4 rw,relatime", "available"),
        ("/ ext4 rw,relatime,prjquota", "enabled"),
        ("/var xfs rw,noatime", "available"),
        ("/var xfs rw,noatime,prjquota", "enabled"),
        ("/var xfs rw,pquota", "enabled"),
        ("/data btrfs rw,relatime", "unsupported"),
        ("/ overlay rw", "unsupported"),
        ("", "unknown"),
    ],
)
def test_whether_the_filesystem_could_hold_a_kernel_quota(
    tmp_path: Path, disk: Disk, mount: str, expected: str
) -> None:
    disk.mount = mount

    facts = app_disk.quota_facts(tmp_path)

    assert facts.project_quota == expected
    assert ("findmnt", "-no", "TARGET,FSTYPE,OPTIONS", "-T", str(tmp_path)) in disk.calls


def test_noust_sets_no_quota_of_its_own(releases_app: App, disk: Disk) -> None:
    app_disk.set_disk_limit(releases_app, 128, actor="ana")
    app_disk.measure_and_record(releases_app)
    app_disk.deploy_refusal(releases_app)

    programs = {call[0] for call in disk.calls}
    assert not programs & {"xfs_quota", "setquota", "chattr", "tune2fs", "mount"}


# -- The deploy ---------------------------------------------------------------------------------------


def test_a_deploy_is_held_while_the_application_is_over_its_limit(
    releases_app: App, disk: Disk, store: NoustStore
) -> None:
    app_disk.set_disk_limit(releases_app, 128, actor="ana")

    problems = app_disk.deploy_refusal(releases_app)

    assert len(problems) == 1
    assert "over its disk limit: 400.0 MB of 128.0 MB" in problems[0]
    assert "noust app disk shop.example.com --limit" in problems[0]
    assert "earlier release" in problems[0]
    # The fresh measurement is kept, so the page says what the refusal measured.
    record = app_disk_store.get_record(DOMAIN, store=store)
    assert (
        record is not None and record.usage is not None and record.usage["total_bytes"] == 400 * MB
    )


def test_a_deploy_goes_on_under_the_limit_and_with_none(releases_app: App, disk: Disk) -> None:
    assert app_disk.deploy_refusal(releases_app) == []
    assert disk.measured() == []  # no limit, no measuring

    app_disk.set_disk_limit(releases_app, 4096, actor="ana")
    assert app_disk.deploy_refusal(releases_app) == []


def test_a_first_deploy_has_no_limit_to_be_held_to(disk: Disk) -> None:
    assert app_disk.deploy_refusal(None) == []
    assert app_disk.deploy_refusal(App(domain="new.example.com")) == []


def test_a_tree_that_could_not_be_measured_does_not_hold_a_deploy_by_itself(
    releases_app: App, disk: Disk
) -> None:
    # 260 MB were measured; the 140 MB of shared/ were not, so there is no evidence of 400.
    app_disk.set_disk_limit(releases_app, 300, actor="ana")
    disk.slow.add(str(Path(releases_app.app_path) / "shared"))

    assert app_disk.deploy_refusal(releases_app) == []


def test_the_deploy_preflight_refuses_an_application_over_its_limit(
    releases_app: App, disk: Disk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one chokepoint the CLI, the console, the webhook and every update go through."""
    from noust.deployers.helpers import preflight
    from noust.deployers.registry import get_deployer

    app_disk.set_disk_limit(releases_app, 128, actor="ana")
    deployer: Any = get_deployer("nodejs")
    deployer.configure(DOMAIN, "/srv/src", port=3000, webserver="nginx")
    monkeypatch.setattr(preflight, "missing_programs", lambda runner, programs: [])
    monkeypatch.setattr(preflight, "repository_unreachable", lambda runner, source: [])
    monkeypatch.setattr(preflight, "insufficient_disk_space", lambda directory: [])
    monkeypatch.setattr(preflight, "port_taken", lambda *a, **k: [])
    monkeypatch.setattr(preflight, "webserver_down", lambda manager, name: [])

    with pytest.raises(NoustError, match="Pre-flight") as refused:
        deployer.pre_flight_check()

    assert "over its disk limit" in str(refused.value)

    app_disk.set_disk_limit(releases_app, None, actor="ana")
    assert deployer.pre_flight_check() is True


def test_going_back_to_an_earlier_release_is_not_a_deploy(releases_app: App, disk: Disk) -> None:
    """Activating a release that is on disk builds nothing, so no pre-flight check holds it."""
    import inspect

    from noust.deployers import lifecycle

    source = inspect.getsource(lifecycle.activate_release) + inspect.getsource(
        lifecycle.rollback_to_deployment
    )

    assert "pre_flight_check" not in source and "deploy_refusal" not in source
    assert os.path.islink(Path(releases_app.app_path) / "current")


def test_an_update_is_held_before_anything_is_fetched_while_the_application_is_over_its_limit(
    releases_app: App, disk: Disk
) -> None:
    """The chokepoint every update reaches: the command, the console's job, the webhook."""
    from noust.deployers.lifecycle import update_app

    app_disk.set_disk_limit(releases_app, 128, actor="ana")

    with pytest.raises(NoustError, match="Pre-flight") as refused:
        update_app(DOMAIN)

    assert "over its disk limit: 400.0 MB of 128.0 MB" in str(refused.value)
    assert {call[0] for call in disk.calls} <= {"ionice", "du", "findmnt"}  # nothing was fetched


def test_going_back_by_rebuilding_a_commit_is_not_held_up_by_the_limit(
    releases_app: App, disk: Disk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rollback that rebuilds (rollback_of) is how an incident ends: it never asks for room."""
    from noust.deployers import lifecycle

    app_disk.set_disk_limit(releases_app, 128, actor="ana")
    asked: list[object] = []
    monkeypatch.setattr(lifecycle.app_disk, "require_room", lambda app, **k: asked.append(app))
    outcome = SimpleNamespace(deployment_id=None)
    monkeypatch.setattr(lifecycle, "_update_app", lambda *a, **k: outcome)
    monkeypatch.setattr(lifecycle, "_mark_replaced_rolled_back", lambda *a, **k: None)

    result = lifecycle.update_app(DOMAIN, rollback_of=7)

    assert result is outcome and asked == []
