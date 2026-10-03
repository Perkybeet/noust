# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An engine's configuration: a closed set of settings, applied without leaving it broken.

Each host engine offers a fixed list of settings (3.3 spec, 5.2), each with
its current value, the value Noust recommends for this server's memory and
cores, its unit, whether changing it restarts the engine, and a sentence on
what it changes. Nothing outside the list is written: a setting the list does
not have is edited by hand in the distribution's file, which Noust never
touches (spec 9.5).

**Where it is written.** In a file of Noust's own that the engine includes:

- PostgreSQL: ``conf.d/90-noust.conf`` beside the cluster's ``postgresql.conf``,
  whose ``include_dir = 'conf.d'`` Debian and Ubuntu ship; added once when
  missing.
- MySQL: ``/etc/mysql/mysql.conf.d/99-noust.cnf``; MariaDB:
  ``/etc/mysql/mariadb.conf.d/99-noust.cnf``.
- Redis: ``/etc/redis/noust.conf`` (Valkey: ``/etc/valkey/noust.conf``), with
  ``include`` as the last line of the server's file: Redis reads later lines
  over earlier ones, and ``CONFIG REWRITE`` appends after an include, so the
  line is moved back to the end whenever it is no longer last.
- MongoDB: ``/etc/mongod.conf``, its only file, edited as YAML.

**How it is applied** (:meth:`EngineSettings.apply`): the values are checked
strictly (types, ranges, units, enumerations; a quote, a newline or a ``#``
can never reach a file), the file is written through the filesystem seam,
the engine's own checker reads it when there is one (``postgres -C``,
``mysqld --validate-config``, ``mariadbd --help --verbose``), then the engine
is restarted, reloaded or changed at runtime as the settings require, and
Noust waits for it to answer. When it does not, the previous file is put back,
the engine restarted on it, and the error carries the engine's journal
verbatim, as the health gate does for applications.

A change that costs something asks for one explicit confirmation carrying
every warning (:class:`~noust.core.exceptions.ConfirmationRequired`), before
anything is written: opening an engine beyond loopback (removing a listen
setting included, unless what the engine falls back to is known to be
loopback; and what the engine reports once it answers is held to the same
rule), a Redis ``maxmemory`` below what it holds, and Redis persistence
turned off. Opening an engine is refused outright under the ENS profile
(:func:`noust.core.ens.profile.database_remote_listen_allowed`).

**What keeps it safe.** One change per engine at a time (the applications'
lock, under a name no domain has). No path Noust reads or writes the
configuration through may be a symbolic link: the engine's account owns some
of those directories, and Noust writes as root. Every file is on the list to
put back before it is replaced, and the previous files come back whatever
stops the change, an interruption included. A slow start is waited for while
the engine says it is starting. Redis writes a snapshot before Noust restarts
it. Every PostgreSQL step names the one cluster it configures.

