"""
Tests for the storage manager: real mounts, what takes the space, and the closed
list of things that can be cleaned.

The Docker and journal outputs are the ones those tools print
(``tests/fixtures/server/docker`` and ``journal``). The guard under the cleanup
tests is that no endpoint can hand this module a path: an action is a key of a
closed list, and the two Docker actions that look tempting (``system prune`` and
``image prune -a``) are not on it, because one takes the databases' volumes and
the other takes every Compose application's way back.
"""

from __future__ import annotations

import os
import types
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.fs import DryRunFileSystem, RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.managers.server.errors import (
    ConfirmationRequiredError,
    ServerError,
    UnsupportedHostError,
)
from noust.managers.server.storage import (
    CLEANUP_ACTIONS,
    Analysis,
    Candidate,
    StorageManager,
    parse_docker_df,
    parse_docker_images,
    parse_docker_size,
    parse_journal_size,
)
from tests.server_support import fixture, make_host, platform_for


def _partition(device: str, mountpoint: str, fstype: str = "ext4", opts: str = "rw,relatime"):
    return types.SimpleNamespace(device=device, mountpoint=mountpoint, fstype=fstype, opts=opts)


def _usage(total: int, used: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        total=total, used=used, free=total - used, percent=round(used / total * 100, 1)
    )


@pytest.fixture
def fake_disks(monkeypatch: pytest.MonkeyPatch):
    """Put psutil and statvfs on a machine made of a few lines."""
    partitions = [
        _partition("/dev/sda1", "/"),
        _partition("/dev/sdb1", "/var/lib/data"),
        _partition("/dev/sdf", "/mnt/wsl/docker-desktop-bind-mounts/Ubuntu/a"),
        _partition("/dev/sdf", "/mnt/wsl/docker-desktop-bind-mounts/Ubuntu/b"),
        _partition("/dev/sdf", "/mnt/wsl"),
        _partition("overlay", "/var/lib/docker/overlay2/x/merged", "overlay"),
        _partition("tmpfs", "/run", "tmpfs"),
        _partition("/dev/loop3", "/snap/core/1", "squashfs", "ro"),
        _partition("/dev/sdc1", "/var/lib/docker/volumes/v", "ext4"),
        _partition("/dev/sdd1", "/boot", "ext4", "ro,relatime"),
    ]
    usages = {
        "/": _usage(100 * 1024**3, 71 * 1024**3),
        "/var/lib/data": _usage(50 * 1024**3, 46 * 1024**3),
        "/mnt/wsl": _usage(80 * 1024**3, 8 * 1024**3),
        "/boot": _usage(1024**3, int(0.99 * 1024**3)),
    }
    fake = types.SimpleNamespace(
        disk_partitions=lambda all=False: partitions,
        disk_usage=lambda path: usages[path],
    )
    monkeypatch.setattr("noust.managers.server.storage._psutil", lambda: fake)

    def statvfs(path: str) -> types.SimpleNamespace:
        files, free = {"/var/lib/data": (1000, 50)}.get(path, (1_000_000, 900_000))
        return types.SimpleNamespace(f_files=files, f_ffree=free)

    monkeypatch.setattr(os, "statvfs", statvfs)


@pytest.fixture
def host(tmp_path: Path):
    return make_host(tmp_path / "root")


def _manager(runner: FakeRunner, host, **kwargs) -> StorageManager:
    kwargs.setdefault("platform", platform_for("apt"))
    kwargs.setdefault("fs", RecordingFileSystem())
    kwargs.setdefault("release_apps", lambda: [])
    kwargs.setdefault("backups_usage", lambda: {})
    kwargs.setdefault("log_paths", lambda: [])
    return StorageManager(runner=runner, host=host, **kwargs)


