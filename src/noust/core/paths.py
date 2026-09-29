# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every system path and name prefix Noust owns, and the ones WASM used.

Until 3.0 the product was called WASM and kept its configuration in
``/etc/wasm``, its store in ``/var/lib/wasm/wasm.db``, its backups in
``/var/backups/wasm`` and its own systemd units under ``wasm-*``. 3.0 renames
all of them, and :mod:`noust.core.migrate_from_wasm` moves a 2.x server onto
the new names. Between the upgrade and that migration - an unprivileged run,
a unit started by systemd, a migration refused because two paths live on
different filesystems - the old locations are still where the data is.

So nothing else in the tree spells ``/etc/noust`` or ``/var/lib/wasm``: it asks
this module. The rule that matters is in :func:`resolve_dir`: a location that
exists wins over one that would be created. Creating ``/var/lib/wasm`` once
moved a populated store to an empty one and a server with seventeen sites
answered "No applications deployed"; a rename must never reopen that trap.

A central in a container has one volume, and everything that must survive a
restart has to be on it. ``NOUST_DATA_DIR`` names that directory, and the
precedence is:

1. ``NOUST_DATA_DIR`` set: configuration, state (store and secrets), backups
   and logs all live under it (see :func:`data_layout`). The operator named
   where Noust lives, so no legacy WASM location is preferred over it. (The
   store still looks at every place a populated inventory may be before it
   creates an empty one, which is its own rule, in :mod:`noust.core.store`.)
2. Otherwise the system locations, each resolved by :func:`resolve_dir`: a
   real WASM directory wins until the migration moves it.

The constants below are computed when this module is imported, from the
environment the process started with, because the store and the
configuration read them at import time too: every module agrees on one
answer for the life of the process.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

#: The product's name in paths, units and commands.
NAME = "noust"
#: The name it had until 3.0.
LEGACY_NAME = "wasm"

#: The variable naming a single directory for everything Noust keeps. It is
#: new in 3.0, so it has no ``WASM_*`` spelling.
DATA_DIR_ENV = "NOUST_DATA_DIR"


@dataclass(frozen=True)
class DataLayout:
    """
    Where each kind of data lives under a data directory.

    Attributes:
        root: The data directory itself.
        config: config.yaml and the console's state (signing key, token hash,
            sessions, two-factor state, audit log, the minted TLS pair).
        state: The store, the secrets, metrics and locks.
        backups: Backups made by the central itself.
        logs: Noust's own log files.
    """

    root: Path
    config: Path
    state: Path
    backups: Path
    logs: Path

    def all(self) -> tuple[Path, ...]:
        """
        Every directory of the layout, root excluded.

        Returns:
            The directories, in the order they are created.
        """
        return (self.config, self.state, self.backups, self.logs)


def data_layout(root: Path) -> DataLayout:
    """
    Describe the directories under a data directory.

    Args:
        root: The data directory.

    Returns:
        Its layout.
    """
    return DataLayout(
        root=root,
        config=root / "config",
        state=root / "state",
        backups=root / "backups",
        logs=root / "log",
    )


