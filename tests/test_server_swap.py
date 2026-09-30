"""
Tests for swap: reading it, making a swap file, and taking away only that one.

Making swap is five commands that each leave something half done when the next
one fails: a file another user can read while it is being filled, an fstab line
that turns the next boot into emergency mode. So each failing step has a test
that checks what was left behind, and it is nothing.
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.managers.server.errors import ServerError, UnsupportedHostError
from noust.managers.server.swap import (
    FSTAB_MARK,
    SwapManager,
    parse_meminfo,
    parse_swapon,
    suggested_swap_bytes,
)
from tests.server_support import fixture, make_host, platform_for

GIB = 1024**3


@pytest.fixture
def host(tmp_path: Path):
    root = make_host(tmp_path / "root")
    root.fstab.write_text("UUID=abc / ext4 defaults 0 1\n")
    (root.root / "proc").mkdir(exist_ok=True)
    (root.root / "proc/meminfo").write_text(
        "MemTotal:        2048000 kB\nMemAvailable:    1500000 kB\n"
    )
    root.swappiness.write_text("60\n")
    return root


@pytest.fixture(autouse=True)
def roomy_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = shutil._ntuple_diskusage(total=100 * GIB, used=30 * GIB, free=70 * GIB)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage)


def _manager(runner: FakeRunner, host, **kwargs) -> SwapManager:
    kwargs.setdefault("platform", platform_for("apt"))
    return SwapManager(runner=runner, fs=RecordingFileSystem(), host=host, **kwargs)


def _ready(filesystem: str = "ext4") -> FakeRunner:
    runner = FakeRunner()
    runner.script(["findmnt", "-no", "FSTYPE"], stdout=f"{filesystem}\n")
    runner.script(["findmnt", "--verify"], stdout="Success, no errors or warnings detected\n")
    return runner


class TestReading:
    def test_the_real_swapon_line_is_a_partition_that_noust_did_not_make(self) -> None:
        devices = parse_swapon(fixture("system", "swapon.txt"))

        assert len(devices) == 1
        assert devices[0].name == "/dev/sdc"
        assert devices[0].kind == "partition"
        assert devices[0].size_bytes == 8_589_934_592
        assert devices[0].priority == -2
        assert devices[0].noust is False

    def test_zram_is_told_apart(self) -> None:
        devices = parse_swapon("/dev/zram0 partition 4294967296 0 100\n")

        assert devices[0].kind == "zram"

    def test_a_line_that_is_not_a_swap_area_is_skipped(self) -> None:
        assert parse_swapon("NAME TYPE SIZE USED PRIO\nnot enough\n") == []

    def test_meminfo_gives_total_and_available_in_bytes(self) -> None:
        assert parse_meminfo(
            "MemTotal:  2048000 kB\nMemFree: 1 kB\nMemAvailable:  1500000 kB\n"
        ) == (
            2048000 * 1024,
            1500000 * 1024,
        )

    def test_the_suggested_size_follows_the_ram(self) -> None:
        assert suggested_swap_bytes(1 * GIB) == 2 * GIB
        assert suggested_swap_bytes(2 * GIB) == 2 * GIB
        assert suggested_swap_bytes(8 * GIB) == 4 * GIB

    def test_a_small_machine_with_no_swap_is_told_it_should_have_some(self, host) -> None:
        runner = FakeRunner()
        runner.script(["swapon"], stdout="")

        status = _manager(runner, host).status()

        assert status.devices == []
        assert status.recommended is True
        assert status.suggested_bytes == 2 * GIB
        assert status.swappiness == 60
        assert status.supported is True

    def test_the_real_swap_of_this_machine_is_reported_with_its_totals(self, host) -> None:
        runner = FakeRunner()
        runner.script(["swapon"], stdout=fixture("system", "swapon.txt"))

        status = _manager(runner, host).status()

        assert status.total_bytes == 8_589_934_592
        assert status.recommended is False
        assert status.noust_swapfile is False

    def test_the_fstab_mark_is_what_makes_a_swap_file_nousts(self, host) -> None:
        host.fstab.write_text(f"/swapfile none swap sw 0 0 {FSTAB_MARK}\n")
        (host.root / "swapfile").write_text("")
        runner = FakeRunner()
        runner.script(["swapon"], stdout="/swapfile file 2147483648 0 -2\n")
        manager = _manager(runner, host)

        status = manager.status()

        assert manager.owns_swapfile() is True
        assert status.noust_swapfile is True

    def test_a_container_is_told_swap_belongs_to_its_host(self, host) -> None:
        manager = _manager(FakeRunner(), host, platform=platform_for("apt", container="lxc"))

        status = manager.status()

        assert status.supported is False
        assert "lxc" in status.reason

    def test_a_cloud_init_that_configures_swap_is_a_warning(self, host) -> None:
        (host.root / "etc/cloud").mkdir(parents=True)
        (host.root / "etc/cloud/cloud.cfg").write_text(
            "users:\n  - default\nswap:\n  filename: /swap.img\n"
        )

        status = _manager(FakeRunner(), host).status()

        assert any("cloud-init" in warning for warning in status.warnings)


class TestMaking:
    def test_it_creates_formats_switches_on_and_records_it_in_that_order(self, host) -> None:
        runner = _ready()
        manager = _manager(runner, host)

        steps = manager.create(2 * GIB)

        commands = [
            call for call in runner.calls if call[0] in {"fallocate", "mkswap", "swapon", "sysctl"}
        ]
        assert commands == [
            ("fallocate", "-l", str(2 * GIB), "/swapfile"),
            ("mkswap", "/swapfile"),
            ("swapon", "/swapfile"),
            ("sysctl", "-w", "vm.swappiness=10"),
        ]
        assert steps[-1] == "Set vm.swappiness to 10, now and at boot"

    def test_the_file_is_private_and_the_fstab_line_is_marked_and_the_old_fstab_kept(
        self, host
    ) -> None:
        manager = _manager(_ready(), host)

        manager.create(2 * GIB)

        mode = stat.S_IMODE((host.root / "swapfile").stat().st_mode)
        assert mode == 0o600
        fstab = host.fstab.read_text()
        assert fstab.startswith("UUID=abc / ext4 defaults 0 1\n")
        assert fstab.endswith(f"/swapfile none swap sw 0 0 {FSTAB_MARK}\n")
        assert (
            host.fstab.with_name("fstab.noust-bak").read_text() == "UUID=abc / ext4 defaults 0 1\n"
        )
        assert "vm.swappiness = 10" in (host.sysctl_d / "99-noust-swap.conf").read_text()

    def test_an_fstab_without_a_final_newline_does_not_get_two_lines_glued_together(
        self, host
    ) -> None:
        host.fstab.write_text("UUID=abc / ext4 defaults 0 1")

        _manager(_ready(), host).create(2 * GIB)

        assert host.fstab.read_text().splitlines()[0] == "UUID=abc / ext4 defaults 0 1"

    def test_when_fallocate_fails_the_zeros_are_written_with_dd(self, host) -> None:
        runner = _ready()
        runner.script(
            ["fallocate"], stderr="fallocate failed: Operation not supported", exit_code=1
        )

        _manager(runner, host).create(2 * GIB)

        assert (
            "dd",
            "if=/dev/zero",
            "of=/swapfile",
            "bs=1M",
            "count=2048",
            "status=none",
        ) in runner.calls

    def test_a_file_swapon_calls_sparse_is_rewritten_with_dd_and_tried_again(self, host) -> None:
        from tests.server_support import SequencedRunner

        runner = SequencedRunner()
        runner.script(["findmnt", "-no", "FSTYPE"], stdout="xfs\n")
        runner.script(["findmnt", "--verify"], stdout="Success\n")
        runner.sequence(
            ["swapon"],
            [
                {
                    "stdout": "swapon: /swapfile: skipping - it appears to have holes.",
                    "exit_code": 255,
                },
                {"stdout": ""},
            ],
        )

        _manager(runner, host).create(2 * GIB)

        assert [c[0] for c in runner.calls if c[0] in {"fallocate", "dd", "swapon"}] == [
            "fallocate",
            "swapon",
            "dd",
            "swapon",
        ]

    def test_btrfs_makes_the_file_itself(self, host) -> None:
        runner = _ready("btrfs")

        _manager(runner, host).create(2 * GIB)

        assert (
            "btrfs",
            "filesystem",
            "mkswapfile",
            "--size",
            str(2 * GIB),
            "/swapfile",
        ) in runner.calls
        assert not runner.ran("fallocate")
        assert not runner.ran("mkswap")

    def test_a_filesystem_that_cannot_hold_swap_is_refused_before_anything_is_made(
        self, host
    ) -> None:
        runner = _ready("zfs")

        with pytest.raises(UnsupportedHostError, match="zfs"):
            _manager(runner, host).create(2 * GIB)

        assert not (host.root / "swapfile").exists()

    def test_a_container_cannot_make_swap(self, host) -> None:
        manager = _manager(_ready(), host, platform=platform_for("apt", container="openvz"))

        with pytest.raises(UnsupportedHostError, match="container"):
            manager.create(2 * GIB)

    def test_an_existing_swapfile_is_never_overwritten(self, host) -> None:
        (host.root / "swapfile").write_text("something")

        with pytest.raises(ServerError, match="already exists"):
            _manager(_ready(), host).create(2 * GIB)

        assert (host.root / "swapfile").read_text() == "something"

    @pytest.mark.parametrize("size", [0, 100 * 1024**2, 100 * GIB, 2 * GIB + 1, "2G", True])
    def test_a_size_out_of_range_or_not_a_whole_number_of_mib_is_refused(self, host, size) -> None:
        runner = _ready()

        with pytest.raises(ValidationError):
            _manager(runner, host).create(size)

        assert not runner.ran("fallocate")

    def test_a_disk_that_would_be_left_too_full_is_refused(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tight = shutil._ntuple_diskusage(total=10 * GIB, used=7 * GIB, free=3 * GIB)
        monkeypatch.setattr(shutil, "disk_usage", lambda path: tight)

        with pytest.raises(ValidationError, match="too full"):
            _manager(_ready(), host).create(2 * GIB)

    def test_a_bad_swappiness_is_refused_before_a_file_is_made(self, host) -> None:
        with pytest.raises(ValidationError, match="0 to 100"):
            _manager(_ready(), host).create(2 * GIB, swappiness=101)

        assert not (host.root / "swapfile").exists()


class TestUndoing:
    def test_a_failing_mkswap_leaves_no_file_and_the_fstab_as_it_was(self, host) -> None:
        runner = _ready()
        runner.script(["mkswap"], stderr="mkswap: /swapfile: insecure permissions", exit_code=1)

        with pytest.raises(ServerError) as raised:
            _manager(runner, host).create(2 * GIB)

        assert "insecure permissions" in (raised.value.output or "")
        assert not (host.root / "swapfile").exists()
        assert host.fstab.read_text() == "UUID=abc / ext4 defaults 0 1\n"
        assert not runner.ran("swapoff")

    def test_a_failing_swapon_leaves_nothing_behind(self, host) -> None:
        runner = _ready()
        runner.script(
            ["swapon"], stderr="swapon: /swapfile: swapon failed: Invalid argument", exit_code=255
        )

        with pytest.raises(ServerError):
            _manager(runner, host).create(2 * GIB)

        assert not (host.root / "swapfile").exists()
        assert host.fstab.read_text() == "UUID=abc / ext4 defaults 0 1\n"

    def test_an_fstab_that_fails_verification_is_restored_and_the_swap_switched_off(
        self, host
    ) -> None:
        runner = FakeRunner()
        runner.script(["findmnt", "-no", "FSTYPE"], stdout="ext4\n")
        runner.script(["findmnt", "--verify"], stdout="/swapfile: unreachable on boot", exit_code=1)

        with pytest.raises(ServerError, match="did not pass findmnt --verify") as raised:
            _manager(runner, host).create(2 * GIB)

        assert "unreachable on boot" in (raised.value.output or "")
        assert ("swapoff", "/swapfile") in runner.calls
        assert host.fstab.read_text() == "UUID=abc / ext4 defaults 0 1\n"
        assert not (host.root / "swapfile").exists()

    def test_a_failing_sysctl_undoes_everything_before_it(self, host) -> None:
        runner = _ready()
        runner.script(["sysctl"], stderr="sysctl: permission denied", exit_code=1)

        with pytest.raises(ServerError, match=r"vm\.swappiness"):
            _manager(runner, host).create(2 * GIB)

        assert ("swapoff", "/swapfile") in runner.calls
        assert host.fstab.read_text() == "UUID=abc / ext4 defaults 0 1\n"


class TestRemoving:
    def _owned(self, host, runner: FakeRunner) -> SwapManager:
        host.fstab.write_text(
            f"UUID=abc / ext4 defaults 0 1\n/swapfile none swap sw 0 0 {FSTAB_MARK}\n"
        )
        (host.root / "swapfile").write_text("")
        runner.script(["swapon", "--show"], stdout="/swapfile file 2147483648 1048576 -2\n")
        return _manager(runner, host)

    def test_only_the_file_noust_made_is_removed(self, host) -> None:
        runner = FakeRunner()
        manager = self._owned(host, runner)

        steps = manager.remove()

        assert ("swapoff", "/swapfile") in runner.calls
        assert host.fstab.read_text() == "UUID=abc / ext4 defaults 0 1\n"
        assert not (host.root / "swapfile").exists()
        assert steps == [
            "Switched /swapfile off",
            "Removed it from /etc/fstab",
            "Deleted /swapfile",
        ]

    def test_a_swap_noust_did_not_make_is_never_touched(self, host) -> None:
        runner = FakeRunner()
        runner.script(["swapon"], stdout=fixture("system", "swapon.txt"))

        with pytest.raises(ServerError, match="not one Noust made"):
            _manager(runner, host).remove()

        assert not runner.ran("swapoff")

    def test_swap_that_would_not_fit_back_into_memory_stops_the_removal(self, host) -> None:
        runner = FakeRunner()
        manager = self._owned(host, runner)
        runner.script(["swapon", "--show"], stdout="/swapfile file 2147483648 1966080000 -2\n")

        with pytest.raises(ServerError, match="does not fit back into memory"):
            manager.remove()

        assert not runner.ran("swapoff")
        assert (host.root / "swapfile").exists()


class TestSwappiness:
    def test_it_is_set_now_and_kept_for_the_next_boot(self, host) -> None:
        runner = FakeRunner()

        steps = _manager(runner, host).set_swappiness(30)

        assert ("sysctl", "-w", "vm.swappiness=30") in runner.calls
        assert "vm.swappiness = 30" in (host.sysctl_d / "99-noust-swap.conf").read_text()
        assert steps == ["Set vm.swappiness to 30, now and at boot"]

    @pytest.mark.parametrize("bad", [-1, 101, "10", 1.5, True])
    def test_anything_but_a_whole_number_from_0_to_100_is_refused(self, host, bad) -> None:
        runner = FakeRunner()

        with pytest.raises(ValidationError):
            _manager(runner, host).set_swappiness(bad)

        assert not runner.ran("sysctl")
