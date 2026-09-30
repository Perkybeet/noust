# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Swap: what there is, making a swap file, and taking away the one Noust made.

A build that runs out of memory on a two-gigabyte server is killed by the kernel
with no message a person can read, which is why this exists. Making swap is
five commands that each have a way to leave something half done: a file that
another user can read while it is being filled, a ``swapon`` that refuses a file
with holes, an ``fstab`` line that turns the next boot into emergency mode. So
each step is undone if a later one fails, and the ``fstab`` line is checked with
``findmnt --verify`` before it stays.

Only the file Noust made can be removed. It marks its ``fstab`` line, and that
mark is the guard: a partition swap, ``/swap.img`` from the installer or zram
are shown and never touched.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.fs import SECRET_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server.errors import ServerError, UnsupportedHostError
from noust.managers.server.host import HostPaths, Platform, detect_platform, read_text

#: Where Noust makes its swap file.
SWAPFILE = "/swapfile"

#: What marks the ``fstab`` line as Noust's; the guard for removing it.
FSTAB_MARK = "# noust-swap"

#: The smallest and the largest swap file Noust makes, in bytes.
MIN_SWAP_BYTES = 256 * 1024**2
MAX_SWAP_BYTES = 64 * 1024**3

#: Creating swap must leave this much free, and at least this share of the disk.
MIN_FREE_AFTER_BYTES = 2 * 1024**3
MIN_FREE_AFTER_SHARE = 0.15

#: The value the swappiness is set to when a swap file is made. Low, because
#: this swap is a safety net for a build, not extra memory for the applications.
DEFAULT_SWAPPINESS = 10

#: The file that keeps the swappiness across reboots.
SYSCTL_FILE = "99-noust-swap.conf"

#: Deadline of the quick commands, and of writing the file (a few gigabytes with dd).
COMMAND_TIMEOUT = 60
WRITE_TIMEOUT = 900

#: The filesystems a swap file can be made on with ``fallocate``.
_FALLOCATE_FILESYSTEMS = frozenset({"ext4", "ext3", "xfs"})


@dataclass(frozen=True)
class SwapDevice:
    """
    One swap area.

    Attributes:
        name: The device or file.
        kind: ``partition``, ``file`` or ``zram``.
        size_bytes: Its size.
        used_bytes: How much is in use.
        priority: The kernel's priority for it.
        noust: Noust made it, so Noust may remove it.
    """

    name: str
    kind: str
    size_bytes: int
    used_bytes: int
    priority: int
    noust: bool = False


@dataclass
class SwapStatus:
    """
    The swap of this machine.

    Attributes:
        devices: Every active swap area.
        total_bytes: Their total size.
        used_bytes: Their total use.
        swappiness: ``vm.swappiness``, None when unreadable.
        memory_bytes: The machine's RAM.
        suggested_bytes: What a swap file should be, for this much RAM.
        recommended: There is none and the machine is small enough that a
            build may need it.
        supported: A swap file can be made here.
        reason: Why not, when it cannot.
        noust_swapfile: The active swap includes the file Noust made.
        warnings: Things to know: cloud-init would recreate the swap on every boot.
    """

    devices: list[SwapDevice] = field(default_factory=list)
    total_bytes: int = 0
    used_bytes: int = 0
    swappiness: int | None = None
    memory_bytes: int = 0
    suggested_bytes: int = 0
    recommended: bool = False
    supported: bool = True
    reason: str = ""
    noust_swapfile: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the status as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


def suggested_swap_bytes(memory_bytes: int) -> int:
    """
    Suggest a swap size for an amount of RAM.

    Args:
        memory_bytes: The machine's RAM.

    Returns:
        Two gigabytes for two or less, four for anything more.
    """
    return 2 * 1024**3 if memory_bytes <= 2 * 1024**3 else 4 * 1024**3