class TestParsing:
    def test_the_real_journal_line_is_read_in_binary_units(self) -> None:
        assert parse_journal_size(fixture("journal", "disk-usage.txt")) == int(753.0 * 1024**2)
        assert parse_journal_size("nothing here") is None

    def test_docker_counts_in_powers_of_ten(self) -> None:
        assert parse_docker_size("29.98GB (63%)") == 29_980_000_000
        assert parse_docker_size("310.2MB") == 310_200_000
        assert parse_docker_size("57.3kB") == 57_300
        assert parse_docker_size("0B") == 0
        assert parse_docker_size("N/A") is None

    def test_the_real_docker_df_gives_each_kind_its_size_and_what_is_reclaimable(self) -> None:
        table = parse_docker_df(fixture("docker", "system-df.json"))

        assert table["Images"] == {
            "size": 47_430_000_000,
            "reclaimable": 29_980_000_000,
            "count": 61,
        }
        assert table["Build Cache"]["reclaimable"] == 19_500_000_000
        assert table["Local Volumes"]["size"] == 26_140_000_000

    def test_the_real_image_list_is_read_line_by_line(self) -> None:
        images = parse_docker_images(fixture("docker", "image-ls.json"))

        assert len(images) == 6
        assert images[0]["id"] == "646c6686dc07"
        assert images[0]["tag"] == "test"


class TestMounts:
    def test_a_device_mounted_many_times_is_one_row_at_its_shortest_mount_point(
        self, fake_disks, host
    ) -> None:
        mounts = _manager(FakeRunner(), host).mounts()

        assert [m.mount_point for m in mounts].count("/mnt/wsl") == 1
        assert not any("docker-desktop-bind-mounts" in m.mount_point for m in mounts)

    def test_pseudo_filesystems_and_docker_and_snap_are_left_out(self, fake_disks, host) -> None:
        points = {m.mount_point for m in _manager(FakeRunner(), host).mounts()}

        assert points == {"/", "/var/lib/data", "/mnt/wsl", "/boot"}

    def test_the_fullest_comes_first_and_inodes_are_counted(self, fake_disks, host) -> None:
        mounts = _manager(FakeRunner(), host).mounts()

        assert mounts[0].mount_point == "/boot"
        data = next(m for m in mounts if m.mount_point == "/var/lib/data")
        # 1000 inodes, 50 free: the disk is 92 % full and has 95 % of its inodes used.
        assert data.inodes_percent == 95.0
        assert data.status == "warn"

    def test_a_mount_that_is_read_only_is_never_the_worst_because_it_cannot_fill_up(
        self, fake_disks, host
    ) -> None:
        manager = _manager(FakeRunner(), host)

        worst = manager.worst_mount()

        assert worst is not None
        assert worst.mount_point == "/var/lib/data"
        boot = next(m for m in manager.mounts() if m.mount_point == "/boot")
        assert boot.readonly is True

    def test_status_follows_the_thresholds(self, fake_disks, host) -> None:
        by_point = {m.mount_point: m for m in _manager(FakeRunner(), host).mounts()}

        assert by_point["/"].status == "ok"
        assert by_point["/boot"].status == "critical"