Engines in containers are configured by their image and their compose file,
never here (spec 9.3).
"""

from __future__ import annotations

import errno
import grp
import ipaddress
import os
import pwd
import re
import stat
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import yaml  # type: ignore[import-untyped]

from noust.core.applock import AppBusyError, app_lock
from noust.core.exceptions import (
    ConfirmationRequired,
    DatabaseEngineError,
    DatabaseError,
    ValidationError,
)
from noust.core.fs import is_rehearsal
from noust.managers.database import flavours
from noust.managers.database.base import QUERY_TIMEOUT, SERVICE_TIMEOUT, is_loopback

if TYPE_CHECKING:
    from noust.managers.database.base import BaseDatabaseManager

#: What the settings code runs only to look, so a rehearsal can find the files.
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (("pg_lsclusters", "--no-header"),)

#: Deadline for an engine's configuration checker.
CHECK_TIMEOUT = 120

#: How long Noust waits for an engine to answer after a restart, in seconds.
#: Generous on purpose: a healthy engine that is slow to start must not be
#: rolled back half way through it.
WAIT_SECONDS = 120

#: How long Noust keeps waiting while the engine says it is still starting
#: (systemd's ``activating``, Redis's ``LOADING``), in seconds: a large data
#: set loads, or a crash is recovered, for as long as it takes, up to this.
LOADING_SECONDS = 900

#: Deadline of ``systemctl restart``, which returns only once the engine has
#: started: MySQL's crash recovery and a large Redis data set happen inside it.
RESTART_TIMEOUT = 900

#: How long a Redis snapshot taken before a restart may take, in seconds.
SNAPSHOT_SECONDS = 900

#: The largest share of the server's memory a cache setting may take; the
#: rest is what the system and the applications run in.
MEMORY_SHARE = 0.9

#: Lines of the engine's journal an error carries.
JOURNAL_LINES = 50

#: The first line of every file Noust writes.
MARKER = "# Generated by Noust: 'noust db settings {engine}' and the console write this file."

#: The kinds of value a setting holds.
Kind = Literal[
    "addresses",
    "port",
    "integer",
    "size",
    "duration_ms",
    "seconds",
    "gigabytes",
    "boolean",
    "enum",
    "timezone",
    "snapshots",
]

#: Anything outside these characters is refused before a value is parsed:
#: quotes, newlines, ``#``, ``;``, ``=`` and backslashes are how a value
#: would break out of its line in any of the four file formats.
_SAFE_VALUE = re.compile(r"\A[A-Za-z0-9 .,:+\-_/*]*\Z")

#: Longest value accepted.
_MAX_VALUE_LENGTH = 200

#: A size with its unit; a bare number is refused because PostgreSQL reads
#: ``shared_buffers = 1024`` as 1024 pages of 8 kB, not as bytes.
_SIZE = re.compile(r"\A(\d{1,9})\s*(k|kb|m|mb|g|gb|t|tb)\Z", re.IGNORECASE)

#: Size units, in bytes.
_UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}

#: A duration with an optional unit, as PostgreSQL prints it (``250ms``, ``1s``).
_DURATION = re.compile(r"\A(-?\d{1,9})\s*(ms|s|min|h|d)?\Z", re.IGNORECASE)

#: Milliseconds per duration unit.
_DURATION_UNITS = {"ms": 1, "s": 1000, "min": 60_000, "h": 3_600_000, "d": 86_400_000}

#: A time zone name as the tz database spells it (``Europe/Madrid``, ``UTC``).
_ZONE_NAME = re.compile(r"\A[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,2}\Z")

#: MySQL's time zone: ``SYSTEM`` or an offset. A named zone needs the time zone
#: tables loaded, which a Debian install does not do, and MySQL then refuses
#: to start; offsets always work.
_ZONE_OFFSET = re.compile(r"\A(?:SYSTEM|[+-](?:0\d|1[0-4]):[0-5]\d)\Z")

#: Spellings of true and false.
_TRUE = frozenset({"on", "yes", "true", "1"})
_FALSE = frozenset({"off", "no", "false", "0"})

#: The word that removes a setting from Noust's file.
DEFAULT_WORD = "default"


# ------------------------------------------------------------------- values


@dataclass(frozen=True)
class Kept:
    """
    A value of a file that is not Noust's alone, kept exactly as it is.

    Attributes:
        text: The value as the file holds it.
    """

    text: str


@dataclass(frozen=True)
class SettingSpec:
    """
    One setting an engine offers.

    Attributes:
        key: The engine's own name for it, and the stable key the console
            translates its explanation by.
        kind: What it holds.
        description: What it changes, one or two English sentences.
        unit: The unit of a number, for display (``MB``, ``ms``, ``s``).
        restart: Whether changing it restarts the engine. PostgreSQL's
            answer is read from ``pg_settings.context`` when it can be.
        minimum: Smallest value of a number (bytes for a size).
        maximum: Largest value of a number.
        choices: The values of an enumeration.
        separator: How addresses are joined in this engine's syntax.
        single: Only one address (MariaDB's ``bind-address``).
        words: How this engine spells true and false.
        editable: Whether Noust changes it.
        locked_reason: Why not, when it does not.
        memory_share: The largest share of the server's memory a size may
            take (a cache), None for the whole of it.
    """

    key: str
    kind: Kind
    description: str
    unit: str | None = None
    restart: bool = True
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    separator: str = ","
    single: bool = False
    words: tuple[str, str] = ("on", "off")
    editable: bool = True
    locked_reason: str | None = None
    memory_share: float | None = None

    @property
    def listen(self) -> bool:
        """Whether this is where the engine listens."""
        return self.kind == "addresses"


@dataclass(frozen=True)
class Resources:
    """
    What the recommendations are computed from.

    Attributes:
        memory_bytes: Total memory.
        cpus: Logical processors.
    """

    memory_bytes: int
    cpus: int


def server_resources() -> Resources:
    """
    Measure this server.

    Returns:
        Its memory and processors; memory 0 (no recommendations of size,
        no caps) where the system does not say.
    """
    # The standard library, not psutil: psutil is optional, and the CLI loads
    # without it.
    try:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        memory = 0
    return Resources(memory_bytes=max(int(memory), 0), cpus=os.cpu_count() or 1)


def format_size(size: int) -> str:
    """
    Show a size in the largest unit that holds it exactly.

    Args:
        size: Bytes.

    Returns:
        ``256MB``, ``1GB``, ``512kB``; ``0`` for zero.
    """
    if size == 0:
        return "0"
    for suffix, factor in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("kB", 1024)):
        if size % factor == 0:
            return f"{size // factor}{suffix}"
    return f"{size}B"


def _engine_size(size: int, style: str) -> str:
    """
    Write a size the way an engine's file reads it.

    Args:
        size: Bytes, a multiple of 1024.
        style: ``postgresql`` (``256MB``), ``mysql`` (``256M``) or ``redis``
            (``256mb``).

    Returns:
        The size.
    """
    if size == 0:
        return "0"
    shown = format_size(size)
    number = shown.rstrip("kMGTB")
    unit = shown[len(number) :]
    if style == "mysql":
        return number + unit[0].upper()
    if style == "redis":
        return number + unit.lower()
    return shown


def _refuse(spec: SettingSpec, message: str, details: str = "") -> ValidationError:
    """
    Build the refusal of one value.

    Args:
        spec: The setting.
        message: What is wrong.
        details: What is accepted.

    Returns:
        The error, about the setting's field.
    """
    return ValidationError(message, details=details, field=spec.key)


def _addresses(spec: SettingSpec, text: str, engine: str) -> tuple[str, ...]:
    """
    Parse a list of listen addresses.

    Args:
        spec: The setting.
        text: Addresses separated by commas or spaces.
        engine: The engine, which decides which wildcards it reads.

    Returns:
        The addresses, without repeats, in order.

    Raises:
        ValidationError: For anything that is not an IP address, ``localhost``
            or the engine's wildcard.
    """
    parts = [part for part in re.split(r"[\s,]+", text.strip()) if part]
    if not parts:
        raise _refuse(spec, f"{spec.key} needs at least one address")
    if spec.single and len(parts) > 1:
        raise _refuse(spec, f"{spec.key} takes one address", "Use 127.0.0.1, 0.0.0.0 or ::.")
    accepted: list[str] = []
    for part in parts:
        address = part
        if engine == "redis" and address.startswith("-"):
            # Redis's "-" means "skip it when the address is not available".
            address = address[1:]
        if address == "*" and engine in ("postgresql", "mysql", "redis"):
            pass
        elif address == "localhost" and engine in ("postgresql", "mysql", "mongodb"):
            pass
        else:
            try:
                ipaddress.ip_address(address)
            except ValueError:
                raise _refuse(
                    spec,
                    f"{part!r} is not an address {spec.key} can listen on",
                    "Give IP addresses (127.0.0.1, ::1, 10.0.0.5), or 0.0.0.0 for every one.",
                ) from None
        if part not in accepted:
            accepted.append(part)
    return tuple(accepted)


def parse_value(spec: SettingSpec, raw: str, engine: str, resources: Resources) -> Any:
    """
    Check one value strictly and bring it to its canonical form.

    Args:
        spec: The setting.
        raw: The value as typed.
        engine: The engine (addresses and sizes differ between engines).
        resources: This server, for the memory caps.

    Returns:
        An int (ports, integers, sizes in bytes, durations in ms), a float
        (seconds, gigabytes), a bool, a str (enumerations, time zones), a
        tuple of addresses, or a tuple of ints (snapshot rules).

    Raises:
        ValidationError: When the value is not acceptable, naming the field.
    """
    text = str(raw).strip()
    if len(text) > _MAX_VALUE_LENGTH or not _SAFE_VALUE.match(text):
        raise _refuse(
            spec,
            f"{spec.key} holds characters no value of it can have",
            "Quotes, newlines, '#', ';', '=' and backslashes are never accepted.",
        )
    if spec.kind == "addresses":
        return _addresses(spec, text, engine)
    if spec.kind == "boolean":
        word = text.lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
        raise _refuse(spec, f"{spec.key} is on or off", f"Give {spec.words[0]} or {spec.words[1]}.")
    if spec.kind == "enum":
        if text not in spec.choices:
            raise _refuse(spec, f"{text!r} is not a value of {spec.key}", ", ".join(spec.choices))
        return text
    if spec.kind == "timezone":
        if engine == "mysql":
            if not _ZONE_OFFSET.match(text):
                raise _refuse(
                    spec,
                    f"{text!r} is not a time zone MySQL accepts without its zone tables",
                    "Give SYSTEM or an offset such as +00:00 or +01:00.",
                )
        elif not _ZONE_NAME.match(text):
            raise _refuse(spec, f"{text!r} is not a time zone name", "For example UTC.")
        return text
    if spec.kind == "snapshots":
        if text.lower() in ("", "off", "none", "never"):
            return ()
        numbers = text.split()
        if len(numbers) % 2 or not all(number.isdigit() for number in numbers):
            raise _refuse(
                spec,
                f"{spec.key} is pairs of seconds and changes",
                "For example '3600 1 300 100 60 10000', or off.",
            )
        values = tuple(int(number) for number in numbers)
        if any(value <= 0 for value in values) or len(values) > 16:
            raise _refuse(spec, f"{spec.key} takes up to eight positive pairs")
        return values
    number = _parse_number(spec, text)
    if spec.minimum is not None and number < spec.minimum:
        raise _refuse(spec, f"{spec.key} is at least {_shown(spec, spec.minimum)}")
    maximum = spec.maximum
    if spec.kind in ("size", "gigabytes") and resources.memory_bytes:
        memory = resources.memory_bytes if spec.kind == "size" else resources.memory_bytes / 1024**3
        if spec.memory_share is not None:
            share = memory * spec.memory_share
            if number > share and (maximum is None or share < maximum):
                raise _refuse(
                    spec,
                    f"{spec.key} is at most {_shown(spec, share)}, "
                    f"{spec.memory_share:.0%} of this server's memory",
                    "The rest is what the system and the applications run in: a cache that "
                    "takes all of it makes the server swap, or the kernel kill the engine.",
                )
        maximum = min(maximum, memory) if maximum is not None else memory
    if maximum is not None and number > maximum:
        raise _refuse(spec, f"{spec.key} is at most {_shown(spec, maximum)}")
    return number


def _parse_number(spec: SettingSpec, text: str) -> int | float:
    """
    Parse a number of one of the numeric kinds.

    Args:
        spec: The setting.
        text: The value.

    Returns:
        The number, in the kind's canonical unit.

    Raises:
        ValidationError: When it is not a number of that kind.
    """
    if spec.kind == "size":
        if text == "0" and spec.minimum == 0:
            return 0
        match = _SIZE.match(text)
        if not match:
            raise _refuse(
                spec, f"{spec.key} is a size with its unit", "For example 256MB, 1GB or 512kB."
            )
        return int(match.group(1)) * _UNITS[match.group(2)[0].lower()]
    if spec.kind == "duration_ms":
        match = _DURATION.match(text)
        if not match:
            raise _refuse(spec, f"{spec.key} is a number of milliseconds", "For example 1000.")
        return int(match.group(1)) * _DURATION_UNITS[(match.group(2) or "ms").lower()]
    if spec.kind in ("seconds", "gigabytes"):
        if not re.match(r"\A\d{1,7}(?:\.\d{1,6})?\Z", text):
            raise _refuse(spec, f"{spec.key} is a number", "For example 1 or 0.5.")
        return float(text)
    if not re.match(r"\A-?\d{1,9}\Z", text):
        raise _refuse(spec, f"{spec.key} is a whole number")
    return int(text)


def _shown(spec: SettingSpec, number: float) -> str:
    """
    Show a limit of a setting.

    Args:
        spec: The setting.
        number: The limit, in the kind's canonical unit.

    Returns:
        The limit for a sentence.
    """
    if spec.kind == "size":
        return format_size(int(number) // 1024**2 * 1024**2) if number >= 1024**2 else str(number)
    if spec.kind == "gigabytes":
        return f"{number:.2f}".rstrip("0").rstrip(".")
    return f"{number:g}"


def show_value(spec: SettingSpec, value: Any) -> str:
    """
    Show a canonical value the way the report and the console print it.

    Args:
        spec: The setting.
        value: The value, as :func:`parse_value` returns it.

    Returns:
        The text.
    """
    if isinstance(value, Kept):
        return value.text
    if spec.kind == "addresses":
        joiner = " " if spec.separator == " " else ","
        return joiner.join(value)
    if spec.kind == "boolean":
        return spec.words[0] if value else spec.words[1]
    if spec.kind == "size":
        return format_size(int(value))
    if spec.kind == "snapshots":
        return " ".join(str(number) for number in value) if value else "off"
    if spec.kind in ("seconds", "gigabytes"):
        return f"{float(value):g}"
    return str(value)


# ------------------------------------------------------------------- report


@dataclass(frozen=True)
class SettingView:
    """
    One setting as the report shows it.

    Attributes:
        spec: What the setting is.
        current: What the running engine uses, None when it cannot be asked.
        configured: What Noust's file sets, None when it sets nothing.
        recommended: What Noust recommends for this server, None when it
            depends on the applications (a time zone).
        restart: Whether changing it restarts the engine.
        source: The file the engine read the current value from, when the
            engine says (PostgreSQL).
    """

    spec: SettingSpec
    current: str | None
    configured: str | None
    recommended: str | None
    restart: bool
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the setting as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        spec = self.spec
        return {
            "key": spec.key,
            "kind": spec.kind,
            "unit": spec.unit,
            "description": spec.description,
            "current": self.current,
            "configured": self.configured,
            "recommended": self.recommended,
            "restart": self.restart,
            "choices": list(spec.choices),
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "listen": spec.listen,
            "editable": spec.editable,
            "locked_reason": spec.locked_reason,
            "source": self.source,
        }


@dataclass(frozen=True)
class SettingsReport:
    """
    Every setting of one engine.

    Attributes:
        engine: The engine.
        display_name: Its name for a person (MariaDB, Valkey).
        file: The file Noust writes them to.
        running: Whether the engine answered, so ``current`` is known.
        resources: What the recommendations were computed from.
        settings: The settings, in the engine's order.
    """

    engine: str
    display_name: str
    file: str
    running: bool
    resources: Resources
    settings: list[SettingView]

    def to_dict(self) -> dict[str, Any]:
        """
        Render the report as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return {
            "engine": self.engine,
            "display_name": self.display_name,
            "file": self.file,
            "running": self.running,
            "memory_bytes": self.resources.memory_bytes,
            "cpus": self.resources.cpus,
            "settings": [view.to_dict() for view in self.settings],
        }


@dataclass(frozen=True)
class SettingsOutcome:
    """
    What applying settings did.

    Attributes:
        engine: The engine.
        display_name: Its name for a person.
        file: The file written.
        changed: The keys whose value changed, sorted.
        action: ``none`` (nothing changed), ``reload``, ``runtime`` (applied
            to the running engine) or ``restart``.
        exposed: Whether the engine now listens beyond loopback.
        warnings: What the operator must know, in English.
    """

    engine: str
    display_name: str
    file: str
    changed: list[str]
    action: str
    exposed: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the outcome as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return {
            "engine": self.engine,
            "display_name": self.display_name,
            "file": self.file,
            "changed": list(self.changed),
            "action": self.action,
            "exposed": self.exposed,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class LiveValue:
    """
    A setting as the running engine reports it.

    Attributes:
        value: Its value, already shown canonically.
        restart: Whether changing it needs a restart, when the engine says.
        source: The file it came from, when the engine says.
    """

    value: str
    restart: bool | None = None
    source: str | None = None


def exposure_warning(display_name: str, addresses: Sequence[str]) -> str:
    """
    Say what listening beyond loopback means.

    Args:
        display_name: The engine.
        addresses: Where it will listen.

    Returns:
        The warning, in English.
    """
    return (
        f"{display_name} will accept connections from beyond this server "
        f"({', '.join(addresses)}). Anyone who can reach its port can try to sign in: "
        "restrict the port with the firewall to the addresses that need it, give every "
        "account a strong password, and prefer an SSH tunnel for your own access."
    )


#: How long between two attempts to reach an engine; a test replaces it.
_sleep: Callable[[float], None] = time.sleep


# ------------------------------------------------------------------ engines


class EngineSettings(ABC):
    """
    The settings of one engine on this host, and how they are applied.

    A subclass declares its settings and its file format and says how the
    engine is asked, checked, changed and waited for; :meth:`apply` is the
    one write-check-apply-wait-or-put-back sequence for all of them.
    """

    #: The engine.
    ENGINE: ClassVar[str] = ""
    #: Whether settings that need no restart are applied to the running
    #: engine (SET GLOBAL, CONFIG SET) rather than by a reload.
    RUNTIME: ClassVar[bool] = False
    #: Whether the file is Noust's alone. MongoDB's is the distribution's:
    #: values Noust would not write are kept as they are, not refused.
    OWN_FILE: ClassVar[bool] = True

    def __init__(self, manager: BaseDatabaseManager, resources: Resources) -> None:
        """
        Args:
            manager: The engine's manager, through which every process runs.
            resources: This server, for the recommendations and the caps.
        """
        self.manager = manager
        self.resources = resources

    # -- what a subclass declares ------------------------------------------------

    @abstractmethod
    def specs(self) -> tuple[SettingSpec, ...]:
        """
        Returns:
            The settings, in the order they are shown.
        """

    @abstractmethod
    def settings_file(self) -> Path:
        """
        Returns:
            The file Noust writes the settings to.

        Raises:
            DatabaseEngineError: When the installation has no such place.
        """

    @abstractmethod
    def read_file(self, text: str | None) -> dict[str, str]:
        """
        Read the settings a file sets.

        Args:
            text: The file, None when it does not exist.

        Returns:
            Raw values by key, for the keys of :meth:`specs` it sets.
        """

    @abstractmethod
    def render_file(self, previous: str | None, values: Mapping[str, Any]) -> str:
        """
        Write the file for a set of values.

        Args:
            previous: The file as it is, None when it does not exist.
            values: Every setting the file is to set, canonical.

        Returns:
            The file's new text.
        """

    @abstractmethod
    def live_values(self) -> dict[str, LiveValue]:
        """
        Ask the running engine for its settings.

        Returns:
            What it reports by key; empty when it does not answer.
        """

    @abstractmethod
    def recommended(self) -> dict[str, Any]:
        """
        Returns:
            The recommended canonical value per key, None for no advice.
        """

    @abstractmethod
    def ping(self) -> bool:
        """
        Returns:
            Whether the engine answers a trivial request.
        """

    def unit(self) -> str:
        """
        Returns:
            The systemd unit restarted, reloaded and read the journal of.
        """
        return self.manager.service_unit()

    def display_name(self) -> str:
        """
        Returns:
            The engine's name for a person.
        """
        return self.manager.DISPLAY_NAME

    def includes(self) -> list[tuple[Path, str]]:
        """
        Make the engine read Noust's file, when it does not yet.

        Returns:
            The files to change for it and their new text.
        """
        return []

    def check(self, changed: Sequence[str]) -> None:
        """
        Have the engine's own checker read the new configuration.

        Args:
            changed: The keys that changed.

        Raises:
            DatabaseEngineError: When it refuses it, with its output.
        """
        # Redis and MongoDB have no checker that reads a file without starting.
        return None

    def apply_runtime(self, values: Mapping[str, Any]) -> None:
        """
        Apply settings that need no restart.

        Args:
            values: The changed settings and their new canonical values.

        Raises:
            DatabaseError: When the engine refuses one.
        """
        self._reload()

    def after(self, changed: Sequence[str], values: Mapping[str, Any]) -> list[str]:
        """
        Look at the engine once the settings are applied.

        Args:
            changed: The keys that changed.
            values: The settings Noust's file now sets.

        Returns:
            Warnings, in English.
        """
        return []

    def config_paths(self) -> list[Path]:
        """
        Name the paths that must not be symbolic links.

        Noust writes as root into directories the engine's own account may
        own (``/etc/postgresql/<v>/<c>`` is ``postgres``'s, ``/etc/redis``
        ``redis``'s): a link there would turn a settings change into a root
        write wherever that account points it.

        Returns:
            The directory of Noust's file, the file itself and, in a subclass,
            the engine's main file and the directories above it it owns.
        """
        path = self.settings_file()
        return [path.parent, path]

    def listen_fallback(self) -> tuple[str, ...] | None:
        """
        Say where the engine listens when Noust's file sets no address.

        Returns:
            Every address the engine's other configuration gives (all of
            them, not only the one that wins, so a loopback answer is never
            a guess), the engine's default when it gives none, or None when
            Noust cannot tell.
        """
        return None

    def confirmations(self, changes: Mapping[str, Any], live: Mapping[str, LiveValue]) -> list[str]:
        """
        Name what the changes would cost, for the operator to accept first.

        Args:
            changes: The changed settings and their new canonical values;
                None for a setting removed from Noust's file.
            live: What the engine reports now.

        Returns:
            One warning per cost, in English; none by default.
        """
        return []

    def before_restart(self, *, strict: bool) -> None:
        """
        Make a restart lose nothing the engine holds only in memory.

        Args:
            strict: Raise when it cannot be done (before a restart Noust
                chose), rather than log it (before the restart that puts the
                previous settings back, which must happen anyway).

        Raises:
            DatabaseEngineError: When ``strict`` and it cannot be done.
        """
        return None

    def starting(self) -> bool:
        """
        Tell whether the engine is still starting, rather than down.

        systemd says ``activating`` while a unit starts: MySQL's crash
        recovery, PostgreSQL replaying its log. Waiting for it is what keeps
        a slow, healthy start from being rolled back half way.

        Returns:
            True while the engine's unit is starting or reloading.
        """
        result = self.manager._exec(["systemctl", "is-active", self.unit()], timeout=QUERY_TIMEOUT)
        return result.stdout.strip() in ("activating", "reloading")

    # -- the one sequence ---------------------------------------------------------

    def _spec(self, key: str) -> SettingSpec:
        """
        Find a setting by key.

        Args:
            key: The key as given.

        Returns:
            Its spec.

        Raises:
            ValidationError: When the engine offers no such setting.
        """
        for spec in self.specs():
            if spec.key == key:
                return spec
        raise ValidationError(
            f"{key!r} is not a setting Noust changes on {self.display_name()}",
            details=(
                f"Settings: {', '.join(spec.key for spec in self.specs())}. Anything else is "
                "edited by hand in the distribution's own file."
            ),
            field=key,
        )

    def _configured(self, text: str | None) -> dict[str, Any]:
        """
        Read Noust's file into canonical values.

        Args:
            text: The file.

        Returns:
            The values it sets.

        Raises:
            ValidationError: When it holds a value Noust would not write.
        """
        values: dict[str, Any] = {}
        for key, raw in self.read_file(text).items():
            spec = self._spec(key)
            try:
                values[key] = parse_value(spec, raw, self.ENGINE, Resources(0, self.resources.cpus))
            except ValidationError as exc:
                if not self.OWN_FILE:
                    values[key] = Kept(raw)
                    continue
                raise ValidationError(
                    f"{self.settings_file()} sets {key} to {raw!r}, which Noust does not write",
                    details=f"{exc.message}. Correct or remove that line, then try again.",
                    field=key,
                ) from exc
        return values

    def report(self) -> SettingsReport:
        """
        Describe every setting.

        Returns:
            The report.

        Raises:
            DatabaseEngineError: When a configuration path is a symbolic link.
        """
        self._guard_links()
        path = self.settings_file()
        configured = self._configured(_read(path))
        live = self.live_values()
        recommended = self.recommended()
        views: list[SettingView] = []
        for spec in self.specs():
            entry = live.get(spec.key)
            advice = recommended.get(spec.key)
            views.append(
                SettingView(
                    spec=spec,
                    current=entry.value if entry else None,
                    configured=(
                        show_value(spec, configured[spec.key]) if spec.key in configured else None
                    ),
                    recommended=show_value(spec, advice) if advice is not None else None,
                    restart=entry.restart if entry and entry.restart is not None else spec.restart,
                    source=entry.source if entry else None,
                )
            )
        return SettingsReport(
            engine=self.ENGINE,
            display_name=self.display_name(),
            file=str(path),
            running=bool(live),
            resources=self.resources,
            settings=views,
        )

    def apply(
        self,
        requested: Mapping[str, str],
        *,
        confirm: bool = False,
        remote_listen_allowed: bool = True,
    ) -> SettingsOutcome:
        """
        Change settings, and leave the engine on the previous ones if it refuses them.

        Args:
            requested: New values by key, as typed; ``default`` removes a
                setting from Noust's file.
            confirm: The operator accepted what the change costs: listening
                beyond loopback, a Redis that would refuse writes or drop
                keys, persistence turned off (:class:`ConfirmationRequired`
                names each one).
            remote_listen_allowed: Whether the security profile allows
                listening beyond loopback.

        Returns:
            What changed and how it was applied.

        Raises:
            ValidationError: For an unknown setting, a value that is not
                acceptable, or an exposure the profile does not allow.
            ConfirmationRequired: When the change costs something and
                ``confirm`` is not given; nothing was changed.
            DatabaseEngineError: When another change of this engine's
                settings is running, a configuration path is a symbolic link,
                or the engine did not come back on the new settings; the
                previous ones are back, and the error carries the checker's
                output or the engine's journal.
        """
        if not requested:
            raise ValidationError("No setting to change", field="values")
        changes: dict[str, Any] = {}
        for key, raw in requested.items():
            spec = self._spec(key)
            if not spec.editable:
                raise ValidationError(
                    f"Noust does not change {key} on {self.display_name()}",
                    details=spec.locked_reason or "",
                    field=key,
                )
            if str(raw).strip().lower() == DEFAULT_WORD:
                changes[key] = None
            else:
                changes[key] = parse_value(spec, raw, self.ENGINE, self.resources)
        with self._locked():
            self._guard_links()
            return self._apply(
                changes, confirm=confirm, remote_listen_allowed=remote_listen_allowed
            )

    def _apply(
        self, changes: Mapping[str, Any], *, confirm: bool, remote_listen_allowed: bool
    ) -> SettingsOutcome:
        """
        The one write-check-apply-wait-or-put-back sequence, under the engine's lock.

        Args:
            changes: The requested values, canonical; None removes one.
            confirm: See :meth:`apply`.
            remote_listen_allowed: See :meth:`apply`.

        Returns:
            What changed and how it was applied.
        """
        path = self.settings_file()
        previous = _read(path)
        configured = self._configured(previous)
        merged = dict(configured)
        for key, value in changes.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        changed = sorted(key for key in changes if configured.get(key) != merged.get(key))
        if not changed:
            return SettingsOutcome(self.ENGINE, self.display_name(), str(path), [], "none")

        listen_key = self._listen_key()
        exposing = self._exposing(changed, merged)
        if exposing is not None and not remote_listen_allowed:
            raise self._ens_refusal(listen_key)
        live = self.live_values()
        pending = [self._exposure_text(exposing)] if exposing is not None else []
        pending += self.confirmations({key: merged.get(key) for key in changed}, live)
        if pending and not confirm:
            raise ConfirmationRequired(pending)

        restart = any(self._needs_restart(key, live, merged) for key in changed)
        text = self.render_file(previous, merged)
        included = [(target, _read(target), new) for target, new in self.includes()]

        written: list[tuple[Path, str | None]] = []
        stage = "write"
        committed = False
        put_back = False
        exposed_now: bool | None = None
        try:
            for target, before, new in included:
                # Recorded before the write: _write can fail after the file was
                # replaced (handing it back to its owner), and a file that is
                # not on the list is never put back.
                written.append((target, before))
                self._write(target, new, preserve=True)
            written.append((path, previous))
            self._write(path, text, preserve=not self.OWN_FILE)
            self.check(changed)
            if restart:
                self.before_restart(strict=True)
                stage = "restart"
                self._restart()
            else:
                stage = "runtime"
                self.apply_runtime({key: merged.get(key) for key in changed})
            if not self._wait():
                raise DatabaseEngineError(f"{self.display_name()} did not answer")
            if listen_key in changed:
                exposed_now = self._listening_now(
                    listen_key,
                    accepted=exposing is not None,
                    confirm=confirm,
                    remote_listen_allowed=remote_listen_allowed,
                )
            committed = True
        except DatabaseError as exc:
            put_back = True
            raise self._put_back(written, exc, stage=stage, live=live, changed=changed) from exc
        finally:
            if not committed and not put_back:
                # Anything else - an interruption, a cancelled job, an exposure
                # found only once the engine answered - leaves the engine on
                # the previous files too; the exception itself goes on up.
                self._put_back_after_interruption(written, stage=stage, live=live, changed=changed)

        warnings = self.after(changed, merged)
        if listen_key in changed:
            if exposed_now is not None:
                exposed = exposed_now
            elif listen_key in merged:
                exposed = _beyond_loopback(merged[listen_key])
            else:
                exposed = exposing is not None
        else:
            entry = live.get(listen_key)
            exposed = _beyond_loopback(entry.value if entry else merged.get(listen_key, ()))
        if exposed and listen_key in changed:
            warnings.append(self._exposure_text(exposing or ()))
        return SettingsOutcome(
            engine=self.ENGINE,
            display_name=self.display_name(),
            file=str(path),
            changed=changed,
            action="restart" if restart else ("runtime" if self.RUNTIME else "reload"),
            exposed=exposed,
            warnings=warnings,
        )

    def _listen_key(self) -> str:
        """
        Returns:
            The key of the setting that decides where the engine listens.
        """
        return next(spec.key for spec in self.specs() if spec.listen)

    def _exposing(
        self, changed: Sequence[str], merged: Mapping[str, Any]
    ) -> tuple[str, ...] | None:
        """
        Tell whether the change makes the engine listen beyond loopback.

        A listen setting removed from Noust's file is not "no exposure": the
        engine falls back to what its other configuration says, which may be
        every address. It counts as exposing unless that fallback is known to
        be loopback.

        Args:
            changed: The keys that change.
            merged: The values Noust's file will set.

        Returns:
            The addresses it would listen on, ``()`` when they cannot be known,
            or None when it stays on loopback.
        """
        key = self._listen_key()
        if key not in changed:
            return None
        if key in merged:
            value = merged[key]
            return tuple(_split_addresses(value)) if _beyond_loopback(value) else None
        fallback = self.listen_fallback()
        if fallback is None:
            return ()
        return fallback if _beyond_loopback(fallback) else None

    def _exposure_text(self, addresses: tuple[str, ...]) -> str:
        """
        Say what the exposure means.

        Args:
            addresses: Where the engine will listen, ``()`` when unknown.

        Returns:
            The warning, in English.
        """
        if addresses:
            return exposure_warning(self.display_name(), addresses)
        return exposure_warning(
            self.display_name(),
            ("wherever its other configuration says, which Noust cannot tell is this server only",),
        )

    def _ens_refusal(self, key: str) -> ValidationError:
        """
        Build the refusal of an exposure under the ENS profile.

        Args:
            key: The listen setting.

        Returns:
            The error.
        """
        return ValidationError(
            f"The ENS profile does not let {self.display_name()} listen beyond this server",
            details=(
                "Under security.profile ens-medium engines are reached through an SSH "
                "tunnel: 'noust db connect' prints one."
            ),
            field=key,
        )

    def _listening_now(
        self, key: str, *, accepted: bool, confirm: bool, remote_listen_allowed: bool
    ) -> bool | None:
        """
        Read where the engine listens once it answers, and hold it to what was accepted.

        The files said one thing; the running engine is the truth. When it
        listens beyond loopback and that was neither foreseen and confirmed
        nor allowed, the change is refused here and put back by the caller.

        Args:
            key: The listen setting.
            accepted: The exposure was foreseen, so allowed and confirmed.
            confirm: The operator confirmed the change.
            remote_listen_allowed: Whether the security profile allows it.

        Returns:
            Whether it listens beyond loopback; None when it does not say
            (or under a rehearsal, where nothing ran).

        Raises:
            ValidationError: The profile does not allow what it listens on.
            ConfirmationRequired: It was not confirmed.
        """
        if is_rehearsal():
            return None
        entry = self.live_values().get(key)
        if entry is None:
            return None
        addresses = tuple(_split_addresses(entry.value))
        exposed = _beyond_loopback(addresses)
        if exposed and not accepted:
            if not remote_listen_allowed:
                raise self._ens_refusal(key)
            if not confirm:
                raise ConfirmationRequired([exposure_warning(self.display_name(), addresses)])
        return exposed

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """
        Hold this engine's settings lock: one change at a time.

        Two changes interleaved would each put back the other's files on a
        failure. The lock is the applications' own
        (:func:`noust.core.applock.app_lock`), under a name no domain can have.

        Yields:
            Nothing; the lock is held until the block exits.

        Raises:
            DatabaseEngineError: Another change of this engine's settings is
                running.
        """
        with ExitStack() as stack:
            try:
                stack.enter_context(app_lock(f"database-settings@{self.ENGINE}", "settings change"))
            except AppBusyError as exc:
                running = (
                    f" (pid {exc.holder.pid}, since {exc.holder.started_at})" if exc.holder else ""
                )
                raise DatabaseEngineError(
                    f"Another change of {self.display_name()}'s settings is running{running}",
                    details="Wait for it to finish, then try again.",
                ) from exc
            yield

    def _guard_links(self) -> None:
        """
        Refuse to read or write the configuration through a symbolic link.

        Raises:
            DatabaseEngineError: When one of :meth:`config_paths` is a link.
        """
        for path in self.config_paths():
            _refuse_link(path)

    def _needs_restart(self, key: str, live: Mapping[str, LiveValue], merged: Mapping) -> bool:
        """
        Decide whether changing one setting restarts the engine.

        Args:
            key: The setting.
            live: What the engine reported, with its own answer when it gave one.
            merged: The values Noust's file will set.

        Returns:
            True when it needs a restart. Removing a setting that is applied
            at runtime also does: only a restart makes the engine read the
            value its other files give.
        """
        entry = live.get(key)
        if entry is not None and entry.restart is not None:
            return entry.restart
        if self.RUNTIME and key not in merged:
            return True
        return self._spec(key).restart

    def _write(self, path: Path, text: str, *, preserve: bool) -> None:
        """
        Write one file through the filesystem seam.

        Args:
            path: The file.
            text: Its content.
            preserve: Keep the owner and mode it has (a distribution's file,
                which its engine's account may need to read); Noust's own
                files are root's, 0644, and hold no secret.

        Raises:
            DatabaseEngineError: When the file cannot be written or handed
                back, or it or its directory is a symbolic link.
        """
        # Checked again right before the write, so the window a link could be
        # swapped in through is as narrow as it can be made.
        _refuse_link(path.parent)
        _refuse_link(path)
        mode, owner = 0o644, None
        if preserve:
            try:
                info: os.stat_result | None = os.lstat(path)
            except FileNotFoundError:
                info = None
            except OSError as exc:
                raise DatabaseEngineError(f"Could not look at {path}", details=str(exc)) from exc
            if info is not None:
                # Read and write bits only: a configuration file never needs
                # to be executable, and setuid, setgid or sticky copied from
                # a file another account can chmod would be carried by root.
                mode = info.st_mode & 0o666
                if info.st_uid != 0 or info.st_gid != 0:
                    owner = (info.st_uid, info.st_gid)
        try:
            self.manager.fs.make_dir(path.parent, mode=0o755)
            # The owner goes to the temporary file before it takes the path's
            # place. The directory is often the engine account's own
            # (/etc/redis), so a chown by name afterwards would follow a link
            # that account swapped in, handing root's files to it.
            self.manager.fs.write_text(path, text, mode=mode, owner=owner)
        except OSError as exc:
            if owner is None:
                raise DatabaseEngineError(f"Could not write {path}", details=str(exc)) from exc
            user = _account_name(owner[0], pwd.getpwuid, "pw_name")
            group = _account_name(owner[1], grp.getgrgid, "gr_name")
            raise DatabaseEngineError(
                f"Could not write {path} as {user}:{group}", details=str(exc)
            ) from exc

    def _restart(self) -> None:
        """
        Restart the engine's unit.

        Raises:
            DatabaseEngineError: When systemd reports the restart failed.
        """
        result = self.manager._exec(["systemctl", "restart", self.unit()], timeout=RESTART_TIMEOUT)
        if not result.success:
            raise DatabaseEngineError(
                f"{self.display_name()} did not restart",
                output=(result.stderr or result.stdout).strip() or None,
            )

    def _reload(self) -> None:
        """
        Have the engine's unit read its files again.

        Raises:
            DatabaseEngineError: When systemd reports the reload failed.
        """
        result = self.manager._exec(["systemctl", "reload", self.unit()], timeout=SERVICE_TIMEOUT)
        if not result.success:
            raise DatabaseEngineError(
                f"{self.display_name()} did not reload",
                output=(result.stderr or result.stdout).strip() or None,
            )

    def _wait(self) -> bool:
        """
        Wait for the engine to answer.

        Returns:
            Whether it did within :data:`WAIT_SECONDS`, or within
            :data:`LOADING_SECONDS` while it says it is still starting. Under
            a rehearsal nothing was restarted, so there is nothing to wait for.
        """
        if is_rehearsal():
            return True
        waited = 0
        while True:
            if self.ping():
                return True
            if waited >= LOADING_SECONDS:
                return False
            if waited >= WAIT_SECONDS and not self.starting():
                return False
            _sleep(1)
            waited += 1

    def journal(self) -> str:
        """
        Read the end of the engine's journal.

        Returns:
            journalctl's output verbatim, or its error.
        """
        result = self.manager._exec(
            ["journalctl", "-u", self.unit(), "-n", str(JOURNAL_LINES), "--no-pager"],
            timeout=QUERY_TIMEOUT,
        )
        return (result.stdout if result.success else result.stderr).strip()

    def _put_back_after_interruption(
        self,
        written: list[tuple[Path, str | None]],
        *,
        stage: str,
        live: Mapping[str, LiveValue],
        changed: Sequence[str],
    ) -> None:
        """
        Put the previous settings back while an exception other than a database error goes up.

        Args:
            written: The files written so far, with what they held.
            stage: How far the change got (see :meth:`_put_back`).
            live: What the engine reported before.
            changed: The keys that changed.
        """
        if not written:
            return
        outcome = self._put_back(
            written,
            DatabaseEngineError("The change was interrupted"),
            stage=stage,
            live=live,
            changed=changed,
        )
        self.manager.logger.warning(
            f"{self.display_name()}'s settings change stopped half way. {outcome.message}. "
            f"{outcome.details}".strip()
        )

    def _put_back(
        self,
        written: list[tuple[Path, str | None]],
        error: DatabaseError,
        *,
        stage: str,
        live: Mapping[str, LiveValue],
        changed: Sequence[str],
    ) -> DatabaseEngineError:
        """
        Put the previous files back and bring the engine up on them.

        Args:
            written: The files written so far, with what they held.
            error: What went wrong.
            stage: How far it got: ``write`` (writing or checking, the
                engine untouched), ``restart`` or ``runtime``.
            live: What the engine reported before, for a runtime change.
            changed: The keys that changed.

        Returns:
            The error to raise: what happened, whether the engine is back,
            and the checker's output or the engine's journal verbatim.
        """
        problems: list[str] = []
        for path, before in reversed(written):
            try:
                if before is None:
                    self.manager.fs.remove(path)
                else:
                    own = path == self.settings_file() and self.OWN_FILE
                    self._write(path, before, preserve=not own)
            except (OSError, DatabaseError) as exc:
                problems.append(f"{path} could not be put back: {exc}")
        restarted = stage == "restart"
        back = True
        if restarted:
            self.before_restart(strict=False)
            try:
                self._restart()
            except DatabaseEngineError as exc:
                problems.append(exc.message)
            back = self._wait()
        elif stage == "runtime" and self.RUNTIME:
            previous = {key: live[key].value for key in changed if key in live}
            try:
                self.restore_runtime(previous)
            except (DatabaseError, ValidationError) as exc:
                # A value the engine reported that Noust would not write (a
                # wildcard, a unit it does not use) must not stop the rest of
                # the way back.
                problems.append(f"The running values could not all be put back: {exc.message}")
        elif stage == "runtime":
            # A reload that failed, or an engine that stopped answering after
            # one, may already have read some of the new files: only reading
            # the previous ones again puts it back on them.
            try:
                self._reload()
            except DatabaseEngineError as exc:
                problems.append(exc.message)
            back = self._wait()
        if not back:
            problems.append(
                f"{self.display_name()} did not come back on the previous settings either: "
                f"look at 'journalctl -u {self.unit()}' and 'systemctl status {self.unit()}'."
            )
        output = error.output if error.output and not restarted else self.journal()
        if restarted and error.output:
            output = f"{error.output}\n\n{output}".strip()
        return DatabaseEngineError(
            f"{self.display_name()} did not accept the new settings, so the previous ones are back"
            if back and not problems
            else f"{self.display_name()} did not accept the new settings",
            details=" ".join([error.message, *problems]).strip(),
            output=output or None,
        )

    def restore_runtime(self, previous: Mapping[str, str]) -> None:
        """
        Put running values back after a runtime change failed half way.

        Args:
            previous: The values the engine reported before, shown canonically.

        Raises:
            DatabaseError: When the engine refuses one.
            ValidationError: When a value it reported is not one Noust writes.
        """
        parsed = {
            key: parse_value(self._spec(key), value, self.ENGINE, Resources(0, 1))
            for key, value in previous.items()
        }
        self.apply_runtime(parsed)


def _split_addresses(addresses: Any) -> list[str]:
    """
    Turn listen addresses into a list.

    Args:
        addresses: A tuple of addresses, a value kept from a file that is not
            Noust's alone, or an engine's own text (``127.0.0.1 -::1``).

    Returns:
        The addresses.
    """
    if isinstance(addresses, Kept):
        addresses = addresses.text
    if isinstance(addresses, str):
        return [part for part in re.split(r"[\s,]+", addresses) if part]
    return [str(address) for address in addresses]


def _refuse_link(path: Path) -> None:
    """
    Refuse a configuration path that is a symbolic link.

    Args:
        path: A file or directory; one that does not exist passes.

    Raises:
        DatabaseEngineError: When it is a link, or cannot be looked at.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DatabaseEngineError(f"Could not look at {path}", details=str(exc)) from exc
    if not stat.S_ISLNK(info.st_mode):
        return
    try:
        target = f"It points to {os.readlink(path)}. "
    except OSError:
        target = ""
    raise DatabaseEngineError(
        f"{path} is a symbolic link, so Noust will not read or write the settings through it",
        details=(
            f"{target}Noust writes as root, and whoever owns the engine's configuration could "
            "point a link anywhere. Replace the link with the file or directory itself, then "
            "try again."
        ),
    )


def _beyond_loopback(addresses: Any) -> bool:
    """
    Tell whether listen addresses reach beyond this server.

    Args:
        addresses: A tuple of addresses, or a value kept from a file that is
            not Noust's alone.

    Returns:
        True when any address is not a loopback one.
    """
    addresses = _split_addresses(addresses)
    return bool(addresses) and not all(is_loopback(str(a).lstrip("-")) for a in addresses)


def _account_name(number: int, lookup: Callable[[int], Any], attribute: str) -> str:
    """
    Name an account or a group by its number.

    Args:
        number: The uid or gid.
        lookup: ``pwd.getpwuid`` or ``grp.getgrgid``.
        attribute: The name attribute of what it returns.

    Returns:
        The name, or the number when it has none.
    """
    try:
        return str(getattr(lookup(number), attribute))
    except KeyError:
        return str(number)


def _read(path: Path, *, follow_links: bool = False) -> str | None:
    """
    Read a configuration file, if it exists.

    Args:
        path: The file.
        follow_links: Follow a symbolic link at the path. Only for a file
            Noust reads to learn something and never writes (Debian's
            ``my.cnf`` is a link through the alternatives system); a file it
            rewrites is refused when it is a link.

    Returns:
        Its text, or None.

    Raises:
        DatabaseEngineError: When it cannot be read, is not a regular file,
            is not UTF-8, or is a symbolic link not to be followed.
    """
    flags = os.O_RDONLY | os.O_CLOEXEC | (0 if follow_links else os.O_NOFOLLOW)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            _refuse_link(path)
        raise DatabaseEngineError(f"Could not read {path}", details=str(exc)) from exc
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise DatabaseEngineError(
                f"{path} is not a regular file", details="Replace it with the file itself."
            )
        data = handle.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DatabaseEngineError(
            f"{path} is not UTF-8 text, so Noust will not rewrite it", details=str(exc)
        ) from exc


def _round_mb(size: float, minimum: int = 0) -> int:
    """
    Round a size down to whole megabytes.

    Args:
        size: Bytes.
        minimum: The least it may be, in bytes.

    Returns:
        The size.
    """
    return max(int(size) // 1024**2 * 1024**2, minimum)


def _key_value_lines(text: str | None, keys: Sequence[str], separator: str) -> dict[str, str]:
    """
    Read ``key = value`` or ``key value`` lines of a file Noust wrote.

    Args:
        text: The file.
        keys: The keys to keep.
        separator: ``=`` or a space.

    Returns:
        The last value of each key present, unquoted.
    """
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "[")):
            continue
        if separator == "=":
            key, _, value = stripped.partition("=")
        else:
            key, _, value = stripped.partition(" ")
        key, value = key.strip(), value.strip().strip("'\"")
        if key in keys:
            found[key] = value
    return found


# ---------------------------------------------------------------- PostgreSQL

_MEGABYTE = 1024**2

POSTGRES_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "listen_addresses",
        "addresses",
        "The addresses PostgreSQL accepts connections on. localhost keeps it on this "
        "server; anything else opens it to the network, where pg_hba.conf still decides "
        "who may sign in.",
    ),
    SettingSpec(
        "port",
        "port",
        "The TCP port. Applications' connection strings must use the same one.",
        minimum=1024,
        maximum=65535,
    ),
    SettingSpec(
        "max_connections",
        "integer",
        "How many connections at once. Each one costs memory; a pooler (PgBouncer) is the "
        "answer to needing many more.",
        minimum=10,
        maximum=10000,
    ),
    SettingSpec(
        "shared_buffers",
        "size",
        "Memory PostgreSQL keeps for its own cache of tables and indexes. A quarter of "
        "the server's memory is the usual start.",
        unit="MB",
        minimum=16 * _MEGABYTE,
        memory_share=MEMORY_SHARE,
    ),
    SettingSpec(
        "effective_cache_size",
        "size",
        "How much memory the planner assumes the operating system and PostgreSQL cache "
        "together. It reserves nothing; it makes index scans look as cheap as they are.",
        unit="MB",
        restart=False,
        minimum=8 * _MEGABYTE,
    ),
    SettingSpec(
        "work_mem",
        "size",
        "Memory one sort or hash may use before it spills to disk. One query can use it "
        "several times, on every connection.",
        unit="MB",
        restart=False,
        minimum=64 * 1024,
        maximum=2 * 1024**3,
    ),
    SettingSpec(
        "maintenance_work_mem",
        "size",
        "Memory for VACUUM, CREATE INDEX and restores. More makes them faster.",
        unit="MB",
        restart=False,
        minimum=1 * _MEGABYTE,
        maximum=8 * 1024**3,
    ),
    SettingSpec(
        "log_min_duration_statement",
        "duration_ms",
        "Statements slower than this many milliseconds are written to the log, which is "
        "where slow queries are found. -1 turns it off.",
        unit="ms",
        restart=False,
        minimum=-1,
        maximum=86_400_000,
    ),
    SettingSpec(
        "timezone",
        "timezone",
        "The time zone timestamps are shown in when a session does not choose one.",
        restart=False,
    ),
)


#: A value as PostgreSQL's configuration reads it: quoted (a quote doubled
#: inside) or bare, after ``=`` or a space.
_PG_VALUE = r"[ \t]*(?:=[ \t]*|[ \t]+)(?:'((?:[^']|'')*)'|([^\s#']+))"

#: ``include_dir`` in any spelling PostgreSQL accepts; the keyword is
#: case-insensitive.
_PG_INCLUDE_DIR = re.compile(r"^[ \t]*include_dir" + _PG_VALUE, re.IGNORECASE | re.MULTILINE)

#: ``include`` and ``include_if_exists``: a file Noust does not follow.
_PG_INCLUDE = re.compile(r"^[ \t]*include(?:_if_exists)?" + _PG_VALUE, re.IGNORECASE | re.MULTILINE)

#: ``listen_addresses`` in any spelling.
_PG_LISTEN = re.compile(r"^[ \t]*listen_addresses" + _PG_VALUE, re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Cluster:
    """
    A PostgreSQL cluster as ``pg_lsclusters`` lists it.

    Attributes:
        version: Its major version.
        name: Its name (``main``).
        data_directory: Where its data is.
        online: Whether it runs.
    """

    version: str
    name: str
    data_directory: str
    online: bool


class PostgresSettings(EngineSettings):
    """PostgreSQL's settings, in ``conf.d/90-noust.conf`` of the cluster Noust administers."""

    ENGINE = "postgresql"

    def __init__(self, manager: BaseDatabaseManager, resources: Resources) -> None:
        """
        Args:
            manager: The PostgreSQL manager.
            resources: This server.
        """
        super().__init__(manager, resources)
        self._cluster: Cluster | None = None

    def specs(self) -> tuple[SettingSpec, ...]:
        """
        Returns:
            :data:`POSTGRES_SPECS`.
        """
        return POSTGRES_SPECS

    def cluster(self) -> Cluster:
        """
        Find the cluster Noust administers: the one online, or the only one.

        Every step then targets that cluster by name (its files, its unit,
        its checker, and ``PGCLUSTER`` for psql), so the file written, the
        values read and the server waited for are the same cluster's. With
        several clusters running there is no single answer, and guessing one
        would write one cluster's file and judge it by another's answers.

        Returns:
            The cluster.

        Raises:
            DatabaseEngineError: When ``pg_lsclusters`` lists none, or several
                run (or several exist and none runs).
        """
        if self._cluster is not None:
            return self._cluster
        result = self.manager._exec(["pg_lsclusters", "--no-header"], timeout=QUERY_TIMEOUT)
        clusters: list[Cluster] = []
        for line in result.stdout.splitlines() if result.success else []:
            fields = line.split()
            if len(fields) >= 6 and fields[0].isdigit():
                clusters.append(Cluster(fields[0], fields[1], fields[5], "online" in fields[3]))
        if not clusters:
            raise DatabaseEngineError(
                "No PostgreSQL cluster was found to configure",
                details=(
                    "pg_lsclusters lists none. Noust configures the clusters of Debian's and "
                    "Ubuntu's postgresql-common."
                ),
                output=(result.stderr or result.stdout).strip() or None,
            )
        online = [cluster for cluster in clusters if cluster.online]
        candidates = online or clusters
        if len(candidates) > 1:
            names = ", ".join(f"{cluster.version}/{cluster.name}" for cluster in candidates)
            raise DatabaseEngineError(
                f"Several PostgreSQL clusters {'run' if online else 'exist'} ({names}), so Noust "
                "cannot tell which one to configure",
                details=(
                    "Noust configures a server with one cluster. Stop the ones you no longer use "
                    "(pg_ctlcluster <version> <name> stop, and set it to manual in its "
                    "start.conf), or edit each cluster's postgresql.conf by hand."
                ),
                output=(result.stdout or result.stderr).strip() or None,
            )
        self._cluster = candidates[0]
        return self._cluster

    def _sql(self, query: str) -> tuple[bool, str]:
        """
        Run SQL on the cluster Noust configures, not on pg_wrapper's default.

        Args:
            query: The statement, built from this module's constants.

        Returns:
            Whether psql succeeded, and its output or its error text.
        """
        cluster = self.cluster()
        return self.manager._execute_sql(  # type: ignore[attr-defined,no-any-return]
            query, env={"PGCLUSTER": f"{cluster.version}/{cluster.name}"}
        )

    def config_paths(self) -> list[Path]:
        """
        Returns:
            ``/etc/postgresql/<version>``, the cluster's directory (owned by
            ``postgres``), ``conf.d``, Noust's file and ``postgresql.conf``.
        """
        directory = self._config_dir()
        return [
            directory.parent,
            directory,
            *super().config_paths(),
            directory / "postgresql.conf",
        ]

    def listen_fallback(self) -> tuple[str, ...] | None:
        """
        Read ``listen_addresses`` from every other file of the cluster.

        Returns:
            Every address ``postgresql.conf``, the other files of ``conf.d``
            and ``postgresql.auto.conf`` set; ``localhost``, PostgreSQL's
            default, when none does; None when a file cannot be read or
            includes one Noust does not follow.
        """
        directory = self._config_dir()
        main = directory / "postgresql.conf"
        ours = self.settings_file()
        try:
            texts = [_read(main)]
            others = sorted(path for path in ours.parent.glob("*.conf") if path != ours)
            texts += [_read(path, follow_links=True) for path in others]
            cluster = self.cluster()
            texts.append(
                _read(
                    flavours.HOST.at(f"{cluster.data_directory}/postgresql.auto.conf"),
                    follow_links=True,
                )
            )
        except (OSError, DatabaseEngineError):
            return None
        found: list[str] = []
        for text in texts:
            if text is None:
                continue
            if _PG_INCLUDE.search(text):
                return None
            if any(
                not self._is_our_include_dir(main, match)
                for match in _PG_INCLUDE_DIR.finditer(text)
            ):
                return None
            for match in _PG_LISTEN.finditer(text):
                found += _split_addresses(
                    match.group(1) if match.group(1) is not None else match.group(2)
                )
        return tuple(found) or ("localhost",)

    def _is_our_include_dir(self, main: Path, match: re.Match[str]) -> bool:
        """
        Tell whether an ``include_dir`` line names the cluster's ``conf.d``.

        Args:
            main: The ``postgresql.conf`` it is in, which a relative path is
                read from.
            match: The line, matched by :data:`_PG_INCLUDE_DIR`.

        Returns:
            True when it is the directory Noust's file is in.
        """
        value = match.group(1).replace("''", "'") if match.group(1) is not None else match.group(2)
        target = flavours.HOST.at(value) if value.startswith("/") else main.parent / value
        return os.path.normpath(target) == os.path.normpath(self.settings_file().parent)

    def _config_dir(self) -> Path:
        """
        Returns:
            ``/etc/postgresql/<version>/<cluster>``.
        """
        cluster = self.cluster()
        return flavours.HOST.at(f"/etc/postgresql/{cluster.version}/{cluster.name}")

    def settings_file(self) -> Path:
        """
        Returns:
            ``conf.d/90-noust.conf`` of the cluster.
        """
        return self._config_dir() / "conf.d" / "90-noust.conf"

    def unit(self) -> str:
        """
        Returns:
            The cluster's own unit: ``postgresql`` only propagates, and does
            not report a cluster that fails to start.
        """
        cluster = self.cluster()
        return f"postgresql@{cluster.version}-{cluster.name}"

    def includes(self) -> list[tuple[Path, str]]:
        """
        Add ``include_dir = 'conf.d'`` to postgresql.conf when it has none.

        Returns:
            postgresql.conf and its new text, or nothing.
        """
        main = self._config_dir() / "postgresql.conf"
        text = _read(main)
        if text is None:
            raise DatabaseEngineError(
                f"{main} does not exist", details="Noust configures Debian-style clusters."
            )
        if any(self._is_our_include_dir(main, match) for match in _PG_INCLUDE_DIR.finditer(text)):
            return []
        addition = (
            "\n# Added by Noust: 'noust db settings postgresql' writes conf.d/90-noust.conf.\n"
            "include_dir = 'conf.d'\n"
        )
        return [(main, text.rstrip("\n") + "\n" + addition)]

    def read_file(self, text: str | None) -> dict[str, str]:
        """
        Args:
            text: Noust's file.

        Returns:
            Its values.
        """
        return _key_value_lines(text, [spec.key for spec in POSTGRES_SPECS], "=")

    def render_file(self, previous: str | None, values: Mapping[str, Any]) -> str:
        """
        Args:
            previous: Unused: the file is Noust's alone.
            values: The settings.

        Returns:
            The file.
        """
        lines = [MARKER.format(engine=self.ENGINE)]
        for spec in POSTGRES_SPECS:
            if spec.key not in values:
                continue
            value = values[spec.key]
            if spec.kind == "addresses":
                rendered = "'" + ",".join(value) + "'"
            elif spec.kind == "size":
                rendered = "'" + _engine_size(int(value), "postgresql") + "'"
            elif spec.kind == "timezone":
                rendered = f"'{value}'"
            else:
                rendered = str(value)
            lines.append(f"{spec.key} = {rendered}")
        return "\n".join(lines) + "\n"

    def live_values(self) -> dict[str, LiveValue]:
        """
        Ask ``pg_settings``, which also says what each change needs.

        Returns:
            The values, with ``restart`` from ``context`` and the file each
            came from.
        """
        # The names are this module's constants, never input.
        names = ", ".join(f"'{spec.key}'" for spec in POSTGRES_SPECS)
        query = (
            "SELECT lower(name), current_setting(name), context, coalesce(sourcefile, '') "  # noqa: S608
            f"FROM pg_settings WHERE lower(name) IN ({names});"
        )
        success, output = self._sql(query)
        if not success:
            return {}
        found: dict[str, LiveValue] = {}
        for line in output.splitlines():
            fields = line.split("|")
            if len(fields) < 4:
                continue
            key, value, context, source = fields[0], fields[1], fields[2], "|".join(fields[3:])
            spec = next((spec for spec in POSTGRES_SPECS if spec.key == key), None)
            if spec is None:
                continue
            found[key] = LiveValue(
                value=_canonical(spec, value, self.ENGINE),
                restart=context == "postmaster",
                source=source or None,
            )
        return found

    def recommended(self) -> dict[str, Any]:
        """
        A quarter of memory for shared buffers, three quarters assumed cached.

        Returns:
            The recommendations.
        """
        memory = self.resources.memory_bytes
        shared = _round_mb(memory / 4, 128 * _MEGABYTE)
        connections = 100
        return {
            "listen_addresses": ("localhost",),
            "port": 5432,
            "max_connections": connections,
            "shared_buffers": shared,
            "effective_cache_size": _round_mb(memory * 3 / 4, 512 * _MEGABYTE),
            "work_mem": _round_mb(max(memory - shared, 0) / (connections * 3), 4 * _MEGABYTE),
            "maintenance_work_mem": min(
                _round_mb(memory / 16, 64 * _MEGABYTE), 2 * 1024 * _MEGABYTE
            ),
            "log_min_duration_statement": 1000,
            "timezone": None,
        }

    def check(self, changed: Sequence[str]) -> None:
        """
        Have ``postgres -C`` read the configuration, as the cluster's account.

        It parses ``postgresql.conf`` and every file it includes and fails
        on a value PostgreSQL would refuse at start.

        Args:
            changed: The keys that changed; the first is the one printed.

        Raises:
            DatabaseEngineError: When PostgreSQL refuses the configuration.
        """
        cluster = self.cluster()
        binary = f"/usr/lib/postgresql/{cluster.version}/bin/postgres"
        config = f"/etc/postgresql/{cluster.version}/{cluster.name}/postgresql.conf"
        result = self.manager._exec(
            [
                binary,
                "-D",
                cluster.data_directory,
                "-c",
                f"config_file={config}",
                "-C",
                changed[0],
            ],
            timeout=CHECK_TIMEOUT,
            user="postgres",
        )
        if not result.success:
            raise DatabaseEngineError(
                "PostgreSQL's own check refused the new configuration",
                output=(result.stderr or result.stdout).strip() or None,
            )

    def ping(self) -> bool:
        """
        Returns:
            Whether ``SELECT 1`` answers.
        """
        success, _ = self._sql("SELECT 1;")
        return bool(success)

    def after(self, changed: Sequence[str], values: Mapping[str, Any]) -> list[str]:
        """
        Warn about a setting another file still overrides.

        PostgreSQL reads ``postgresql.auto.conf`` (ALTER SYSTEM) after every
        included file, so a value set there wins over Noust's.

        Args:
            changed: The keys that changed.
            values: What Noust's file sets.

        Returns:
            One warning per overridden setting.
        """
        warnings: list[str] = []
        live = self.live_values()
        ours = str(self.settings_file()).split("/etc/postgresql/", 1)[-1]
        for key in changed:
            entry = live.get(key)
            if key in values and entry and entry.source and not entry.source.endswith(ours):
                warnings.append(
                    f"{key} is also set in {entry.source}, which PostgreSQL reads after Noust's "
                    f"file, so it still uses {entry.value}. Remove it there (ALTER SYSTEM RESET "
                    f"{key}) for Noust's value to apply."
                )
        return warnings


def _canonical(spec: SettingSpec, value: str, engine: str) -> str:
    """
    Show a value as the engine reported it, canonically when it parses.

    Args:
        spec: The setting.
        value: The engine's text.
        engine: The engine.

    Returns:
        The canonical text, or the engine's own when it does not parse.
    """
    text = value.strip()
    try:
        if spec.kind == "size" and text.isdigit():
            return format_size(int(text))
        return show_value(spec, parse_value(spec, text, engine, Resources(0, 1)))
    except ValidationError:
        return text


# ------------------------------------------------------------- MySQL/MariaDB

MYSQL_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "bind-address",
        "addresses",
        "The address the server accepts connections on. 127.0.0.1 keeps it on this server; "
        "anything else opens it to the network.",
    ),
    SettingSpec(
        "port",
        "port",
        "The TCP port. Applications' connection strings must use the same one.",
        minimum=1024,
        maximum=65535,
    ),
    SettingSpec(
        "max_connections",
        "integer",
        "How many clients may be connected at once.",
        restart=False,
        minimum=10,
        maximum=100_000,
    ),
    SettingSpec(
        "innodb_buffer_pool_size",
        "size",
        "Memory InnoDB keeps for its cache of tables and indexes, the setting that matters "
        "most. A quarter of memory suits a server that also runs applications.",
        unit="MB",
        restart=False,
        minimum=32 * _MEGABYTE,
        memory_share=MEMORY_SHARE,
    ),
    SettingSpec(
        "innodb_log_file_size",
        "size",
        "Size of each redo log file. Larger absorbs bursts of writes better and makes "
        "recovery after a crash slower.",
        unit="MB",
        minimum=4 * _MEGABYTE,
        maximum=512 * 1024**3,
    ),
    SettingSpec(
        "slow_query_log",
        "boolean",
        "Writes statements slower than long_query_time to the slow query log.",
        restart=False,
        words=("ON", "OFF"),
    ),
    SettingSpec(
        "long_query_time",
        "seconds",
        "What counts as a slow query, in seconds.",
        unit="s",
        restart=False,
        minimum=0,
        maximum=3600,
    ),
    SettingSpec(
        "character-set-server",
        "enum",
        "The character set of new databases. utf8mb4 holds every Unicode character, emoji "
        "included.",
        restart=False,
        choices=("utf8mb4", "utf8mb3", "latin1", "ascii", "binary"),
    ),
    SettingSpec(
        "default-time-zone",
        "timezone",
        "The time zone NOW() and TIMESTAMP columns use: SYSTEM, or an offset such as +00:00.",
        restart=False,
    ),
)

