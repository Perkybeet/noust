# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every system path and name prefix Noust owns.

Nothing else in the tree spells ``/etc/noust`` or ``/var/lib/noust``: it asks this
module, so a location changes in one place.

A central in a container has one volume, and everything that must survive a
restart has to be on it. ``NOUST_DATA_DIR`` names that directory, and the
precedence is:

1. ``NOUST_DATA_DIR`` set: configuration, state (store and secrets), backups
   and logs all live under it (see :func:`data_layout`). (The store still looks
   at every place a populated inventory may be before it creates an empty one,
   which is its own rule, in :mod:`noust.core.store`.)
2. Otherwise the system locations below.

The constants are computed when this module is imported, from the environment the
process started with, because the store and the configuration read them at import
time too: every module agrees on one answer for the life of the process.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

#: The product's name in paths, units and commands.
NAME = "noust"

#: The variable naming a single directory for everything Noust keeps.
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

STATE_DIR = _LAYOUT.state if _LAYOUT else Path("/var/lib/noust")

BACKUP_DIR = _LAYOUT.backups if _LAYOUT else Path("/var/backups/noust")

LOG_DIR = _LAYOUT.logs if _LAYOUT else Path("/var/log/noust")

#: Per-application build caches (npm, pip, composer), owned by the account
#: sandboxed builds run as; see noust.deployers.helpers.sandbox.
BUILD_CACHE_DIR = Path("/var/cache/noust/build")

#: The language runtimes Noust installs for applications that pin one
#: (``build: {runtime: node@22}``): ``<dir>/<language>/<version>``. Outside the
#: state directory on purpose: a sandboxed build cannot open that one at all
#: (it holds the store), and builds and services alike must be able to execute
#: what is in here. root owns it and it is world-readable; nothing a
#: repository says is ever written into it.
RUNTIMES_DIR = (DATA_DIR / "runtimes") if DATA_DIR is not None else Path("/opt/noust/runtimes")

#: The repositories ``git push`` deploys from (``noust push enable``), and the home of the
#: unprivileged account that receives the pushes. Outside the state directory on purpose:
#: that one holds the store and is root's alone, and the account has to be able to reach its
#: repositories. A central in a container keeps them in its data directory.
PUSH_DIR = (DATA_DIR / "git") if DATA_DIR is not None else Path("/var/lib/noust-git")

#: The unprivileged account the pushes log in as and the repositories belong to.
PUSH_USER = "noust-git"

#: Where the runner keeps a sandboxed command's environment file and result
#: while it runs: on tmpfs, root only.
SANDBOX_RUNTIME_DIR = Path("/run/noust/sandbox")

#: The store's file name inside the state directory.
STORE_NAME = "noust.db"

#: nginx's blue/green upstream snippets, included by every site that uses one.
NGINX_UPSTREAMS_DIR = Path("/etc/nginx/noust-upstreams")

#: PID file of a console started with ``noust web start -d`` as root.
WEB_PID_FILE = Path("/run/noust-web.pid")

