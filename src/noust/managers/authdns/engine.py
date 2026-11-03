# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Knot DNS as Noust runs it: installing it, giving it a configuration and zone files, reloading it,
and asking it what it knows.

Everything goes through the runner (argv, a timeout on each) and the file system seam, so a
rehearsal (``--dry-run``) changes nothing. Three rules decide what this module does with a
machine that already has something on it:

- **A Knot that already serves zones is not taken over.** The configuration Noust writes replaces
  the whole file, so a file that is not marked as Noust's and names zones is an operator's DNS;
  it is refused, not rewritten. One that names none (the package's own sample) is kept beside the
  new one as ``knot.conf.before-noust`` and put back when Noust DNS is removed.
- **A configuration is checked before it replaces the working one** (``knotc conf-check`` on the
  candidate), and when the server does not take it the previous file is put back and reloaded.
- **Removing it keeps the keys.** The signing keys are the only copy of what the registrar's DS
  record points at; removing Noust DNS stops the server and puts the configuration back, and
  leaves the keys where they are.
"""

from __future__ import annotations

import grp
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.core import paths
from noust.core.exceptions import IntegrationError, ServiceError
from noust.core.fs import FileSystem, get_fs
from noust.core.package_family import install_apt_packages
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.managers.authdns.knotconf import CONFIG_MARKER
from noust.managers.server.errors import ConfirmationRequiredError, UnsupportedHostError
from noust.managers.server.host import PackageFamily, detect_platform
from noust.managers.server.security_fail2ban import EPEL_PACKAGES
from noust.managers.server.software_record import packages_named_in, record_install

__all__ = [
    "BACKUP_SUFFIX",
    "ENV",
    "MIN_VERSION",
    "DsRecord",
    "EngineError",
    "KnotEngine",
    "ZoneStatus",
]

log = logging.getLogger(__name__)

#: The oldest Knot that generates and interprets catalog zones (RFC 9432), which is how a
#: secondary learns the zones of its primary.
MIN_VERSION = (3, 1)

#: Beside the configuration, where the package's own file is kept the first time Noust replaces it.
BACKUP_SUFFIX = ".before-noust"

#: The environment of every package and Knot command.
ENV = {"LC_ALL": "C", "TERM": "dumb", "DEBIAN_FRONTEND": "noninteractive"}

#: Seconds a package install may take, and any other command.
INSTALL_TIMEOUT = 600
COMMAND_TIMEOUT = 60

#: How many times, half a second apart, a Knot that was just started is asked whether it answers.
READY_ATTEMPTS = 40
READY_PAUSE = 0.5

_VERSION = re.compile(r"Knot DNS (\d+)\.(\d+)(?:\.(\d+))?")
_DOMAIN_LINE = re.compile(r"^\s*-\s*domain:\s*[\"']?([^\s\"'#]+)", re.MULTILINE)
_ZONE_STATUS = re.compile(r"\[(?P<zone>[^\]]+)\]\s*(?P<rest>.*)")
_DS = re.compile(r"(?P<zone>\S+)\s+DS\s+(\d+)\s+(\d+)\s+(\d+)\s+([0-9a-fA-F]+)")


class EngineError(ServiceError, IntegrationError):
    """
    Knot, or its configuration, could not be put into the state asked for.

    The program refused what it was given (a zone that fails its checks, a configuration it does
    not read) or would not start: an answer of the API is 502, "the program under Noust said no",
    with its own words in ``output``, and not a 500 for a fault in Noust.
    """


@dataclass(frozen=True)
class ZoneStatus:
    """
    What Knot says of a zone it serves.

    Attributes:
        zone: The zone, without the final dot.
        role: ``master`` (it holds the zone) or ``slave`` (it copies it), as Knot says.
        serial: The SOA serial it serves now; None when it has none yet.
        catalog: The catalog the zone belongs to, or how it takes part (``generate``,
            ``interpret``); empty for a zone that is in none.
    """

    zone: str
    role: str
    serial: int | None
    catalog: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The status for the API.
        """
        return {
            "zone": self.zone,
            "role": self.role,
            "serial": self.serial,
            "catalog": self.catalog,
        }


@dataclass(frozen=True)
class DsRecord:
    """
    A DS record: what the registrar is given so the parent zone vouches for this zone's key.

    Attributes:
        zone: The zone.
        key_tag: The key's tag.
        algorithm: The signing algorithm's number (13 is ECDSA P-256).
        digest_type: 2 is SHA-256, 4 is SHA-384.
        digest: The digest, hexadecimal in lower case.
    """

    zone: str
    key_tag: int
    algorithm: int
    digest_type: int
    digest: str

    @property
    def text(self) -> str:
        """The record as a registrar's form takes it: ``tag algorithm digest-type digest``."""
        return f"{self.key_tag} {self.algorithm} {self.digest_type} {self.digest}"

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The record for the API.
        """
        return {
            "key_tag": self.key_tag,
            "algorithm": self.algorithm,
            "digest_type": self.digest_type,
            "digest": self.digest,
            "text": self.text,
        }


class KnotEngine:
    """
    Knot DNS on this machine.

    Args:
        runner: Runs the programs; the process-wide runner by default.
        fs: Writes the files; the process-wide file system by default.
        config_path: Knot's configuration file.
        zones_dir: Where the zone files are.
        group: The group Knot runs as, which must read what Noust writes; None writes the files
            for root only (tests, and a machine with no such group yet).
        sleep: Waits (tests pass one that does not).
    """

    def __init__(
        self,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        *,
        config_path: Path | None = None,
        zones_dir: Path | None = None,
        group: str | None = "knot",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._runner = runner
        self._fs = fs
        self.config_path = config_path or paths.AUTHDNS_CONFIG
        self.zones_dir = zones_dir or paths.AUTHDNS_ZONES
        self.group = group
        self._sleep = sleep

    @property
    def runner(self) -> CommandRunner:
        """The runner."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The file system."""
        return self._fs or get_fs()

    def _run(self, argv: Sequence[str], timeout: int = COMMAND_TIMEOUT) -> CommandResult:
        return self.runner.run(list(argv), timeout=timeout, env=ENV)

    def _knotc(self, *args: str, timeout: int = COMMAND_TIMEOUT) -> CommandResult:
        return self._run(["knotc", "-c", str(self.config_path), *args], timeout)

    # -- what is installed ---------------------------------------------------------------

    def installed(self) -> bool:
        """
        Returns:
            Knot and its tools are on this machine.
        """
        return all(self.runner.exists(program) for program in ("knotd", "knotc", "keymgr"))

    def version(self) -> tuple[int, ...] | None:
        """
        Returns:
            The version of Knot (``(3, 4, 6)``), or None when it is not installed or does not say.
        """
        if not self.runner.exists("knotd"):
            return None
        result = self._run(["knotd", "--version"])
        found = _VERSION.search(f"{result.stdout}\n{result.stderr}")
        if not result.success or found is None:
            return None
        return tuple(int(part) for part in found.groups() if part is not None)

    def require_version(self) -> tuple[int, ...]:
        """
        Refuse a Knot too old to interpret a catalog.

        Returns:
            Its version.

        Raises:
            EngineError: It is not installed, or older than :data:`MIN_VERSION`.
        """
        found = self.version()
        if found is None:
            raise EngineError(
                "Knot DNS is not installed",
                details="Install it with: noust dns server install",
            )
        if found[:2] < MIN_VERSION:
            wanted = ".".join(str(part) for part in MIN_VERSION)
            raise EngineError(
                f"This system's Knot DNS is {'.'.join(str(p) for p in found)}, and Noust DNS needs "
                f"{wanted} or newer",
                details="Secondaries learn the zones of their primary through catalog zones, which "
                f"Knot has from {wanted}. Install a newer Knot from the project's own repository "
                "(knot-dns.cz) and run this again.",
            )
        return found

    def is_active(self) -> bool:
        """
        Returns:
            The Knot service is running.
        """
        result = self._run(["systemctl", "is-active", paths.AUTHDNS_UNIT])
        return result.success and result.stdout.strip() == "active"

    # -- installing ----------------------------------------------------------------------

    def install_plan(self) -> tuple[list[list[str]], bool]:
        """
        How Knot is installed here.

        Returns:
            The commands (EPEL's first, when needed) and whether EPEL is needed.

        Raises:
            UnsupportedHostError: Noust cannot install packages on this system.
        """
        platform = detect_platform(self.runner)
        os_id = platform.os.id
        major = platform.os.version_id.split(".")[0]
        if platform.family is PackageFamily.APT:
            return [["apt-get", "install", "-y", "knot"]], False
        if platform.family is PackageFamily.ZYPPER:
            return [["zypper", "--non-interactive", "install", "knot"]], False
        if platform.family is PackageFamily.DNF:
            if os_id == "fedora":
                return [["dnf", "install", "-y", "knot"]], False
            if os_id == "rhel":
                raise UnsupportedHostError(
                    "Noust cannot install Knot DNS here",
                    f"On RHEL, Knot comes from EPEL, which needs your subscription's CodeReady "
                    f"Builder repository: subscription-manager repos --enable "
                    f"codeready-builder-for-rhel-{major}-$(arch)-rpms; dnf install -y "
                    f"https://dl.fedoraproject.org/pub/epel/epel-release-latest-{major}.noarch.rpm; "
                    "dnf install -y knot. Then run noust dns server setup again.",
                )
            epel = EPEL_PACKAGES.get(os_id)
            if epel is None and "rhel" in platform.os.id_like:
                epel = "epel-release"
            if epel is not None:
                return (
                    [
                        ["dnf", "install", "-y", epel.format(major=major)],
                        ["dnf", "install", "-y", "knot"],
                    ],
                    True,
                )
            return [["dnf", "install", "-y", "knot"]], False
        raise UnsupportedHostError(
            "Noust cannot install Knot DNS here",
            platform.why_updates_unsupported() or "No supported package manager.",
        )

    def installation_plan(self, *, epel: bool = False) -> list[list[str]]:
        """
        How Knot is installed here, once the operator has agreed to what it takes.

        Args:
            epel: The operator agreed to enable EPEL, where Knot comes from it.

        Returns:
            The commands.

        Raises:
            ConfirmationRequiredError: EPEL is needed and ``epel`` is False.
            UnsupportedHostError: It cannot be installed here.
        """
        commands, needs_epel = self.install_plan()
        if needs_epel and not epel:
            raise ConfirmationRequiredError(
                "Knot DNS comes from EPEL on this system",
                details="EPEL is a third-party repository maintained by Fedora. Confirm that it "
                "may be enabled to install Knot from it.",
                required={"epel": True, "packages": [argv[-1] for argv in commands]},
            )
        return commands

    def install(
        self, *, epel: bool = False, on_output: Callable[[str], None] | None = None
    ) -> tuple[int, ...]:
        """
        Install Knot, unless it is, and check that it is new enough.

        Args:
            epel: The operator agreed to enable EPEL, where Knot comes from it.
            on_output: Receives each command and its output.

        Returns:
            The version installed.

        Raises:
            ConfirmationRequiredError: EPEL is needed and ``epel`` is False.
            UnsupportedHostError: It cannot be installed here.
            EngineError: A step failed, or the version is too old.
        """
        if self.installed():
            return self.require_version()
        commands = self.installation_plan(epel=epel)
        for argv in commands:
            if argv[:3] == ["apt-get", "install", "-y"]:
                result = install_apt_packages(
                    self.runner, argv[3:], timeout=INSTALL_TIMEOUT, env=ENV
                )
            else:
                result = self._run(argv, INSTALL_TIMEOUT)
            if on_output is not None:
                on_output("$ " + " ".join(argv))
                for line in (result.stdout + result.stderr).splitlines():
                    on_output(line)
            if not result.success:
                raise EngineError(
                    f"Installing failed: {' '.join(argv)}",
                    output=(result.stderr or result.stdout).strip() or None,
                )
        record_install("knot", via="knot", packages=packages_named_in("knot", commands))
        return self.require_version()

    # -- the configuration ---------------------------------------------------------------

    def current_config(self) -> str | None:
        """
        Returns:
            The text of Knot's configuration, or None when there is none.
        """
        try:
            return self.config_path.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, IsADirectoryError):
            return None

    def foreign_zones(self) -> list[str]:
        """
        Returns:
            The zones a configuration that is not Noust's names, which are somebody's DNS.
        """
        text = self.current_config()
        if text is None or CONFIG_MARKER in text:
            return []
        uncommented = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        return _DOMAIN_LINE.findall(uncommented)

    def _owner(self) -> tuple[int, int] | None:
        """
        Returns:
            The owner of the files Knot reads: root, in Knot's group; None when no group is set.

        Raises:
            EngineError: The group does not exist (Knot is not installed).
        """
        if self.group is None:
            return None
        try:
            return (0, grp.getgrnam(self.group).gr_gid)
        except KeyError:
            raise EngineError(
                f"The group {self.group} does not exist on this machine",
                details="Knot's package creates it: run noust dns server install.",
            ) from None

    def apply_config(self, text: str, *, restart: bool = False) -> None:
        """
        Make a configuration the one Knot runs, putting the old one back if Knot does not take it.

        Args:
            text: The new configuration.
            restart: Restart Knot instead of reloading it (the addresses it listens on changed).

        Raises:
            EngineError: This machine's Knot serves zones of its own, Knot refused the
                configuration, or it would not start with it; the previous file is back.
        """
        foreign = self.foreign_zones()
        if foreign:
            raise EngineError(
                "This server already runs Knot DNS with zones of its own",
                details=f"{self.config_path} names {', '.join(foreign[:5])}"
                f"{' and more' if len(foreign) > 5 else ''}. Noust DNS writes that whole file, so it "
                "does not take over a DNS you run yourself: use a server without one.",
            )
        previous = self.current_config()
        owner = self._owner()
        candidate = self.config_path.with_name(self.config_path.name + ".noust-new")
        self.fs.make_dir(self.config_path.parent)
        self.fs.write_text(candidate, text, mode=0o640, owner=owner)
        checked = self._run(["knotc", "-c", str(candidate), "conf-check"])
        if not checked.success:
            self.fs.remove(candidate)
            raise EngineError(
                "Knot DNS did not accept the configuration Noust wrote",
                details="This is a defect of Noust: report it with the output below.",
                output=(checked.stderr or checked.stdout).strip() or None,
            )
        backup = self.config_path.with_name(self.config_path.name + BACKUP_SUFFIX)
        if previous is not None and CONFIG_MARKER not in previous and not backup.exists():
            self.fs.write_text(backup, previous, mode=0o644)
        self.fs.rename(candidate, self.config_path)
        try:
            self._start_or_reload(restart=restart)
        except EngineError:
            if previous is None:
                self.fs.remove(self.config_path)
            else:
                self.fs.write_text(self.config_path, previous, mode=0o640, owner=owner)
            self._run(["systemctl", "restart", paths.AUTHDNS_UNIT], INSTALL_TIMEOUT)
            raise

    def _start_or_reload(self, *, restart: bool) -> None:
        """
        Bring the service to the configuration on disk.

        Args:
            restart: Restart it even if it is running.

        Raises:
            EngineError: It did not start or did not accept the reload.
        """
        if restart or not self.is_active():
            result = self._run(["systemctl", "enable", paths.AUTHDNS_UNIT], COMMAND_TIMEOUT)
            if not result.success:
                raise EngineError(
                    "Knot could not be enabled",
                    output=(result.stderr or result.stdout).strip() or None,
                )
            result = self._run(["systemctl", "restart", paths.AUTHDNS_UNIT], INSTALL_TIMEOUT)
            if not result.success:
                raise EngineError(
                    "Knot would not start with this configuration",
                    details="See why: journalctl -u knot",
                    output=(result.stderr or result.stdout).strip() or None,
                )
            self._wait_until_answering()
            return
        result = self._knotc("-b", "reload")
        if not result.success:
            raise EngineError(
                "Knot did not accept the new configuration",
                details="Nothing was changed: the previous configuration is back.",
                output=(result.stderr or result.stdout).strip() or None,
            )

    def _wait_until_answering(self) -> None:
        """
        Wait for a Knot that was just started to open its control socket.

        Raises:
            EngineError: It never answered.
        """
        result = self._knotc("status")
        for _ in range(READY_ATTEMPTS):
            if result.success:
                return
            self._sleep(READY_PAUSE)
            result = self._knotc("status")
        raise EngineError(
            "Knot started and never answered",
            details="See why: journalctl -u knot",
            output=(result.stderr or result.stdout).strip() or None,
        )

    def restore_config(self) -> None:
        """
        Stop Knot and put the configuration it had before Noust back (or remove Noust's).

        The zone files Noust wrote go; the keys stay.
        """
        self._run(["systemctl", "disable", "--now", paths.AUTHDNS_UNIT], INSTALL_TIMEOUT)
        backup = self.config_path.with_name(self.config_path.name + BACKUP_SUFFIX)
        current = self.current_config()
        if current is not None and CONFIG_MARKER in current:
            if backup.exists():
                self.fs.rename(backup, self.config_path)
            else:
                self.fs.remove(self.config_path)
        for path in self.zone_files():
            self.fs.remove(path)

    # -- zone files ----------------------------------------------------------------------

    def zone_file(self, zone: str) -> Path:
        """
        Args:
            zone: A zone, judged.

        Returns:
            Its file.
        """
        return self.zones_dir / f"{zone}.zone"

    def zone_files(self) -> list[Path]:
        """
        Returns:
            The zone files Noust wrote and still has.
        """
        if not self.zones_dir.is_dir():
            return []
        return sorted(
            path
            for path in self.zones_dir.glob("*.zone")
            if path.is_file() and not path.is_symlink() and self._wrote(path)
        )

    @staticmethod
    def _wrote(path: Path) -> bool:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return paths.UNIT_MARKER in handle.readline()
        except OSError:
            return False

    def prepare_zones_dir(self) -> None:
        """Make the directory the zone files are in, owned the way Knot needs it."""
        self.fs.make_dir(self.zones_dir, mode=0o750)
        if self.group is not None:
            self.fs.set_owner(self.zones_dir, user=self.group, group=self.group, mode=0o750)

    def write_zone(self, zone: str, text: str) -> bool:
        """
        Write a zone file, unless it already says exactly this.

        Args:
            zone: The zone.
            text: The file.

        Returns:
            True when the file changed.
        """
        path = self.zone_file(zone)
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except (FileNotFoundError, UnicodeDecodeError):
            pass
        self.fs.write_text(path, text, mode=0o640, owner=self._owner())
        return True

    def remove_zone_file(self, zone: str) -> None:
        """
        Args:
            zone: The zone.
        """
        self.fs.remove(self.zone_file(zone))

    # -- asking and telling Knot ---------------------------------------------------------

    def reload(self, zones: Sequence[str] = ()) -> None:
        """
        Make Knot read the configuration and the zone files that changed.

        Args:
            zones: Zones whose file must be applied even if Knot thinks it did not change (a zone
                that stopped being signed keeps its signatures until its file is read again).

        Raises:
            EngineError: Knot did not accept it; the zone it was serving before keeps being served.
        """
        result = self._knotc("-b", "reload")
        if not result.success:
            raise EngineError(
                "Knot did not take the change",
                details="The zone is still served as it was before.",
                output=(result.stderr or result.stdout).strip() or None,
            )
        for zone in zones:
            result = self._knotc("-b", "zone-reload", f"{zone}.")
            if not result.success:
                raise EngineError(
                    f"Knot did not take the file of {zone}",
                    details="The zone is still served as it was before.",
                    output=(result.stderr or result.stdout).strip() or None,
                )

    def zone_status(self) -> dict[str, ZoneStatus]:
        """
        Returns:
            What Knot says of each zone it serves, by name; empty when it does not answer.
        """
        result = self._knotc("zone-status")
        found: dict[str, ZoneStatus] = {}
        if not result.success:
            return found
        for line in result.stdout.splitlines():
            matched = _ZONE_STATUS.fullmatch(line.strip())
            if matched is None:
                continue
            fields: dict[str, str] = {}
            for part in matched["rest"].split("|"):
                key, _, value = part.partition(":")
                fields[key.strip()] = value.strip()
            zone = matched["zone"].lower().rstrip(".")
            serial = fields.get("serial", "")
            found[zone] = ZoneStatus(
                zone=zone,
                role=fields.get("role", ""),
                serial=int(serial) if serial.isdigit() else None,
                catalog=fields.get("catalog", "").removeprefix("@"),
            )
        return found

    def ds_records(self, zone: str) -> list[DsRecord]:
        """
        Args:
            zone: A signed zone.

        Returns:
            The DS records of its key, which the registrar is given; empty when it has no key.
        """
        result = self._run(["keymgr", "-c", str(self.config_path), f"{zone}.", "ds"])
        records = []
        for line in result.stdout.splitlines() if result.success else []:
            matched = _DS.fullmatch(line.strip())
            if matched is None:
                continue
            records.append(
                DsRecord(
                    zone=zone,
                    key_tag=int(matched[2]),
                    algorithm=int(matched[3]),
                    digest_type=int(matched[4]),
                    digest=matched[5].lower(),
                )
            )
        return records

    def confirm_ds_published(self, zone: str) -> None:
        """
        Tell Knot that the parent zone now has the DS, which lets the key go on to be used as
        the active one.

        Args:
            zone: The zone.

        Raises:
            EngineError: Knot refused (the zone is not signed, or its key is not waiting).
        """
        result = self._knotc("zone-ksk-submitted", f"{zone}.")
        if not result.success:
            raise EngineError(
                f"Knot has no key of {zone} waiting for its DS",
                details="The zone must be signed, and its key not yet confirmed.",
                output=(result.stderr or result.stdout).strip() or None,
            )