#: The server variable behind each option.
_MYSQL_VARIABLES: dict[str, str] = {
    "bind-address": "bind_address",
    "character-set-server": "character_set_server",
    "default-time-zone": "time_zone",
}


class MySQLSettings(EngineSettings):
    """MySQL's and MariaDB's settings, in ``99-noust.cnf`` of the flavour's include directory."""

    ENGINE = "mysql"
    RUNTIME = True

    def _mariadb(self) -> bool:
        """
        Returns:
            Whether the server is MariaDB.
        """
        return bool(getattr(self.manager, "is_mariadb", False))

    def specs(self) -> tuple[SettingSpec, ...]:
        """
        Returns:
            :data:`MYSQL_SPECS`; MariaDB's ``bind-address`` takes one address.
        """
        if not self._mariadb():
            return MYSQL_SPECS
        return tuple(
            replace(spec, single=True) if spec.key == "bind-address" else spec
            for spec in MYSQL_SPECS
        )

    def settings_file(self) -> Path:
        """
        Returns:
            ``/etc/mysql/mariadb.conf.d/99-noust.cnf`` or ``mysql.conf.d``.

        Raises:
            DatabaseEngineError: When the include directory does not exist.
        """
        directory = "mariadb.conf.d" if self._mariadb() else "mysql.conf.d"
        folder = flavours.HOST.at(f"/etc/mysql/{directory}")
        if not folder.is_dir():
            raise DatabaseEngineError(
                f"{folder} does not exist, so Noust has nowhere to write the settings",
                details=(
                    "Noust configures the Debian and Ubuntu layout, where /etc/mysql/my.cnf "
                    f"includes {folder}."
                ),
            )
        return folder / "99-noust.cnf"

    def listen_fallback(self) -> tuple[str, ...] | None:
        """
        Read ``bind-address`` from every other option file of the server.

        Returns:
            Every address the other files set, in any section; ``*`` when
            none does, which is where MySQL 8 and MariaDB listen by default;
            None when a file cannot be read.
        """
        etc = flavours.HOST.at("/etc/mysql")
        ours = self.settings_file()
        candidates = [
            etc / "my.cnf",
            etc / "mysql.cnf",
            etc / "mariadb.cnf",
            etc / "debian.cnf",
            flavours.HOST.at("/etc/my.cnf"),
        ]
        try:
            for directory in ("conf.d", "mysql.conf.d", "mariadb.conf.d"):
                candidates += sorted((etc / directory).glob("*.cnf"))
            # Read, never written: Debian's my.cnf is a link through the
            # alternatives system, and following it is the only way to read it.
            texts = [_read(path, follow_links=True) for path in candidates if path != ours]
        except (OSError, DatabaseEngineError):
            return None
        found: list[str] = []
        for text in texts:
            for line in (text or "").splitlines():
                key, separator, value = line.strip().partition("=")
                if separator and key.strip().lower().replace("_", "-") == "bind-address":
                    found += _split_addresses(value.split("#", 1)[0].strip().strip("'\""))
        return tuple(found) or ("*",)

    def read_file(self, text: str | None) -> dict[str, str]:
        """
        Args:
            text: Noust's file.

        Returns:
            Its values.
        """
        return _key_value_lines(text, [spec.key for spec in MYSQL_SPECS], "=")

    def _option(self, spec: SettingSpec, value: Any) -> str:
        """
        Write a value in option file syntax.

        Args:
            spec: The setting.
            value: Canonical value.

        Returns:
            The text after ``=``.
        """
        if spec.kind == "size":
            return _engine_size(int(value), "mysql")
        return show_value(spec, value)

    def render_file(self, previous: str | None, values: Mapping[str, Any]) -> str:
        """
        Args:
            previous: Unused: the file is Noust's alone.
            values: The settings.

        Returns:
            The file.
        """
        lines = [MARKER.format(engine=self.ENGINE), "[mysqld]"]
        for spec in self.specs():
            if spec.key in values:
                lines.append(f"{spec.key} = {self._option(spec, values[spec.key])}")
        return "\n".join(lines) + "\n"

    def live_values(self) -> dict[str, LiveValue]:
        """
        Returns:
            ``SHOW GLOBAL VARIABLES`` for every setting.
        """
        variables = {_MYSQL_VARIABLES.get(spec.key, spec.key): spec for spec in self.specs()}
        names = ", ".join(f"'{name}'" for name in variables)
        success, output = self.manager._execute_sql(  # type: ignore[attr-defined]
            f"SHOW GLOBAL VARIABLES WHERE Variable_name IN ({names});"
        )
        if not success:
            return {}
        found: dict[str, LiveValue] = {}
        for line in output.splitlines():
            name, _, value = line.partition("\t")
            spec = variables.get(name.strip())
            if spec is not None:
                found[spec.key] = LiveValue(_canonical(spec, value, self.ENGINE))
        return found

    def recommended(self) -> dict[str, Any]:
        """
        A quarter of memory for InnoDB, a log a quarter of that.

        Returns:
            The recommendations.
        """
        pool = _round_mb(self.resources.memory_bytes / 4, 128 * _MEGABYTE)
        return {
            "bind-address": ("127.0.0.1",),
            "port": 3306,
            "max_connections": 151,
            "innodb_buffer_pool_size": pool,
            "innodb_log_file_size": min(_round_mb(pool / 4, 48 * _MEGABYTE), 2 * 1024**3),
            "slow_query_log": True,
            "long_query_time": 1.0,
            "character-set-server": "utf8mb4",
            "default-time-zone": None,
        }

    def check(self, changed: Sequence[str]) -> None:
        """
        Have the server read its option files without starting.

        MySQL has ``--validate-config``; MariaDB has none, and ``--help
        --verbose`` reads every option file and fails on an unknown option.

        Args:
            changed: Unused.

        Raises:
            DatabaseEngineError: When the server refuses the configuration.
        """
        if self._mariadb():
            binary = "mariadbd" if self.manager.runner.exists("mariadbd") else "mysqld"
            argv = [binary, "--help", "--verbose"]
        else:
            argv = ["mysqld", "--validate-config"]
        result = self.manager._exec(argv, timeout=CHECK_TIMEOUT)
        if not result.success:
            raise DatabaseEngineError(
                f"{self.display_name()}'s own check refused the new configuration",
                output=(result.stderr or result.stdout).strip() or None,
            )

    def _sql_value(self, spec: SettingSpec, value: Any) -> str:
        """
        Write a value as a SET GLOBAL literal.

        Args:
            spec: The setting.
            value: Canonical value, already validated.

        Returns:
            The literal.
        """
        if spec.kind == "boolean":
            return "ON" if value else "OFF"
        if spec.kind in ("integer", "size", "port"):
            return str(int(value))
        if spec.kind == "seconds":
            return f"{float(value):g}"
        return "'" + str(value).replace("'", "''") + "'"

    def apply_runtime(self, values: Mapping[str, Any]) -> None:
        """
        Apply each setting with SET GLOBAL.

        Args:
            values: The changed settings.

        Raises:
            DatabaseQueryError: When the server refuses one.
        """
        for key, value in values.items():
            spec = self._spec(key)
            variable = _MYSQL_VARIABLES.get(key, key)
            success, output = self.manager._execute_sql(  # type: ignore[attr-defined]
                f"SET GLOBAL {variable} = {self._sql_value(spec, value)};"
            )
            if not success:
                raise DatabaseEngineError(
                    f"{self.display_name()} refused {key}",
                    output=output.strip() or None,
                )

    def ping(self) -> bool:
        """
        Returns:
            Whether ``SELECT 1`` answers.
        """
        success, _ = self.manager._execute_sql("SELECT 1;")  # type: ignore[attr-defined]
        return bool(success)