def data_dir(environ: Mapping[str, str] | None = None) -> Path | None:
    """
    Read the data directory the environment names, if any.

    Args:
        environ: The environment; the process's own by default.

    Returns:
        The directory, or None when ``NOUST_DATA_DIR`` is unset or blank.

    Raises:
        ConfigError: When the value is a relative path, which would mean a
            different directory for every working directory a command is
            started from.
    """
    env = os.environ if environ is None else environ
    value = (env.get(DATA_DIR_ENV) or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        from noust.core.exceptions import ConfigError

        raise ConfigError(
            f"{DATA_DIR_ENV} must be an absolute path, not {value!r}",
            details="Set it to the directory Noust keeps its data in, such as /data.",
        )
    return path


#: The data directory this process runs with, or None for the system layout.
DATA_DIR = data_dir()
_LAYOUT = data_layout(DATA_DIR) if DATA_DIR is not None else None

# -- system directories -------------------------------------------------------

CONFIG_DIR = _LAYOUT.config if _LAYOUT else Path("/etc/noust")
LEGACY_CONFIG_DIR = Path("/etc/wasm")

STATE_DIR = _LAYOUT.state if _LAYOUT else Path("/var/lib/noust")
LEGACY_STATE_DIR = Path("/var/lib/wasm")

BACKUP_DIR = _LAYOUT.backups if _LAYOUT else Path("/var/backups/noust")
LEGACY_BACKUP_DIR = Path("/var/backups/wasm")

LOG_DIR = _LAYOUT.logs if _LAYOUT else Path("/var/log/noust")
LEGACY_LOG_DIR = Path("/var/log/wasm")

#: The store's file name inside the state directory.
STORE_NAME = "noust.db"
LEGACY_STORE_NAME = "wasm.db"

#: nginx's blue/green upstream snippets, included by every site that uses one.
NGINX_UPSTREAMS_DIR = Path("/etc/nginx/noust-upstreams")
LEGACY_NGINX_UPSTREAMS_DIR = Path("/etc/nginx/wasm-upstreams")

#: PID file of a console started with ``noust web start -d`` as root.
WEB_PID_FILE = Path("/run/noust-web.pid")
LEGACY_WEB_PID_FILE = Path("/var/run/wasm-web.pid")

#: Prefix of a PHP-FPM pool file Noust writes: ``noust-<app>.conf``.
PHP_POOL_PREFIX = "noust-"
LEGACY_PHP_POOL_PREFIX = "wasm-"

# -- systemd --------------------------------------------------------------------

#: Prefix of Noust's own units (console, monitor, previews, cron, backups).
#: Application units are named after the application and never carry it.
UNIT_PREFIX = "noust-"
#: What WASM's own units were called, and what application units written
#: before 0.14.1 still are: ``wasm-<app>.service`` is an application's unit.
LEGACY_UNIT_PREFIX = "wasm-"

#: The comment every unit Noust writes carries, so a hand-written unit with a
#: colliding name is never overwritten or removed.
UNIT_MARKER = "Generated by Noust"
LEGACY_UNIT_MARKER = "Generated by WASM"
#: Either marker makes a unit ours: 2.x wrote every application unit, cron
#: job and backup timer with the old one, and those are not rewritten.
UNIT_MARKERS: tuple[str, ...] = (UNIT_MARKER, LEGACY_UNIT_MARKER)

WEB_UNIT = f"{UNIT_PREFIX}web"
MONITOR_UNIT = f"{UNIT_PREFIX}monitor"
PREVIEWS_UNIT = f"{UNIT_PREFIX}previews"
CRON_UNIT_PREFIX = f"{UNIT_PREFIX}cron-"
BACKUP_UNIT_PREFIX = f"{UNIT_PREFIX}backup-"

LEGACY_WEB_UNIT = f"{LEGACY_UNIT_PREFIX}web"
LEGACY_MONITOR_UNIT = f"{LEGACY_UNIT_PREFIX}monitor"
LEGACY_PREVIEWS_UNIT = f"{LEGACY_UNIT_PREFIX}previews"
LEGACY_CRON_UNIT_PREFIX = f"{LEGACY_UNIT_PREFIX}cron-"
LEGACY_BACKUP_UNIT_PREFIX = f"{LEGACY_UNIT_PREFIX}backup-"

#: The fixed names of Noust's own units, new and legacy.
OWN_UNITS: frozenset[str] = frozenset(
    {
        WEB_UNIT,
        MONITOR_UNIT,
        PREVIEWS_UNIT,
        LEGACY_WEB_UNIT,
        LEGACY_MONITOR_UNIT,
        LEGACY_PREVIEWS_UNIT,
    }
)
#: The prefixes of Noust's scheduled-work units, new and legacy.
OWN_UNIT_PREFIXES: tuple[str, ...] = (
    CRON_UNIT_PREFIX,
    BACKUP_UNIT_PREFIX,
    LEGACY_CRON_UNIT_PREFIX,
    LEGACY_BACKUP_UNIT_PREFIX,
)

# -- repository files -----------------------------------------------------------

#: The per-repository nginx overrides file, in the order they are looked for:
#: the Noust name first, then the WASM one, so a repository written for WASM
#: keeps working unchanged, then the unbranded names.
NGINX_OVERRIDE_FILES: tuple[str, ...] = (
    "noust.nginx.yaml",
    "noust.nginx.yml",
    "wasm.nginx.yaml",
    "wasm.nginx.yml",
    "nginx.yaml",
    "nginx.yml",
)

# -- environment ----------------------------------------------------------------

#: Prefix of the environment variables Noust reads. ``WASM_*`` is still read
#: when the ``NOUST_*`` spelling is absent.
ENV_PREFIX = "NOUST_"
LEGACY_ENV_PREFIX = "WASM_"

#: Upgrade notes linked from every notice about the rename.
UPGRADE_NOTES_URL = "https://github.com/Perkybeet/noust/blob/main/docs/UPGRADING-3.0.md"


def getenv(name: str, default: str | None = None) -> str | None:
    """
    Read a ``NOUST_*`` variable, falling back to its ``WASM_*`` spelling.

    Args:
        name: The variable's name without prefix, for example ``WEB_STATE_DIR``.
        default: Returned when neither spelling is set.

    Returns:
        The value of ``NOUST_<name>``, else ``WASM_<name>``, else ``default``.
    """
    value = os.environ.get(f"{ENV_PREFIX}{name}")
    if value is None:
        value = os.environ.get(f"{LEGACY_ENV_PREFIX}{name}")
    return default if value is None else value


def resolve_dir(new: Path, legacy: Path) -> Path:
    """
    Choose between a directory's new name and the one WASM gave it.

    A real WASM directory - not the symlink the migration leaves behind - is
    data that has not been moved yet, and it wins even when the new directory
    exists: a package that creates ``/etc/noust`` or ``/var/lib/noust`` on
    install would otherwise point every command at an empty directory while
    the configuration, the secrets and the store sit one name away. After the
    migration the legacy name is a symlink, and the new one is used. Nothing
    here creates or moves anything.

    Args:
        new: The Noust location.
        legacy: The WASM location.

    Returns:
        ``new`` when a data directory was named (it is the operator's
        explicit choice), else ``legacy`` while it is a real directory, else
        ``new``.
    """
    if DATA_DIR is not None:
        return new
    if legacy.is_dir() and not legacy.is_symlink():
        return legacy
    return new


def config_dir() -> Path:
    """
    Return the directory holding config.yaml and the console's state.

    Returns:
        ``/etc/noust``, or ``/etc/wasm`` on a server not yet migrated.
    """
    return resolve_dir(CONFIG_DIR, LEGACY_CONFIG_DIR)


def state_dir() -> Path:
    """
    Return the directory holding the store, secrets, metrics and locks.

    Returns:
        ``/var/lib/noust``, or ``/var/lib/wasm`` on a server not yet migrated.
    """
    return resolve_dir(STATE_DIR, LEGACY_STATE_DIR)


def backup_dir() -> Path:
    """
    Return the default directory for backups.

    Returns:
        ``/var/backups/noust``, or ``/var/backups/wasm`` on a server not yet
        migrated.
    """
    return resolve_dir(BACKUP_DIR, LEGACY_BACKUP_DIR)


def log_dir() -> Path:
    """
    Return the directory for Noust's own log files.

    Returns:
        ``/var/log/noust``, or ``/var/log/wasm`` on a server not yet migrated.
    """
    return resolve_dir(LOG_DIR, LEGACY_LOG_DIR)


def nginx_upstreams_dir() -> Path:
    """
    Return the directory holding the blue/green upstream snippets.

    Returns:
        ``/etc/nginx/noust-upstreams``, or the WASM name on a server whose
        sites still include it.
    """
    return resolve_dir(NGINX_UPSTREAMS_DIR, LEGACY_NGINX_UPSTREAMS_DIR)


def user_data_dir(home: Path | None = None) -> Path:
    """
    Return the per-user data directory: ``~/.local/share/noust``.

    Args:
        home: Home directory, defaulting to the current user's.

    Returns:
        The Noust directory.
    """
    return (home or Path.home()) / ".local" / "share" / NAME


def legacy_user_data_dir(home: Path | None = None) -> Path:
    """
    Return the per-user data directory WASM used: ``~/.local/share/wasm``.

    Args:
        home: Home directory, defaulting to the current user's.

    Returns:
        The WASM directory.
    """
    return (home or Path.home()) / ".local" / "share" / LEGACY_NAME


def user_cache_dir(home: Path | None = None) -> Path:
    """
    Return the per-user cache directory: ``~/.cache/noust``.

    A cache is only a cache, so the WASM one is never read: the first run of
    Noust rebuilds what it needs.

    Args:
        home: Home directory, defaulting to the current user's.

    Returns:
        The cache directory.
    """
    return (home or Path.home()) / ".cache" / NAME


def is_own_unit_name(name: str) -> bool:
    """
    Report whether a unit name is one of Noust's (or WASM's) own units.

    Args:
        name: Unit name without the ``.service``/``.timer`` suffix.

    Returns:
        True for the console, the monitor, the previews sweep and the
        scheduled-work units, under either name.
    """
    return name in OWN_UNITS or name.startswith(OWN_UNIT_PREFIXES)


def carries_unit_marker(content: str) -> bool:
    """
    Report whether a unit file says Noust or WASM wrote it.

    Args:
        content: The unit file's text.

    Returns:
        True when either marker is present.
    """
    return any(marker in content for marker in UNIT_MARKERS)


#: What :mod:`noust.core.migrate_from_wasm` did, written when it finishes. Its
#: presence is how the console knows to tell the operator about the rename.
MIGRATION_RECORD = STATE_DIR / "migrated-from-wasm.json"


def came_from_wasm() -> bool:
    """
    Report whether this server ran WASM before it ran Noust.

    Returns:
        True when the migration left its record, or when WASM's directories
        are still there (migrated to symlinks, or not migrated yet).
    """
    return (
        MIGRATION_RECORD.exists()
        or os.path.lexists(LEGACY_CONFIG_DIR)
        or os.path.lexists(LEGACY_STATE_DIR)
    )


def user_state_dir(home: Path | None = None) -> Path:
    """
    Return the per-user state directory: ``~/.local/state/noust``.

    Args:
        home: Home directory, defaulting to the current user's.

    Returns:
        The directory; created by whoever writes to it.
    """
    return (home or Path.home()) / ".local" / "state" / NAME