class TestUsage:
    def test_the_journal_and_docker_are_measured_without_walking_any_tree(
        self, fake_disks, host
    ) -> None:
        runner = FakeRunner()
        runner.script(["journalctl", "--disk-usage"], stdout=fixture("journal", "disk-usage.txt"))
        runner.script(["docker", "system", "df"], stdout=fixture("docker", "system-df.json"))

        usage = _manager(runner, host).usage()

        ids = [c.id for c in usage["candidates"]]
        assert ids == ["journal", "docker-build-cache", "docker-images", "docker-volumes"]
        journal = usage["candidates"][0]
        assert journal.reclaimable_bytes == int(753.0 * 1024**2) - 200 * 1024**2
        assert journal.action == "journal"
        assert not runner.ran("du") and not runner.ran("ionice")

    def test_volumes_are_shown_and_never_offered_for_cleaning(self, fake_disks, host) -> None:
        runner = FakeRunner()
        runner.script(["docker", "system", "df"], stdout=fixture("docker", "system-df.json"))

        volumes = next(
            c for c in _manager(runner, host).usage()["candidates"] if c.id == "docker-volumes"
        )

        assert volumes.action is None
        assert volumes.reclaimable_bytes is None

    def test_docker_images_are_reported_but_only_removed_one_by_one(self, fake_disks, host) -> None:
        runner = FakeRunner()
        runner.script(["docker", "system", "df"], stdout=fixture("docker", "system-df.json"))

        images = next(
            c for c in _manager(runner, host).usage()["candidates"] if c.id == "docker-images"
        )

        assert images.action is None

    def test_a_machine_without_docker_or_a_journal_shows_neither(self, fake_disks, host) -> None:
        runner = FakeRunner().only_knows("apt-get")

        usage = _manager(runner, host).usage()

        assert usage["candidates"] == []

    def test_the_last_analysis_fills_in_what_the_quick_probes_do_not_measure(
        self, fake_disks, host
    ) -> None:
        analysis = Analysis(
            measured_at="2026-09-29T20:00:00+00:00",
            candidates=[Candidate("releases", 900, 400, "releases"), Candidate("journal", 1, 1)],
        )
        runner = FakeRunner()
        runner.script(["journalctl", "--disk-usage"], stdout=fixture("journal", "disk-usage.txt"))

        usage = _manager(runner, host).usage(analysis)

        assert [c.id for c in usage["candidates"]] == ["journal", "releases"]
        # The journal was measured now; the stale copy in the analysis does not replace it.
        assert usage["candidates"][0].size_bytes > 1
        assert usage["analysis_at"] == "2026-09-29T20:00:00+00:00"


class TestAnalysis:
    def test_it_measures_with_du_at_low_priority_on_one_filesystem(self, host) -> None:
        runner = FakeRunner()
        cache = host.at("/var/cache/apt/archives")
        cache.mkdir(parents=True)
        runner.script(["ionice"], stdout=f"640000000\t{cache}\n")

        analysis = _manager(runner, host).analyze()

        argv = runner.calls[0]
        assert argv == (
            "ionice",
            "-c3",
            "nice",
            "-n",
            "19",
            "du",
            "-sx",
            "-B1",
            "--",
            str(cache),
        )
        candidate = next(c for c in analysis.candidates if c.id == "pkg-cache")
        assert candidate.size_bytes == 640_000_000
        assert candidate.reclaimable_bytes == 640_000_000
        assert candidate.action == "pkg-cache"

    def test_a_path_that_is_not_there_is_not_measured_and_is_not_an_error(self, host) -> None:
        runner = FakeRunner()

        analysis = _manager(runner, host).analyze()

        assert analysis.candidates == []
        assert analysis.errors == []
        assert not runner.ran("ionice")

    def test_a_path_that_cannot_be_measured_in_time_is_reported_and_the_scan_goes_on(
        self, host
    ) -> None:
        runner = FakeRunner()
        crash = host.at("/var/crash")
        crash.mkdir(parents=True)
        tmp = host.at("/var/tmp")
        tmp.mkdir(parents=True)
        runner.script(["ionice"], stdout=f"4096\t{tmp}\n")
        runner.script(
            ["ionice", "-c3", "nice", "-n", "19", "du", "-sx", "-B1", "--", str(crash)],
            exit_code=-1,
        )

        analysis = _manager(runner, host).analyze()

        assert any(str(crash) in error for error in analysis.errors)
        assert any(c.id == "var-tmp" for c in analysis.candidates)

    def test_progress_names_each_group_as_it_starts(self, host) -> None:
        steps: list[str] = []

        _manager(FakeRunner(), host).analyze(steps.append)

        assert steps[0] == "Package caches"
        assert "Releases of applications" in steps
        assert steps[-1] == "Database data directories"

    def test_the_backups_are_counted_from_the_backup_managers_own_answer(self, host) -> None:
        manager = _manager(
            FakeRunner(),
            host,
            backups_usage=lambda: {"total_size_bytes": 5000, "total_backups": 7},
        )

        backups = next(c for c in manager.analyze().candidates if c.id == "backups")

        assert backups.size_bytes == 5000
        assert "7 backups" in backups.detail

    def _releases_app(self, tmp_path: Path) -> types.SimpleNamespace:
        root = tmp_path / "apps" / "shop"
        ids = [
            "20260925-100000-aaaaaaa",
            "20260926-100000-bbbbbbb",
            "20260927-100000-ccccccc",
            "20260928-100000-ddddddd",
        ]
        for release in ids:
            (root / "releases" / release).mkdir(parents=True)
        (root / "current").symlink_to(f"releases/{ids[-1]}")
        return types.SimpleNamespace(
            domain="shop.example.com", app_path=str(root), keep_releases=2, layout="releases"
        )

    def test_the_releases_a_prune_would_remove_are_measured_without_removing_them(
        self, host, tmp_path: Path
    ) -> None:
        app = self._releases_app(tmp_path)
        runner = FakeRunner()
        runner.script(["ionice"], stdout="1000\tx\n")
        manager = _manager(runner, host, release_apps=lambda: [app])

        candidate = next(c for c in manager.analyze().candidates if c.id == "releases")

        # Four releases of 1000 bytes; keep 2 plus the one before the active: two go.
        assert candidate.size_bytes == 4000
        assert candidate.reclaimable_bytes == 2000
        assert (tmp_path / "apps" / "shop" / "releases" / "20260925-100000-aaaaaaa").exists()