# -------------------------------------------------------------- Redis/Valkey

REDIS_POLICIES = (
    "noeviction",
    "allkeys-lru",
    "allkeys-lfu",
    "allkeys-random",
    "volatile-lru",
    "volatile-lfu",
    "volatile-random",
    "volatile-ttl",
)

REDIS_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "bind",
        "addresses",
        "The addresses the server accepts connections on. 127.0.0.1 -::1 keeps it on this "
        "server; anything else opens it to the network.",
        separator=" ",
    ),
    SettingSpec(
        "port",
        "port",
        "The TCP port.",
        minimum=1024,
        maximum=65535,
        editable=False,
        locked_reason=(
            "Noust's own client reaches the server on its default port, so moving it here "
            "would cut Noust off from it."
        ),
    ),
    SettingSpec(
        "maxmemory",
        "size",
        "The most memory the data may use; 0 is no limit. What happens at the limit is "
        "maxmemory-policy.",
        unit="MB",
        restart=False,
        minimum=0,
        memory_share=MEMORY_SHARE,
    ),
    SettingSpec(
        "maxmemory-policy",
        "enum",
        "What happens at maxmemory: noeviction refuses writes, which queues need; the "
        "allkeys policies drop keys, which only suits a pure cache.",
        restart=False,
        choices=REDIS_POLICIES,
    ),
    SettingSpec(
        "appendonly",
        "boolean",
        "Logs every write to disk, so a crash loses at most a second instead of everything "
        "since the last snapshot.",
        restart=False,
        words=("yes", "no"),
    ),
    SettingSpec(
        "save",
        "snapshots",
        "When to write a snapshot: pairs of seconds and changes ('3600 1 300 100' means "
        "after an hour with one change, or five minutes with a hundred). off writes none.",
        restart=False,
    ),
)