def parse_swapon(text: str, noust_file: str | None = None) -> list[SwapDevice]:
    """
    Read ``swapon --show --bytes --raw --noheadings``.

    Args:
        text: ``/dev/sdc partition 8589934592 0 -2``, one line per area.
        noust_file: The path of the file Noust made, to mark it.

    Returns:
        The swap areas.
    """
    devices = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 5 or not fields[2].isdigit() or not fields[3].isdigit():
            continue
        name, kind = fields[0], fields[1]
        if name.startswith("/dev/zram"):
            kind = "zram"
        devices.append(
            SwapDevice(
                name=name,
                kind=kind,
                size_bytes=int(fields[2]),
                used_bytes=int(fields[3]),
                priority=int(fields[4]) if fields[4].lstrip("-").isdigit() else 0,
                noust=noust_file is not None and name == noust_file,
            )
        )
    return devices


def parse_meminfo(text: str) -> tuple[int, int]:
    """
    Read the RAM of the machine from ``/proc/meminfo``.

    Args:
        text: The file.

    Returns:
        Total and available memory in bytes; 0 for what is missing.
    """
    values = {}
    for line in text.splitlines():
        match = re.match(r"^(MemTotal|MemAvailable):\s+(\d+) kB", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    return values.get("MemTotal", 0), values.get("MemAvailable", 0)


class SwapManager:
    """Reports swap, makes a swap file and removes the one Noust made."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        platform: Platform | None = None,
    ) -> None:
        """
        Args:
            runner: Command runner; the process-wide one when omitted.
            fs: Filesystem seam; the process-wide one when omitted.
            host: Where the system files are.
            platform: What the machine is; detected once when omitted.
        """
        self._runner = runner
        self._fs = fs
        self.host = host or HostPaths()
        self._platform = platform

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem in force right now."""
        return self._fs or get_fs()

    @property
    def platform(self) -> Platform:
        """What the machine is, detected on first use."""
        if self._platform is None:
            self._platform = detect_platform(self._runner, self.host)
        return self._platform

    @property
    def swapfile(self) -> Path:
        """Where the swap file is made, below the host root."""
        return self.host.at(SWAPFILE)

    # -- reading --------------------------------------------------------------

    def _fstab(self) -> str:
        """
        Read ``fstab``.

        Returns:
            Its text; empty when it cannot be read.
        """
        return read_text(self.host.fstab) or ""

    def owns_swapfile(self) -> bool:
        """
        Tell whether the swap file is Noust's.

        Returns:
            True when ``fstab`` carries Noust's mark on the swap file's line.
        """
        return any(SWAPFILE in line and FSTAB_MARK in line for line in self._fstab().splitlines())

    def _why_unsupported(self) -> str:
        """
        Say why a swap file cannot be made here.

        Returns:
            A sentence; empty when it can.
        """
        if self.platform.container:
            return (
                f"This is a container ({self.platform.container}), where swap belongs to the "
                "host: swapon is refused inside it."
            )
        return ""

    def _cloud_init_swap(self) -> bool:
        """
        Look for a cloud-init configuration that would recreate the swap on every boot.

        Returns:
            True when cloud-init is configured with a ``swap:`` section.
        """
        candidates = [self.host.at("/etc/cloud/cloud.cfg")]
        try:
            candidates += sorted(self.host.cloud_cfg_d.glob("*.cfg"))
        except OSError:
            pass
        return any(re.search(r"^swap:", read_text(path) or "", re.MULTILINE) for path in candidates)

    def status(self) -> SwapStatus:
        """
        Read the state of swap.

        Returns:
            The status.
        """
        result = self.runner.run(
            ["swapon", "--show", "--bytes", "--raw", "--noheadings"], timeout=COMMAND_TIMEOUT
        )
        devices = parse_swapon(
            result.stdout if result.success else "",
            SWAPFILE if self.owns_swapfile() else None,
        )
        memory, _available = parse_meminfo(read_text(self.host.at("/proc/meminfo")) or "")
        text = read_text(self.host.swappiness)
        swappiness = int(text) if text and text.strip().isdigit() else None
        reason = self._why_unsupported()
        warnings = []
        if self._cloud_init_swap():
            warnings.append(
                "cloud-init is configured with a swap: section, so it recreates the swap on "
                "every boot. Remove that section, or leave the swap to cloud-init."
            )
        return SwapStatus(
            devices=devices,
            total_bytes=sum(d.size_bytes for d in devices),
            used_bytes=sum(d.used_bytes for d in devices),
            swappiness=swappiness,
            memory_bytes=memory,
            suggested_bytes=suggested_swap_bytes(memory),
            recommended=not devices and 0 < memory <= 2 * 1024**3,
            supported=not reason,
            reason=reason,
            noust_swapfile=any(d.noust for d in devices),
            warnings=warnings,
        )

    # -- making ---------------------------------------------------------------

    def check_size(self, size_bytes: int) -> None:
        """
        Refuse a size that would leave the disk too full.

        Args:
            size_bytes: The size asked for.

        Raises:
            ValidationError: It is out of range, or the disk would be left with
                less than 2 GiB or 15 % free.
        """
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
            raise ValidationError(
                "The size must be a whole number of bytes", f"Got {size_bytes!r}."
            )
        if size_bytes % 1024**2:
            raise ValidationError(
                "The size must be a whole number of MiB", f"Got {size_bytes} bytes."
            )
        if not MIN_SWAP_BYTES <= size_bytes <= MAX_SWAP_BYTES:
            raise ValidationError(
                f"A swap file has to be between {MIN_SWAP_BYTES // 1024**2} MiB and "
                f"{MAX_SWAP_BYTES // 1024**3} GiB",
                f"Got {size_bytes // 1024**2} MiB.",
            )
        usage = shutil.disk_usage(self.host.at("/"))
        left = usage.free - size_bytes
        if left < MIN_FREE_AFTER_BYTES or left < usage.total * MIN_FREE_AFTER_SHARE:
            raise ValidationError(
                "The swap file would leave the disk too full",
                f"{left // 1024**2} MiB would be free; Noust keeps at least "
                f"{MIN_FREE_AFTER_BYTES // 1024**2} MiB and {int(MIN_FREE_AFTER_SHARE * 100)} % of the disk.",
            )

    def _filesystem(self) -> str:
        """
        Find the filesystem the swap file would live on.

        Returns:
            Its type, such as ``ext4``.
        """
        result = self.runner.run(["findmnt", "-no", "FSTYPE", "-T", "/"], timeout=COMMAND_TIMEOUT)
        return result.stdout.strip()

    def _step(
        self,
        argv: list[str],
        on_line: Callable[[str], None],
        message: str,
        *,
        timeout: int = COMMAND_TIMEOUT,
    ) -> str:
        """
        Run one command of making the swap, streaming what it prints.

        Args:
            argv: The command.
            on_line: Receives each output line.
            message: The sentence for the error when it fails.
            timeout: Deadline in seconds.

        Returns:
            What it printed.

        Raises:
            ServerError: It failed, carrying its output.
        """
        lines: list[str] = []

        def relay(line: str) -> None:
            lines.append(line)
            on_line(line)

        result = self.runner.stream(argv, on_line=relay, timeout=timeout)
        if not result.success:
            raise ServerError(
                f"{message} (exit code {result.exit_code})",
                "The steps already done are being undone.",
                output="\n".join(lines) or result.stderr,
            )
        return "\n".join(lines)

    def create(
        self,
        size_bytes: int,
        *,
        on_line: Callable[[str], None] | None = None,
        swappiness: int | None = DEFAULT_SWAPPINESS,
    ) -> list[str]:
        """
        Make ``/swapfile`` and switch it on, now and at every boot.

        Args:
            size_bytes: Its size.
            on_line: Receives what the tools print, verbatim.
            swappiness: ``vm.swappiness`` to set with it; None leaves it alone.

        Returns:
            One sentence per thing done.

        Raises:
            UnsupportedHostError: This is a container, or the filesystem cannot
                hold a swap file.
            ValidationError: The size is out of range or would fill the disk.
            ServerError: A step failed; what had been done is undone and the
                error carries the tool's output.
        """
        emit = on_line or (lambda line: None)
        reason = self._why_unsupported()
        if reason:
            raise UnsupportedHostError("Swap cannot be made here", reason)
        if self.swapfile.exists():
            raise ServerError(
                f"{SWAPFILE} already exists",
                "Noust does not overwrite it. Remove it yourself if it is not in use, or "
                "use it: 'swapon --show' says whether it is.",
            )
        self.check_size(size_bytes)
        if swappiness is not None:
            _valid_swappiness(swappiness)
        filesystem = self._filesystem()
        if filesystem not in _FALLOCATE_FILESYSTEMS | {"btrfs"}:
            raise UnsupportedHostError(
                f"A swap file cannot be made on {filesystem or 'this filesystem'}",
                "ext4, xfs and btrfs hold one. Make a swap partition instead.",
            )

        steps: list[str] = []
        fstab = self.host.fstab
        fstab_before = self._fstab()
        sysctl = self.host.sysctl_d / SYSCTL_FILE
        swap_on = False
        try:
            if filesystem == "btrfs":
                self._step(
                    ["btrfs", "filesystem", "mkswapfile", "--size", str(size_bytes), SWAPFILE],
                    emit,
                    "Could not make the swap file on btrfs",
                    timeout=WRITE_TIMEOUT,
                )
                steps.append(f"Made {SWAPFILE} with btrfs mkswapfile")
            else:
                self._write_swapfile(size_bytes, emit, steps)
            try:
                self._step(["swapon", SWAPFILE], emit, "swapon refused the swap file")
            except ServerError as refused:
                # fallocate can leave a file swapon calls sparse on some
                # filesystems; writing the zeros out is the portable way.
                if "holes" not in (refused.output or "") or filesystem == "btrfs":
                    raise
                self.fs.remove(self.swapfile)
                self._write_swapfile(size_bytes, emit, steps, reserve=False)
                self._step(["swapon", SWAPFILE], emit, "swapon refused the swap file")
            swap_on = True
            steps.append(f"Switched {SWAPFILE} on")

            backup = fstab.with_name(fstab.name + ".noust-bak")
            if fstab_before and not backup.exists():
                self.fs.write_text(backup, fstab_before)
            separator = "" if not fstab_before or fstab_before.endswith("\n") else "\n"
            self.fs.write_text(
                fstab, f"{fstab_before}{separator}{SWAPFILE} none swap sw 0 0 {FSTAB_MARK}\n"
            )
            steps.append("Added it to /etc/fstab")
            verify = self.runner.run(["findmnt", "--verify"], timeout=COMMAND_TIMEOUT)
            if not verify.success:
                raise ServerError(
                    "The new fstab line did not pass findmnt --verify",
                    "It is being removed, so the next boot is not sent into emergency mode.",
                    output="\n".join(
                        p for p in (verify.stdout.strip(), verify.stderr.strip()) if p
                    ),
                )
            if swappiness is not None:
                steps += self._apply_swappiness(swappiness)
        except (ServerError, OSError):
            self._undo(swap_on, fstab_before, sysctl)
            raise
        return steps

    def _write_swapfile(
        self,
        size_bytes: int,
        emit: Callable[[str], None],
        steps: list[str],
        *,
        reserve: bool = True,
    ) -> None:
        """
        Make the file, private before it is filled, and format it.

        Args:
            size_bytes: Its size.
            emit: Receives what the tools print.
            steps: Where to note what was done.
            reserve: Try ``fallocate`` first; False goes straight to writing zeros.

        Raises:
            ServerError: A command failed.
        """
        # Created empty and 0600 before a byte is reserved: a file that is
        # filled first and locked afterwards is readable, for that moment, by
        # every user of the machine.
        self.fs.write_text(self.swapfile, "", mode=SECRET_MODE)
        reserved = False
        if reserve:
            reserved = self.runner.run(
                ["fallocate", "-l", str(size_bytes), SWAPFILE], timeout=WRITE_TIMEOUT
            ).success
        if reserved:
            steps.append(f"Reserved {size_bytes // 1024**2} MiB with fallocate")
        else:
            megabytes = size_bytes // 1024**2
            self._step(
                [
                    "dd",
                    "if=/dev/zero",
                    f"of={SWAPFILE}",
                    "bs=1M",
                    f"count={megabytes}",
                    "status=none",
                ],
                emit,
                "Could not fill the swap file",
                timeout=WRITE_TIMEOUT,
            )
            steps.append(f"Wrote {megabytes} MiB to {SWAPFILE} with dd")
        self.fs.chmod(self.swapfile, SECRET_MODE)
        self._step(["mkswap", SWAPFILE], emit, "mkswap failed")
        steps.append("Formatted it with mkswap")

    def _apply_swappiness(self, value: int) -> list[str]:
        """
        Set ``vm.swappiness`` now and at every boot.

        Args:
            value: 0 to 100.

        Returns:
            What was done.

        Raises:
            ValidationError: The value is out of range.
            ServerError: sysctl refused.
        """
        _valid_swappiness(value)
        result = self.runner.run(
            ["sysctl", "-w", f"vm.swappiness={value}"], timeout=COMMAND_TIMEOUT
        )
        if not result.success:
            raise ServerError(
                "Could not set vm.swappiness",
                "sysctl refused.",
                output="\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p),
            )
        self.fs.write_text(
            self.host.sysctl_d / SYSCTL_FILE,
            f"# Generated by Noust\nvm.swappiness = {value}\n",
        )
        return [f"Set vm.swappiness to {value}, now and at boot"]

    def set_swappiness(self, value: int) -> list[str]:
        """
        Set how eagerly the kernel swaps.

        Args:
            value: 0 to 100.

        Returns:
            What was done.

        Raises:
            ValidationError: The value is out of range.
            ServerError: sysctl refused.
        """
        return self._apply_swappiness(value)

    def _undo(self, swap_on: bool, fstab_before: str, sysctl: Path) -> None:
        """
        Put back what a failed swap creation changed.

        Args:
            swap_on: ``swapon`` had succeeded.
            fstab_before: What ``fstab`` said before.
            sysctl: Noust's sysctl file.
        """
        if swap_on:
            self.runner.run(["swapoff", SWAPFILE], timeout=COMMAND_TIMEOUT)
        if self._fstab() != fstab_before:
            self.fs.write_text(self.host.fstab, fstab_before)
        if self.swapfile.exists():
            self.fs.remove(self.swapfile)
        if sysctl.exists():
            self.fs.remove(sysctl)

    # -- removing -------------------------------------------------------------

    def remove(self, on_line: Callable[[str], None] | None = None) -> list[str]:
        """
        Switch off and delete the swap file Noust made.

        Args:
            on_line: Receives what the tools print.

        Returns:
            One sentence per thing done.

        Raises:
            ServerError: The swap file is not Noust's, or what is in it does
                not fit in memory, or ``swapoff`` failed.
        """
        emit = on_line or (lambda line: None)
        if not self.owns_swapfile():
            raise ServerError(
                "This swap is not one Noust made",
                "Noust removes only the swap file it created, marked in /etc/fstab. A swap "
                "partition, the installer's swap image or zram are left alone.",
            )
        status = self.status()
        ours = next((d for d in status.devices if d.noust), None)
        _total, available = parse_meminfo(read_text(self.host.at("/proc/meminfo")) or "")
        if ours is not None and available and ours.used_bytes > available * 0.9:
            raise ServerError(
                "The swap in use does not fit back into memory",
                f"{ours.used_bytes // 1024**2} MiB are swapped out and only "
                f"{available // 1024**2} MiB of RAM are available. Stop something first.",
            )
        steps = []
        if ours is not None:
            self._step(["swapoff", SWAPFILE], emit, "swapoff failed")
            steps.append(f"Switched {SWAPFILE} off")
        fstab_text = self._fstab()
        kept = [
            line
            for line in fstab_text.splitlines(keepends=True)
            if not (SWAPFILE in line and FSTAB_MARK in line)
        ]
        self.fs.write_text(self.host.fstab, "".join(kept))
        steps.append("Removed it from /etc/fstab")
        if self.swapfile.exists():
            self.fs.remove(self.swapfile)
            steps.append(f"Deleted {SWAPFILE}")
        return steps


def _valid_swappiness(value: Any) -> int:
    """
    Check a ``vm.swappiness`` value.

    Args:
        value: What the caller gave.

    Returns:
        The value.

    Raises:
        ValidationError: It is not a whole number from 0 to 100.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValidationError("vm.swappiness is a whole number from 0 to 100", f"Got {value!r}.")
    return int(value)