class TestCleanupIsAClosedList:
    def test_the_dangerous_docker_commands_are_not_actions(self) -> None:
        assert "docker-system-prune" not in CLEANUP_ACTIONS
        assert "docker-volumes" not in CLEANUP_ACTIONS
        assert "docker-image-prune-all" not in CLEANUP_ACTIONS

    def test_an_action_that_is_not_on_the_list_is_refused(self, host) -> None:
        manager = _manager(FakeRunner(), host)

        with pytest.raises(ValidationError, match="Unknown cleanup action"):
            manager.cleanup("rm -rf /")

    def test_the_journal_is_rotated_then_vacuumed_to_a_size(self, host) -> None:
        runner = FakeRunner()
        runner.script(["journalctl", "--disk-usage"], stdout=fixture("journal", "disk-usage.txt"))

        result = _manager(runner, host).cleanup("journal", size_mb=200)

        assert runner.calls[1:3] == [
            ("journalctl", "--rotate"),
            ("journalctl", "--vacuum-size=200M"),
        ]
        assert result.commands == ["journalctl --rotate", "journalctl --vacuum-size=200M"]

    def test_the_journal_can_be_vacuumed_by_age_instead(self, host) -> None:
        runner = FakeRunner()

        _manager(runner, host).cleanup("journal", days=14)

        assert ("journalctl", "--vacuum-time=14d") in runner.calls

    def test_freed_space_is_the_journal_before_minus_after(self, host) -> None:
        from tests.server_support import SequencedRunner

        runner = SequencedRunner()
        runner.sequence(
            ["journalctl", "--disk-usage"],
            [
                {"stdout": "Archived and active journals take up 753.0M in the file system."},
                {"stdout": "Archived and active journals take up 200.0M in the file system."},
            ],
        )

        result = _manager(runner, host).cleanup("journal")

        assert result.freed_bytes == int(553.0 * 1024**2)

    @pytest.mark.parametrize("bad", [0, -5, "ten", True, 1.5])
    def test_a_size_that_is_not_a_positive_whole_number_is_refused_before_anything_runs(
        self, host, bad
    ) -> None:
        runner = FakeRunner()

        with pytest.raises(ValidationError):
            _manager(runner, host).cleanup("journal", size_mb=bad)

        assert runner.calls == []

    def test_a_size_and_an_age_together_are_refused(self, host) -> None:
        with pytest.raises(ValidationError, match="not both"):
            _manager(FakeRunner(), host).cleanup("journal", size_mb=100, days=3)

    @pytest.mark.parametrize(
        ("family", "argv"),
        [
            ("apt", ["apt-get", "clean"]),
            ("dnf", ["dnf", "clean", "packages"]),
            ("zypper", ["zypper", "clean", "--all"]),
        ],
    )
    def test_the_package_cache_is_cleaned_by_the_machines_own_package_manager(
        self, host, family: str, argv: list[str]
    ) -> None:
        runner = FakeRunner()

        _manager(runner, host, platform=platform_for(family)).cleanup("pkg-cache")

        assert runner.calls[0] == tuple(argv)

    def test_a_system_without_a_package_manager_says_there_is_no_cache(self, host) -> None:
        with pytest.raises(UnsupportedHostError, match="no package cache"):
            _manager(FakeRunner(), host, platform=platform_for("none")).cleanup("pkg-cache")

    def test_a_failing_tool_is_an_error_with_its_own_words(self, host) -> None:
        runner = FakeRunner()
        runner.script(["apt-get", "clean"], stdout="E: Could not open lock", exit_code=100)

        with pytest.raises(ServerError) as raised:
            _manager(runner, host).cleanup("pkg-cache")

        assert "Could not open lock" in (raised.value.output or "")