class RedisSettings(EngineSettings):
    """Redis's and Valkey's settings, in ``noust.conf`` included last by the server's file."""

    ENGINE = "redis"
    RUNTIME = True

    def _directory(self) -> str:
        """
        Returns:
            ``/etc/valkey`` for Valkey, ``/etc/redis`` for Redis.
        """
        return "/etc/valkey" if self.manager.installed_flavour() == "valkey" else "/etc/redis"

    def specs(self) -> tuple[SettingSpec, ...]:
        """
        Returns:
            :data:`REDIS_SPECS`.
        """
        return REDIS_SPECS

    def settings_file(self) -> Path:
        """
        Returns:
            ``noust.conf`` beside the server's file.
        """
        return flavours.HOST.at(f"{self._directory()}/noust.conf")

    def _main_file(self) -> Path:
        """
        Returns:
            ``redis.conf`` or ``valkey.conf``.
        """
        name = "valkey.conf" if self._directory() == "/etc/valkey" else "redis.conf"
        return flavours.HOST.at(f"{self._directory()}/{name}")

    def includes(self) -> list[tuple[Path, str]]:
        """
        Make ``include`` the last directive of the server's file.

        Returns:
            The server's file and its new text, or nothing when it already is.

        Raises:
            DatabaseEngineError: When the server's file does not exist.
        """
        main = self._main_file()
        text = _read(main)
        if text is None:
            raise DatabaseEngineError(f"{main} does not exist", details="Is the server installed?")
        directive = f"include {self._directory()}/noust.conf"
        lines = text.splitlines()
        meaningful = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
        if meaningful and meaningful[-1] == directive:
            return []
        kept = [line for line in lines if line.strip() != directive]
        while kept and not kept[-1].strip():
            kept.pop()
        kept += [
            "",
            "# Added by Noust: 'noust db settings' writes this file. Keep it last.",
            directive,
        ]
        return [(main, "\n".join(kept) + "\n")]

    def read_file(self, text: str | None) -> dict[str, str]:
        """
        Args:
            text: Noust's file.

        Returns:
            Its values; ``save`` gathers its pairs.
        """
        found = _key_value_lines(
            text, [spec.key for spec in REDIS_SPECS if spec.key != "save"], " "
        )
        pairs: list[str] = []
        seen = False
        for line in (text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("save "):
                seen = True
                rest = stripped[5:].strip()
                if rest not in ('""', "''"):
                    pairs.append(rest)
        if seen:
            found["save"] = " ".join(pairs) or "off"
        return found

    def render_file(self, previous: str | None, values: Mapping[str, Any]) -> str:
        """
        Args:
            previous: Unused: the file is Noust's alone.
            values: The settings.

        Returns:
            The file. ``save ""`` comes first: it clears the rules the server's
            own file set, which further ``save`` lines would otherwise add to.
        """
        lines = [MARKER.format(engine=self.ENGINE)]
        for spec in REDIS_SPECS:
            if spec.key not in values:
                continue
            value = values[spec.key]
            if spec.kind == "snapshots":
                lines.append('save ""')
                lines += [f"save {value[i]} {value[i + 1]}" for i in range(0, len(value), 2)]
            elif spec.kind == "size":
                lines.append(f"{spec.key} {_engine_size(int(value), 'redis')}")
            else:
                lines.append(f"{spec.key} {show_value(spec, value)}")
        return "\n".join(lines) + "\n"

    def live_values(self) -> dict[str, LiveValue]:
        """
        Returns:
            ``CONFIG GET`` for every setting.
        """
        found: dict[str, LiveValue] = {}
        for spec in REDIS_SPECS:
            success, output = self.manager._execute_redis("CONFIG", "GET", spec.key)  # type: ignore[attr-defined]
            lines = output.splitlines() if success else []
            if len(lines) >= 2 and lines[0].strip() == spec.key:
                found[spec.key] = LiveValue(_canonical(spec, lines[1], self.ENGINE))
            elif len(lines) == 1 and lines[0].strip() == spec.key:
                found[spec.key] = LiveValue(_canonical(spec, "", self.ENGINE))
        return found

    def recommended(self) -> dict[str, Any]:
        """
        An eighth of memory, writes refused rather than keys dropped.

        Returns:
            The recommendations.
        """
        return {
            "bind": ("127.0.0.1", "-::1"),
            "port": 6379,
            "maxmemory": _round_mb(self.resources.memory_bytes / 8, 64 * _MEGABYTE),
            "maxmemory-policy": "noeviction",
            "appendonly": True,
            "save": (3600, 1, 300, 100, 60, 10000),
        }

    def apply_runtime(self, values: Mapping[str, Any]) -> None:
        """
        Apply each setting with CONFIG SET.

        Args:
            values: The changed settings.

        Raises:
            DatabaseEngineError: When the server refuses one.
        """
        for key, value in values.items():
            spec = self._spec(key)
            if spec.kind == "snapshots":
                text = " ".join(str(number) for number in value)
            elif spec.kind == "size":
                text = str(int(value))
            else:
                text = show_value(spec, value)
            success, output = self.manager._execute_redis("CONFIG", "SET", key, text)  # type: ignore[attr-defined]
            if not success:
                raise DatabaseEngineError(
                    f"{self.display_name()} refused {key}", output=output.strip() or None
                )

    def ping(self) -> bool:
        """
        Returns:
            Whether PING answers PONG.
        """
        success, output = self.manager._execute_redis("PING")  # type: ignore[attr-defined]
        return bool(success) and "PONG" in output

    def starting(self) -> bool:
        """
        Returns:
            True while Redis answers ``LOADING`` (it is reading its data set
            back into memory, which takes as long as the data set is large),
            or its unit is starting.
        """
        _, output = self.manager._execute_redis("PING")  # type: ignore[attr-defined]
        return "LOADING" in output or super().starting()

    def config_paths(self) -> list[Path]:
        """
        Returns:
            ``/etc/redis`` (owned by ``redis``), Noust's file and the server's.
        """
        return [*super().config_paths(), self._main_file()]

    def listen_fallback(self) -> tuple[str, ...] | None:
        """
        Read ``bind`` from the server's own file.

        Returns:
            Every address its ``bind`` lines give; ``*`` when there is none,
            since Redis then listens on every interface; None when the file
            includes another one Noust does not follow.
        """
        text = _read(self._main_file())
        if text is None:
            return None
        directive = f"include {self._directory()}/noust.conf"
        found: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            word, _, rest = stripped.partition(" ")
            if word.lower() == "include" and stripped != directive:
                return None
            if word.lower() == "bind":
                found += rest.split()
        return tuple(found) or ("*",)

    def _info(self, section: str) -> dict[str, str] | None:
        """
        Read one section of ``INFO``.

        Args:
            section: ``memory`` or ``persistence``.

        Returns:
            Its fields, or None when Redis did not answer.
        """
        success, output = self.manager._execute_redis("INFO", section)  # type: ignore[attr-defined]
        if not success:
            return None
        fields: dict[str, str] = {}
        for line in output.splitlines():
            key, separator, value = line.strip().partition(":")
            if separator:
                fields[key] = value
        return fields or None

    def confirmations(self, changes: Mapping[str, Any], live: Mapping[str, LiveValue]) -> list[str]:
        """
        Name the changes that would lose data, or refuse the applications' writes.

        - ``maxmemory`` below what Redis holds now: under ``noeviction`` every
          write that needs memory fails; under any other policy keys are
          dropped at once until it fits.
        - ``appendonly no`` and ``save off``: what is written stops reaching
          the disk the way it did.

        Args:
            changes: The changed settings and their new values.
            live: What Redis reports now.

        Returns:
            One warning per cost.
        """
        name = self.display_name()
        warnings: list[str] = []
        if "maxmemory" in changes or "maxmemory-policy" in changes:
            limit = changes.get("maxmemory") if "maxmemory" in changes else None
            if "maxmemory" not in changes and "maxmemory" in live:
                limit = _parsed(self._spec("maxmemory"), live["maxmemory"].value, self.ENGINE)
            info = self._info("memory") if isinstance(limit, int) and limit > 0 else None
            used = info.get("used_memory", "") if info else ""
            if isinstance(limit, int) and used.isdigit() and limit < int(used):
                policy = changes.get("maxmemory-policy") or (
                    live["maxmemory-policy"].value if "maxmemory-policy" in live else "noeviction"
                )
                holds = f"{name} holds {_about(int(used))} now"
                shown = format_size(limit)
                if policy == "noeviction":
                    warnings.append(
                        f"{holds}. With maxmemory {shown} it refuses every write that needs "
                        "more memory (OOM errors) until data is removed: applications that "
                        "write to it will fail."
                    )
                else:
                    warnings.append(
                        f"{holds}. With maxmemory {shown} and {policy} it drops keys at once "
                        "until it fits under the limit, and the keys it drops are gone."
                    )
        if changes.get("appendonly") is False and _live_text(live, "appendonly") != "no":
            warnings.append(
                f"With appendonly no, {name} stops logging every write: a crash or a restart "
                "loses everything written since its last snapshot."
            )
        if changes.get("save", None) == () and _live_text(live, "save") != "off":
            warnings.append(
                f"With save off, {name} writes no snapshots: unless appendonly is on, a restart "
                "loses everything it holds."
            )
        return warnings

    def before_restart(self, *, strict: bool) -> None:
        """
        Have Redis write its data set to disk before Noust restarts it.

        Whatever was written since the last snapshot, and is not in an
        append-only file, would otherwise be lost with the process. A
        background save is waited for rather than a ``SAVE``, which would
        block every client, and the wait is long enough for a large data set.

        Args:
            strict: Raise when the snapshot cannot be taken (before a restart
                Noust chose); otherwise log it and go on.

        Raises:
            DatabaseEngineError: When ``strict`` and the snapshot failed.
        """
        try:
            self._snapshot()
        except DatabaseEngineError as exc:
            if strict:
                raise
            self.manager.logger.warning(
                f"{self.display_name()} restarts without a fresh snapshot: {exc.message}"
            )

    def _snapshot(self) -> None:
        """
        Take a background snapshot and wait for it to finish.

        Raises:
            DatabaseEngineError: When Redis refuses it, it fails, or it does
                not finish within :data:`SNAPSHOT_SECONDS`.
        """
        if is_rehearsal():
            # Nothing is restarted under a rehearsal; the command is still
            # handed to the runner so it is listed among what would run.
            self.manager._execute_redis("BGSAVE")  # type: ignore[attr-defined]
            return
        self._snapshot_finished()  # one already running would refuse BGSAVE
        success, output = self.manager._execute_redis("BGSAVE")  # type: ignore[attr-defined]
        if not success:
            raise DatabaseEngineError(
                f"{self.display_name()} could not save its data before the restart",
                details=(
                    "Noust restarts it only once what it holds in memory is on disk. Look at "
                    "its 'dir' and the free space there, then try again."
                ),
                output=output.strip() or None,
            )
        fields = self._snapshot_finished()
        if fields.get("rdb_last_bgsave_status") != "ok":
            raise DatabaseEngineError(
                f"{self.display_name()}'s snapshot before the restart failed",
                details="Its log says why: journalctl -u " + self.unit(),
                output="\n".join(f"{key}:{value}" for key, value in fields.items()) or None,
            )

    def _snapshot_finished(self) -> dict[str, str]:
        """
        Wait until no background snapshot runs.

        Returns:
            ``INFO persistence`` once none does.

        Raises:
            DatabaseEngineError: When Redis does not say, or one is still
                running after :data:`SNAPSHOT_SECONDS`.
        """
        for waited in range(SNAPSHOT_SECONDS + 1):
            fields = self._info("persistence")
            if fields is None or "rdb_bgsave_in_progress" not in fields:
                raise DatabaseEngineError(
                    f"Noust could not tell whether {self.display_name()} saved its data",
                    details="INFO persistence did not answer, so the restart did not happen.",
                )
            if fields["rdb_bgsave_in_progress"] == "0":
                return fields
            if waited < SNAPSHOT_SECONDS:
                _sleep(1)
        raise DatabaseEngineError(
            f"{self.display_name()}'s snapshot was still running after {SNAPSHOT_SECONDS} seconds",
            details="Noust did not restart it. Try again once the snapshot has finished.",
        )


def _parsed(spec: SettingSpec, text: str, engine: str) -> Any:
    """
    Parse a value an engine reported, when it parses.

    Args:
        spec: The setting.
        text: The engine's canonical text.
        engine: The engine.

    Returns:
        The canonical value, or None.
    """
    try:
        return parse_value(spec, text, engine, Resources(0, 1))
    except ValidationError:
        return None


def _live_text(live: Mapping[str, LiveValue], key: str) -> str | None:
    """
    Args:
        live: What the engine reports.
        key: A setting.

    Returns:
        Its reported value, or None when it did not say.
    """
    entry = live.get(key)
    return entry.value if entry else None


def _about(size: int) -> str:
    """
    Show a size roughly, for a sentence.

    Args:
        size: Bytes.

    Returns:
        ``1.5GB``, ``300MB`` or ``512kB``.
    """
    if size >= 1024**3:
        return f"{size / 1024**3:.1f}GB"
    if size >= 1024**2:
        return f"{size / 1024**2:.0f}MB"
    return f"{max(size // 1024, 1)}kB"


# ------------------------------------------------------------------- MongoDB

MONGODB_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "net.bindIp",
        "addresses",
        "The addresses mongod accepts connections on. 127.0.0.1 keeps it on this server; "
        "anything else opens it to the network.",
    ),
    SettingSpec(
        "net.port",
        "port",
        "The TCP port.",
        minimum=1024,
        maximum=65535,
        editable=False,
        locked_reason=(
            "Noust's own shell reaches mongod on its default port, so moving it here would "
            "cut Noust off from it."
        ),
    ),
    SettingSpec(
        "storage.wiredTiger.engineConfig.cacheSizeGB",
        "gigabytes",
        "Memory WiredTiger keeps for its cache. MongoDB's default is half of memory minus "
        "1 GB, too much on a server that also runs applications.",
        unit="GB",
        minimum=0.25,
    ),
    SettingSpec(
        "operationProfiling.slowOpThresholdMs",
        "integer",
        "Operations slower than this many milliseconds are logged as slow.",
        unit="ms",
        minimum=0,
        maximum=3_600_000,
    ),
)