#: Prefix of a PHP-FPM pool file Noust writes: ``noust-<app>.conf``.
PHP_POOL_PREFIX = "noust-"

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
#: The timer and service that update an application on a schedule (``noust deploy schedule``).
UPDATE_UNIT_PREFIX = f"{UNIT_PREFIX}update-"
#: The socket and service of a database made reachable from the network
#: (``noust-dbexpose-<engine>-<port>``).
DB_EXPOSE_UNIT_PREFIX = f"{UNIT_PREFIX}dbexpose-"
#: The unit, the Compose project and the container of a database instance Noust
#: runs in a container (``noust-db-<name>``).
DB_INSTANCE_UNIT_PREFIX = f"{UNIT_PREFIX}db-"
#: The connector of a Cloudflare Tunnel Noust created (``noust-tunnel-<name>``).
TUNNEL_UNIT_PREFIX = f"{UNIT_PREFIX}tunnel-"
#: The transient unit noust-monitor has systemd run the hardening checks in.
SECURITY_CHECKS_UNIT = f"{UNIT_PREFIX}security-checks"
#: The transient unit noust-monitor has systemd run the network's hourly upkeep in
#: (DNS records nobody uses, Cloudflare's address ranges).
NETWORK_MAINTENANCE_UNIT = f"{UNIT_PREFIX}network-maintenance"
#: The timer and service that run the scheduled cleaning of the server's disk.
STORAGE_CLEAN_UNIT = f"{UNIT_PREFIX}storage-clean"
#: The timer and service that let Noust install its own new releases inside the window the
#: operator set (``noust self-update``).
AUTO_UPDATE_UNIT = f"{UNIT_PREFIX}auto-update"
#: The service that keeps a server's link to its central up: ``noust-link-<central>``.
LINK_UNIT_PREFIX = f"{UNIT_PREFIX}link-"

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
        STORAGE_CLEAN_UNIT,
        AUTO_UPDATE_UNIT,
        LEGACY_WEB_UNIT,
        LEGACY_MONITOR_UNIT,
        LEGACY_PREVIEWS_UNIT,
    }
)
#: The prefixes of Noust's scheduled-work units, new and legacy.
OWN_UNIT_PREFIXES: tuple[str, ...] = (
    CRON_UNIT_PREFIX,
    BACKUP_UNIT_PREFIX,
    UPDATE_UNIT_PREFIX,
    DB_EXPOSE_UNIT_PREFIX,
    DB_INSTANCE_UNIT_PREFIX,
    TUNNEL_UNIT_PREFIX,
    LINK_UNIT_PREFIX,
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

#: Prefix of the environment variables Noust reads.
ENV_PREFIX = "NOUST_"


def getenv(name: str, default: str | None = None) -> str | None:
    """
    Read a ``NOUST_*`` variable.

    Args:
        name: The variable's name without prefix, for example ``WEB_STATE_DIR``.
        default: Returned when it is not set.

    Returns:
        The value of ``NOUST_<name>``, else ``default``.
    """
    value = os.environ.get(f"{ENV_PREFIX}{name}")
    return default if value is None else value


def config_dir() -> Path:
    """
    Return the directory holding config.yaml and the console's state.

    Returns:
        ``/etc/noust``, or the configuration directory under ``NOUST_DATA_DIR``.
    """
    return CONFIG_DIR


def push_dir() -> Path:
    """
    Return the directory holding the repositories ``git push`` deploys from.

    Returns:
        ``/var/lib/noust-git``, or ``<data directory>/git`` for a central in a container.
    """
    return PUSH_DIR


def link_dir() -> Path:
    """
    Return the directory a server keeps the keys of its links to its centrals in.

    Returns:
        ``<state directory>/link``: one directory per central, root's alone.
    """
    return state_dir() / "link"


def uploads_dir() -> Path:
    """
    Return the directory holding the code people uploaded as archives.

    One folder per upload, named by its identifier, holding the extracted project; the
    sessions still being sent live in ``.pending`` beside them. Inside the state directory,
    so root's alone: the code is copied into each release from here.

    Returns:
        ``uploads`` under the state directory.
    """
    return state_dir() / "uploads"


def state_dir() -> Path:
    """
    Return the directory holding the store, secrets, metrics and locks.

    Returns:
        ``/var/lib/noust``, or the state directory under ``NOUST_DATA_DIR``.
    """
    return STATE_DIR


def backup_dir() -> Path:
    """
    Return the default directory for backups.

    Returns:
        ``/var/backups/noust``, or the backups directory under ``NOUST_DATA_DIR``.
    """
    return BACKUP_DIR


def log_dir() -> Path:
    """
    Return the directory for Noust's own log files.

    Returns:
        ``/var/log/noust``, or the log directory under ``NOUST_DATA_DIR``.
    """
    return LOG_DIR


def nginx_upstreams_dir() -> Path:
    """
    Return the directory holding the blue/green upstream snippets.

    Returns:
        ``/etc/nginx/noust-upstreams``.
    """
    return NGINX_UPSTREAMS_DIR


def user_data_dir(home: Path | None = None) -> Path:
    """
    Return the per-user data directory: ``~/.local/share/noust``.

    Args:
        home: Home directory, defaulting to the current user's.

    Returns:
        The Noust directory.
    """
    return (home or Path.home()) / ".local" / "share" / NAME


def user_cache_dir(home: Path | None = None) -> Path:
    """
    Return the per-user cache directory: ``~/.cache/noust``.

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


def user_state_dir(home: Path | None = None) -> Path:
    """
    Return the per-user state directory: ``~/.local/state/noust``.

    Args:
        home: Home directory, defaulting to the current user's.

    Returns:
        The directory; created by whoever writes to it.
    """
    return (home or Path.home()) / ".local" / "state" / NAME