class TestDockerCleanup:
    def test_the_build_cache_is_pruned_by_age_and_needs_a_yes(self, host) -> None:
        runner = FakeRunner()
        manager = _manager(runner, host)

        with pytest.raises(ConfirmationRequiredError) as raised:
            manager.cleanup("docker-build-cache")

        assert raised.value.required["commands"] == ["docker builder prune -f --filter until=168h"]
        assert not runner.ran("docker")

    def test_once_confirmed_the_reclaimed_space_is_read_from_dockers_words(self, host) -> None:
        runner = FakeRunner()
        runner.script(
            ["docker", "builder", "prune"],
            stdout="Deleted build cache objects:\nabc\n\nTotal reclaimed space: 6.1GB\n",
        )

        result = _manager(runner, host).cleanup("docker-build-cache", confirm=True)

        assert result.freed_bytes == 6_100_000_000
        assert runner.calls[0] == ("docker", "builder", "prune", "-f", "--filter", "until=168h")

    def test_dangling_images_are_pruned_without_dash_a(self, host) -> None:
        runner = FakeRunner()

        _manager(runner, host).cleanup("docker-dangling-images", confirm=True)

        assert runner.calls[0] == ("docker", "image", "prune", "-f")

    def test_without_docker_it_says_so(self, host) -> None:
        runner = FakeRunner().only_knows("apt-get")

        with pytest.raises(UnsupportedHostError, match="Docker is not installed"):
            _manager(runner, host).cleanup("docker-build-cache", confirm=True)

    def _images(self, runner: FakeRunner) -> None:
        runner.script(
            ["docker", "image", "ls"],
            stdout="\n".join(
                [
                    '{"Containers":"0","ID":"aaaaaaaaaaaa","Repository":"shop","Size":"1GB","Tag":"latest"}',
                    '{"Containers":"0","ID":"bbbbbbbbbbbb","Repository":"shop","Size":"1GB","Tag":"wasm-previous"}',
                    '{"Containers":"1","ID":"cccccccccccc","Repository":"redis","Size":"100MB","Tag":"7-alpine"}',
                    '{"Containers":"0","ID":"dddddddddddd","Repository":"old","Size":"5MB","Tag":"1"}',
                ]
            ),
        )
        runner.script(["docker", "ps"], stdout="redis:7-alpine\n")

    def test_unused_images_leave_out_the_way_back_and_what_a_container_uses(self, host) -> None:
        runner = FakeRunner()
        self._images(runner)

        unused = _manager(runner, host).docker_unused_images()

        assert [image["id"] for image in unused] == ["aaaaaaaaaaaa", "dddddddddddd"]

    def test_an_image_is_removed_by_id_without_force(self, host) -> None:
        runner = FakeRunner()
        self._images(runner)

        result = _manager(runner, host).cleanup("docker-image", confirm=True, target="dddddddddddd")

        assert ("docker", "image", "rm", "dddddddddddd") in runner.calls
        assert "-f" not in runner.calls[-1] and "--force" not in runner.calls[-1]
        assert result.removed == ["dddddddddddd"]

    def test_the_rollback_image_of_a_compose_application_cannot_be_removed(self, host) -> None:
        runner = FakeRunner()
        self._images(runner)

        with pytest.raises(ValidationError, match="not in the list of unused"):
            _manager(runner, host).cleanup("docker-image", confirm=True, target="bbbbbbbbbbbb")

        assert not runner.ran("docker", "image", "rm")

    def test_an_image_a_container_uses_cannot_be_removed(self, host) -> None:
        runner = FakeRunner()
        self._images(runner)

        with pytest.raises(ValidationError):
            _manager(runner, host).cleanup("docker-image", confirm=True, target="cccccccccccc")

    @pytest.mark.parametrize("bad", ["--all", "; rm -rf /", "x" * 100, None, "../../x"])
    def test_a_target_that_is_not_an_image_id_never_reaches_docker(self, host, bad) -> None:
        runner = FakeRunner()
        self._images(runner)

        with pytest.raises(ValidationError):
            _manager(runner, host).cleanup("docker-image", confirm=True, target=bad)

        assert not runner.ran("docker", "image", "rm")