def _dig(data: Any, dotted: str) -> Any:
    """
    Read a dotted path from nested dictionaries.

    Args:
        data: The YAML document.
        dotted: ``net.bindIp``.

    Returns:
        The value, or None.
    """
    for part in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


class MongoSettings(EngineSettings):
    """MongoDB's settings, in ``/etc/mongod.conf``, its only file."""

    ENGINE = "mongodb"
    OWN_FILE = False

    def specs(self) -> tuple[SettingSpec, ...]:
        """
        Returns:
            :data:`MONGODB_SPECS`.
        """
        return MONGODB_SPECS

    def settings_file(self) -> Path:
        """
        Returns:
            mongod's configuration file.
        """
        from noust.managers.database import mongodb

        return Path(mongodb.MONGOD_CONF)

    def config_paths(self) -> list[Path]:
        """
        Returns:
            ``mongod.conf`` alone: its directory is ``/etc``, root's.
        """
        return [self.settings_file()]

    def listen_fallback(self) -> tuple[str, ...] | None:
        """
        Returns:
            ``127.0.0.1``: ``mongod.conf`` is its only file, and without
            ``net.bindIp`` mongod listens on loopback (since MongoDB 3.6).
        """
        return ("127.0.0.1",)

    def _document(self, text: str | None) -> dict[str, Any]:
        """
        Parse mongod.conf.

        Args:
            text: Its text.

        Returns:
            The document; empty for an empty file.

        Raises:
            DatabaseEngineError: When it is not YAML Noust can edit.
        """
        try:
            document = yaml.safe_load(text or "") or {}
        except yaml.YAMLError as exc:
            raise DatabaseEngineError(
                f"{self.settings_file()} is not valid YAML", details=str(exc)
            ) from exc
        if not isinstance(document, dict):
            raise DatabaseEngineError(f"{self.settings_file()} is not a YAML mapping")
        return document

    def read_file(self, text: str | None) -> dict[str, str]:
        """
        Args:
            text: mongod.conf.

        Returns:
            The values it sets for the offered settings.
        """
        document = self._document(text)
        found: dict[str, str] = {}
        for spec in MONGODB_SPECS:
            value = _dig(document, spec.key)
            if value is not None:
                found[spec.key] = str(value)
        return found

    def render_file(self, previous: str | None, values: Mapping[str, Any]) -> str:
        """
        Edit only the offered settings, keeping everything else as it is.

        Args:
            previous: mongod.conf as it is.
            values: The settings it is to set.

        Returns:
            The new YAML.
        """
        document = self._document(previous)
        for spec in MONGODB_SPECS:
            parts = spec.key.split(".")
            if spec.key in values:
                value = values[spec.key]
                if isinstance(value, Kept):
                    continue
                node = document
                for part in parts[:-1]:
                    child = node.get(part)
                    if not isinstance(child, dict):
                        child = {}
                        node[part] = child
                    node = child
                node[parts[-1]] = ",".join(value) if spec.kind == "addresses" else value
            else:
                _prune(document, parts)
        return str(yaml.safe_dump(document, sort_keys=False))

    def live_values(self) -> dict[str, LiveValue]:
        """
        Returns:
            What ``getCmdLineOpts`` reports, with mongod's defaults filled in.
        """
        success, data = self.manager._execute_mongo_json(  # type: ignore[attr-defined]
            "db.adminCommand({getCmdLineOpts: 1}).parsed"
        )
        if not success or not isinstance(data, dict):
            return {}
        defaults = {
            "net.bindIp": "127.0.0.1",
            "net.port": "27017",
            "operationProfiling.slowOpThresholdMs": "100",
        }
        found: dict[str, LiveValue] = {}
        for spec in MONGODB_SPECS:
            value = _dig(data, spec.key)
            text = str(value) if value is not None else defaults.get(spec.key)
            if text is not None:
                found[spec.key] = LiveValue(_canonical(spec, text, self.ENGINE))
        return found

    def recommended(self) -> dict[str, Any]:
        """
        A quarter of memory for WiredTiger.

        Returns:
            The recommendations.
        """
        cache = max(round(self.resources.memory_bytes / 4 / 1024**3, 2), 0.25)
        return {
            "net.bindIp": ("127.0.0.1",),
            "net.port": 27017,
            "storage.wiredTiger.engineConfig.cacheSizeGB": cache,
            "operationProfiling.slowOpThresholdMs": 100,
        }

    def ping(self) -> bool:
        """
        Returns:
            Whether ``ping`` answers.
        """
        success, output = self.manager._execute_mongo(  # type: ignore[attr-defined]
            "db.adminCommand({ping: 1}).ok"
        )
        return bool(success) and output.strip().endswith("1")


def _prune(document: dict[str, Any], parts: Sequence[str]) -> None:
    """
    Remove a dotted key and the mappings it leaves empty.

    Args:
        document: The YAML document.
        parts: The key's parts.
    """
    trail: list[tuple[dict[str, Any], str]] = []
    node: Any = document
    for part in parts[:-1]:
        if not isinstance(node, dict) or not isinstance(node.get(part), dict):
            return
        trail.append((node, part))
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return
    del node[parts[-1]]
    for parent, part in reversed(trail):
        if parent[part]:
            break
        del parent[part]


#: The settings of each engine.
BACKENDS: dict[str, type[EngineSettings]] = {
    "postgresql": PostgresSettings,
    "mysql": MySQLSettings,
    "redis": RedisSettings,
    "mongodb": MongoSettings,
}


def settings_for(
    manager: BaseDatabaseManager, resources: Resources | None = None
) -> EngineSettings:
    """
    Give an engine's settings.

    Args:
        manager: The engine's manager, on this host.
        resources: This server; measured when None.

    Returns:
        Its settings.

    Raises:
        DatabaseEngineError: When the manager drives a container, whose image
            and compose file configure it (3.3 spec, 9.3), or when Noust
            offers no settings for the engine.
    """
    manager.refuse_in_container("configure")
    backend = BACKENDS.get(manager.ENGINE_NAME)
    if backend is None:
        raise DatabaseEngineError(f"Noust offers no settings for {manager.DISPLAY_NAME}")
    return backend(manager, resources or server_resources())