class TestReleaseCleanup:
    def test_the_plan_lists_what_would_go_and_touches_nothing(self, host, tmp_path: Path) -> None:
        root = tmp_path / "apps" / "shop"
        ids = [
            "20260925-100000-aaaaaaa",
            "20260926-100000-bbbbbbb",
            "20260927-100000-ccccccc",
            "20260928-100000-ddddddd",
        ]
        for release in ids:
            (root / "releases" / release).mkdir(parents=True)
        (root / "current").symlink_to(f"releases/{ids[-1]}")
        app = types.SimpleNamespace(
            domain="shop.example.com", app_path=str(root), keep_releases=2, layout="releases"
        )
        manager = _manager(FakeRunner(), host, release_apps=lambda: [app])

        plan = manager.plan_cleanup("releases")

        assert set(plan.items) == {
            "shop.example.com: 20260925-100000-aaaaaaa",
            "shop.example.com: 20260926-100000-bbbbbbb",
        }
        assert plan.needs_confirmation is False
        assert (root / "releases" / ids[0]).exists()

    def test_the_cleanup_goes_through_the_release_retention_that_a_deploy_uses(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = types.SimpleNamespace(
            domain="shop.example.com", app_path="/nonexistent", keep_releases=3, layout="releases"
        )
        calls: list[tuple[str, int]] = []

        def retention(domain: str, keep: int):
            calls.append((domain, keep))
            return types.SimpleNamespace(pruned=("20260925-100000-aaaaaaa",))

        monkeypatch.setattr("noust.deployers.lifecycle.set_release_retention", retention)
        manager = _manager(FakeRunner(), host, release_apps=lambda: [app])
        lines: list[str] = []

        result = manager.cleanup("releases", on_line=lines.append)

        # Its own limit, unchanged: a cleanup prunes to the retention, it never lowers it.
        assert calls == [("shop.example.com", 3)]
        assert result.removed == ["shop.example.com: 20260925-100000-aaaaaaa"]
        assert lines == ["shop.example.com: removed release 20260925-100000-aaaaaaa"]


def test_a_rehearsal_is_reported_as_one(host) -> None:
    runner = FakeRunner()

    result = _manager(runner, host, fs=DryRunFileSystem()).cleanup("pkg-cache")

    assert result.dry_run is True
