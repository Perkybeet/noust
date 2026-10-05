#!/usr/bin/env python3
"""
Run the real Noust panel against a seeded, sandboxed machine.

This is the backend the console is developed and tested against: the real
FastAPI application from :func:`noust.web.server.create_app`, served by uvicorn
so its lifespan runs and the ``/events`` stream and the WebSockets work, over
a store seeded by :func:`tests.panel_factory.seed_console_state` - running,
stopped, failed and static applications, deployments with captured build
logs, sites, certificates, databases, backups, cron jobs and a job history.

Nothing touches the machine it runs on:

- every path Noust reads or writes (the store, the config file, the panel's
  secrets and sessions, backups, systemd units, nginx sites, certificates) is
  redirected into a temporary directory, removed on exit;
- a :class:`SandboxFileSystem` refuses any write that would still land
  outside that directory;
- a :class:`ConsoleRunner` answers ``systemctl``, ``journalctl``, ``nginx``
  and ``certbot`` from an in-memory model of the seeded machine, so starting
  or stopping an application from the console changes what the next status
  query reports, and no process is ever spawned.

It prints exactly one JSON line on stdout once the server accepts
connections, then serves until SIGINT or SIGTERM::

    {"url": "http://127.0.0.1:43127", "token": "...", "totp_secret": null}

Develop the console against it::

    python scripts/console_server.py --port 8080      # terminal 1
    cd panel && npm run dev                           # terminal 2

Vite proxies ``/api``, ``/events``, ``/hooks`` and ``/ws`` to ``VITE_BACKEND``
(default ``http://127.0.0.1:8080``); sign in with the printed token. The
Playwright suite in ``panel/e2e`` starts one of these per worker.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import socket

# Only for the fleet's --seal bootstrap (see seal_sandbox_before_serving): a real,
# throwaway process that seals a sandbox's secrets before this one serves it, so this
# process never learns the passphrase itself and starts up genuinely locked. Nothing
# this script serves ever runs through it - noust.core.runner.CommandRunner still is,
# via the fake installed below - and CONTRIBUTING.md's rule 1 is about src/noust, not this
# development and test-only script.
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parent.parent

#: Most commands a :class:`ConsoleRunner` remembers. It runs for as long as a
#: developer leaves it up, and an unbounded call log is a slow memory leak.
CALL_HISTORY = 256

#: Names a domain this server should seed as its own single application, so a
#: fleet E2E test (panel/e2e/fleet.spec.ts) can tell, from the Apps page alone,
#: that it is reading this server's node and not the central's cache of it. An
#: environment variable, not a flag: it is set once by the Playwright fixture that
#: starts a node, never typed by a developer, and keeping it off the CLI's surface
#: keeps it out of everything that only ever runs one console_server.py.
FLEET_NODE_APP_ENV = "NOUST_E2E_FLEET_APP"

#: Set to a version ("3.0.0") by the fleet's E2E suite to make this node pretend to be an
#: older Noust: it reports that version and does not offer OLDER_NODE_MISSING (see
#: pretend_older_node), so a central's views say "unsupported" for it.
OLDER_NODE_ENV = "NOUST_E2E_OLDER_NODE"

#: What a 3.0 node does not offer of what a 3.1 central's fleet views ask.
OLDER_NODE_MISSING = (
    "/api/overview",
    "/api/server/summary",
    "/api/system/update",
    "/api/auth/fleet/self",
)

#: Seconds uvicorn waits for open connections at shutdown. The event stream
#: never ends by itself, so without a bound a Ctrl+C would wait for the tab.
GRACEFUL_SHUTDOWN_SECONDS = 2


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """
    Parse the command line.

    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The parsed options.
    """
    parser = argparse.ArgumentParser(
        description="Serve the real Noust panel over a seeded, sandboxed machine."
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="loopback address to bind (default 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=0, help="port to bind; 0 picks a free one (default)"
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the sandbox directory on exit and print it"
    )
    parser.add_argument(
        "--totp",
        action="store_true",
        help="enable two-factor sign-in and print its secret as totp_secret",
    )
    parser.add_argument(
        "--backup-codes",
        type=int,
        default=None,
        metavar="N",
        help=(
            "with --totp, enrol N backup codes instead of the usual count and print them as "
            "backup_codes: each is good for one sign-in or confirmation, where a TOTP code is "
            "good for one per 30-second step, which a test suite signing in every few seconds "
            "cannot wait for"
        ),
    )
    parser.add_argument(
        "--static-dir",
        type=Path,
        default=None,
        help=(
            "serve the console build from this directory instead of noust/web/static, "
            "so several builds can be exercised side by side (development only)"
        ),
    )
    parser.add_argument(
        "--misplaced-backups",
        action="store_true",
        help=(
            "also seed backups outside the backup directory, where an empty backup.directory "
            "once sent them, so the Backups page names them and the command that imports them"
        ),
    )
    parser.add_argument(
        "--expired-certificate",
        action="store_true",
        help=(
            "seed the first certificate as expired three days ago, so the health report "
            "turns critical with a reason naming it"
        ),
    )
    parser.add_argument(
        "--showcase",
        action="store_true",
        help=(
            "seed tests.showcase's invented agency instead of tests.panel_factory's "
            "example.com machine: no real project, domain or person, and no rename-era "
            "example.com sites, for docs/assets/console's screenshots (panel/e2e/docs.spec.ts)"
        ),
    )
    parser.add_argument(
        "--hostname",
        default=None,
        help=(
            "report this hostname from the machine strip instead of this machine's own "
            "(development and screenshots only)"
        ),
    )
    parser.add_argument(
        "--central-role",
        choices=("server", "hub"),
        default=None,
        help="set central.role in the written config (the fleet's E2E suite only)",
    )
    parser.add_argument(
        "--seal",
        action="store_true",
        help=(
            "seal this central's secrets before it serves, with a passphrase read from "
            "standard input (never argv); this process never unlocks them itself, so it "
            "starts genuinely locked, as a central restarted after 'noust central seal' "
            "does (the fleet's E2E suite only)"
        ),
    )
    parser.add_argument(
        "--fleet-node",
        action="append",
        default=[],
        metavar="NAME=HOST:PORT",
        help=(
            "register, on this central, a node console already serving at HOST:PORT "
            "under NAME, without ssh - see join_fleet_node() (the fleet's E2E suite only)"
        ),
    )
    parser.add_argument(
        "--internal-seal",
        metavar="SANDBOX_ROOT",
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    try:
        loopback = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback = args.host == "localhost"
    if not loopback:
        # The token printed on stdout opens a panel with root semantics over the
        # sandbox; it is never meant to be reachable from another machine.
        parser.error(f"--host must be a loopback address, not {args.host}")
    return args


# ---------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sandbox:
    """
    Where the sandboxed machine keeps everything.

    Attributes:
        root: The temporary directory holding all of it.
    """

    root: Path

    @property
    def home(self) -> Path:
        """The HOME every per-user path resolves under."""
        return self.root / "home"

    @property
    def etc(self) -> Path:
        """Stands in for ``/etc``."""
        return self.root / "etc"

    @property
    def var(self) -> Path:
        """Stands in for ``/var``."""
        return self.root / "var"

    @property
    def state_dir(self) -> Path:
        """The panel's secrets, sessions and audit log."""
        return self.etc / "noust"

    @property
    def config_file(self) -> Path:
        """The Noust configuration file."""
        return self.etc / "noust" / "config.yaml"

    @property
    def store_file(self) -> Path:
        """The SQLite store; job and deploy logs live beside it."""
        return self.var / "lib" / "noust" / "noust.db"

    @property
    def systemd_dir(self) -> Path:
        """Stands in for ``/etc/systemd/system``."""
        return self.etc / "systemd" / "system"

    @property
    def backup_dir(self) -> Path:
        """Application backups."""
        return self.var / "backups" / "noust"

    @property
    def apps_dir(self) -> Path:
        """Stands in for ``/var/www/apps``."""
        return self.var / "www" / "apps"

    def contains(self, path: Path) -> bool:
        """
        Report whether a path lies inside the sandbox.

        Args:
            path: Any path.

        Returns:
            True when it resolves under :attr:`root`.
        """
        try:
            Path(os.path.abspath(path)).relative_to(self.root)
        except ValueError:
            return False
        return True


def prepare_environment(sandbox: Sandbox) -> None:
    """
    Point every per-user path at the sandbox, before Noust is imported.

    ``noust.core.store`` computes its per-user database path from ``HOME`` at
    import time, so this has to run first.

    Args:
        sandbox: The sandbox.
    """
    for directory in (
        sandbox.home,
        sandbox.state_dir,
        sandbox.systemd_dir,
        sandbox.store_file.parent,
        sandbox.backup_dir,
        sandbox.apps_dir,
        sandbox.var / "log" / "noust",
    ):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["HOME"] = str(sandbox.home)
    os.environ["XDG_DATA_HOME"] = str(sandbox.home / ".local" / "share")
    os.environ["XDG_CONFIG_HOME"] = str(sandbox.home / ".config")
    os.environ["XDG_CACHE_HOME"] = str(sandbox.home / ".cache")
    os.environ["NOUST_WEB_STATE_DIR"] = str(sandbox.state_dir)
    # Defensive: nothing seeded here models a server migrated from WASM, so
    # the automatic migration noust.cli.app runs on a privileged invocation
    # must never fire, even if some future code path reaches it.
    os.environ["NOUST_NO_AUTO_MIGRATE"] = "1"
    # The repository's own code, never an installed copy, and tests.panel_factory.
    for entry in (str(REPO), str(REPO / "src")):
        if entry not in sys.path:
            sys.path.insert(0, entry)


def redirect_system_paths(sandbox: Sandbox) -> None:
    """
    Repoint every system path Noust binds at import time into the sandbox.

    :mod:`noust.core.paths` is patched first, and everything that reads it -
    directly, or through one of its functions - is imported only after:
    several modules compute a path once at import time from its constants
    (``noust.core.config``'s ``DEFAULT_CONFIG_PATH``, ``noust.core.store``'s
    ``DEFAULT_DB_PATH``, ``noust.managers.webserver``'s
    ``NGINX_UPSTREAMS_DIR``...), and ``paths.came_from_wasm()`` is read at
    request time, on every ``/api/auth/session`` response. Patched after the
    fact, any of those would keep resolving against the real ``/etc`` and
    ``/var`` - exactly the developer's machine this script promises never to
    touch, and on this one ``~/.local/share/wasm/wasm.db`` is real.

    Module constants are rebound on the module that reads them; class
    attributes on the class. A path this misses is still covered by
    :class:`SandboxFileSystem` for writes, and is only ever read.

    Args:
        sandbox: The sandbox.
    """
    import noust.core.paths as paths_module

    etc, var = sandbox.etc, sandbox.var

    # The legacy names are pointed at sandbox paths that are never created,
    # exactly like a machine that has never run WASM: nothing seeded here
    # models a migration, so paths.came_from_wasm() must always answer False,
    # whatever /etc/wasm or /var/lib/wasm hold on the machine running this.
    paths_module.CONFIG_DIR = sandbox.state_dir
    paths_module.LEGACY_CONFIG_DIR = etc / "wasm"
    paths_module.STATE_DIR = sandbox.store_file.parent
    paths_module.LEGACY_STATE_DIR = var / "lib" / "wasm"
    paths_module.BACKUP_DIR = sandbox.backup_dir
    paths_module.LEGACY_BACKUP_DIR = var / "backups" / "wasm"
    paths_module.LOG_DIR = var / "log" / "noust"
    paths_module.NGINX_UPSTREAMS_DIR = etc / "nginx" / "noust-upstreams"
    paths_module.LEGACY_NGINX_UPSTREAMS_DIR = etc / "nginx" / "wasm-upstreams"
    paths_module.MIGRATION_RECORD = paths_module.STATE_DIR / "migrated-from-wasm.json"

    import noust.core.config as config_module
    import noust.core.store as store_module
    import noust.managers.diagnose as diagnose_module
    import noust.managers.webserver as webserver_module
    import noust.monitor.observation_store as observations_module
    import noust.monitor.process_monitor as process_monitor_module
    import noust.monitor.timeseries as timeseries_module
    from noust.managers.backup_manager import BackupManager
    from noust.managers.backup_scheduler import BackupScheduler
    from noust.managers.cert_manager import CertManager
    from noust.managers.cron_manager import CronManager
    from noust.managers.database.base import BaseDatabaseManager
    from noust.managers.service_manager import ServiceManager

    config_module.DEFAULT_CONFIG_PATH = sandbox.config_file
    config_module.DEFAULT_APPS_DIR = sandbox.apps_dir
    config_module.DEFAULT_LOG_DIR = var / "log" / "noust"
    config_module.NGINX_SITES_AVAILABLE = etc / "nginx" / "sites-available"
    config_module.NGINX_SITES_ENABLED = etc / "nginx" / "sites-enabled"
    config_module.APACHE_SITES_AVAILABLE = etc / "apache2" / "sites-available"
    config_module.APACHE_SITES_ENABLED = etc / "apache2" / "sites-enabled"
    config_module.SYSTEMD_DIR = sandbox.systemd_dir

    store_module.DEFAULT_DB_PATH = sandbox.store_file
    store_module.USER_DB_PATH = sandbox.home / ".local" / "share" / "noust" / "noust.db"
    # Never created, for the same reason as the paths.py constants above: a
    # real WASM store at either legacy location - system or per-user - must
    # never surface through store_module._store_candidates().
    store_module.LEGACY_DB_PATH = var / "lib" / "wasm" / "wasm.db"
    store_module.LEGACY_USER_DB_PATH = sandbox.home / ".local" / "share" / "wasm" / "wasm.db"
    # A backup is restored where the store says the application is, and the
    # seeded store records the paths a real server has (/var/www/apps/...):
    # outside the sandbox, a restore would write to the machine's own /var.
    # The same path under the sandbox's root instead.
    import noust.managers.backup_manager as backup_module

    deployed_at = backup_module._deployed_at

    def sandboxed_deployed_at(domain: str, app_name: str, config: Any) -> Path:
        path = deployed_at(domain, app_name, config)
        return path if sandbox.contains(path) else sandbox.root / path.relative_to(path.anchor)

    backup_module._deployed_at = sandboxed_deployed_at
    timeseries_module.SYSTEM_DB_PATH = var / "lib" / "noust" / "metrics.db"
    observations_module.SYSTEM_DB_PATH = var / "lib" / "noust" / "observations.db"
    diagnose_module.NGINX_ERROR_LOG = var / "log" / "nginx" / "error.log"

    for name in ("NGINX_BACKEND", "APACHE_BACKEND"):
        backend = getattr(webserver_module, name, None)
        if backend is None:
            continue
        server = "nginx" if name == "NGINX_BACKEND" else "apache2"
        setattr(
            webserver_module,
            name,
            replace(
                backend,
                sites_available=etc / server / "sites-available",
                sites_enabled=etc / server / "sites-enabled",
                # Blue/green upstream files: nginx has a directory for them, Apache none.
                upstreams_dir=(
                    etc / server / "noust-upstreams" if backend.upstreams_dir is not None else None
                ),
            ),
        )

    ServiceManager.SYSTEMD_DIR = sandbox.systemd_dir
    # Only the sandbox: the real /usr/lib/systemd/system would make every
    # seeded unit look shadowed by a system unit, or not, depending on the host.
    ServiceManager.UNIT_SEARCH_DIRS = (sandbox.systemd_dir,)
    CronManager.SYSTEMD_DIR = sandbox.systemd_dir
    BackupScheduler.SYSTEMD_DIR = sandbox.systemd_dir
    # ProcessMonitor.install_service() writes its unit through
    # noust.core.utils.write_file(), plain pathlib rather than the fs seam
    # (SandboxFileSystem never sees the call, so it cannot refuse it). Without
    # this, "Install monitor" in the console would write
    # /etc/systemd/system/noust-monitor.service on whatever machine runs this
    # script - exactly what the module docstring promises never happens.
    process_monitor_module.SYSTEMD_DIR = sandbox.systemd_dir
    BackupManager.DEFAULT_BACKUP_DIR = sandbox.backup_dir
    # The misplaced-backup hint looks in /root and /: on a developer's machine those are the
    # developer's own, so the sandbox has none.
    BackupManager.MISPLACED_BACKUP_ROOTS = ()
    BaseDatabaseManager.BACKUP_DIR = sandbox.backup_dir / "databases"
    CertManager.LETSENCRYPT_DIR = etc / "letsencrypt"
    CertManager.LIVE_DIR = etc / "letsencrypt" / "live"

    for directory in (
        etc / "nginx" / "sites-available",
        etc / "nginx" / "sites-enabled",
        etc / "nginx" / "noust-upstreams",
        var / "log" / "nginx",
    ):
        directory.mkdir(parents=True, exist_ok=True)


def write_config(
    sandbox: Sandbox, *, central_role: str | None = None, ssl_email: str = "ops@example.com"
) -> None:
    """
    Write the Noust configuration the sandboxed machine runs with.

    Args:
        sandbox: The sandbox.
        central_role: ``central.role`` to write (``server`` or ``hub``), for the
            fleet's E2E suite; omitted, this server has none, which is the same
            as ``server`` (see ``noust.central.role``).
        ssl_email: The certificate contact address to write; ``--showcase`` gives its own
            agency's, so nothing this server writes to disk carries example.com.
    """
    import yaml

    config: dict[str, Any] = {
        "apps_directory": str(sandbox.apps_dir),
        "webserver": "nginx",
        "service_user": "www-data",
        "service_group": "www-data",
        "ssl": {"enabled": True, "provider": "certbot", "email": ssl_email},
        "backup": {"directory": str(sandbox.backup_dir), "max_per_app": 10},
    }
    if central_role is not None:
        config["central"] = {"role": central_role}
    sandbox.config_file.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")
    sandbox.config_file.chmod(0o600)


def forbid_real_processes() -> None:
    """
    Make any process spawn that bypasses the runner fail instead of running.

    The runner is the only sanctioned way to start a process (CONTRIBUTING.md rule
    1), and here it is a fake. A code path that spawns through asyncio
    directly would otherwise reach the developer's real journal or systemd.
    """

    async def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError(f"console_server runs no real process: {args[:1]!r}")

    asyncio.create_subprocess_exec = refuse  # type: ignore[assignment]
    asyncio.create_subprocess_shell = refuse  # type: ignore[assignment]


def make_sandbox_filesystem(sandbox: Sandbox) -> Any:
    """
    Build the filesystem that writes inside the sandbox and nowhere else.

    Args:
        sandbox: The sandbox.

    Returns:
        A :class:`noust.core.fs.RealFileSystem` that skips, and reports, any
        change outside the sandbox.
    """
    from noust.core.fs import RealFileSystem

    class SandboxFileSystem(RealFileSystem):
        """A real filesystem confined to the sandbox."""

        def __init__(self) -> None:
            """Start with nothing skipped."""
            self.skipped: deque[str] = deque(maxlen=CALL_HISTORY)

        def _inside(self, action: str, *paths: Path) -> bool:
            """
            Allow a change only when every path it touches is in the sandbox.

            Args:
                action: What would have happened, for the report.
                paths: The paths the change touches.

            Returns:
                True when the change may go ahead.
            """
            if all(sandbox.contains(path) for path in paths):
                return True
            message = f"skipped {action} outside the sandbox: {', '.join(map(str, paths))}"
            self.skipped.append(message)
            print(f"console_server: {message}", file=sys.stderr, flush=True)
            return False

        def write_text(
            self,
            path: Path,
            content: str,
            *,
            mode: int = 0o644,
            owner: tuple[int, int] | None = None,
        ) -> None:
            # The owner is dropped: the console server is not root, and the
            # modelled machine's accounts do not exist on this one.
            if self._inside("write", path):
                super().write_text(path, content, mode=mode)

        def make_dir(
            self, path: Path, *, mode: int = 0o755, parents: bool = True, exist_ok: bool = True
        ) -> None:
            if self._inside("mkdir", path):
                super().make_dir(path, mode=mode, parents=parents, exist_ok=exist_ok)

        def remove(self, path: Path, *, missing_ok: bool = True) -> None:
            if self._inside("remove", path):
                super().remove(path, missing_ok=missing_ok)

        def remove_tree(self, path: Path) -> None:
            if self._inside("remove_tree", path):
                super().remove_tree(path)

        def move(self, source: Path, destination: Path) -> None:
            if self._inside("move", source, destination):
                super().move(source, destination)

        def rename(self, source: Path, destination: Path) -> None:
            if self._inside("rename", source, destination):
                super().rename(source, destination)

        def copy_tree(self, source: Path, destination: Path) -> None:
            if self._inside("copy_tree", destination):
                super().copy_tree(source, destination)

        def chmod(self, path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
            if self._inside("chmod", path):
                super().chmod(path, mode, follow_symlinks=follow_symlinks)

        def set_owner(self, path: Path, *, user: str, group: str, mode: int) -> None:
            # Only the mode: a chown to the modelled machine's accounts needs
            # root, which the console server never is. A file the fake runner
            # only pretended to write (a staged dump's cp) is not there to
            # change, and on the modelled machine the hand-over would succeed.
            if self._inside("chown", path) and os.path.lexists(path):
                super().chmod(path, mode, follow_symlinks=False)

        def symlink(self, target: Path, link: Path) -> None:
            if self._inside("symlink", link):
                super().symlink(target, link)

    return SandboxFileSystem()


# ---------------------------------------------------------------------------
# The machine the runner answers for
# ---------------------------------------------------------------------------


@dataclass
class Unit:
    """
    One systemd unit of the modelled machine.

    Attributes:
        active: ``active``, ``inactive`` or ``failed``.
        enabled: Whether it starts at boot.
        pid: Main PID while active.
        since: When it last changed state.
        restarts: How many times systemd restarted it.
        managed: Whether it is one of Noust's units (listed by ``list-units``).
    """

    active: str
    enabled: bool = True
    pid: int = 0
    since: datetime = field(default_factory=datetime.now)
    restarts: int = 0
    managed: bool = True

    @property
    def sub(self) -> str:
        """The sub-state systemd reports beside the active state."""
        return {"active": "running", "failed": "failed"}.get(self.active, "dead")


def _foreign_unit_lines(units: dict[str, Unit], patterns: list[str]) -> list[str]:
    """
    Extra ``systemctl list-units`` lines for the machine's non-Noust units.

    ``_list_units`` only walks units Noust would create (it skips
    ``unit.managed is False`` entries): the model's postgresql/mysql/redis-server/nginx
    exist for direct queries such as ``is-active nginx``, not for a full listing. The
    show-all-units toggle (``GET /api/services?noust_only=false``) asks
    ``ServiceManager.list_services(all_services=True)``, which lists with
    ``patterns=["*"]`` and does its own managed/foreign split in Python, so it needs those
    units to appear here too - this is the console's only foreign, always-visible unit for
    that flow.

    Args:
        units: The modelled machine's units.
        patterns: The patterns ``systemctl list-units`` was called with.

    Returns:
        One ``systemctl list-units`` line per foreign unit, when ``patterns`` is the
        unrestricted ``["*"]`` an all-units listing sends; empty otherwise, so a scoped
        listing (``noust-*`` and friends) is unaffected.
    """
    if patterns != ["*"]:
        return []
    return [
        f"{name}.service loaded {unit.active} {unit.sub} {name}"
        for name, unit in sorted(units.items())
        if not unit.managed
    ]


def _git_commit_subject_line(args: tuple[str, ...]) -> str | None:
    """
    Fake ``git log -1 --format=%s``, which the deploy pipeline runs to record
    a deployment's ``commit_message``.

    The modelled machine never really clones anything - every seeded app's
    checkout already exists on disk without a real ``.git`` history - so a
    fixed sentence is enough to answer the one invocation that matters:
    demoing that a deployment's history carries a commit subject at all.
    Nothing here reads it back for content.

    Args:
        args: The full argv the runner was asked to execute.

    Returns:
        The fake subject line, or None when ``args`` is not this exact call.
    """
    if args[:4] == ("git", "log", "-1", "--format=%s"):
        return "Seed data for the console demo"
    return None


#: Programs the modelled machine has on PATH.
INSTALLED_PROGRAMS = (
    "systemctl",
    "systemd-analyze",
    "journalctl",
    "nginx",
    "certbot",
    "openssl",
    "git",
    "node",
    "npm",
    "psql",
    "pg_dump",
    # A dump's check (pg_restore --list), answered from the model below.
    "pg_restore",
    "mysql",
    "mysqldump",
    "redis-cli",
    "tar",
    # Backup destinations copy over rclone; the model answers it from a directory per remote.
    "rclone",
    # The fleet's own key pair (NodeKeys.ensure_keypair), faked in ConsoleRunner
    # below: this sandbox never opens a real SSH connection with it.
    "ssh-keygen",
)

#: What each database client prints for ``--version``.
CLIENT_VERSIONS = {
    "psql": "psql (PostgreSQL) 16.4 (Ubuntu 16.4-0ubuntu0.24.04.2)\n",
    "mysql": "mysql  Ver 8.0.39-0ubuntu0.24.04.2 for Linux on x86_64 ((Ubuntu))\n",
    "redis-cli": "redis-cli 7.0.15\n",
}

#: What a Next.js app writes to the journal, for the logs views.
JOURNAL_LINES = (
    "Started {unit}.service - {domain}.",
    "   ▲ Next.js 15.2.4",
    "   - Local:        http://localhost:{port}",
    " ✓ Starting...",
    " ✓ Ready in 412ms",
    "GET / 200 in 38ms",
    "GET /api/health 200 in 3ms",
    "GET /_next/static/chunks/main-app.js 200 in 2ms",
)

#: What a crashing one writes before systemd gives up on it.
FAILED_JOURNAL_LINES = (
    "Started {unit}.service - {domain}.",
    "Error: Cannot find module '/var/www/apps/{domain}/current/.next/standalone/server.js'",
    "    at Module._resolveFilename (node:internal/modules/cjs/loader:1225:15)",
    "{unit}.service: Main process exited, code=exited, status=1/FAILURE",
    "{unit}.service: Failed with result 'exit-code'.",
    "{unit}.service: Scheduled restart job, restart counter is at 5.",
    "{unit}.service: Start request repeated too quickly.",
)

# ---------------------------------------------------------------------------
# Console: the Databases page needs more than `list_databases` out of the
# fake SQL clients - the detail page (owner, size, table count) and the
# users list run their own queries, and the SQL console needs a query that
# answers with rows instead of nothing. Each engine manager's exact SQL text
# is matched by a fragment unique to that query, so the generic listing
# queries below (kept as they were) still answer everything else.
# ---------------------------------------------------------------------------

#: PostgreSQL databases the demo machine knows about: owner and size in bytes,
#: matching what `example_production` and `example_staging` list as.
_PG_DATABASES: dict[str, tuple[str, int]] = {
    "postgres": ("postgres", 7553827),
    "example_production": ("wasm_app", 48218931),
    "example_staging": ("wasm_app", 9120563),
}

#: PostgreSQL roles `list_users` reports: name, superuser, createdb, createrole,
#: and (5th column) the databases it may connect to, comma-separated - the same
#: shape `PostgresManager.list_users`'s combined query now answers in one row.
_PG_USERS = (
    "postgres|t|t|t|postgres,example_production,example_staging\n"
    "wasm_app|f|f|f|example_production,example_staging\n"
    "wasm_readonly|f|f|f|example_production,example_staging\n"
)

#: A read the SQL console can run against any PostgreSQL database, pipe-separated
#: the way `psql -t -A` prints it (no header row: see databases.py's docstring).
_PG_DEMO_ROWS = (
    "1024|maria@example.com|129.90\n1025|jon@example.com|54.00\n1026|priya@example.com|312.40\n"
)

#: The same read, in the structured console's shape: a header row, comma
#: separated, the way `psql --csv` prints it (see execute_query_structured).
_PG_DEMO_ROWS_CSV = (
    "id,email,total\n1024,maria@example.com,129.90\n1025,jon@example.com,54.00\n"
    "1026,priya@example.com,312.40\n"
)

#: MySQL/MariaDB schemas the demo machine knows about: size in bytes, table count.
_MYSQL_DATABASES: dict[str, tuple[int, int]] = {
    "shop_wp": (52_428_800, 18),
    "example_shop": (23_068_672, 11),
}

#: MySQL users `list_users` reports: name, host and (3rd column) the databases
#: it has database-level grants on, comma-separated - the same shape
#: `MySQLManager.list_users`'s combined query now answers in one row.
_MYSQL_USERS = "root\tlocalhost\t\nshop\t%\tshop_wp\nexample\t%\texample_shop\n"

#: The same read as `_PG_DEMO_ROWS`, tab-separated the way `mysql -N -B` prints it.
_MYSQL_DEMO_ROWS = "1024\tmaria@example.com\t129.90\n1025\tjon@example.com\t54.00\n1026\tpriya@example.com\t312.40\n"

#: The same read, in the structured console's shape: a header row, tab
#: separated, the way `mysql -B` (without -N) prints it.
_MYSQL_DEMO_ROWS_HEADERS = (
    "id\temail\ttotal\n1024\tmaria@example.com\t129.90\n1025\tjon@example.com\t54.00\n"
    "1026\tpriya@example.com\t312.40\n"
)

#: Matches the literal a `WHERE datname = '...'` or `WHERE SCHEMA_NAME = '...'`
#: clause quotes, so a canned answer can be specific to the database it was
#: asked about instead of always answering as the first one in the list.
_SQL_LITERAL = re.compile(r"=\s*'([^']*)'")


def _psql_console_sql(args: Sequence[str]) -> str:
    """
    Recover the SQL a psql invocation carries in its ``-c`` options.

    The console statement reaches psql as ``-c`` strings rather than on stdin
    (see PostgresManager._console_argv), so the model reads it from argv.

    Args:
        args: The psql argv.

    Returns:
        The ``-c`` strings joined by newlines, or "" when there are none.
    """
    return "\n".join(args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "-c")


def _sql_literal(statement: str) -> str | None:
    """
    Args:
        statement: SQL text containing a single-quoted literal.

    Returns:
        The literal's contents, or None when the statement has none.
    """
    match = _SQL_LITERAL.search(statement)
    return match.group(1) if match else None


def _sql_identifier(statement: str, quote: str, *, after: str | None = None) -> str | None:
    """
    Extracts a quoted identifier from a CREATE/DROP DATABASE statement.

    Args:
        statement: SQL text.
        quote: The quote character the engine uses for identifiers: `"` for
            PostgreSQL (`_escape_identifier`), `` ` `` for MySQL/MariaDB.
        after: When given, only matches the identifier immediately following
            this keyword (`"OWNER"`), for the optional owner clause; the
            first quoted identifier in the statement otherwise.

    Returns:
        The identifier, with its doubled escape quote un-escaped, or None
        when the statement holds no such identifier.
    """
    lead = rf"{re.escape(after)}\s+" if after else ""
    q = re.escape(quote)
    match = re.search(rf"{lead}{q}((?:[^{q}]|{q}{q})*){q}", statement)
    if not match:
        return None
    return match.group(1).replace(quote + quote, quote)


# ---------------------------------------------------------------------------
# Console: engine settings and database containers (3.3). An engine's settings page
# reads the cluster and the engine's own answers (pg_settings, SHOW GLOBAL VARIABLES)
# and writes Noust's file under the host tree; the model reads that same file back, so a
# change made through the API is what the next read reports, as after a real restart.
# A database container is a Compose service of the modelled machine: Docker's listing,
# inspect, start/stop and the client Noust runs inside it, answered from one model.
# ---------------------------------------------------------------------------

#: Debian's ``pg_lsclusters --no-header`` for the one cluster the modelled machine has:
#: version, name, port, status, owner, data directory, log file.
_PG_CLUSTER_LINE = (
    "16  main    5432 online postgres /var/lib/postgresql/16/main "
    "/var/log/postgresql/postgresql-16-main.log\n"
)

#: The cluster's own systemd unit, which a settings change restarts. systemd runs it as a
#: template instance; the model answers it as the machine's ``postgresql`` unit.
_PG_CLUSTER_UNIT = "postgresql@16-main"

#: The cluster's configuration directory, as it is on the machine.
_PG_CONFIG_DIR = "/etc/postgresql/16/main"

#: What ``pg_settings`` reports for each setting Noust edits before it writes anything:
#: the value as ``current_setting`` prints it, the ``context`` (``postmaster`` needs a
#: restart) and the file that sets it ("" for the compiled-in default).
_PG_SETTING_DEFAULTS: dict[str, tuple[str, str, str]] = {
    "listen_addresses": ("localhost", "postmaster", ""),
    "port": ("5432", "postmaster", f"{_PG_CONFIG_DIR}/postgresql.conf"),
    "max_connections": ("100", "postmaster", f"{_PG_CONFIG_DIR}/postgresql.conf"),
    "shared_buffers": ("128MB", "postmaster", f"{_PG_CONFIG_DIR}/postgresql.conf"),
    "effective_cache_size": ("4GB", "user", ""),
    "work_mem": ("4MB", "user", ""),
    "maintenance_work_mem": ("64MB", "user", ""),
    "log_min_duration_statement": ("-1", "superuser", ""),
    "timezone": ("Europe/Madrid", "user", f"{_PG_CONFIG_DIR}/postgresql.conf"),
}

#: What ``SHOW GLOBAL VARIABLES`` reports for each variable Noust edits on a stock MySQL 8.0
#: (sizes in bytes, ``long_query_time`` with its six decimals).
_MYSQL_VARIABLE_DEFAULTS: dict[str, str] = {
    "bind_address": "127.0.0.1",
    "port": "3306",
    "max_connections": "151",
    "innodb_buffer_pool_size": "134217728",
    "innodb_log_file_size": "50331648",
    "slow_query_log": "OFF",
    "long_query_time": "10.000000",
    "character_set_server": "utf8mb4",
    "time_zone": "SYSTEM",
}

#: The file each of Noust's settings writes, below the host tree.
_PG_NOUST_FILE = f"{_PG_CONFIG_DIR}/conf.d/90-noust.conf"
_MYSQL_NOUST_FILE = "/etc/mysql/mysql.conf.d/99-noust.cnf"

#: The modelled server's memory and processors, which the recommendations are computed
#: from: a 2 GiB VPS, as the Server area describes it, whatever machine runs this script.
_MODELLED_MEMORY_BYTES = 2 * 1024**3
_MODELLED_CPUS = 2


def _file_assignments(path: Path) -> dict[str, str]:
    """
    Read the ``key = value`` lines of a file the modelled engine reads.

    Args:
        path: The file; a missing one sets nothing.

    Returns:
        The last value of each key, without quotes or a trailing comment.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "[")):
            continue
        key, separator, value = stripped.partition("=")
        if separator:
            found[key.strip()] = value.split(" #", 1)[0].strip().strip("'\"")
    return found


def _postgres_settings(host: Path | None) -> dict[str, tuple[str, str, str]]:
    """
    What the modelled PostgreSQL reports: its defaults, with Noust's file over them.

    Args:
        host: The host tree's root; None answers the defaults alone.

    Returns:
        ``(value, context, source file)`` by setting.
    """
    settings = dict(_PG_SETTING_DEFAULTS)
    written = _file_assignments(host / _PG_NOUST_FILE.lstrip("/")) if host else {}
    for key, value in written.items():
        if key in settings:
            settings[key] = (value, settings[key][1], _PG_NOUST_FILE)
    return settings


def _mysql_bytes(value: str) -> str:
    """
    Turn an option file's size (``256M``) into the bytes a variable reports.

    Args:
        value: The option as written.

    Returns:
        The decimal bytes, or the text itself when it is not a size.
    """
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    if value[-1:].upper() in units and value[:-1].isdigit():
        return str(int(value[:-1]) * units[value[-1].upper()])
    return value


def _mysql_variables(host: Path | None) -> dict[str, str]:
    """
    What the modelled MySQL reports: its defaults, with Noust's file over them.

    Args:
        host: The host tree's root; None answers the defaults alone.

    Returns:
        The value of each variable, as ``SHOW GLOBAL VARIABLES`` prints it.
    """
    variables = dict(_MYSQL_VARIABLE_DEFAULTS)
    written = _file_assignments(host / _MYSQL_NOUST_FILE.lstrip("/")) if host else {}
    for option, value in written.items():
        name = "time_zone" if option == "default-time-zone" else option.replace("-", "_")
        if name not in variables:
            continue
        if name in ("innodb_buffer_pool_size", "innodb_log_file_size"):
            value = _mysql_bytes(value)
        elif name == "long_query_time":
            value = f"{float(value):.6f}"
        variables[name] = value
    return variables


def _host_tree() -> Path | None:
    """
    Returns:
        The modelled server's host tree, once the Server area's model is built.
    """
    return _SERVER_HOST.root if _SERVER_HOST is not None else None


#: The Compose project of the modelled machine's one database container. It is the name of
#: a seeded application's directory (``catalogo.example.org``), which is how the console
#: tells whose database it is: ``assign_apps`` matches a project to ``app_name``.
DB_CONTAINER_PROJECT = "catalogo-example-org"

#: The Compose service and the container Compose names after it.
DB_CONTAINER_SERVICE = "postgres"
DB_CONTAINER_NAME = f"{DB_CONTAINER_PROJECT}-{DB_CONTAINER_SERVICE}-1"

#: The database that container's Postgres holds, and the account that owns it (the image's
#: ``POSTGRES_USER`` is its superuser).
DB_CONTAINER_DATABASE = "catalogo"

#: The host port the container publishes its 5432 on: the host's own PostgreSQL has 5432.
DB_CONTAINER_HOST_PORT = 5433


@dataclass
class _DatabaseContainer:
    """
    One container of the modelled machine that runs a database.

    Attributes:
        name: The container's name.
        image: The image it was created from.
        project: Its Compose project.
        service: Its Compose service.
        env: Its environment, as ``docker inspect`` lists it.
        container_port: The port its engine listens on inside it.
        host_port: The host port that is published to it.
        databases: The databases its engine holds: owner and size in bytes.
        users: What its role listing answers.
        state: ``running`` or ``exited``; ``docker start`` and ``stop`` change it.
    """

    name: str
    image: str
    project: str
    service: str
    env: tuple[str, ...]
    container_port: int
    host_port: int
    databases: dict[str, tuple[str, int]]
    users: str
    state: str = "running"

    @property
    def container_id(self) -> str:
        """The 64 hex digits Docker would give it, fixed by its name."""
        return hashlib.sha256(self.name.encode()).hexdigest()

    def inspect(self) -> dict[str, Any]:
        """
        Returns:
            What ``docker inspect`` prints for it: only the fields Noust reads.
        """
        port = f"{self.container_port}/tcp"
        binding = [{"HostIp": "127.0.0.1", "HostPort": str(self.host_port)}]
        return {
            "Id": self.container_id,
            "Name": f"/{self.name}",
            "State": {"Status": self.state, "Running": self.state == "running"},
            "Config": {
                "Image": self.image,
                "Env": list(self.env),
                "ExposedPorts": {port: {}},
                "Labels": {
                    "com.docker.compose.project": self.project,
                    "com.docker.compose.service": self.service,
                    "com.docker.compose.container-number": "1",
                    "com.docker.compose.project.working_dir": f"/var/www/apps/{self.project}",
                },
            },
            "NetworkSettings": {"Ports": {port: binding} if self.state == "running" else {}},
            "HostConfig": {"PortBindings": {port: binding}},
        }


class _DatabaseContainers:
    """Docker's answers about the machine's database containers, and what runs in them."""

    #: How ``docker ps`` is asked: every container, with the id and image Noust filters on.
    LISTING = ("docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}}\t{{.Image}}")

    def __init__(self) -> None:
        """Model the one container: Postgres 16 of ``catalogo.example.org``'s Compose project."""
        self.lock = threading.Lock()
        catalogo = _DatabaseContainer(
            name=DB_CONTAINER_NAME,
            image="postgres:16-alpine",
            project=DB_CONTAINER_PROJECT,
            service=DB_CONTAINER_SERVICE,
            env=(
                "POSTGRES_USER=catalogo",
                f"POSTGRES_DB={DB_CONTAINER_DATABASE}",
                "POSTGRES_PASSWORD=Cat-demo-4Rn8",
                "PGDATA=/var/lib/postgresql/data",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            ),
            container_port=5432,
            host_port=DB_CONTAINER_HOST_PORT,
            databases={
                "postgres": ("catalogo", 7_553_827),
                DB_CONTAINER_DATABASE: ("catalogo", 31_457_280),
            },
            users=f"catalogo|t|t|t|postgres,{DB_CONTAINER_DATABASE}\n",
        )
        self.containers = {catalogo.name: catalogo}

    def listing(self) -> str:
        """
        Returns:
            What ``docker ps -a --no-trunc --format '{{.ID}}\\t{{.Image}}'`` prints.
        """
        with self.lock:
            return "".join(f"{c.container_id}\t{c.image}\n" for c in self.containers.values())

    def inspect(self, ids: Sequence[str]) -> str | None:
        """
        Args:
            ids: The arguments of ``docker inspect``.

        Returns:
            Its JSON for the containers named, or None when an argument names none of the
            modelled ones (another caller's question, left to the rest of the runner).
        """
        with self.lock:
            found = [c for c in self.containers.values() if c.container_id in ids or c.name in ids]
            if not ids or len(found) != len(ids):
                return None
            return json.dumps([c.inspect() for c in found])

    def named(self, name: str) -> _DatabaseContainer | None:
        """
        Args:
            name: A container's name or id.

        Returns:
            The modelled container, or None.
        """
        with self.lock:
            return next(
                (c for c in self.containers.values() if name in (c.name, c.container_id)), None
            )

    def exec_target(
        self, args: tuple[str, ...]
    ) -> tuple[_DatabaseContainer, tuple[str, ...]] | None:
        """
        Read the ``docker exec`` Noust builds for a client inside a database container.

        Args:
            args: The argv.

        Returns:
            The container and the command that runs in it (program first), or None when
            this is not such an exec.
        """
        from noust.managers.database.instances import CLIENT_SCRIPT

        if args[:2] != ("docker", "exec") or CLIENT_SCRIPT not in args:
            return None
        position = args.index(CLIENT_SCRIPT)
        container = self.named(args[position - 3]) if position >= 3 else None
        # After the script: "sh", the mode, the variable, the sources, then the programs to
        # try (comma separated, the first preferred) and the program's own arguments.
        if container is None or len(args) < position + 6:
            return None
        return container, (args[position + 5].split(",")[0], *args[position + 6 :])

    def answer(self, args: tuple[str, ...]) -> tuple[int, str, str] | None:
        """
        Answer a ``docker`` command about a modelled container.

        Args:
            args: The argv.

        Returns:
            ``(exit code, stdout, stderr)``, or None for any other docker command.
        """
        verb = args[1] if len(args) > 1 else ""
        if args == self.LISTING:
            return 0, self.listing(), ""
        if verb == "inspect" and not any(a.startswith("-") for a in args[2:]):
            printed = self.inspect(args[2:])
            return (0, printed, "") if printed is not None else None
        container = self.named(args[-1]) if len(args) > 2 else None
        if container is None:
            return None
        if verb in ("start", "restart"):
            container.state = "running"
            return 0, f"{container.name}\n", ""
        if verb == "stop":
            container.state = "exited"
            return 0, f"{container.name}\n", ""
        if verb == "logs":
            started = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            return (
                0,
                "",
                f"{started}.318 UTC [1] LOG:  starting PostgreSQL 16.4 on x86_64-pc-linux-musl\n"
                f'{started}.318 UTC [1] LOG:  listening on IPv4 address "0.0.0.0", port 5432\n'
                f"{started}.331 UTC [1] LOG:  database system is ready to accept connections\n",
            )
        return None


# ---------------------------------------------------------------------------
# Console: what a database's own page reads (3.1). The data browser, the row
# editor, the metrics, the access list and the dumps run their own catalog
# queries, answered here from a small model of example_production's tables:
# each query is matched by a fragment only it contains, before the generic
# listing answers of _sql.
# ---------------------------------------------------------------------------

#: The one database the model has tables for.
_DEMO_DATABASE = "example_production"

#: A column: name, type as printed, pg_type.typname, typcategory, nullable,
#: default, generated.
_DemoColumn = tuple[str, str, str, str, bool, str | None, bool]


def _demo_catalog() -> dict[tuple[str, str], dict[str, Any]]:
    """
    Build the model's tables, with their rows.

    Returns:
        ``(schema, name)`` to the relation: its kind, columns, primary key,
        indexes, constraints and rows (column name to Python value).
    """
    now = datetime(2026, 9, 29, 17, 42, 11, tzinfo=timezone.utc)
    names = ("Maria", "Jon", "Priya", "Lucas", "Aiko", "Omar", "Sofia", "Ines", "Tomas", "Lena")
    countries = ("ES", "PT", "FR", "DE", None, "IE", "NL")
    customers = [
        {
            "id": index,
            "email": f"{names[index % len(names)].lower()}{index}@example.com",
            "name": f"{names[index % len(names)]} {chr(65 + index % 26)}.",
            "country": countries[index % len(countries)],
            "created_at": (now - timedelta(days=400 - index)).isoformat(),
        }
        for index in range(1, 241)
    ]
    statuses = ("paid", "paid", "shipped", "pending", "refunded", "paid", "shipped")
    long_note = ("Customer asked to split the delivery. " * 70).strip()
    orders = []
    for index in range(1284):
        status = statuses[index % len(statuses)]
        receipt = None
        if index % 9 == 0:
            receipt = bytes.fromhex(
                "255044462d312e370a25e2e3cfd30a312030206f626a0a3c3c2f547970652f436174"
            )
        orders.append(
            {
                "id": 1001 + index,
                "customer_id": 1 + (index * 7) % 240,
                "total": round(((index * 37) % 500) + 9.9, 2),
                "status": status,
                "paid": status in ("paid", "shipped", "refunded"),
                "created_at": (now - timedelta(hours=3 * (1284 - index))).isoformat(),
                "notes": long_note if index == 1280 else ("Gift wrap" if index % 13 == 0 else None),
                "metadata": json.dumps(
                    {
                        "channel": "web" if index % 3 else "shop",
                        "coupon": None if index % 5 else "AUTUMN10",
                    }
                ),
                "receipt": receipt,
            }
        )
    items = [
        {
            "order_id": 1001 + index // 3,
            "sku": f"SKU-{(index * 11) % 97:03d}",
            "quantity": 1 + index % 4,
            "unit_price": round(4.5 + (index % 40) * 1.25, 2),
        }
        for index in range(3000)
    ]
    events = [
        {
            "at": (now - timedelta(minutes=17 * index)).isoformat(),
            "actor": ("worker", "web", "cron")[index % 3],
            "action": ("login", "checkout", "export", "refund")[index % 4],
        }
        for index in range(500)
    ]
    revenue = [
        {
            "day": (now - timedelta(days=index)).date().isoformat(),
            "orders": 8 + (index * 5) % 17,
            "revenue": round(420 + (index * 97) % 900 + 0.5, 2),
        }
        for index in range(90)
    ]
    bigint = ("bigint", "int8", "N")
    text = ("text", "text", "S")
    stamp = ("timestamp with time zone", "timestamptz", "D")

    def col(
        name: str,
        kind: tuple[str, str, str],
        nullable: bool = False,
        default: str | None = None,
        generated: bool = False,
    ) -> _DemoColumn:
        return (name, kind[0], kind[1], kind[2], nullable, default, generated)

    return {
        ("public", "customers"): {
            "kind": "table",
            "columns": [
                col("id", bigint, generated=True),
                col("email", text),
                col("name", text),
                col("country", text, True),
                col("created_at", stamp, default="now()"),
            ],
            "primary_key": ["id"],
            "indexes": [
                ("customers_pkey", ["id"], True, True),
                ("customers_email_key", ["email"], True, False),
            ],
            "constraints": [
                ("customers_pkey", "p", "PRIMARY KEY (id)"),
                ("customers_email_key", "u", "UNIQUE (email)"),
            ],
            "rows": customers,
            "size": 90_112,
        },
        ("public", "orders"): {
            "kind": "table",
            "columns": [
                col("id", bigint, default="nextval('orders_id_seq'::regclass)"),
                col("customer_id", bigint),
                col("total", ("numeric(10,2)", "numeric", "N")),
                col("status", text, default="'pending'::text"),
                col("paid", ("boolean", "bool", "B"), default="false"),
                col("created_at", stamp, default="now()"),
                col("notes", text, True),
                col("metadata", ("jsonb", "jsonb", "U"), default="'{}'::jsonb"),
                col("receipt", ("bytea", "bytea", "U"), True),
            ],
            "primary_key": ["id"],
            "indexes": [
                ("orders_pkey", ["id"], True, True),
                ("orders_customer_id_idx", ["customer_id"], False, False),
                ("orders_created_at_idx", ["created_at"], False, False),
            ],
            "constraints": [
                (
                    "orders_customer_id_fkey",
                    "f",
                    "FOREIGN KEY (customer_id) REFERENCES customers(id)",
                ),
                ("orders_pkey", "p", "PRIMARY KEY (id)"),
                ("orders_total_check", "c", "CHECK ((total >= (0)::numeric))"),
            ],
            "rows": orders,
            "size": 1_556_480,
        },
        ("public", "order_items"): {
            "kind": "table",
            "columns": [
                col("order_id", bigint),
                col("sku", text),
                col("quantity", ("integer", "int4", "N"), default="1"),
                col("unit_price", ("numeric(10,2)", "numeric", "N")),
            ],
            "primary_key": ["order_id", "sku"],
            "indexes": [("order_items_pkey", ["order_id", "sku"], True, True)],
            "constraints": [
                ("order_items_order_id_fkey", "f", "FOREIGN KEY (order_id) REFERENCES orders(id)"),
                ("order_items_pkey", "p", "PRIMARY KEY (order_id, sku)"),
            ],
            "rows": items,
            "size": 434_176,
        },
        ("public", "audit_events"): {
            "kind": "table",
            "columns": [col("at", stamp, default="now()"), col("actor", text), col("action", text)],
            "primary_key": [],
            "indexes": [],
            "constraints": [],
            "rows": events,
            "size": 73_728,
        },
        ("public", "paid_orders"): {
            "kind": "view",
            "columns": [
                col("id", bigint, True),
                col("total", ("numeric(10,2)", "numeric", "N"), True),
                col("created_at", stamp, True),
            ],
            "primary_key": [],
            "indexes": [],
            "constraints": [],
            "rows": [
                {"id": row["id"], "total": row["total"], "created_at": row["created_at"]}
                for row in orders
                if row["paid"]
            ],
            "size": None,
        },
        ("analytics", "daily_revenue"): {
            "kind": "table",
            "columns": [
                col("day", ("date", "date", "D")),
                col("orders", ("integer", "int4", "N")),
                col("revenue", ("numeric(12,2)", "numeric", "N")),
            ],
            "primary_key": ["day"],
            "indexes": [("daily_revenue_pkey", ["day"], True, True)],
            "constraints": [("daily_revenue_pkey", "p", "PRIMARY KEY (day)")],
            "rows": revenue,
            "size": 16_384,
        },
    }


_DEMO_KINDS = {"N": "numeric", "B": "boolean", "D": "datetime", "S": "text", "A": "array"}


def _demo_kind(column: _DemoColumn) -> str:
    """How the dialect classifies a model column (PostgresDialect.kind)."""
    if column[2] in ("json", "jsonb"):
        return "json"
    if column[2] == "bytea":
        return "binary"
    return _DEMO_KINDS.get(column[3], "other")


def _demo_literal(text: str) -> str:
    """The value inside a PostgreSQL literal ('...' or E'...')."""
    body = text[1:] if text.startswith(("E'", "e'")) else text
    body = body[1:-1] if body.startswith("'") and body.endswith("'") else body
    value = body.replace("''", "'")
    return value.replace("\\\\", "\\") if text.startswith(("E'", "e'")) else value


def _demo_cell(value: Any, kind: str) -> tuple[Any, bool]:
    """A value as the browser's SQL renders it, and whether it was cut."""
    if value is None:
        return None, False
    if kind == "binary":
        raw = value if isinstance(value, bytes) else b""
        return {"bytes": len(raw) * 64, "hex": raw[:32].hex()}, False
    if kind in ("numeric", "boolean"):
        return value, False
    text = str(value)
    if kind in ("text", "json", "array", "other"):
        return text[:2000], len(text) > 2000
    return text, False


def _demo_matches(row: dict[str, Any], condition: str, columns: dict[str, _DemoColumn]) -> bool:
    """Whether a row satisfies one condition of PostgresDialect.condition's shapes."""
    condition = condition.strip()
    match = re.fullmatch(r'"(.+?)" IS (NOT )?NULL', condition)
    if match:
        return (row.get(match.group(1)) is None) != bool(match.group(2))
    match = re.fullmatch(r'"(.+?)"::text (I?LIKE) (E?\'.*\')', condition)
    if match:
        pattern = (
            "^"
            + re.escape(_demo_literal(match.group(3))).replace("%", ".*").replace("_", ".")
            + "$"
        )
        value = row.get(match.group(1))
        flags = re.IGNORECASE if match.group(2) == "ILIKE" else 0
        return value is not None and re.search(pattern, str(value), flags) is not None
    match = re.fullmatch(r'"(.+?)" IN \((.*)\)', condition)
    if match:
        options = [
            _demo_literal(item.strip()) for item in re.findall(r"E?'(?:[^']|'')*'", match.group(2))
        ]
        return str(row.get(match.group(1))) in options
    match = re.fullmatch(r"\((.+?)\) ([<>]) \((.+)\)", condition)
    if match:
        names = re.findall(r'"(.+?)"', match.group(1))
        wanted = [_demo_literal(item) for item in re.findall(r"E?'(?:[^']|'')*'", match.group(3))]
        have = tuple(_demo_sortable(row.get(name), columns[name]) for name in names)
        other = tuple(
            _demo_sortable(_demo_typed(text, columns[name]), columns[name])
            for name, text in zip(names, wanted, strict=False)
        )
        return have > other if match.group(2) == ">" else have < other
    match = re.fullmatch(r'"(.+?)" (=|<>|<=|>=|<|>) (E?\'.*\')', condition)
    if match:
        name, operator = match.group(1), match.group(2)
        column = columns.get(name)
        if column is None:
            return True
        left = _demo_sortable(row.get(name), column)
        right = _demo_sortable(_demo_typed(_demo_literal(match.group(3)), column), column)
        if row.get(name) is None:
            return False
        return {
            "=": left == right,
            "<>": left != right,
            "<": left < right,
            "<=": left <= right,
            ">": left > right,
            ">=": left >= right,
        }[operator]
    return True


def _demo_typed(text: str, column: _DemoColumn) -> Any:
    """A literal's text as the model stores the column's values."""
    kind = _demo_kind(column)
    if kind == "numeric":
        try:
            number = float(text)
        except ValueError:
            return text
        return int(number) if number.is_integer() and "." not in text else number
    if kind == "boolean":
        return text.lower() in ("true", "t", "1")
    return text


def _demo_sortable(value: Any, column: _DemoColumn) -> Any:
    """A value made comparable with the others of its column."""
    if value is None:
        return (1, 0)
    if _demo_kind(column) == "numeric":
        try:
            return (0, float(value))
        except (TypeError, ValueError):
            return (0, 0.0)
    return (0, str(value))


class _DemoDatabase:
    """example_production as the data browser, the metrics and the access list read it."""

    def __init__(self) -> None:
        self.relations = _demo_catalog()
        self.lock = threading.Lock()
        # Roles the console created this run: a new user is looked up right after it is made.
        self.roles: set[str] = set()

    def answer(
        self,
        database: str | None,
        statement: str,
        *,
        csv: bool,
        container: _DatabaseContainer | None = None,
    ) -> str | None:
        """
        Answer a statement the database page sends, or None to leave it to _sql.

        Args:
            database: The database the client was pointed at (``-d``).
            statement: The SQL.
            csv: The structured console asked for a header row.
            container: The database container the client runs in, whose databases and
                roles the server-wide answers describe instead of the host's.

        Returns:
            What psql prints, or None.
        """
        mine = database == _DEMO_DATABASE
        created = re.match(r'\s*CREATE (?:ROLE|USER) "?([A-Za-z0-9_]+)"?', statement)
        if created:
            self.roles.add(created.group(1))
            return None
        asked = re.match(r"\s*SELECT 1 FROM pg_roles WHERE rolname = '([^']*)'", statement)
        if asked and asked.group(1) in self.roles:
            return "1\n"
        if "'schemas', coalesce((SELECT json_agg(n.nspname" in statement:
            return json.dumps(self.catalog() if mine else {"schemas": ["public"], "relations": []})
        if "'columns', coalesce((SELECT json_agg(json_build_object(" in statement and mine:
            return self.describe(statement)
        if "SELECT json_build_object('rows'" in statement and mine:
            return self.rows(statement)
        if "noust_row_change" in statement and mine:
            return self.edit(statement)
        if container is not None and "'blks_hit', (SELECT sum(blks_hit)" in statement:
            return json.dumps(
                {
                    "connections": 2,
                    "max_connections": 100,
                    "blks_hit": 4_120_311,
                    "blks_read": 20_311,
                    "databases": [
                        {
                            "name": name,
                            "size_bytes": size,
                            "connections": 2,
                            "xact": 318_207,
                            "blks_hit": 4_120_311,
                            "blks_read": 20_311,
                        }
                        for name, (_owner, size) in container.databases.items()
                        if name != "postgres"
                    ],
                }
            )
        if "'blks_hit', (SELECT sum(blks_hit) FROM pg_catalog.pg_stat_database)" in statement:
            return json.dumps(
                {
                    "connections": 9,
                    "max_connections": 100,
                    "blks_hit": 91_827_345,
                    "blks_read": 120_311,
                    "databases": [
                        {
                            "name": "example_production",
                            "size_bytes": 48_218_931,
                            "connections": 6,
                            "xact": 1_829_384,
                            "blks_hit": 77_123_450,
                            "blks_read": 98_765,
                        },
                        {
                            "name": "example_staging",
                            "size_bytes": 9_120_563,
                            "connections": 1,
                            "xact": 45_012,
                            "blks_hit": 1_203_111,
                            "blks_read": 9_871,
                        },
                    ],
                }
            )
        if "'server_connections', (SELECT count(*) FROM pg_catalog.pg_stat_activity" in statement:
            size = container.databases.get(database or "", (None, 0))[1] if container else None
            return self.database_metrics(mine, size_bytes=size)
        if "e.extname = 'pg_stat_statements'" in statement:
            return json.dumps({"schema": "public" if mine else None, "preloaded": mine})
        if "pg_stat_statements p" in statement:
            return json.dumps(
                [
                    {
                        "query": "SELECT o.*, c.email FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.created_at > $1 ORDER BY o.created_at DESC",
                        "calls": 18_422,
                        "total_ms": 1_214_993.2,
                        "mean_ms": 65.95,
                        "rows": 921_100,
                    },
                    {
                        "query": "UPDATE orders SET status = $1, paid = $2 WHERE id = $3",
                        "calls": 4_102,
                        "total_ms": 61_530.4,
                        "mean_ms": 15.0,
                        "rows": 4_102,
                    },
                    {
                        "query": "SELECT count(*) FROM order_items WHERE order_id = ANY($1)",
                        "calls": 9_870,
                        "total_ms": 88_830.0,
                        "mean_ms": 9.0,
                        "rows": 9_870,
                    },
                    {
                        "query": "INSERT INTO audit_events (actor, action) VALUES ($1, $2)",
                        "calls": 52_004,
                        "total_ms": 104_008.1,
                        "mean_ms": 2.0,
                        "rows": 52_004,
                    },
                ]
            )
        if "aclexplode(d.defaclacl)" in statement:
            if container is not None:
                # The image's POSTGRES_USER owns what it created and is the superuser.
                return "catalogo|t|t|0|0|0|f|f\n"
            if not mine:
                return "wasm_app|t|f|0|0|0|f|f\npostgres|f|t|0|0|0|f|f\n"
            return (
                "analytics_reader|f|f|5|5|0|f|f\n"
                "example_production|f|f|5|5|5|t|t\n"
                "postgres|f|t|5|5|5|f|f\n"
                "wasm_app|t|f|5|5|5|f|f\n"
                "wasm_ro_example_production|f|f|5|5|0|t|f\n"
            )
        if mine and re.search(r"^\s*EXPLAIN\b", statement, re.IGNORECASE | re.MULTILINE):
            analyze = "ANALYZE" in statement.upper()
            node: dict[str, Any] = {
                "Node Type": "Limit",
                "Total Cost": 12.4,
                "Plan Rows": 20,
                "Plans": [
                    {
                        "Node Type": "Index Scan Backward",
                        "Relation Name": "orders",
                        "Index Name": "orders_created_at_idx",
                        "Total Cost": 88.1,
                        "Plan Rows": 1284,
                    }
                ],
            }
            if analyze:
                node["Actual Total Time"] = 0.412
                node["Plans"][0]["Actual Total Time"] = 0.388
            return json.dumps([{"Plan": node, "Planning Time": 0.11}])
        if mine and csv and re.search(r"\bfrom\s+(public\.)?orders\b", statement, re.IGNORECASE):
            rows = self.relations[("public", "orders")]["rows"][-20:][::-1]
            lines = ["id,customer_id,total,status,paid,created_at"]
            lines += [
                f"{row['id']},{row['customer_id']},{row['total']:.2f},{row['status']},{'t' if row['paid'] else 'f'},{row['created_at']}"
                for row in rows
            ]
            return "\n".join(lines) + "\n"
        return None

    def catalog(self) -> dict[str, Any]:
        """The schemas and relations, as PostgresDialect.catalog_sql answers."""
        return {
            "schemas": sorted({schema for schema, _ in self.relations}),
            "relations": [
                {
                    "schema": schema,
                    "name": name,
                    "kind": relation["kind"],
                    "rows_estimate": len(relation["rows"]) if relation["kind"] == "table" else None,
                    "size_bytes": relation["size"],
                }
                for (schema, name), relation in sorted(self.relations.items())
            ],
        }

    def _relation(self, statement: str) -> tuple[tuple[str, str], dict[str, Any]] | None:
        match = (
            re.search(r'FROM "([^"]+)"\."([^"]+)"', statement)
            or re.search(r'INTO "([^"]+)"\."([^"]+)"', statement)
            or re.search(r'UPDATE "([^"]+)"\."([^"]+)"', statement)
        )
        if match is None:
            literals = re.findall(r"n\.nspname = '([^']*)' AND r\.relname = '([^']*)'", statement)
            if not literals:
                return None
            key = literals[0]
        else:
            key = (match.group(1), match.group(2))
        relation = self.relations.get(key)
        return (key, relation) if relation is not None else None

    def describe(self, statement: str) -> str:
        """A relation's structure, as PostgresDialect.describe_sql answers."""
        found = self._relation(statement)
        if found is None:
            return ""
        _, relation = found
        return json.dumps(
            {
                "columns": [
                    {
                        "name": c[0],
                        "type": c[1],
                        "base": c[2],
                        "category": c[3],
                        "nullable": c[4],
                        "default": c[5],
                        "generated": c[6],
                    }
                    for c in relation["columns"]
                ],
                "primary_key": relation["primary_key"],
                "indexes": [
                    {
                        "name": name,
                        "columns": cols,
                        "unique": unique,
                        "primary": primary,
                        "definition": f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} ON {found[0][0]}.{found[0][1]} USING btree ({', '.join(cols)})",
                    }
                    for name, cols, unique, primary in relation["indexes"]
                ],
                "constraints": [
                    {"name": name, "type": kind, "definition": definition}
                    for name, kind, definition in relation["constraints"]
                ],
            }
        )

    def rows(self, statement: str) -> str:
        """One page of rows, as PostgresDialect.rows_sql answers."""
        found = self._relation(statement)
        if found is None:
            return json.dumps({"rows": []})
        _, relation = found
        columns = {column[0]: column for column in relation["columns"]}
        inner = statement[statement.index("FROM (SELECT * FROM") :]
        where = re.search(r" WHERE (.+?)(?: ORDER BY | LIMIT )", inner)
        conditions = re.split(r" AND (?=[\"(])", where.group(1)) if where else []
        with self.lock:
            rows = [
                row
                for row in relation["rows"]
                if all(_demo_matches(row, condition, columns) for condition in conditions)
            ]
        order = re.search(r"ORDER BY (.+?) LIMIT", inner)
        if order:
            for name, direction in reversed(re.findall(r'"(.+?)" (ASC|DESC)', order.group(1))):
                if name in columns:
                    rows.sort(
                        key=lambda row, n=name: _demo_sortable(row.get(n), columns[n]),
                        reverse=direction == "DESC",
                    )
        limit = int(re.search(r"LIMIT (\d+)", inner).group(1))  # type: ignore[union-attr]
        offset_match = re.search(r"OFFSET (\d+)", inner)
        offset = int(offset_match.group(1)) if offset_match else 0
        page = rows[offset : offset + limit]
        rendered = []
        for row in page:
            cells, flags = [], []
            for column in relation["columns"]:
                kind = _demo_kind(column)
                cell, cut = _demo_cell(row.get(column[0]), kind)
                cells.append(cell)
                if kind in ("text", "json", "array", "other"):
                    flags.append(cut)
            rendered.append(
                [cells, flags, [str(row.get(name)) for name in relation["primary_key"]]]
            )
        answer: dict[str, Any] = {"rows": rendered}
        if "'count', (SELECT count(*)" in statement:
            answer["count"] = len(rows)
        return json.dumps(answer)

    def edit(self, statement: str) -> str:
        """One row inserted, updated or deleted, as PostgresDialect._edit's script answers."""
        found = self._relation(statement)
        if found is None:
            return ""
        _, relation = found
        columns = {column[0]: column for column in relation["columns"]}

        def image(row: dict[str, Any] | None) -> Any:
            if row is None:
                return None
            return {
                name: (("\\x" + value.hex()) if isinstance(value, bytes) else value)
                for name, value in row.items()
            }

        def assigned(text: str) -> dict[str, Any]:
            values: dict[str, Any] = {}
            for name, literal in re.findall(r'"(\w+)" = (NULL|E?\'(?:[^\']|\'\')*\')', text):
                values[name] = (
                    None
                    if literal == "NULL"
                    else _demo_typed(_demo_literal(literal), columns[name])
                    if name in columns
                    else _demo_literal(literal)
                )
            return values

        with self.lock:
            rows: list[dict[str, Any]] = relation["rows"]
            update = re.search(r"AS t SET (.+?) WHERE (.+?) RETURNING", statement, re.DOTALL)
            if update:
                key = assigned(update.group(2))
                for row in rows:
                    if all(str(row.get(name)) == str(value) for name, value in key.items()):
                        before = dict(row)
                        row.update(assigned(update.group(1)))
                        return json.dumps({"before": image(before), "after": image(row)})
                return json.dumps({"before": None, "after": None})
            delete = re.search(r"DELETE FROM .+? AS t WHERE (.+?) RETURNING", statement, re.DOTALL)
            if delete:
                key = assigned(delete.group(1))
                for index, row in enumerate(rows):
                    if all(str(row.get(name)) == str(value) for name, value in key.items()):
                        rows.pop(index)
                        return json.dumps({"before": image(row), "after": None})
                return json.dumps({"before": None, "after": None})
            insert = re.search(r"AS t \((.+?)\) VALUES \((.+?)\) RETURNING", statement, re.DOTALL)
            new: dict[str, Any] = dict.fromkeys(columns)
            if insert:
                names = re.findall(r'"(\w+)"', insert.group(1))
                literals = re.findall(r"NULL|E?'(?:[^']|'')*'", insert.group(2))
                for name, literal in zip(names, literals, strict=False):
                    new[name] = (
                        None
                        if literal == "NULL"
                        else _demo_typed(_demo_literal(literal), columns[name])
                    )
            for name in relation["primary_key"]:
                if new.get(name) is None and _demo_kind(columns[name]) == "numeric":
                    new[name] = max((int(row.get(name) or 0) for row in rows), default=0) + 1
            if "created_at" in columns and new.get("created_at") is None:
                new["created_at"] = datetime.now(timezone.utc).isoformat()
            rows.append(new)
            return json.dumps({"before": None, "after": image(new)})

    def database_metrics(self, mine: bool, *, size_bytes: int | None = None) -> str:
        """A database's statistics, as the metrics reader's per-database query answers."""
        if not mine:
            return json.dumps(
                {
                    "size_bytes": size_bytes or 9_120_563,
                    "server_connections": 9,
                    "max_connections": 100,
                    "stat": {
                        "connections": 1,
                        "xact": 45_012,
                        "blks_hit": 1_203_111,
                        "blks_read": 9_871,
                        "deadlocks": 0,
                    },
                    "tables": [],
                }
            )
        tables = sorted(
            (
                {
                    "schema": schema,
                    "name": name,
                    "size_bytes": relation["size"],
                    "rows_estimate": len(relation["rows"]),
                }
                for (schema, name), relation in self.relations.items()
                if relation["kind"] == "table"
            ),
            key=lambda table: -(table["size_bytes"] or 0),
        )
        return json.dumps(
            {
                "size_bytes": 48_218_931,
                "server_connections": 9,
                "max_connections": 100,
                "stat": {
                    "connections": 6,
                    "xact": 1_829_384,
                    "blks_hit": 77_123_450,
                    "blks_read": 98_765,
                    "deadlocks": 2,
                },
                "tables": tables,
            }
        )


#: What pg_dump writes for the model: a custom-format archive's signature and some bytes.
_PG_DUMP_BYTES = "PGDMP\x01\x0e\x00\x04\x08\x01\x01\x01" + "x" * 4096

#: What pg_restore --list prints of it.
_PG_RESTORE_LIST = (
    ";\n; Archive created at 2026-09-29 02:00:04 UTC\n;     dbname: example_production\n;\n"
    "215; 1259 16390 TABLE public customers wasm_app\n"
    "216; 1259 16402 TABLE public orders wasm_app\n"
    "217; 1259 16420 TABLE public order_items wasm_app\n"
    "218; 1259 16431 TABLE public audit_events wasm_app\n"
    "219; 1259 16440 VIEW public paid_orders wasm_app\n"
    "220; 1259 16450 TABLE analytics daily_revenue wasm_app\n"
    "3310; 0 16390 TABLE DATA public customers wasm_app\n"
    "3311; 0 16402 TABLE DATA public orders wasm_app\n"
)


def _psql_database(args: Sequence[str]) -> str | None:
    """The database a psql invocation is pointed at (``-d NAME`` or ``--dbname=NAME``)."""
    for index, arg in enumerate(args):
        if arg == "-d" and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--dbname="):
            return arg.split("=", 1)[1]
    return None


def make_runner(
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    certs: list[str],
    systemd_dir: Path,
) -> Any:
    """
    Build the runner that answers for the modelled machine.

    Args:
        units: Unit name (without ``.service``) to its state; mutated by
            ``systemctl start``, ``stop`` and ``restart``.
        ports: Unit name to the port its application listens on.
        domains: Unit name to its application's domain.
        certs: Domains holding a certificate, for ``certbot certificates``.
        systemd_dir: The sandboxed unit directory, whose cron timers
            ``systemctl list-unit-files`` reports.

    Returns:
        A :class:`noust.core.runner.FakeRunner` answering from the model.
    """
    from noust.core.runner import CommandResult, FakeRunner, runuser_prefix

    lock = threading.Lock()

    def unit_of(argument: str) -> str:
        return argument.removesuffix(".service")

    def base_name(name: str) -> str:
        """The unit's name stripped of `.service` or `.timer`, for tracking that applies to both."""
        return name.removesuffix(".service").removesuffix(".timer")

    def template_of(name: str) -> Path | None:
        """
        The template file an instance (``shop@green``) is loaded from, when it is installed.

        systemd loads an instance from ``<prefix>@.service``; with no template on disk the
        instance does not exist, whatever it was before.
        """
        prefix, at, instance = name.partition("@")
        if not at or not prefix or not instance:
            return None
        template = systemd_dir / f"{prefix}@.service"
        return template if template.is_file() else None

    def instance_port(template: Path, instance: str) -> int | None:
        """
        The port an instance listens on: PORT in ``<colors dir>/<instance>.env``.

        The template names that file (``EnvironmentFile=<colors>/%i.env``); systemd expands
        ``%i`` to the instance, and the application reads PORT from it.
        """
        for line in template.read_text(encoding="utf-8").splitlines():
            if line.startswith("EnvironmentFile=") and line.endswith("/%i.env"):
                env_file = Path(line.split("=", 1)[1].lstrip("-").replace("%i", instance))
                try:
                    match = re.search(r"^PORT=(\d+)", env_file.read_text(encoding="utf-8"), re.M)
                except OSError:
                    return None
                return int(match.group(1)) if match else None
        return None

    # Enable/disable of a unit `_systemctl` does not otherwise model (a cron timer, or a
    # service created through the API this run rather than seeded into `units`): systemctl
    # enable/disable always succeeds against a real unit file, and `list-unit-files` and
    # `is-enabled` must be able to answer it afterwards. Keyed by base_name() so a `.timer`
    # and its paired `.service` - and a plain service - all agree. Absent means "enabled": a
    # freshly written unit is enabled by default, matching what `noust service create` and
    # `noust cron create` do on a real machine before this ever runs.
    enabled_overrides: dict[str, bool] = {}

    def ok(argv: tuple[str, ...], stdout: str = "", exit_code: int = 0) -> CommandResult:
        return CommandResult(argv=argv, exit_code=exit_code, stdout=stdout, stderr="")

    class ConsoleRunner(FakeRunner):
        """A FakeRunner that behaves like the seeded machine."""

        def __init__(self) -> None:
            """Start with a bounded call history."""
            super().__init__()
            # The model itself, for the fakes installed beside the runner (a health probe,
            # a port check) that must agree with it about what runs and where.
            self.model_units = units
            self.model_ports = ports
            self.model_domains = domains
            self.calls = deque(maxlen=CALL_HISTORY)  # type: ignore[assignment]
            self.inputs = deque(maxlen=CALL_HISTORY)  # type: ignore[assignment]
            self._stdin = threading.local()
            # What the modelled machine has installed. MongoDB and Docker are
            # deliberately absent, so the console shows an engine to install.
            self.only_knows(*INSTALLED_PROGRAMS, *SERVER_PROGRAMS)
            # Per-instance copies: the Databases page can create and drop
            # databases, and a stateless dict would have `create_database`
            # succeed and then have its own follow-up `get_database_info`
            # report "does not exist" (PostgresManager re-checks existence).
            self._pg_databases: dict[str, tuple[str, int]] = dict(_PG_DATABASES)
            self._mysql_databases: dict[str, tuple[int, int]] = dict(_MYSQL_DATABASES)
            # What a database's own page reads: its tables, rows, metrics, access.
            self._demo_database = _DemoDatabase()
            # Docker's database containers, and the one a command is being run inside.
            self._containers = _DatabaseContainers()
            self._inside = threading.local()

        def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
            # Noust's own SQL reaches the database clients on stdin; the psql
            # console statement arrives in -c instead (see _psql_console_sql).
            self._stdin.value = kwargs.get("input") or ""
            return super().run(argv, **kwargs)

        def _lookup(
            self,
            argv: Sequence[str],
            user: str | None = None,
            env: Mapping[str, str] | None = None,
            *,
            record: bool = True,
        ) -> CommandResult:
            args = tuple(str(a) for a in argv)
            recorded = (*runuser_prefix(user), *args) if user is not None else args
            if record:
                self.calls.append(recorded)
                self.envs.append(dict(env) if env is not None else None)
            # Answered as the command it runs: switching the account (runuser -u
            # postgres -- psql) asks the same question of the machine.
            program = args[0] if args else ""
            # The Server area's tools first: see model_server_host.
            server = _SERVER_HOST.answer(args) if _SERVER_HOST is not None else None
            if server is not None:
                return server
            with lock:
                if program == "systemctl":
                    return self._systemctl(args)
                if program == "journalctl":
                    return self._journal(args)
            if program == "docker":
                inside = self._containers.exec_target(args)
                if inside is not None:
                    return self._exec_in_container(args, *inside)
                answered = self._containers.answer(args)
                if answered is not None:
                    return CommandResult(args, *answered)
            if program == "pg_lsclusters":
                return ok(args, _PG_CLUSTER_LINE)
            if program.endswith("/bin/postgres") and "-C" in args:
                # postgres -C NAME prints the value its configuration files give NAME.
                asked = _postgres_settings(_host_tree()).get(args[args.index("-C") + 1])
                return ok(args, f"{asked[0]}\n" if asked else "")
            if program == "nginx":
                if "-v" in args:
                    return CommandResult(args, 0, "", "nginx version: nginx/1.24.0 (Ubuntu)\n")
                return CommandResult(
                    args,
                    0,
                    "",
                    "nginx: the configuration file /etc/nginx/nginx.conf syntax is ok\n"
                    "nginx: configuration file /etc/nginx/nginx.conf test is successful\n",
                )
            if "--version" in args and program in CLIENT_VERSIONS:
                return ok(args, CLIENT_VERSIONS[program])
            if program == "pg_dump":
                return ok(args, _PG_DUMP_BYTES)
            if program == "pg_restore" and "--list" in args:
                return ok(args, _PG_RESTORE_LIST)
            if program == "psql":
                demo = self._demo_database.answer(
                    _psql_database(args),
                    getattr(self._stdin, "value", "") or _psql_console_sql(args),
                    csv="--csv" in args,
                    container=getattr(self._inside, "container", None),
                )
                if demo is not None:
                    return ok(args, demo)
            if program in ("psql", "mysql", "redis-cli"):
                # The structured SQL console asks for a header row: psql with
                # `--csv`, mysql with `-B` alone (no `-N`). Every other caller
                # of these clients keeps the old headerless shape.
                headers = "--csv" in args or (
                    program == "mysql" and "-B" in args and "-N" not in args
                )
                statement = getattr(self._stdin, "value", "") or _psql_console_sql(args)
                return ok(args, self._sql(program, statement, headers=headers))
            if program == "certbot" and args[1:2] == ("certificates",):
                return ok(args, self._certificates())
            if program == "certbot" and "--version" in args:
                return ok(args, "certbot 2.9.0\n")
            if program == "openssl" and "-issuer" in args:
                # CertManager reads the issuer straight from the certificate
                # file through openssl; the model answers the same line real
                # Let's Encrypt leaf certificates carry, regardless of which
                # file it was pointed at.
                return ok(args, "issuer=C = US, O = Let's Encrypt, CN = R11\n")
            if program == "systemd-analyze" and args[1:2] == ("calendar",):
                return ok(args, self._systemd_analyze_calendar(args))
            if program == "systemd-analyze" and args[1:2] == ("verify",):
                return self._systemd_analyze_verify(args)
            if program == "systemd-analyze" and "--version" in args:
                return ok(args, "systemd 255 (255.4-1ubuntu8)\n")
            if program == "git":
                subject = _git_commit_subject_line(args)
                if subject is not None:
                    return ok(args, subject + "\n")
            if program == "ssh-keygen":
                return self._ssh_keygen(args)
            return ok(args)

        def _exec_in_container(
            self, args: tuple[str, ...], container: _DatabaseContainer, inner: tuple[str, ...]
        ) -> CommandResult:
            """
            Run a client inside a database container: the same answers as the host's engine,
            over the container's own databases.

            Args:
                args: The ``docker exec`` as Noust built it.
                container: The container it names.
                inner: The command that runs in it, program first.

            Returns:
                What the client prints, or Docker's refusal for a container that is stopped.
            """
            if container.state != "running":
                return CommandResult(
                    args,
                    1,
                    "",
                    f"Error response from daemon: container {container.container_id} "
                    "is not running\n",
                )
            self._inside.container = container
            try:
                return replace(self._lookup(inner, record=False), argv=args)
            finally:
                self._inside.container = None

        @staticmethod
        def _ssh_keygen(args: tuple[str, ...]) -> CommandResult:
            """
            Fake the fleet's own key pair (``NodeKeys.ensure_keypair``).

            Writes a structurally valid ed25519 pair, exactly as
            ``tests.fleet_support.KeygenRunner`` already does for pytest (rule 3: one
            implementation, reused here rather than copied). No real key material is
            needed: this console never opens a real SSH connection with it - see
            ``make_loopback_tunnels`` - so nothing ever reads these files for their
            cryptographic content, only for their shape and for the fingerprint the
            fleet's join code carries.
            """
            from tests.fleet_support import ed25519_line

            if "-f" not in args:
                return ok(args)
            path = Path(args[args.index("-f") + 1])
            comment = args[args.index("-C") + 1] if "-C" in args else ""
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"
            )
            path.chmod(0o600)
            # Deterministic within this process, and different per node name (the
            # comment noust always passes), which is all a fingerprint needs here.
            seed = hash(str(path)) & 0xFFFF
            Path(f"{path}.pub").write_text(ed25519_line(seed, comment) + "\n")
            return ok(args)

        def _systemctl(self, args: tuple[str, ...]) -> CommandResult:
            verb = next((a for a in args[1:] if not a.startswith("-")), "")
            targets = [a for a in args[2:] if not a.startswith("-") and a != verb]
            if verb == "--version" or "--version" in args:
                return ok(args, "systemd 255 (255.4-1ubuntu8)\n")
            if verb == "list-unit-files":
                return ok(args, self._unit_files(targets))
            if verb == "list-units":
                return ok(args, self._list_units(targets))
            name = unit_of(targets[-1]) if targets else ""
            if name == _PG_CLUSTER_UNIT:
                # The cluster's own unit is the machine's `postgresql`: one state for both.
                name = "postgresql"
            unit = units.get(name)
            template = template_of(name)
            if unit is None and template is not None and verb in ("start", "restart"):
                # A blue/green instance comes to exist when it is first started: loaded from
                # its template, listening on the port its own environment file gives it.
                instance = name.partition("@")[2]
                unit = units[name] = Unit(active="inactive", enabled=False)
                base = name.partition("@")[0]
                domains[name] = domains.get(base, base)
                port = instance_port(template, instance)
                if port is not None:
                    ports[name] = port
            if unit is not None and verb in ("start", "restart") and "@" in name:
                # Its port may have moved since it last ran (a switch rewrites the file).
                if template is not None:
                    port = instance_port(template, name.partition("@")[2])
                    if port is not None:
                        ports[name] = port
            if verb == "is-active":
                state = unit.active if unit else "inactive"
                return ok(args, f"{state}\n", 0 if state == "active" else 3)
            if verb == "is-enabled":
                enabled = (
                    unit.enabled
                    if unit is not None
                    else enabled_overrides.get(base_name(name), True)
                )
                return ok(args, "enabled\n" if enabled else "disabled\n", 0 if enabled else 1)
            if verb == "show":
                if "-p" in args:
                    return ok(args, "FragmentPath=\n")
                if any(a.startswith("--property=") for a in args):
                    return ok(args, self._cron_properties(targets[-1] if targets else ""))
                return ok(args, self._show(name, unit))
            if unit is not None and verb in ("start", "restart"):
                unit.active, unit.since, unit.pid = "active", datetime.now(), 40000 + len(name)
                return ok(args)
            if unit is not None and verb == "stop":
                unit.active, unit.since, unit.pid = "inactive", datetime.now(), 0
                return ok(args)
            if verb in ("enable", "disable"):
                if unit is not None:
                    unit.enabled = verb == "enable"
                # Recorded regardless of whether `unit` models this name, so a cron timer or
                # an ad-hoc created service (see the comment on enabled_overrides) is tracked
                # exactly like one `units` already knows.
                enabled_overrides[base_name(name)] = verb == "enable"
                return ok(args)
            if verb in ("start", "stop", "restart") and unit is None:
                # Cron job units (noust-cron-{name}.service) are written to the sandboxed
                # systemd directory but never registered in the live `units` model: they have
                # no persistent active/inactive state worth modelling, only a one-shot run.
                # "noust cron run" starts one by name, so a unit that genuinely exists on disk
                # succeeds here; only a name with no unit file at all is "not found".
                if (systemd_dir / f"{name}.service").exists():
                    return ok(args)
                return CommandResult(
                    args, 5, "", f"Failed to {verb} {targets[-1]}: Unit not found.\n"
                )
            return ok(args)

        @staticmethod
        def _show(name: str, unit: Unit | None) -> str:
            if unit is None and template_of(name) is not None:
                # An instance of an installed template nothing has started yet.
                return "LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nMemoryCurrent=0\nResult=success\n"
            if unit is None:
                # A unit written to disk but never given live state here (created through the
                # API this run, or a cron timer) is loaded and inactive, the way systemd
                # reports one nothing has started - never plain "not-found". Either way,
                # MainPID always appears as a number: ServiceInfo.pid is `int | None`, and
                # `details.get("MainPID", "")` used to default to "", which pydantic refused
                # to parse as an int and turned GET /api/services into a 500 the moment a
                # service was created without a modelled Unit.
                if (systemd_dir / f"{name}.service").exists():
                    return "LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\nMemoryCurrent=0\nResult=success\n"
                return "LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\n"
            # `since` is local wall-clock time; systemd's answer here is labelled UTC, so it is
            # converted first rather than relabelled (off by the machine's offset otherwise).
            since = unit.since.astimezone(timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S UTC")
            return (
                f"Id={name}.service\n"
                "LoadState=loaded\n"
                f"ActiveState={unit.active}\n"
                f"SubState={unit.sub}\n"
                f"MainPID={unit.pid}\n"
                f"MemoryCurrent={96 * 1024 * 1024 if unit.active == 'active' else 0}\n"
                f"ActiveEnterTimestamp={since if unit.active == 'active' else ''}\n"
                f"NRestarts={unit.restarts}\n"
                f"Result={'exit-code' if unit.active == 'failed' else 'success'}\n"
            )

        def _sql(self, program: str, statement: str, *, headers: bool = False) -> str:
            # An instance method, not a staticmethod: `self._pg_databases` and
            # `self._mysql_databases` start as copies of the module's seed data
            # and are mutated by CREATE/DROP DATABASE below, so a database the
            # Databases page just created answers to every query about it
            # afterwards - including PostgresManager.create_database's own
            # follow-up call to get_database_info, which re-checks existence.
            #
            # A client run inside a database container (see _exec_in_container) is the
            # same PostgreSQL over the container's own databases and roles.
            container = getattr(self._inside, "container", None)
            pg_databases = container.databases if container is not None else self._pg_databases
            pg_users = container.users if container is not None else _PG_USERS
            # The settings page: pg_settings and SHOW GLOBAL VARIABLES, as the engine
            # would answer them over the files Noust has written (see _postgres_settings).
            if program == "psql" and "FROM pg_settings" in statement:
                return "".join(
                    f"{key}|{value}|{context}|{source}\n"
                    for key, (value, context, source) in _postgres_settings(_host_tree()).items()
                    if f"'{key}'" in statement
                )
            if program == "mysql" and "SHOW GLOBAL VARIABLES" in statement:
                return "".join(
                    f"{name}\t{value}\n"
                    for name, value in _mysql_variables(_host_tree()).items()
                    if f"'{name}'" in statement
                )
            #
            # More specific matches first: PostgresManager.get_database_info's owner
            # query and database_exists' existence check both contain "pg_database"
            # as a substring (inside "pg_database_size" and "FROM pg_database"), so
            # they would otherwise fall through to the generic listing below.
            #
            # list_databases()'s own query joins pg_roles the same way
            # get_database_info()'s does (for the owner column), so both contain
            # "d.datdba = r.oid"; list_databases() is the one with no per-database
            # WHERE literal ("datistemplate = false" instead of "datname = '...'")
            # and must be checked first, or its listing query would be mistaken for
            # a lookup of one database and answer as though `name` were None.
            if (
                program == "psql"
                and "d.datdba = r.oid" in statement
                and "datistemplate" in statement
            ):
                return "".join(
                    f"{name}|UTF8|{size}|{owner}\n" for name, (owner, size) in pg_databases.items()
                )
            if program == "psql" and "d.datdba = r.oid" in statement:
                name = _sql_literal(statement)
                info = pg_databases.get(name or "")
                return f"{name}|UTF8|{info[1]}|{info[0]}\n" if info else ""
            if program == "psql" and statement.lstrip().startswith("SELECT 1 FROM pg_database"):
                return "1\n" if _sql_literal(statement) in pg_databases else ""
            if program == "psql" and statement.lstrip().startswith("CREATE DATABASE"):
                name = _sql_identifier(statement, '"')
                if name is not None:
                    owner = _sql_identifier(statement, '"', after="OWNER") or "postgres"
                    pg_databases[name] = (owner, 8192)
                return ""
            if program == "psql" and statement.lstrip().startswith("DROP DATABASE"):
                name = _sql_identifier(statement, '"')
                if name is not None:
                    pg_databases.pop(name, None)
                return ""
            # list_users()'s combined query aliases every column ("r.rolsuper"
            # rather than the old bare "rolsuper"), so it no longer contains
            # that literal; matched instead on `has_database_privilege`, the
            # function unique to this query's databases-per-role column - see
            # _PG_USERS for its (now 5-column) shape.
            if program == "psql" and "has_database_privilege" in statement:
                return pg_users
            # PostgresManager.server_port() asks the superuser session which
            # port the cluster listens on, for connection strings and the
            # read-only console's TCP login.
            if program == "psql" and statement.strip() == "SHOW port;":
                return "5432\n"
            if program == "psql" and statement.strip() == "SHOW listen_addresses;":
                return "localhost\n"
            if program == "psql" and "FROM demo_orders" in statement:
                return _PG_DEMO_ROWS_CSV if headers else _PG_DEMO_ROWS
            if program == "psql" and "pg_database" in statement:
                return "".join(
                    f"{name}|UTF8|{size}\n" for name, (_owner, size) in pg_databases.items()
                )
            # Same reasoning for MySQL: get_database_info's size query sums
            # bare "DATA_LENGTH + INDEX_LENGTH" columns (single table, no
            # alias needed); list_databases()'s own query joins
            # INFORMATION_SCHEMA.TABLES aliased "t" for the same sum, so it
            # reads "t.DATA_LENGTH + t.INDEX_LENGTH" instead and is matched
            # separately, checked first since the alias makes it more specific.
            if program == "mysql" and "t.DATA_LENGTH + t.INDEX_LENGTH" in statement:
                seeded = "".join(
                    f"{name}\tutf8mb4\t{size}\n"
                    for name, (size, _tables) in self._mysql_databases.items()
                )
                return "information_schema\tutf8mb3\t0\nmysql\tutf8mb4\t0\n" + seeded
            if program == "mysql" and "DATA_LENGTH + INDEX_LENGTH" in statement:
                info = self._mysql_databases.get(_sql_literal(statement) or "")
                return f"{info[0]}\t{info[1]}\n" if info else "0\t0\n"
            if program == "mysql" and statement.lstrip().startswith("SELECT SCHEMA_NAME FROM"):
                name = _sql_literal(statement)
                return f"{name}\n" if name in self._mysql_databases else ""
            if program == "mysql" and statement.lstrip().startswith(
                "SELECT DEFAULT_CHARACTER_SET_NAME"
            ):
                return "utf8mb4\n"
            if program == "mysql" and statement.lstrip().startswith("CREATE DATABASE"):
                name = _sql_identifier(statement, "`")
                if name is not None:
                    self._mysql_databases[name] = (0, 0)
                return ""
            if program == "mysql" and statement.lstrip().startswith("DROP DATABASE"):
                name = _sql_identifier(statement, "`")
                if name is not None:
                    self._mysql_databases.pop(name, None)
                return ""
            # list_users()'s combined query still contains this fragment (the
            # join is "FROM mysql.user u"), with a 3rd column of comma
            # separated databases appended - see _MYSQL_USERS.
            if program == "mysql" and "mysql.user" in statement:
                return _MYSQL_USERS
            if program == "mysql" and "FROM demo_orders" in statement:
                return _MYSQL_DEMO_ROWS_HEADERS if headers else _MYSQL_DEMO_ROWS
            if program == "mysql" and "SCHEMATA" in statement:
                return "information_schema\tutf8mb3\nmysql\tutf8mb4\n" + "".join(
                    f"{name}\tutf8mb4\n" for name in self._mysql_databases
                )
            if program == "redis-cli" and "keyspace" in statement:
                return "# Keyspace\ndb0:keys=1284,expires=210,avg_ttl=0\ndb2:keys=37,expires=0\n"
            if program == "redis-cli" and "databases" in statement:
                return "databases\n16\n"
            return ""

        @staticmethod
        def _systemd_analyze_calendar(args: tuple[str, ...]) -> str:
            """
            Fake `systemd-analyze calendar --iterations=N <expr>` for the cron
            job dialog's "next N runs" preview.

            Not real calendar arithmetic - the console only needs believable,
            parseable, always-in-the-future dates to prove the round trip
            works, not the exact semantics of an arbitrary OnCalendar
            expression. Every run is a day apart starting tomorrow at 02:00,
            regardless of what the expression actually says.
            """
            expr = args[-1] if len(args) > 1 else "*-*-* 02:00:00"
            iterations = 5
            for arg in args:
                if arg.startswith("--iterations="):
                    iterations = int(arg.split("=", 1)[1])
            start = datetime.now().replace(hour=2, minute=0, second=0, microsecond=0) + timedelta(
                days=1
            )
            lines = [f"  Original form: {expr}", f"Normalized form: {expr}"]
            for index in range(iterations):
                stamp = (start + timedelta(days=index)).strftime("%a %Y-%m-%d %H:%M:%S UTC")
                label = "Next elapse" if index == 0 else f"Iter. #{index + 1}"
                lines.append(f"      {label}: {stamp}")
            return "\n".join(lines) + "\n"

        @staticmethod
        def _systemd_analyze_verify(args: tuple[str, ...]) -> CommandResult:
            """
            Fake `systemd-analyze verify <path>` for the unit editor's "test
            configuration" button.

            Reads the candidate unit the caller staged (through the real,
            sandboxed filesystem - `ServiceManager.verify_unit` writes it
            before running this) and reports the one failure a hand-edited
            unit realistically hits: no `ExecStart=`. Anything else is
            reported as a clean pass, matching systemd-analyze's own silence
            on success.
            """
            path = Path(args[-1]) if len(args) > 1 else None
            content = path.read_text() if path and path.exists() else ""
            if not re.search(r"^ExecStart=\S", content, re.MULTILINE):
                name = path.name if path else "unit"
                return CommandResult(
                    args,
                    1,
                    "",
                    f"{name}: Service has no ExecStart= setting. Refusing.\n",
                )
            return ok(args)

        @staticmethod
        def _unit_files(patterns: list[str]) -> str:
            lines = []
            for path in sorted(systemd_dir.glob("*")):
                if patterns and not any(fnmatch(path.name, p) for p in patterns):
                    continue
                state = (
                    "enabled" if enabled_overrides.get(base_name(path.name), True) else "disabled"
                )
                lines.append(f"{path.name} {state} {state}")
            return "\n".join(lines) + "\n"

        @staticmethod
        def _cron_properties(unit: str) -> str:
            now = datetime.now()
            stamp = "%a %Y-%m-%d %H:%M:%S UTC"
            if unit.endswith(".timer"):
                calendar = "*-*-* 02:00:00"
                try:
                    text = (systemd_dir / unit).read_text(encoding="utf-8")
                    calendar = next(
                        line.split("=", 1)[1]
                        for line in text.splitlines()
                        if line.startswith("OnCalendar=")
                    )
                except (OSError, StopIteration):
                    pass
                return (
                    f"TimersCalendar={{ OnCalendar={calendar} ; next_elapse=n/a }}\n"
                    f"LastTriggerUSec={(now - timedelta(hours=9)).strftime(stamp)}\n"
                    f"NextElapseUSecRealtime={(now + timedelta(hours=15)).strftime(stamp)}\n"
                )
            failed = "cleanup" in unit
            return (
                f"ExecMainStatus={1 if failed else 0}\n"
                f"ExecMainExitTimestamp={(now - timedelta(hours=9)).strftime(stamp)}\n"
                f"Result={'exit-code' if failed else 'success'}\n"
            )

        @staticmethod
        def _list_units(patterns: list[str]) -> str:
            lines = ["UNIT LOAD ACTIVE SUB DESCRIPTION"]
            for name, unit in sorted(units.items()):
                if not unit.managed:
                    continue
                if template_of(name) is None and not (systemd_dir / f"{name}.service").exists():
                    # A unit whose file was removed (an instance: its template) is no longer
                    # loaded: blue/green retires an application's own unit, and removes the
                    # instances' template when it is turned off.
                    continue
                if patterns and not any(
                    fnmatch(f"{name}.service", p) or fnmatch(name, p) for p in patterns
                ):
                    continue
                description = domains.get(name, name)
                lines.append(f"{name}.service loaded {unit.active} {unit.sub} {description}")
            lines.extend(_foreign_unit_lines(units, patterns))
            return "\n".join(lines) + "\n"

        @staticmethod
        def _journal(args: tuple[str, ...]) -> CommandResult:
            name = ""
            if "-u" in args:
                index = args.index("-u")
                name = unit_of(args[index + 1]) if index + 1 < len(args) else ""
            unit = units.get(name)
            template = FAILED_JOURNAL_LINES if unit and unit.active == "failed" else JOURNAL_LINES
            start = datetime.now() - timedelta(minutes=len(template))
            lines = [
                f"{(start + timedelta(minutes=i)).strftime('%b %d %H:%M:%S')} acme "
                f"{name or 'systemd'}[{unit.pid if unit and unit.pid else 1}]: "
                + line.format(unit=name, domain=domains.get(name, name), port=ports.get(name, 3000))
                for i, line in enumerate(template)
            ]
            return CommandResult(args, 0, "\n".join(lines) + "\n", "")

        @staticmethod
        def _certificates() -> str:
            blocks = ["Saving debug log to /var/log/letsencrypt/letsencrypt.log", "", "-" * 79]
            blocks.append("Found the following certs:")
            for offset, domain in enumerate(certs):
                expiry = datetime.now() + timedelta(days=12 + offset * 17)
                days = (expiry - datetime.now()).days
                blocks += [
                    f"  Certificate Name: {domain}",
                    "    Serial Number: 4a3f9c2e1b7d",
                    "    Key Type: ECDSA",
                    f"    Domains: {domain} www.{domain}",
                    f"    Expiry Date: {expiry.strftime('%Y-%m-%d %H:%M:%S')}+00:00 "
                    f"(VALID: {days} days)",
                    f"    Certificate Path: /etc/letsencrypt/live/{domain}/fullchain.pem",
                    f"    Private Key Path: /etc/letsencrypt/live/{domain}/privkey.pem",
                ]
            blocks.append("-" * 79)
            return "\n".join(blocks) + "\n"

    return ConsoleRunner()


# ---------------------------------------------------------------------------
# Seeding what the store cannot hold
# ---------------------------------------------------------------------------


def seed_machine(
    sandbox: Sandbox,
    *,
    expired_certificate: bool = False,
    showcase: bool = False,
    hostname: str | None = None,
) -> tuple[dict[str, Unit], dict[str, int], dict[str, str], list[str]]:
    """
    Seed the store, then the files and units that live beside it.

    Args:
        sandbox: The sandbox.
        expired_certificate: Seed the first certificate as expired (``--expired-certificate``).
        showcase: Seed :func:`tests.showcase.seed_showcase_state`'s invented agency instead
            of :func:`tests.panel_factory.seed_console_state`'s example.com machine
            (``--showcase``). Everything below this point reads the seeded application,
            deployment and certificate domains off the store rather than by name, so it
            seeds the showcase's machine exactly as it does the default one; only the
            2.2/2.3 features that are inherently about *tests.panel_factory*'s own domains
            (zero-downtime, previews, the GitHub App, backup destinations, the tabs'
            release history) are skipped, since none of the documentation screenshots need
            them.
        hostname: With ``showcase``, which of :data:`tests.showcase.NODES` to seed
            (``--hostname``); the agency's main server when None, matching
            :func:`tests.showcase.seed_showcase_state`'s own default. Ignored otherwise.

    Returns:
        The unit model, each unit's port, each unit's domain, and the domains
        holding certificates - what :func:`make_runner` answers from.
    """
    from noust.core.store import get_store
    from noust.managers.service_manager import UNIT_MARKER

    store = get_store(sandbox.store_file)
    tabs_history: dict[str, _TabsApp] = {}
    showcase_cert_alt_names: dict[str, str] | None = None
    if showcase:
        from tests.showcase import cert_alt_names, seed_showcase_state

        state: Any = seed_showcase_state(store, node=hostname or "fra-1")
        showcase_cert_alt_names = cert_alt_names(hostname or "fra-1")
    else:
        from tests.panel_factory import seed_console_state

        # First, so the tabs' old deploys get lower ids than the ones seeded as of now.
        tabs_history = seed_app_tabs_history(store)
        seed_deploylinks_commit_messages(store)
        state = seed_console_state(store)

    units: dict[str, Unit] = {}
    ports: dict[str, int] = {}
    domains: dict[str, str] = {}
    apps = {app.domain: app for app in store.list_apps()}
    for service in store.list_services():
        app = next((a for a in apps.values() if a.id == service.app_id), None)
        if app is None:
            continue
        failed = app.domain in state.failed_domains
        active = "failed" if failed else ("active" if service.status == "active" else "inactive")
        units[service.name] = Unit(
            active=active,
            enabled=service.enabled,
            pid=41000 + (service.id or 0) if active == "active" else 0,
            since=datetime.now() - timedelta(hours=6, minutes=(service.id or 0) * 7),
            restarts=5 if failed else 0,
        )
        ports[service.name] = service.port or 3000
        domains[service.name] = app.domain
        # The file that makes ServiceManager treat it as a unit Noust owns and
        # that exists, which is what start, stop and restart require.
        (sandbox.systemd_dir / f"{service.name}.service").write_text(
            f"# {UNIT_MARKER}\n"
            "[Unit]\n"
            f"Description={app.domain}\n\n"
            "[Service]\n"
            f"WorkingDirectory={service.working_directory}\n"
            f"ExecStart={service.command}\n"
            f"Environment=PORT={service.port}\n",
            encoding="utf-8",
        )

    # The database engines, answered as system units Noust does not own.
    for engine, active in (
        ("postgresql", "active"),
        ("mysql", "active"),
        ("redis-server", "inactive"),
    ):
        units[engine] = Unit(
            active=active, pid=900 + len(engine) if active == "active" else 0, managed=False
        )

    seed_backups(sandbox, state.backup_domains + state.static_domains[:1])
    seed_cron(state.domains[0])
    # Only the agency's main server carries the standalone failed worker and the one
    # benign monitor notice: "ams-3" (a couple of healthy client sites) and "lon-2" (a
    # staging server, everything green) are calmer servers by design (coordinator review),
    # not "fra-1" with its own findings copied onto every node in the fleet.
    node = hostname or "fra-1"
    showcase_calm_node = showcase and node != "fra-1"
    if not showcase_calm_node:
        seed_overview_failed_worker(
            sandbox,
            store,
            units,
            domain=state.domains[0] if showcase else "example.com",
        )
    seed_monitor(sandbox, units, showcase=showcase, showcase_notice=not showcase_calm_node)
    quiet_process_sampling()
    seed_job_history(sandbox, store, state.domains[0])
    seed_activity_audit_log(sandbox, state.domains[0])
    if showcase:
        # The three generic pieces of seed_app_tabs below (despite the name: none of
        # them reads a "tabs" domain, only the units/ports/domains models), without
        # which every application diagnoses as "down: not listening" and every running
        # one's port reads "No answer" (noust.core.app_state.resolve_state does a real
        # socket connect, and nothing in the sandbox actually listens on any port).
        _tabs_journal_model(units, domains, ports)
        _tabs_diagnose_model(units, ports)
        _tabs_port_model(units, ports)
    else:
        seed_app_tabs(sandbox, store, units, ports, domains, tabs_history)
    seed_domains_and_sources(
        sandbox,
        store,
        units,
        list(state.cert_domains),
        expired_certificate=expired_certificate,
        # The showcase's own site already carries the alias tests.showcase gave it
        # through store.add_domain; this is only the nginx server_name list a site's
        # own config carries (SitesTab, not the AppDomainsTab), on one of its domains
        # in place of the default machine's example.net.
        server_names=(state.domains[0], ("www",))
        if showcase
        else ("example.net", ("www", "shop", "status")),
        cert_alt_names=showcase_cert_alt_names,
        proggest=not showcase,
    )
    if not showcase:
        seed_release_22(sandbox, store, units, ports, domains)
    seed_release_23(sandbox)
    if showcase:
        seed_showcase_php_fpm_pools(sandbox, store)
        seed_showcase_machine_metrics()
        seed_showcase_app_metrics(store)
        if state.failed_domains:
            seed_showcase_nginx_error_log(sandbox, state.failed_domains[0])
    # PHP 8.3's FPM, a system unit Noust does not own, running the pools recipes write.
    units["php8.3-fpm"] = Unit(active="active", pid=912, managed=False)
    return units, ports, domains, list(state.cert_domains)


def seed_overview_failed_worker(
    sandbox: Sandbox, store: Any, units: dict[str, Unit], *, domain: str = "example.com"
) -> None:
    """
    Seed a Noust unit that belongs to no application and has failed.

    A worker created with ``noust service create`` (a queue consumer, a mailer)
    fails without any application's state saying so. The overview names the
    failed units beyond those an application already accounts for, and this
    is the one that makes that count non-zero on the seeded machine.

    Args:
        sandbox: The sandbox whose unit directory receives the unit file.
        store: The seeded store, which tracks the service.
        units: The unit model the runner answers from; gains the worker.
        domain: The application whose tree the worker's script lives beside; one of the
            default machine's own by default, one of ``--showcase``'s otherwise.
    """
    from noust.core.store import Service
    from noust.managers.service_manager import UNIT_MARKER

    name = "queue-worker"
    command = f"/usr/bin/node /var/www/apps/{domain}/current/worker.js"
    store.create_service(
        Service(
            name=name,
            unit_file=str(sandbox.systemd_dir / f"{name}.service"),
            working_directory=f"/var/www/apps/{domain}/current",
            command=command,
            status="failed",
        )
    )
    units[name] = Unit(
        active="failed",
        since=datetime.now() - timedelta(minutes=42),
        restarts=5,
    )
    (sandbox.systemd_dir / f"{name}.service").write_text(
        f"# {UNIT_MARKER}\n"
        "[Unit]\n"
        f"Description=Queue worker for {domain}\n\n"
        "[Service]\n"
        f"WorkingDirectory=/var/www/apps/{domain}/current\n"
        f"ExecStart={command}\n"
        "Restart=on-failure\n",
        encoding="utf-8",
    )


def seed_backups(sandbox: Sandbox, domains: list[str], *, root: Path | None = None) -> None:
    """
    Write backups the way :class:`~noust.managers.backup_manager.BackupManager` lists them.

    A metadata file beside a real, tiny archive: the manager only lists a
    backup whose archive is present.

    Args:
        sandbox: The sandbox.
        domains: Domains to give backups to, two each.
        root: Where the ``<app>/`` directories go; the backup directory by default.
    """
    from noust.core.utils import domain_to_app_name
    from noust.managers.backup_manager import BackupMetadata

    now = datetime.now()
    for index, domain in enumerate(domains):
        app_name = domain_to_app_name(domain)
        directory = (root or sandbox.backup_dir) / app_name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for age_days, tags in ((index + 1, ["scheduled"]), (index + 8, ["pre-deploy"])):
            created = now - timedelta(days=age_days, hours=index)
            backup_id = f"{app_name}_{created.strftime('%Y%m%d_%H%M%S')}"
            archive = directory / f"{backup_id}.tar.gz"
            payload = directory / f"{backup_id}.README"
            payload.write_text(f"Seeded backup of {domain}\n", encoding="utf-8")
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(payload, arcname="app/README")
            payload.unlink()
            metadata = BackupMetadata(
                id=backup_id,
                domain=domain,
                app_name=app_name,
                created_at=created.isoformat(),
                size_bytes=archive.stat().st_size,
                app_type="nextjs",
                version="2.0.0",
                description="Nightly backup" if "scheduled" in tags else "Before deploy",
                includes_env=True,
                includes_node_modules=False,
                git_commit="9f2c41a",
                git_branch="main",
                tags=tags,
            )
            (directory / f"{backup_id}.json").write_text(
                json.dumps(metadata.to_dict(), indent=2), encoding="utf-8"
            )


def seed_misplaced_backups(sandbox: Sandbox) -> None:
    """
    Leave backups where an empty ``backup.directory`` once sent them (``--misplaced-backups``).

    Production looks for them in ``/root`` and ``/`` (``MISPLACED_BACKUP_ROOTS``), which
    :func:`redirect_system_paths` empties on a developer's machine; this points it at a
    stand-in for ``/root`` inside the sandbox instead, holding two backups of an application
    the backup directory already knows - only known applications are looked for.

    Args:
        sandbox: The sandbox, its backups already seeded.
    """
    from noust.managers.backup_manager import BackupManager

    known = sorted(sandbox.backup_dir.glob("*/*.json"))[0]
    domain = str(json.loads(known.read_text(encoding="utf-8"))["domain"])
    home = sandbox.root / "root"
    seed_backups(sandbox, [domain], root=home)
    BackupManager.MISPLACED_BACKUP_ROOTS = (home,)


def seed_cron(domain: str) -> None:
    """
    Create cron jobs through :class:`~noust.managers.cron_manager.CronManager` itself.

    The units are written into the sandboxed systemd directory; enabling the
    timer is answered by the fake runner.

    Args:
        domain: The application the first job belongs to.
    """
    from noust.managers.cron_manager import CronJob, CronManager

    manager = CronManager()
    for job in (
        CronJob(
            name="sitemap",
            command="/usr/bin/node scripts/sitemap.js",
            schedule="daily",
            user="www-data",
            app_domain=domain,
        ),
        CronJob(
            name="cleanup-tmp",
            command="/usr/bin/find /tmp -name 'upload-*' -mtime +2 -delete",
            schedule="*-*-* 03:30:00",
            user="root",
        ),
    ):
        manager.create_job(job)


# ---------------------------------------------------------------------------
# Console: the Server page's monitor card needs an installed, running unit
# and a small findings history to show status, observations and the
# acknowledge flow; the Activity page needs at least one job with a captured
# log, since none of tests.panel_factory.seed_console_state's jobs record one.
# ---------------------------------------------------------------------------


def seed_monitor(
    sandbox: Sandbox,
    units: dict[str, Unit],
    *,
    showcase: bool = False,
    showcase_notice: bool = True,
) -> None:
    """
    Install the monitor's unit and give it a short findings history.

    Mirrors a machine where ``noust monitor install --enable`` has already run
    for a while: the unit active and enabled (answered by the fake runner,
    the same way an application's unit is), and a few observations already on
    record - a couple still open, one already acknowledged - for the
    console's monitor card, the acknowledge flow, and the overview's "Needs
    attention" (Task 3.1, already wired to the same endpoint).

    Args:
        sandbox: The sandbox.
        units: The modelled machine's units, mutated in place so the fake
            runner reports the monitor unit as installed, active and enabled.
        showcase: Seed at most one plausible, benign notice instead of the default
            machine's cryptominer and raw-listener findings (``--showcase``): a
            documentation screenshot of a small agency's server should not look actively
            compromised, and the failed application, the failed unit and the expiring
            certificate already carry "Needs attention" on their own.
        showcase_notice: With ``showcase``, whether to seed even that one open notice.
            False for a fleet node meant to read as calm and fully healthy (a second
            region, a staging server): every open finding, not only the alarming ones,
            counts towards "Needs attention".
    """
    from noust.monitor.models import (
        SEVERITY_NOTICE,
        SEVERITY_WARNING,
        SIGNAL_NAME_PATTERN,
        SIGNAL_RESOURCE_USAGE,
        ProcessInfo,
        ProcessObservation,
    )
    from noust.monitor.observation_store import ObservationStore
    from noust.monitor.process_monitor import ProcessMonitor

    unit_name = ProcessMonitor.SERVICE_NAME
    units[unit_name] = Unit(
        active="active", enabled=True, pid=2114, since=datetime.now() - timedelta(days=6)
    )
    (sandbox.systemd_dir / f"{unit_name}.service").write_text(
        "[Unit]\nDescription=Noust resource monitor\nAfter=network.target\n\n"
        "[Service]\nType=simple\nExecStart=/usr/bin/noust monitor run\nRestart=always\n\n"
        "[Install]\nWantedBy=multi-user.target\n",
        encoding="utf-8",
    )

    now = datetime.now()
    store = ObservationStore()
    open_observations: list[ProcessObservation] = (
        (
            [
                ProcessObservation(
                    process=ProcessInfo(
                        pid=2087,
                        name="tar",
                        user="www-data",
                        cpu_percent=76.0,
                        memory_percent=3.0,
                        command="tar czf backup.tar.gz .",
                    ),
                    signal=SIGNAL_RESOURCE_USAGE,
                    severity=SEVERITY_NOTICE,
                    detail="Short CPU spike during the nightly backup",
                    observed_at=now - timedelta(hours=3),
                ),
            ]
            if showcase_notice
            else []
        )
        if showcase
        else [
            ProcessObservation(
                process=ProcessInfo(
                    pid=41823,
                    name="node",
                    user="www-data",
                    cpu_percent=92.4,
                    memory_percent=18.2,
                    command="node server.js",
                ),
                signal=SIGNAL_RESOURCE_USAGE,
                severity=SEVERITY_WARNING,
                detail="CPU above 90% for more than five minutes",
                observed_at=now - timedelta(hours=2),
            ),
            ProcessObservation(
                process=ProcessInfo(
                    pid=9931,
                    name="xmrig",
                    user="www-data",
                    cpu_percent=100.0,
                    memory_percent=2.1,
                    command="xmrig -o pool.example.com:4444",
                ),
                signal=SIGNAL_NAME_PATTERN,
                severity=SEVERITY_WARNING,
                detail="Process name matches a known cryptominer pattern",
                observed_at=now - timedelta(hours=5),
            ),
            ProcessObservation(
                process=ProcessInfo(
                    pid=512,
                    name="nc",
                    user="root",
                    cpu_percent=0.1,
                    memory_percent=0.1,
                    command="nc -lvp 4444",
                ),
                signal=SIGNAL_NAME_PATTERN,
                severity=SEVERITY_NOTICE,
                detail="Listener started with a raw networking tool",
                observed_at=now - timedelta(days=2),
            ),
        ]
    )
    store.save_many(open_observations)
    # One already-dismissed row, so the observations list and "Needs
    # attention" show the difference between open and acknowledged findings.
    acknowledged_id = store.save(
        ProcessObservation(
            process=ProcessInfo(
                pid=7710,
                name="ffmpeg",
                user="www-data",
                cpu_percent=88.0,
                memory_percent=6.4,
                command="ffmpeg -i input.mp4 -f null -",
            ),
            signal=SIGNAL_RESOURCE_USAGE,
            severity=SEVERITY_NOTICE,
            detail="Short-lived CPU spike during a video conversion",
            observed_at=now - timedelta(days=3),
        )
    )
    store.acknowledge(acknowledged_id)


def seed_activity_audit_log(sandbox: Sandbox, domain: str) -> None:
    """
    Write a few audit entries beyond what a real session generates.

    Every E2E test that signs in produces one real ``auth.login`` entry
    through the actual login endpoint, but that alone is only ever one actor
    and one result. This adds the mix an operator actually sees on the
    Activity page - a refusal, and an API token acting instead of a browser
    session - written through :class:`noust.web.auth.AuditLogger`, the same
    writer the API server installs, at the path :class:`SecurityConfig`
    resolves. This runs before :func:`noust.web.server.create_app` replaces
    the global logger with its own instance over that same file, so these
    are simply older lines already in the log the server reads from - never
    a hand-written format.

    Args:
        sandbox: The sandbox.
        domain: An application named in the denied action's resource.
    """
    from noust.web.auth import AuditLogger, SecurityConfig

    audit = AuditLogger(SecurityConfig(state_dir=sandbox.state_dir).audit_log)
    # The real refusal a deploy-scoped token gets from require_scope() (auth.py) when it
    # reaches an admin-only endpoint - deleting an app needs "admin", not "deploy".
    audit.record(
        action="auth.scope",
        result="denied",
        client_ip="203.0.113.7",
        actor="token:ci-deploy",
        resource=f"/api/apps/{domain}",
        detail="scope 'deploy' below required 'admin'",
    )
    audit.record(
        action="config.update",
        result="success",
        client_ip="127.0.0.1",
        actor="master",
        resource="/api/config",
        detail="updated ssl.email",
    )


def seed_job_history(sandbox: Sandbox, store: Any, domain: str) -> None:
    """
    Add one job with a captured log, for the Activity page's log drawer.

    :func:`tests.panel_factory.seed_console_state` seeds a small job history
    already (a deploy, a backup, a cert renewal, a failed update), none of
    which record ``log_path``: nothing wrote a file for them to point at.
    This adds one more, the way the real job manager does - a text file next
    to the store, referenced by the record - so opening a job's log on the
    Activity page has something real to show instead of every row answering
    "no log captured".

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        domain: The application the job ran against.
    """
    from noust.core.store import JobRecord

    log_dir = sandbox.store_file.parent / "job-logs"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    unit = domain.replace(".", "-")
    log_path = log_dir / "seed-update-0001.log"
    log_path.write_text(
        f"==> Updating {domain}\n"
        "Fetching origin\n"
        "HEAD is now at 9f2c41a Update dependencies\n"
        "Installing dependencies\n"
        f"Restarting wasm-{unit}.service\n"
        f"Update of {domain} finished\n",
        encoding="utf-8",
    )
    started = datetime.now() - timedelta(minutes=45)
    store.create_job(
        JobRecord(
            # Job ids are validated as hexadecimal (JOB_ID_PATTERN in
            # web/api/jobs.py) wherever one names a single job, unlike
            # tests.panel_factory's own "seed0000" ids, which only ever
            # appear in the list.
            id="c0ffee01",
            type="update",
            name=f"Update {domain}",
            description=f"Updating the application at {domain}",
            actor="master",
            status="completed",
            progress=100,
            domain=domain,
            created_at=started.isoformat(),
            started_at=started.isoformat(),
            finished_at=(started + timedelta(seconds=38)).isoformat(),
            log_path=str(log_path),
        )
    )


# ---------------------------------------------------------------------------
# Console: an application's tabs (deployments, logs, metrics, environment,
# diagnose, settings) need applications whose trees are real, in the sandbox:
# one on the release layout with releases to roll back to, one in place whose
# update runs the real update sequence (slowly enough to watch it stream), and
# two in place that can be migrated to releases. Their journals stream over
# /ws/logs from a model of the machine, since no real journalctl may run.
# ---------------------------------------------------------------------------

#: On the release layout: three releases on disk, one failed build that was
#: removed, resource limits, webhook deliveries and a long deployment history.
TABS_RELEASE_APP = "tienda.example.org"

#: In place, with a real tree: its .env is read and written, and an update runs
#: the real update sequence with the build output streamed a line at a time.
TABS_LIVE_APP = "pedidos.example.org"

#: In place, to be migrated to releases. A migration cannot be undone through
#: the API, so the E2E suite migrates one per theme project.
TABS_MIGRATE_APPS = ("blog.example.org", "docs.example.org")

#: Seconds between the lines of a streamed npm install or build of the live app.
TABS_BUILD_LINE_DELAY = 0.35

#: Seconds between the request lines the modelled journal appends while followed.
TABS_JOURNAL_TICK = 1.5

#: What npm prints while installing and building the live app.
_TABS_NPM_INSTALL = (
    "npm warn deprecated inflight@1.0.6: This module is not supported, and leaks memory.",
    "npm warn deprecated glob@7.2.3: Glob versions prior to v9 are no longer supported",
    "",
    "added 214 packages, and audited 215 packages in 6s",
    "",
    "38 packages are looking for funding",
    "  run `npm fund` for details",
    "",
    "found 0 vulnerabilities",
)
_TABS_NPM_BUILD = (
    "",
    "> pedidos@2.4.0 build",
    "> tsc -p tsconfig.build.json && node scripts/copy-assets.mjs",
    "",
    "Compiling 142 files...",
    "Copied 18 assets to dist/public",
    "Build finished in 4.2s",
)

#: What the modelled journal appends while the Logs tab follows an app.
_TABS_REQUESTS = (
    "GET / 200 in 38ms",
    "GET /api/orders?page=1 200 in 41ms",
    "POST /api/cart 201 in 87ms",
    "GET /_next/static/chunks/app/page-4f1c.js 200 in 2ms",
    "GET /api/health 200 in 3ms",
    "warn: slow query on orders (1204ms): SELECT * FROM orders WHERE status = $1",
    "GET /checkout 200 in 112ms",
    "Error: connect ECONNREFUSED 127.0.0.1:6379 (redis cache unavailable, serving from database)",
    "GET /api/products/88 200 in 19ms",
    "POST /api/checkout 200 in 342ms",
)


def _tabs_serve_ok() -> int:
    """
    Answer HTTP on a free loopback port, as an application that is up would.

    The health gate probes ``http://127.0.0.1:<port>/`` after activating a
    release or migrating; with nothing listening, every rollback and every
    migration would be undone as unhealthy.

    Returns:
        The port.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Ok(BaseHTTPRequestHandler):
        """Answers 200 to anything."""

        def do_GET(self) -> None:
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self) -> None:
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Ok)
    threading.Thread(target=server.serve_forever, name="tabs-health", daemon=True).start()
    return int(server.server_address[1])


def _tabs_node_tree(root: Path, name: str, *, env: str, uploads: bool) -> None:
    """
    Write a small Node application: the files an update and a migration read.

    Args:
        root: Where the tree goes.
        name: Its package name.
        env: The ``.env`` it holds, verbatim.
        uploads: Whether it has written user uploads into its own tree.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "2.4.0",
                "private": True,
                "scripts": {"build": "tsc -p tsconfig.build.json", "start": "node dist/server.js"},
                "dependencies": {"express": "^4.21.0", "pg": "^8.13.0"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    (root / "dist").mkdir(exist_ok=True)
    (root / "dist" / "server.js").write_text(
        "require('http').createServer((q, s) => s.end('ok')).listen(process.env.PORT);\n",
        encoding="utf-8",
    )
    env_file = root / ".env"
    env_file.write_text(env, encoding="utf-8")
    env_file.chmod(0o600)
    if uploads:
        for relative, content in (
            ("uploads/2026/09/invoice-1024.pdf", "%PDF-1.7 seeded invoice\n"),
            ("uploads/2026/09/logo.png", "seeded image\n"),
            ("storage/sessions/sess_4f1c", "seeded session\n"),
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")


def _tabs_register(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    app: Any,
    *,
    working_directory: Path,
) -> None:
    """
    Record an application, its unit and its site, and model the unit as running.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        app: The application row to create.
        working_directory: Where its unit runs from.
    """
    from noust.core.store import Service, Site
    from noust.core.utils import domain_to_app_name
    from noust.managers.service_manager import UNIT_MARKER

    created = store.create_app(app)
    unit = domain_to_app_name(app.domain)
    unit_file = sandbox.systemd_dir / f"{unit}.service"
    command = "/usr/bin/node dist/server.js"
    unit_file.write_text(
        f"# {UNIT_MARKER}\n"
        "[Unit]\n"
        f"Description={app.domain}\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        "User=www-data\n"
        f"WorkingDirectory={working_directory}\n"
        f"ExecStart={command}\n"
        f"Environment=PORT={app.port}\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n",
        encoding="utf-8",
    )
    store.create_service(
        Service(
            app_id=created.id,
            name=unit,
            unit_file=str(unit_file),
            working_directory=str(working_directory),
            command=command,
            status="active",
            enabled=True,
            port=app.port,
        )
    )
    site_file = sandbox.etc / "nginx" / "sites-available" / app.domain
    site_file.write_text(
        "server {\n"
        f"    server_name {app.domain};\n"
        "    listen 80;\n"
        "    location / {\n"
        f"        proxy_pass http://127.0.0.1:{app.port};\n"
        "        proxy_set_header Host $host;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    store.create_site(
        Site(
            app_id=created.id,
            domain=app.domain,
            webserver="nginx",
            config_path=str(site_file),
            enabled=True,
            proxy_port=app.port,
            ssl_enabled=True,
        )
    )
    units[unit] = Unit(
        active="active",
        enabled=True,
        pid=43000 + (created.id or 0),
        since=datetime.now() - timedelta(hours=2, minutes=(created.id or 0) * 3),
    )
    ports[unit] = int(app.port)
    domains[unit] = app.domain


def _tabs_stamped(start: datetime, steps: Sequence[tuple[float, str]]) -> str:
    """
    Render a captured build log the way :class:`DeploymentRecorder` writes it.

    Args:
        start: When the deployment started.
        steps: Seconds after the start, and the line.

    Returns:
        The log, one ``[YYYY-MM-DD HH:MM:SS] line`` per line.
    """
    return "".join(
        f"[{(start + timedelta(seconds=offset)).strftime('%Y-%m-%d %H:%M:%S')}] {line}\n"
        for offset, line in steps
    )


def _tabs_release_log(
    kind: str, *, source: str, commit: str, release_id: str, previous: str | None, port: int
) -> tuple[list[tuple[float, str]], str | None]:
    """
    The captured log of one deploy of the release app, and its error when it failed.

    Args:
        kind: ``ok``, ``reused`` (same lockfile), ``build`` (a type error) or
            ``health`` (the release never answered).
        source: The repository.
        commit: The commit built.
        release_id: The release it built.
        previous: The release that was serving.
        port: The application's port.

    Returns:
        The lines with their offsets in seconds, and the error text or None.
    """
    lines: list[tuple[float, str]] = [
        (0, "[1/9] Fetching source into a new release..."),
        (0.4, f"      → Source: {source} (main)"),
        (2.1, f"      → HEAD is now at {commit}"),
        (2.3, f"      → Release: {release_id}"),
        (2.4, "      → Linked .env to shared/.env"),
        (2.4, "      → Linked uploads to shared/uploads"),
    ]
    if kind == "reused":
        lines += [
            (2.6, "[2/9] Installing dependencies..."),
            (3.9, f"      → Dependencies reused from {previous}: the lockfile did not change"),
        ]
        at = 4.0
    else:
        lines += [
            (2.6, "[2/9] Installing dependencies..."),
            (2.7, "      → Running: npm ci"),
            (15.8, "added 812 packages, and audited 813 packages in 13s"),
            (15.9, "found 0 vulnerabilities"),
        ]
        at = 16.0
    lines += [
        (at, "[3/9] Building application..."),
        (at + 0.1, "      → Running: npm run build"),
        (at + 1.4, "   ▲ Next.js 15.2.4"),
        (at + 1.5, "   Creating an optimized production build ..."),
    ]
    if kind == "build":
        lines += [
            (at + 24.2, "Failed to compile."),
            (at + 24.2, ""),
            (at + 24.2, "./app/checkout/page.tsx:42:7"),
            (at + 24.2, "Type error: Property 'total' does not exist on type 'Order'."),
            (at + 24.3, "✗ Deployment failed: npm run build exited with status 1"),
            (
                at + 24.4,
                f"      → Removed release {release_id}; {previous or 'nothing'} keeps serving",
            ),
        ]
        return lines, (
            "npm run build exited with status 1\n"
            "./app/checkout/page.tsx:42:7\n"
            "Type error: Property 'total' does not exist on type 'Order'."
        )
    at += 27.0
    lines += [
        (at - 5.6, " ✓ Compiled successfully in 21.4s"),
        (at - 1.2, " ✓ Generating static pages (38/38)"),
        (at, "[4/9] Setting permissions..."),
        (at + 0.8, "[5/9] Creating site configuration..."),
        (at + 1.0, "[6/9] Obtaining SSL certificate..."),
        (at + 1.1, "      → The existing certificate keeps serving what it covers"),
        (at + 1.3, "[7/9] Creating systemd service..."),
        (at + 1.6, "[8/9] Activating release..."),
        (
            at + 1.7,
            f"      → Activated release {release_id}"
            + (f" (was {previous})" if previous is not None else ""),
        ),
        (at + 1.8, f"      → Checking: http://127.0.0.1:{port}/"),
    ]
    if kind == "health":
        lines += [
            (at + 31.8, f"⚠ Release {release_id} did not pass its health check"),
            (at + 31.9, f"      → Going back to release {previous}"),
            (at + 34.0, f"✗ Deployment failed: release {release_id} did not answer"),
        ]
        return lines, (
            f"Release {release_id} did not pass its health check: "
            f"http://127.0.0.1:{port}/ did not answer after 15 attempts "
            "([Errno 111] Connection refused)\n"
            "-- journal --\n"
            "Error: Cannot find module '/var/www/apps/tienda-example-org/current/.next/standalone/server.js'\n"
            "tienda-example-org.service: Main process exited, code=exited, status=1/FAILURE"
        )
    lines += [
        (at + 3.9, "[9/9] Health check..."),
        (at + 4.0, f"✓ Release {release_id} answered 200 in 84 ms"),
        (at + 4.1, "✓ Deployed tienda.example.org"),
    ]
    return lines, None


def _tabs_inplace_log(
    domain: str, *, commit: str, source: str, port: int
) -> list[tuple[float, str]]:
    """
    The captured log of a successful in-place deploy of a Node application.

    Args:
        domain: The application.
        commit: The commit deployed.
        source: The repository.
        port: The application's port.

    Returns:
        The lines with their offsets in seconds.
    """
    return [
        (0, "[1/8] Fetching source code..."),
        (0.3, f"      → Source: {source}"),
        (1.8, f"      → HEAD is now at {commit}"),
        (2.0, "[2/8] Installing dependencies..."),
        (2.1, "      → Running: npm ci"),
        (9.4, "added 214 packages, and audited 215 packages in 7s"),
        (9.6, "[3/8] Building application..."),
        (9.7, "      → Running: npm run build"),
        (14.1, "Build finished in 4.2s"),
        (14.2, "[4/8] Setting permissions..."),
        (14.9, "[5/8] Creating site configuration..."),
        (15.1, "[6/8] Obtaining SSL certificate..."),
        (15.2, "      → The existing certificate keeps serving what it covers"),
        (15.4, "[7/8] Creating systemd service..."),
        (15.9, "[8/8] Starting application..."),
        (18.2, f"      → Checking: http://127.0.0.1:{port}/"),
        (18.4, f"✓ Deployed {domain}"),
    ]


def _tabs_deployment(
    store: Any,
    domain: str,
    *,
    trigger: str,
    commit: str,
    started: datetime,
    lines: Sequence[tuple[float, str]],
    status: str,
    error: str | None = None,
) -> int:
    """
    Record one finished deployment at a moment in the past, with its captured log.

    Args:
        store: The seeded store.
        domain: The application.
        trigger: ``cli``, ``panel`` or ``webhook``.
        commit: The commit deployed.
        started: When it started.
        lines: Its log, as offsets in seconds and lines; the last offset is
            when it finished.
        status: How it ended.
        error: What went wrong, verbatim, when it failed.

    Returns:
        The deployment id.
    """
    deployment_id = store.record_deployment_start(
        domain, trigger, git_commit=commit, git_branch="main"
    )
    seconds = max(offset for offset, _ in lines) if lines else 0.0
    log_dir = store.db_path.parent / "deploy-logs" / domain
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = log_dir / f"{deployment_id}.log"
    log_path.write_text(_tabs_stamped(started, lines), encoding="utf-8")
    log_path.chmod(0o600)
    # finish_deployment stamps "now"; history is written with the times it had.
    with store._transaction() as cursor:
        cursor.execute(
            "UPDATE deployments SET status = ?, error = ?, started_at = ?, finished_at = ?, "
            "duration_s = ?, log_path = ? WHERE id = ?",
            (
                status,
                error,
                started.isoformat(),
                (started + timedelta(seconds=seconds)).isoformat(),
                round(seconds, 1),
                str(log_path),
                deployment_id,
            ),
        )
    return int(deployment_id)


@dataclass
class _TabsApp:
    """
    What the first seeding pass decided for one of the tabs' applications.

    Attributes:
        port: The port its health listener holds.
        deploys: Its deployments, oldest first: when, commit, kind, status and
            the release each built.
    """

    port: int
    deploys: list[tuple[datetime, str, str, str, str]] = field(default_factory=list)

    @property
    def starts(self) -> list[datetime]:
        """When each deployment started."""
        return [started for started, *_ in self.deploys]


#: The release app's history, oldest first: days ago, trigger, commit, kind, status.
_TABS_RELEASE_HISTORY = (
    (27.2, "webhook", "3c1e9a0", "ok", "success"),
    (24.1, "webhook", "7f2d4b1", "ok", "success"),
    (21.0, "panel", "7f2d4b1", "reused", "success"),
    (18.3, "webhook", "a94c0e2", "build", "failed"),
    (18.28, "webhook", "b1e7f93", "ok", "success"),
    (15.4, "cli", "b1e7f93", "reused", "success"),
    (12.2, "webhook", "c5d2a61", "ok", "rolled_back"),
    (9.1, "webhook", "d08e4f7", "health", "failed"),
    (7.3, "webhook", "e3b9c12", "ok", "success"),
    (5.2, "webhook", "f41a8d3", "ok", "success"),
    (3.1, "cli", "0c7e5b9", "ok", "success"),
    (1.05, "webhook", "19d3f6e", "reused", "success"),
    (0.09, "webhook", "2a8b7c4", "ok", "success"),
)

#: The releases of the release app still on disk, by commit.
_TABS_ON_DISK = frozenset({"0c7e5b9", "19d3f6e", "2a8b7c4"})

#: The in-place apps' histories, oldest first: days ago, trigger, commit.
_TABS_INPLACE_HISTORY: dict[str, tuple[tuple[float, str, str], ...]] = {
    TABS_LIVE_APP: (
        (20.4, "cli", "4e1f2a7"),
        (6.2, "panel", "8b3c9d0"),
        (2.1, "webhook", "5a7e1c3"),
    ),
    **dict.fromkeys(TABS_MIGRATE_APPS, ((41.0, "cli", "9d4e2b8"),)),
}


def _tabs_release_id(started: datetime, commit: str) -> str:
    """
    Args:
        started: When the release was built.
        commit: The commit it was built from.

    Returns:
        Its id, the way :class:`ReleaseManager` names a release directory.
    """
    from datetime import timezone

    return f"{started.astimezone(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{commit}"


def seed_app_tabs_history(store: Any) -> dict[str, _TabsApp]:
    """
    Record the tabs' applications' deployment history, before anything else is seeded.

    Their history lies days in the past, and the API orders deployments by id:
    recorded after :func:`seed_console_state`'s deploys of "now", they would
    list as the newest on the overview. Recorded first, ids follow time. Each
    app's health listener starts here too, since its port is in the logs.

    Args:
        store: The store.

    Returns:
        Per domain, its port and its deployments.
    """
    now = datetime.now()
    apps: dict[str, _TabsApp] = {}

    source = "https://github.com/example-org/tienda.git"
    release = _TabsApp(port=_tabs_serve_ok())
    previous: str | None = None
    for days, trigger, commit, kind, status in _TABS_RELEASE_HISTORY:
        started = now - timedelta(days=days)
        rid = _tabs_release_id(started, commit)
        lines, error = _tabs_release_log(
            kind, source=source, commit=commit, release_id=rid, previous=previous, port=release.port
        )
        deployment_id = _tabs_deployment(
            store,
            TABS_RELEASE_APP,
            trigger=trigger,
            commit=commit,
            started=started,
            lines=lines,
            status=status,
            error=error,
        )
        # What the recorder notes once the fetch has named the release: a deployment on
        # releases can be gone back to while its release is on disk and not serving.
        store.annotate_deployment(deployment_id, release_id=rid)
        release.deploys.append((started, commit, kind, status, rid))
        if status == "success":
            previous = rid
    apps[TABS_RELEASE_APP] = release

    from noust.core.utils import domain_to_app_name

    for domain, history in _TABS_INPLACE_HISTORY.items():
        seeded = _TabsApp(port=_tabs_serve_ok())
        for days, trigger, commit in history:
            started = now - timedelta(days=days)
            _tabs_deployment(
                store,
                domain,
                trigger=trigger,
                commit=commit,
                started=started,
                lines=_tabs_inplace_log(
                    domain,
                    commit=commit,
                    source="https://github.com/example-org/" + domain_to_app_name(domain),
                    port=seeded.port,
                ),
                status="success",
            )
            seeded.deploys.append((started, commit, "ok", "success", ""))
        apps[domain] = seeded
    seed_deploy_actions_history(store, apps)
    return apps


#: Commit subjects for the tabs apps' seeded history, keyed by the short hash
#: each row already carries. Real deploys and updates learn theirs from
#: ``git log -1 --format=%s`` on a real checkout (see
#: ``_commit_message_for_recording``); the fake ``git`` handler above answers
#: that for anything the sandbox actually runs. The tabs' history predates the
#: sandbox entirely - :func:`_tabs_deployment` writes each row straight
#: through the store - so nothing ever asks git for theirs.
_TABS_COMMIT_MESSAGES: dict[str, str] = {
    "3c1e9a0": "Add product search filters",
    "7f2d4b1": "Fix checkout total rounding",
    "a94c0e2": "Add order CSV export",
    "b1e7f93": "Bump Next.js to 15.2",
    "c5d2a61": "Rework the cart summary layout",
    "d08e4f7": "Add Stripe webhook retries",
    "e3b9c12": "Cache product listings",
    "f41a8d3": "Add a wishlist page",
    "0c7e5b9": "Tune image loader sizes",
    "19d3f6e": "Update footer legal links",
    "2a8b7c4": "Add gift card redemption",
    "4e1f2a7": "Add delivery tracking page",
    "8b3c9d0": "Fix invoice PDF totals",
    "5a7e1c3": "Add an order status webhook",
    "9d4e2b8": "Initial import",
}


def seed_deploylinks_commit_messages(store: Any) -> None:
    """
    Back-fill ``commit_message`` on the tabs apps' seeded deployment history.

    :func:`_tabs_deployment` records each row directly through the store, not
    through the deploy pipeline that would normally learn the subject line
    from git and annotate it: without this, every one of those rows would be
    the one case in the modelled machine where a deployment has a commit but
    no message, unlike a deploy or update actually run through the sandbox.
    This calls the same store method the real pipeline does, keyed by the
    commit each row was already written with.

    Args:
        store: The seeded store, after :func:`seed_app_tabs_history` ran.
    """
    for domain in (TABS_RELEASE_APP, TABS_LIVE_APP, *TABS_MIGRATE_APPS):
        for record in store.list_deployments(domain=domain, limit=100):
            if record.id is None or record.commit_message is not None:
                continue
            message = _TABS_COMMIT_MESSAGES.get(record.git_commit or "")
            if message is not None:
                store.annotate_deployment(record.id, commit_message=message)


def _tabs_release_app(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    seeded: _TabsApp,
) -> None:
    """
    Seed the release-layout application: its tree, its releases, its unit and site.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        seeded: Its port and history, from :func:`seed_app_tabs_history`.
    """
    from datetime import timezone

    from noust.core.store import App, ReleaseRecord
    from noust.core.utils import domain_to_app_name

    domain = TABS_RELEASE_APP
    root = sandbox.apps_dir / domain_to_app_name(domain)
    port = seeded.port
    env = (
        "NODE_ENV=production\n"
        f"PORT={port}\n"
        "DATABASE_URL=postgres://tienda:K9v2xQ7mLp@127.0.0.1:5432/tienda\n"
        "REDIS_URL=redis://127.0.0.1:6379/2\n"
        "SESSION_SECRET=6b1f0e4c2d9a8b7e5f3c1a0d9e8f7a6b\n"
        "STRIPE_SECRET_KEY=sk_l" + "ive_51Hx8exampleShopSeeded\n"
        "STRIPE_PUBLISHABLE_KEY=pk_l" + "ive_51Hx8exampleShopSeeded\n"
        "NEXT_PUBLIC_SITE_URL=https://tienda.example.org\n"
        "MAIL_FROM=Example Shop <orders@example.org>\n"
        "LOG_LEVEL=info\n"
    )
    shared = root / "shared"
    (shared / "uploads" / "products").mkdir(parents=True, exist_ok=True)
    (shared / "uploads" / "products" / "88.webp").write_text("seeded image\n", encoding="utf-8")
    (shared / ".env").write_text(env, encoding="utf-8")
    (shared / ".env").chmod(0o600)
    (root / "repo").mkdir(parents=True, exist_ok=True)
    (root / "releases").mkdir(parents=True, exist_ok=True)

    app = App(
        domain=domain,
        app_type="nextjs",
        source="https://github.com/example-org/tienda.git",
        branch="main",
        port=port,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
        layout="releases",
        keep_releases=5,
        persistent_paths=["uploads"],
        memory_max_mb=512,
        cpu_quota_percent=150,
        tasks_max=256,
    )
    _tabs_register(sandbox, store, units, ports, domains, app, working_directory=root / "current")
    stored = store.get_app(domain)
    store.set_webhook_secret(domain, "seeded-webhook-secret-not-shown")
    if stored is None or stored.id is None:
        return

    serving: str | None = None
    for number, (started, commit, kind, status, rid) in enumerate(seeded.deploys, start=1):
        if commit in _TABS_ON_DISK and status == "success":
            release_dir = root / "releases" / rid
            release_dir.mkdir(parents=True, exist_ok=True)
            (release_dir / "package.json").write_text(
                json.dumps({"name": "tienda", "version": f"3.{number}.0"}) + "\n",
                encoding="utf-8",
            )
            (release_dir / ".next").mkdir(exist_ok=True)
            (release_dir / ".next" / "BUILD_ID").write_text(commit + "\n", encoding="utf-8")
            for name, target in (
                (".env", "../../shared/.env"),
                ("uploads", "../../shared/uploads"),
            ):
                link = release_dir / name
                if not link.is_symlink():
                    link.symlink_to(target)
        if (status == "success" and commit in _TABS_ON_DISK) or kind == "health":
            store.record_release(
                ReleaseRecord(
                    id=rid,
                    app_id=stored.id,
                    git_commit=commit,
                    created_at=started.astimezone(timezone.utc).isoformat(),
                    activated_at=None
                    if kind == "health"
                    else (started + timedelta(minutes=1)).astimezone(timezone.utc).isoformat(),
                    status="failed" if kind == "health" else "superseded",
                    path=str(root / "releases" / rid),
                )
            )
        if status == "success":
            serving = rid

    if serving is not None:
        (root / "current").symlink_to(Path("releases") / serving)
        store.mark_release_active(stored.id, serving)


def _tabs_inplace_app(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    domain: str,
    seeded: _TabsApp,
) -> Path:
    """
    Seed an application deployed in place, with a real tree in the sandbox.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        domain: The application.
        seeded: Its port and history, from :func:`seed_app_tabs_history`.

    Returns:
        Its directory.
    """
    from noust.core.store import App
    from noust.core.utils import domain_to_app_name

    name = domain_to_app_name(domain)
    root = sandbox.apps_dir / name
    # Recorded as a local directory: an update of a tree that is not a git
    # checkout fetches its recorded source again, which here is this copy.
    source_dir = sandbox.root / "sources" / name
    port = seeded.port
    env = (
        "# Written by noust env configure\n"
        "NODE_ENV=production\n"
        f"PORT={port}\n"
        f"DATABASE_URL=postgres://{name.split('-')[0]}:Wm4p8Zr2@127.0.0.1:5432/{name.split('-')[0]}\n"
        "JWT_SECRET=2f7c9e1a4b6d8f0a3c5e7b9d1f2a4c6e\n"
        f"PUBLIC_URL=https://{domain}\n"
        "SMTP_HOST=smtp.example.org\n"
        "SMTP_PASSWORD=\n"
        "FEATURE_FLAGS=checkout-v2,fast-search\n"
    )
    for tree in (root, source_dir):
        _tabs_node_tree(tree, name.split("-")[0], env=env, uploads=tree == root)
    app = App(
        domain=domain,
        app_type="nodejs",
        source=str(source_dir),
        branch="main",
        port=port,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
    )
    _tabs_register(sandbox, store, units, ports, domains, app, working_directory=root)
    return root


def _tabs_metrics(domain: str, deploys: Sequence[datetime], *, base_mb: float) -> None:
    """
    Write thirty days of CPU and memory history for an application.

    The collector reads an app's cgroup, which no sandboxed unit has, so the
    history the Metrics tab draws is written here: denser the more recent,
    as the store's own tiers keep it, with memory falling back after every
    deploy (the process restarted) and a CPU burst while each one warmed up.

    Args:
        domain: The application.
        deploys: When it was deployed.
        base_mb: Its resident memory just after a start, in MB.
    """
    import math

    from noust.web.metrics_collector import get_metrics_store

    store = get_metrics_store()
    now = int(time.time())
    cpu_name, mem_name = f"app.{domain}.cpu.percent", f"app.{domain}.mem.bytes"
    stamps = [
        *range(now - 30 * 86_400, now - 86_400, 1_800),
        *range(now - 86_400, now - 3_600, 120),
        *range(now - 3_600, now, 10),
    ]
    deploy_times = sorted(int(moment.timestamp()) for moment in deploys)
    for stamp in stamps:
        since = [stamp - t for t in deploy_times if t <= stamp]
        age = min(since) if since else 30 * 86_400
        daily = math.sin((stamp % 86_400) / 86_400 * 2 * math.pi - math.pi / 2)
        wobble = math.sin(stamp / 977.0) * 0.6 + math.sin(stamp / 331.0) * 0.4
        cpu = 3.2 + 2.4 * daily + 1.1 * wobble + (38.0 * math.exp(-age / 240.0))
        memory_mb = base_mb + min(age / 3_600, 96) * 0.9 + 6 * wobble
        store.record_many(
            [(cpu_name, max(0.1, cpu)), (mem_name, memory_mb * 1024 * 1024)], ts=stamp
        )
    store.consolidate(now=now)


def _tabs_slow_live_builds(root: Path) -> None:
    """
    Stream npm's output for the live app a line at a time, as a real build does.

    The runner answers every command at once; an update would be over before
    the console could show it running. Only npm under the live app's tree is
    slowed down; everything else is answered as before.

    Args:
        root: The live app's directory.
    """
    from noust.core.runner import CommandResult, get_runner

    runner = get_runner()
    original = runner.stream

    def stream(argv: Sequence[str], *, on_line: Any, cwd: Path | None = None, **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        inside = cwd is not None and (Path(cwd) == root or root in Path(cwd).parents)
        if not (inside and "npm" in args):
            return original(argv, on_line=on_line, cwd=cwd, **kwargs)
        runner.calls.append(args)
        output = _TABS_NPM_BUILD if "build" in args else _TABS_NPM_INSTALL
        for line in output:
            time.sleep(TABS_BUILD_LINE_DELAY)
            on_line(line)
        return CommandResult(argv=args, exit_code=0, stdout="\n".join(output) + "\n", stderr="")

    runner.stream = stream  # type: ignore[method-assign]


class _TabsJournalFollow:
    """
    ``journalctl -u <unit> -f`` over the modelled machine, as asyncio sees a process.

    The logs WebSocket spawns journalctl itself; here it gets this instead: a
    backlog in ``short-iso`` form from the unit's state, then a request line
    every :data:`TABS_JOURNAL_TICK` seconds while the unit is active.
    """

    def __init__(
        self,
        args: Sequence[str],
        units: dict[str, Unit],
        domains: dict[str, str],
        ports: dict[str, int],
    ) -> None:
        """
        Args:
            args: The journalctl argv.
            units: The modelled units.
            domains: Each unit's domain.
            ports: Each unit's port.
        """
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self._unit = args[args.index("-u") + 1].removesuffix(".service") if "-u" in args else ""
        backlog = int(args[args.index("-n") + 1]) if "-n" in args else 10
        self._state = units.get(self._unit)
        self._domain = domains.get(self._unit, self._unit)
        self._port = ports.get(self._unit, 3000)
        self._count = 0
        for line in self._backlog()[-backlog:]:
            self.stdout.feed_data(f"{line}\n".encode())
        self._task: asyncio.Task[None] | None = (
            asyncio.get_running_loop().create_task(self._follow())
            if "-f" in args and self._state is not None and self._state.active == "active"
            else None
        )

    def _line(self, moment: datetime, message: str) -> str:
        pid = self._state.pid if self._state and self._state.pid else 1
        stamp = moment.astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        return f"{stamp} acme {self._unit}[{pid}]: {message}"

    def _backlog(self) -> list[str]:
        failed = self._state is not None and self._state.active == "failed"
        template = FAILED_JOURNAL_LINES if failed else JOURNAL_LINES
        now = datetime.now()
        start = now - timedelta(minutes=70)
        lines = [
            self._line(
                start + timedelta(seconds=i),
                text.format(unit=self._unit, domain=self._domain, port=self._port),
            )
            for i, text in enumerate(template)
        ]
        if not failed:
            for i in range(96):
                moment = start + timedelta(seconds=40 * (i + 1))
                lines.append(self._line(moment, _TABS_REQUESTS[i % len(_TABS_REQUESTS)]))
        return lines

    async def _follow(self) -> None:
        while self.returncode is None:
            await asyncio.sleep(TABS_JOURNAL_TICK)
            message = _TABS_REQUESTS[(self._count * 3) % len(_TABS_REQUESTS)]
            self._count += 1
            self.stdout.feed_data(f"{self._line(datetime.now(), message)}\n".encode())

    def terminate(self) -> None:
        """Stop following, as SIGTERM would."""
        if self.returncode is None:
            self.returncode = -15
            if self._task is not None:
                self._task.cancel()
            self.stdout.feed_eof()

    def kill(self) -> None:
        """Stop following, as SIGKILL would."""
        self.terminate()

    async def wait(self) -> int:
        """
        Returns:
            The exit status.
        """
        return self.returncode if self.returncode is not None else 0


def _tabs_journal_model(
    units: dict[str, Unit], domains: dict[str, str], ports: dict[str, int]
) -> None:
    """
    Answer the logs WebSocket's ``journalctl -f`` from the modelled machine.

    Every other spawn is still refused by :func:`forbid_real_processes`.

    Args:
        units: The modelled units.
        domains: Each unit's domain.
        ports: Each unit's port.
    """
    refuse = asyncio.create_subprocess_exec

    async def spawn(*argv: Any, **kwargs: Any) -> Any:
        args = [str(a) for a in argv]
        if args and args[0] == "journalctl":
            return _TabsJournalFollow(args, units, domains, ports)
        return await refuse(*argv, **kwargs)

    asyncio.create_subprocess_exec = spawn  # type: ignore[assignment]


def _tabs_diagnose_model(units: dict[str, Unit], ports: dict[str, int]) -> None:
    """
    Answer the questions ``noust diagnose`` asks that the runner could not.

    ``ss -ltnpH`` lists the port of every active modelled unit, owned by its
    main PID, ``systemctl show -p A,B <unit>`` answers the properties asked
    for from the unit's state, and the kernel log (``journalctl -k``) has no
    OOM kills. Without them every app diagnoses as "down: not listening",
    whatever its state.

    Args:
        units: The modelled units.
        ports: Each unit's port.
    """
    from noust.core.runner import CommandResult, get_runner

    runner = get_runner()
    original = runner.run

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        if args[:2] == ("ss", "-ltnpH"):
            runner.calls.append(args)
            listening = [
                f"LISTEN 0 511 127.0.0.1:{ports[name]} 0.0.0.0:* "
                f'users:(("node",pid={unit.pid},fd=19))'
                for name, unit in sorted(units.items())
                if unit.active == "active" and name in ports
            ]
            return CommandResult(args, 0, "\n".join(listening) + "\n", "")
        if args[:1] == ("journalctl",) and "-k" in args:
            # The kernel log of the modelled machine has no OOM kills in it; without
            # this, every failed app's diagnosis names memory as the probable cause.
            runner.calls.append(args)
            return CommandResult(args, 0, "", "")
        if args[:3] == ("systemctl", "show", "-p") and len(args) >= 5:
            unit = units.get(args[-1].removesuffix(".service"))
            if unit is not None:
                runner.calls.append(args)
                failed = unit.active == "failed"
                known = {
                    "ActiveState": unit.active,
                    "SubState": unit.sub,
                    "Result": "exit-code" if failed else "success",
                    "ExecMainStatus": "1" if failed else "0",
                    "NRestarts": str(unit.restarts),
                    "MainPID": str(unit.pid),
                }
                wanted = args[3].split(",")
                return CommandResult(
                    args, 0, "".join(f"{key}={known.get(key, '')}\n" for key in wanted), ""
                )
        return original(argv, **kwargs)

    runner.run = run  # type: ignore[method-assign]


def _tabs_port_model(units: dict[str, Unit], ports: dict[str, int]) -> None:
    """
    Say a port answers when an active modelled unit listens on it.

    An application's state checks that its port accepts connections
    (:func:`noust.core.app_state.port_answers`). The seeded units run no
    process, so without this every running app reads "No answer"; a port a
    real listener holds (the health servers above) still answers for real.

    Args:
        units: The modelled units.
        ports: Each unit's port.
    """
    import noust.core.app_state as app_state_module

    real = app_state_module.port_answers

    def port_answers(port: int, *args: Any, **kwargs: Any) -> bool:
        modelled = any(
            unit.active == "active" and ports.get(name) == port for name, unit in units.items()
        )
        return modelled or real(port, *args, **kwargs)

    app_state_module.port_answers = port_answers  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Console: "Investigate this stretch" (3.2, flow F1). A CPU peak of the release
# app half an hour ago with the processes behind it, minute by minute, and the
# unit failure the monitor saw then; the collector's own process ranking is
# switched off, so this sandbox never records the development machine's
# processes (or their command lines).
# ---------------------------------------------------------------------------

#: How long ago the seeded peak was, in minutes, and how many minutes it lasted.
TIMELINE_PEAK_MINUTES_AGO = 40
TIMELINE_PEAK_LENGTH = 10


def quiet_process_sampling() -> None:
    """Keep the collector from ranking this development machine's own processes."""
    import noust.monitor.process_samples as process_samples

    process_samples.read_process_table = lambda: []  # type: ignore[assignment]


def seed_timeline_peak(domain: str) -> None:
    """
    Write the processes of a CPU peak, and what the monitor saw during it.

    Args:
        domain: The application whose build caused the peak.
    """
    from noust.monitor.process_samples import RANK_CPU, RANK_MEMORY, ProcessSample
    from noust.monitor.timeseries import EVENT_UNIT_FAILED
    from noust.web.metrics_collector import get_metrics_store

    quiet_process_sampling()
    store = get_metrics_store()
    unit = domain.replace(".", "-")
    first = (int(time.time()) // 60 - TIMELINE_PEAK_MINUTES_AGO) * 60
    rows: list[ProcessSample] = []
    for minute in range(TIMELINE_PEAK_LENGTH):
        ts = first + minute * 60
        build = 92.0 - abs(minute - TIMELINE_PEAK_LENGTH / 2) * 6
        cpu = [
            (
                "node",
                4211,
                "www-data",
                build,
                610,
                domain,
                f"{unit}.service",
                "node /var/www/apps/tienda-example-org/current/node_modules/.bin/next build",
            ),
            (
                "postgres",
                990,
                "postgres",
                14.0,
                880,
                None,
                "postgresql@16-main.service",
                "postgres: 16/main: tienda tienda 127.0.0.1(51122) SELECT",
            ),
            ("nginx", 1021, "www-data", 3.1, 42, None, "nginx.service", "nginx: worker process"),
        ]
        memory = [
            cpu[1],
            cpu[0],
            (
                "redis-server",
                977,
                "redis",
                0.4,
                210,
                None,
                "redis-server.service",
                "/usr/bin/redis-server 127.0.0.1:6379",
            ),
        ]
        for rank, ranked in ((RANK_CPU, cpu), (RANK_MEMORY, memory)):
            for position, (name, pid, user, percent, mb, app, owner, command) in enumerate(
                ranked, start=1
            ):
                rows.append(
                    ProcessSample(
                        ts=ts,
                        rank=rank,
                        position=position,
                        pid=pid,
                        name=name,
                        user=user,
                        cpu_percent=percent,
                        memory_bytes=mb * 1024 * 1024,
                        memory_percent=round(mb / 16_384 * 100, 2),
                        app=app,
                        owner_kind="unit",
                        owner=owner,
                        command=command,
                    )
                )
    store.record_process_samples(rows)
    store.record_monitor_event(
        EVENT_UNIT_FAILED,
        unit,
        detail=f"systemd reports {unit} failed: result exit-code, main process exit status 1.",
        reason="failed",
        ts=first + (TIMELINE_PEAK_LENGTH - 2) * 60,
    )


def seed_app_tabs(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    history: dict[str, _TabsApp],
) -> None:
    """
    Seed what an application's tabs need beyond :func:`seed_console_state`.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        history: What :func:`seed_app_tabs_history` recorded first.
    """
    _tabs_release_app(sandbox, store, units, ports, domains, history[TABS_RELEASE_APP])
    live_root = _tabs_inplace_app(
        sandbox, store, units, ports, domains, TABS_LIVE_APP, history[TABS_LIVE_APP]
    )
    for domain in TABS_MIGRATE_APPS:
        _tabs_inplace_app(sandbox, store, units, ports, domains, domain, history[domain])
    # The in-place app rolls back to a backup: give it the two a machine would have.
    seed_backups(sandbox, [TABS_LIVE_APP])
    _tabs_metrics(TABS_RELEASE_APP, history[TABS_RELEASE_APP].starts, base_mb=182)
    _tabs_metrics(TABS_LIVE_APP, history[TABS_LIVE_APP].starts, base_mb=96)
    seed_timeline_peak(TABS_RELEASE_APP)
    _tabs_slow_live_builds(live_root)
    seed_deploy_actions(sandbox, store, units, ports, domains, history)
    _tabs_journal_model(units, domains, ports)
    _tabs_diagnose_model(units, ports)
    _tabs_port_model(units, ports)


# ---------------------------------------------------------------------------
# Console: a deploy's actions (rebuild its commit, roll back to it), an update
# with nothing new, a static site's deploy and a source the wizard cannot
# deploy as it is. The tabs' release app already has releases on disk to go
# back to; this adds what else those screens branch on: an in-place
# deployment whose snapshot backup still exists, an app on releases whose
# branch head is the commit that is live, a static site's deploy (no health
# check) and a repository holding a lone Dockerfile.
# ---------------------------------------------------------------------------

#: On releases, with a git source whose branch head (as the modelled remote
#: answers `git ls-remote`) is the commit that is live: an update from the
#: console answers 409 nothing_new.
NOTHING_NEW_APP = "catalogo.example.org"

#: Its repository, and the commit both its live release and the remote's main are at.
NOTHING_NEW_SOURCE = "https://github.com/example-org/catalogo.git"
NOTHING_NEW_COMMIT = "6d1e0b27c4a95f3e8d20b61c7a4f9e5d3b2c1a08"

#: A static site with one deploy of ten seconds in its history: its Health phase does not apply.
STATIC_DEPLOY_APP = "bodas.example.com"

#: The wizard's source that is a lone Dockerfile: no type matches, and the verdict says to add compose.
WIZARD_DOCKERFILE_SOURCE = "container-api"


def seed_deploy_actions_history(store: Any, apps: dict[str, _TabsApp]) -> None:
    """
    Record the deployments the deploy actions need, with the tabs' history.

    Recorded before :func:`seed_console_state`'s deploys of "now", for the
    same reason the tabs' are: the API orders deployments by id.

    Args:
        store: The store.
        apps: The tabs' applications, given the nothing-new app's port and
            deployment here.
    """
    now = datetime.now()

    started = now - timedelta(days=3, hours=2)
    commit = NOTHING_NEW_COMMIT[:7]
    rid = _tabs_release_id(started, commit)
    seeded = _TabsApp(port=_tabs_serve_ok())
    deployment_id = _tabs_deployment(
        store,
        NOTHING_NEW_APP,
        trigger="webhook",
        commit=commit,
        started=started,
        lines=[
            (0, "[1/9] Fetching source into a new release..."),
            (0.4, f"      → Source: {NOTHING_NEW_SOURCE} (main)"),
            (1.6, f"      → HEAD is now at {commit}"),
            (1.7, f"      → Release: {rid}"),
            (1.9, "[2/9] Installing dependencies..."),
            (2.0, "      → Running: npm ci"),
            (11.2, "added 402 packages, and audited 403 packages in 9s"),
            (11.4, "[3/9] Building application..."),
            (11.5, "      → Running: npm run build"),
            (29.8, " ✓ Compiled successfully in 17.9s"),
            (30.1, "[8/9] Activating release..."),
            (30.2, f"      → Activated release {rid}"),
            (30.3, f"      → Checking: http://127.0.0.1:{seeded.port}/"),
            (30.6, f"✓ Release {rid} answered 200 in 61 ms"),
            (30.7, f"✓ Deployed {NOTHING_NEW_APP}"),
        ],
        status="success",
    )
    store.annotate_deployment(
        deployment_id, release_id=rid, commit_message="Show stock per warehouse"
    )
    seeded.deploys.append((started, commit, "ok", "success", rid))
    apps[NOTHING_NEW_APP] = seeded

    # A static deploy: the web server serves the files, so nothing is probed.
    _tabs_deployment(
        store,
        STATIC_DEPLOY_APP,
        trigger="panel",
        commit="4c2e9f1",
        started=now - timedelta(days=2, hours=5),
        lines=[
            (0, "[1/6] Fetching source code..."),
            (0.3, "      → Source: https://github.com/you/landing"),
            (1.2, "      → HEAD is now at 4c2e9f1"),
            (1.4, "[2/6] Setting permissions..."),
            (2.0, "[3/6] Creating site configuration..."),
            (2.6, "[4/6] Obtaining SSL certificate..."),
            (2.7, "      → The existing certificate keeps serving what it covers"),
            (6.0, "[5/6] Activating site..."),
            (10.0, f"✓ Deployed {STATIC_DEPLOY_APP}"),
        ],
        status="success",
    )


def _nothing_new_ls_remote(args: tuple[str, ...]) -> Any:
    """
    Answer ``git ls-remote`` for the nothing-new app's repository.

    Args:
        args: The argv the runner was asked to run.

    Returns:
        Its ``main`` at the live commit, or None for any other command.
    """
    from noust.core.runner import CommandResult

    if args[:1] != ("git",) or "ls-remote" not in args or NOTHING_NEW_SOURCE not in args:
        return None
    return CommandResult(args, 0, f"{NOTHING_NEW_COMMIT}\trefs/heads/main\n", "")


def seed_deploy_actions(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    history: dict[str, _TabsApp],
) -> None:
    """
    Seed the states a deploy's actions and an update's "nothing new" branch on.

    - The in-place app's deployment before its live one gets the pre-update
      backup the update after it took, as its snapshot: it can be gone back to.
    - The nothing-new app, on releases with its one release serving.
    - The wizard's lone-Dockerfile source.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        history: What :func:`seed_app_tabs_history` recorded first.
    """
    from datetime import timezone

    from noust.core.runner import get_runner
    from noust.core.store import App, ReleaseRecord
    from noust.core.utils import domain_to_app_name

    # The snapshot: the older of the two backups seed_backups gave the in-place app.
    backups = sandbox.backup_dir / domain_to_app_name(TABS_LIVE_APP)
    pre_deploy = sorted(
        path.stem
        for path in backups.glob("*.json")
        if "pre-deploy" in json.loads(path.read_text(encoding="utf-8")).get("tags", [])
    )
    live_history = store.list_deployments(domain=TABS_LIVE_APP, limit=100)
    if pre_deploy and len(live_history) >= 2 and live_history[1].id is not None:
        store.set_deployment_snapshot(live_history[1].id, pre_deploy[0])

    seeded = history[NOTHING_NEW_APP]
    started, commit, _, _, rid = seeded.deploys[0]
    root = sandbox.apps_dir / domain_to_app_name(NOTHING_NEW_APP)
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / ".env").write_text(f"NODE_ENV=production\nPORT={seeded.port}\n", encoding="utf-8")
    (shared / ".env").chmod(0o600)
    (root / "repo").mkdir(parents=True, exist_ok=True)
    release_dir = root / "releases" / rid
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / "package.json").write_text(
        json.dumps({"name": "catalogo", "version": "1.8.0"}) + "\n", encoding="utf-8"
    )
    (release_dir / ".env").symlink_to("../../shared/.env")
    app = App(
        domain=NOTHING_NEW_APP,
        app_type="nextjs",
        source=NOTHING_NEW_SOURCE,
        branch="main",
        port=seeded.port,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
        layout="releases",
        keep_releases=5,
    )
    _tabs_register(sandbox, store, units, ports, domains, app, working_directory=root / "current")
    stored = store.get_app(NOTHING_NEW_APP)
    if stored is not None and stored.id is not None:
        store.record_release(
            ReleaseRecord(
                id=rid,
                app_id=stored.id,
                git_commit=commit,
                created_at=started.astimezone(timezone.utc).isoformat(),
                activated_at=(started + timedelta(seconds=31)).astimezone(timezone.utc).isoformat(),
                status="superseded",
                path=str(release_dir),
            )
        )
        (root / "current").symlink_to(Path("releases") / rid)
        store.mark_release_active(stored.id, rid)

    runner = get_runner()
    original = runner.run

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        answer = _nothing_new_ls_remote(args)
        if answer is None:
            return original(argv, **kwargs)
        runner.calls.append(args)
        return answer

    runner.run = run  # type: ignore[method-assign]

    # Only a Dockerfile: Noust runs containers through Compose, and says so.
    container = sandbox.var / "www" / "src" / WIZARD_DOCKERFILE_SOURCE
    container.mkdir(parents=True, exist_ok=True)
    (container / "Dockerfile").write_text(
        "FROM node:22-alpine\nWORKDIR /app\nCOPY . .\nRUN npm ci\nEXPOSE 3000\n"
        'CMD ["node", "server.js"]\n',
        encoding="utf-8",
    )
    (container / "server.js").write_text(
        "require('http').createServer((q, s) => s.end('ok')).listen(3000);\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Console: the Domains and certificates page, an application's Domains tab and
# the new-app wizard need a web server, certificates and DNS that behave, and
# sources to point the wizard at. Every seeded site gets a real configuration
# file rendered by the managers' own templates, so the editor has something to
# load; nginx's syntax check reads the files it is pointed at, so a broken edit
# fails the way nginx fails; certbot issues, expands, renews, revokes and
# deletes lineages on disk, and refuses a name the modelled DNS does not point
# here; that DNS answers from a fixed zone instead of the real network; and
# /var/www/src holds a Next.js project and a static site to inspect and deploy.
# ---------------------------------------------------------------------------

#: This machine's public addresses, as the modelled DNS knows them.
DOMAINS_MACHINE_ADDRESSES = ("203.0.113.10", "2001:db8::10")

#: Where a name that points at another server resolves.
DOMAINS_ELSEWHERE_ADDRESSES = ("198.51.100.23",)

#: Names under a seeded zone starting with these point at another server.
DOMAINS_ELSEWHERE_PREFIXES = ("old.", "legacy.")

#: Names under a seeded zone starting with these have no record yet.
DOMAINS_UNRESOLVED_PREFIXES = ("new.", "soon.")

#: The wizard's sources, under the sandbox's stand-in for /var/www/src.
WIZARD_SOURCES = ("storefront", "landing")

_DOMAINS_PEM = "-----BEGIN CERTIFICATE-----\nc2FuZGJveA==\n-----END CERTIFICATE-----\n"

_DOMAINS_CERTBOT_LOG = "Saving debug log to /var/log/letsencrypt/letsencrypt.log"

#: What certbot adds after a failed challenge, per authenticator.
_DOMAINS_CERTBOT_HINTS = {
    "nginx": "The Certificate Authority failed to verify the temporary {server} configuration "
    "changes made by Certbot. Ensure the listed domains point to this {server} server and "
    "that it is accessible from the internet.",
    "webroot": "The Certificate Authority failed to download the temporary challenge files "
    "created by Certbot. Ensure that the listed domains serve their content from the provided "
    "--webroot-path/-w and that files created there can be downloaded from the internet.",
    "standalone": "The Certificate Authority failed to download the challenge files from the "
    "temporary standalone webserver started by Certbot on port 80. Ensure that the listed "
    "domains point to this machine and that it can accept inbound connections from the "
    "internet.",
}
_DOMAINS_CERTBOT_HINTS["apache"] = _DOMAINS_CERTBOT_HINTS["nginx"]


def _domains_resolve(name: str, zones: frozenset[str]) -> tuple[str, ...]:
    """
    Answer a name from the modelled DNS.

    Args:
        name: The name to resolve.
        zones: The registrable domains the machine's applications live under.

    Returns:
        The addresses it resolves to; empty when it has no record.
    """
    name = name.strip().lower().rstrip(".")
    if not any(name == zone or name.endswith(f".{zone}") for zone in zones):
        return ()
    if name.startswith(DOMAINS_UNRESOLVED_PREFIXES):
        return ()
    if name.startswith(DOMAINS_ELSEWHERE_PREFIXES):
        return DOMAINS_ELSEWHERE_ADDRESSES
    return DOMAINS_MACHINE_ADDRESSES


def _domains_points_here(name: str, zones: frozenset[str]) -> bool:
    """
    Args:
        name: A domain.
        zones: The modelled DNS zones.

    Returns:
        True when every address the name resolves to is this machine's.
    """
    resolved = _domains_resolve(name, zones)
    return bool(resolved) and set(resolved) <= set(DOMAINS_MACHINE_ADDRESSES)


#: Arguments the simple directives of Noust's templates take: (fewest, most).
#: A lost semicolon joins two statements into one, which nginx reports as the
#: wrong number of arguments of the first.
_DOMAINS_NGINX_ARITY: dict[str, tuple[int, int]] = {
    "access_log": (1, 4),
    "add_header": (2, 3),
    "allow": (1, 1),
    "client_max_body_size": (1, 1),
    "deny": (1, 1),
    "error_log": (1, 2),
    "expires": (1, 2),
    "gzip": (1, 1),
    "gzip_comp_level": (1, 1),
    "gzip_proxied": (1, 9),
    "gzip_types": (1, 64),
    "gzip_vary": (1, 1),
    "include": (1, 1),
    "index": (1, 16),
    "listen": (1, 16),
    "proxy_cache_bypass": (1, 16),
    "proxy_connect_timeout": (1, 1),
    "proxy_http_version": (1, 1),
    "proxy_pass": (1, 1),
    "proxy_read_timeout": (1, 1),
    "proxy_send_timeout": (1, 1),
    "proxy_set_header": (2, 2),
    "return": (1, 2),
    "root": (1, 1),
    "server_name": (1, 64),
    "ssl_certificate": (1, 1),
    "ssl_certificate_key": (1, 1),
    "ssl_ciphers": (1, 1),
    "ssl_prefer_server_ciphers": (1, 1),
    "ssl_protocols": (1, 8),
    "ssl_session_cache": (1, 2),
    "ssl_session_timeout": (1, 1),
    "ssl_stapling": (1, 1),
    "ssl_stapling_verify": (1, 1),
    "try_files": (2, 16),
}


def _domains_nginx_syntax(text: str) -> tuple[str, int] | None:
    """
    Check a configuration the way nginx's parser does.

    The grammar - statements end in ``;``, blocks open with ``{`` after a
    directive and close with ``}``, strings are quoted, ``#`` starts a
    comment, ``${var}`` belongs to its token - and the argument count of the
    simple directives Noust's templates use. That is enough for the mistakes
    a hand edit makes (a lost semicolon, a lost brace), reported in nginx's
    own words.

    Args:
        text: The configuration.

    Returns:
        ``(message, line)`` for the first error, or None when it parses.
    """
    depth = 0
    statement: list[str] = []
    line = 1
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\n":
            line += 1
            index += 1
        elif char in " \t\r":
            index += 1
        elif char == "#":
            while index < length and text[index] != "\n":
                index += 1
        elif char == ";":
            if not statement:
                return 'unexpected ";"', line
            fewest, most = _DOMAINS_NGINX_ARITY.get(statement[0], (0, 1024))
            if not fewest <= len(statement) - 1 <= most:
                return f'invalid number of arguments in "{statement[0]}" directive', line
            statement = []
            index += 1
        elif char == "{":
            if not statement:
                return 'unexpected "{"', line
            depth += 1
            statement = []
            index += 1
        elif char == "}":
            if statement or depth == 0:
                return 'unexpected "}"', line
            depth -= 1
            index += 1
        elif char in "\"'":
            start, start_line = index, line
            index += 1
            while index < length and text[index] != char:
                if text[index] == "\\":
                    index += 1
                if index < length and text[index] == "\n":
                    line += 1
                index += 1
            if index >= length:
                return 'unexpected end of file, expecting ";" or "}"', start_line
            index += 1
            statement.append(text[start:index])
        else:
            start = index
            while index < length and text[index] not in " \t\r\n;{}\"'":
                if text.startswith("${", index):
                    close = text.find("}", index)
                    index = length if close == -1 else close + 1
                    continue
                index += 1
            statement.append(text[start:index])
    if statement:
        return 'unexpected end of file, expecting ";" or "}"', line
    if depth > 0:
        return 'unexpected end of file, expecting "}"', line
    return None


class _DomainsWebTools:
    """
    nginx's configuration test and certbot, answered from the sandbox's files.

    Attributes:
        sandbox: The sandbox.
        zones: The modelled DNS zones.
        lineages: Certificate name to the domains it covers.
        expiry: Certificate name to when it expires.
        revoked: Certificate names revoked but not deleted.
    """

    def __init__(self, sandbox: Sandbox, zones: frozenset[str]) -> None:
        """
        Args:
            sandbox: The sandbox.
            zones: The modelled DNS zones.
        """
        self.sandbox = sandbox
        self.zones = zones
        self.lineages: dict[str, list[str]] = {}
        self.expiry: dict[str, datetime] = {}
        self.revoked: set[str] = set()
        self._lock = threading.Lock()

    @property
    def live_dir(self) -> Path:
        """The sandbox's /etc/letsencrypt/live."""
        return self.sandbox.etc / "letsencrypt" / "live"

    def shown(self, path: Path | str) -> str:
        """
        Args:
            path: A path inside the sandbox.

        Returns:
            The path as the machine it stands in for would print it.
        """
        text = str(path)
        root = str(self.sandbox.root)
        return text[len(root) :] if text.startswith(root) else text

    def seed(
        self,
        names: Sequence[str],
        *,
        first_expired: bool = False,
        alt_names: dict[str, str] | None = None,
    ) -> None:
        """
        Issue the seeded certificates: the name and its www, the expiry
        :func:`make_runner` always gave them (12, 29, 46... days).

        Args:
            names: Certificate names, in the order the store seeded them.
            first_expired: Make the first one expired three days ago instead
                (``--expired-certificate``), for a health report that is critical.
            alt_names: A name's own alias, for the extra SAN it shows instead of
                "www.<name>" (``--showcase`` only). A bare, two-label domain still gets
                "www.<name>" regardless - that is a real certificate's own shape - but
                "www.api.kestrelworks.io" is not: a subdomain not in this mapping gets no
                second SAN at all, rather than one nobody would actually request
                (coordinator review, on the domains page). None (every other caller) keeps
                the original "name and its www" for every certificate, whatever domain it
                is.
        """
        for offset, name in enumerate(names):
            days = -3 if first_expired and offset == 0 else 12 + offset * 17
            if alt_names is None or name.count(".") == 1:
                domains = [name, f"www.{name}"]
            elif name in alt_names:
                domains = [name, alt_names[name]]
            else:
                domains = [name]
            self._issue(name, domains, datetime.now() + timedelta(days=days))

    def _issue(self, name: str, domains: list[str], expires: datetime) -> None:
        """
        Record a lineage and write its files.

        Args:
            name: Certificate name.
            domains: Domains it covers.
            expires: When it expires.
        """
        self.lineages[name] = list(domains)
        self.expiry[name] = expires
        self.revoked.discard(name)
        directory = self.live_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        for file in ("fullchain.pem", "cert.pem", "chain.pem", "privkey.pem"):
            (directory / file).write_text(_DOMAINS_PEM, encoding="utf-8")

    def _forget(self, name: str) -> None:
        """
        Drop a lineage and its files.

        Args:
            name: Certificate name.
        """
        self.lineages.pop(name, None)
        self.expiry.pop(name, None)
        self.revoked.discard(name)
        shutil.rmtree(self.live_dir / name, ignore_errors=True)

    def answer(self, args: tuple[str, ...]) -> Any:
        """
        Answer a command this model owns.

        Args:
            args: The argv.

        Returns:
            A CommandResult, or None for a command it leaves to the runner.
        """
        if args[:1] == ("nginx",) and "-t" in args:
            return self._nginx_test(args)
        if args[:1] == ("certbot",) and len(args) > 1:
            with self._lock:
                return self._certbot(args)
        return None

    # -- nginx -----------------------------------------------------------

    def _nginx_test(self, args: tuple[str, ...]) -> Any:
        """
        ``nginx -t``, or ``nginx -t -c <wrapper>`` for a staged configuration.

        Args:
            args: The argv.

        Returns:
            nginx's answer.
        """
        from noust.core.runner import CommandResult

        if "-c" in args and args.index("-c") + 1 < len(args):
            main = Path(args[args.index("-c") + 1])
            try:
                wrapper = main.read_text(encoding="utf-8")
            except OSError as exc:
                return CommandResult(
                    args,
                    1,
                    "",
                    f'nginx: [emerg] open() "{self.shown(main)}" failed ({exc.strerror})\n',
                )
            files = [Path(match) for match in re.findall(r"include\s+([^;\s]+);", wrapper)]
            main_shown = self.shown(main)
        else:
            enabled = self.sandbox.etc / "nginx" / "sites-enabled"
            files = sorted(enabled.iterdir()) if enabled.is_dir() else []
            main_shown = "/etc/nginx/nginx.conf"
            live = self.sandbox.etc / "nginx" / "nginx.conf"
            wrapper = live.read_text(encoding="utf-8") if live.is_file() else ""
        texts: list[tuple[Path, str]] = []
        for file in files:
            try:
                text = file.read_text(encoding="utf-8")
            except OSError:
                continue
            texts.append((file, text))
            problem = _domains_nginx_syntax(text)
            if problem is not None:
                message, line = problem
                return CommandResult(
                    args,
                    1,
                    "",
                    f"nginx: [emerg] {message} in {self.shown(file)}:{line}\n"
                    f"nginx: configuration file {main_shown} test failed\n",
                )
        problem_text = _sites_context_problem(wrapper, texts, self.shown)
        if problem_text is not None:
            return CommandResult(
                args, 1, "", f"{problem_text}\nnginx: configuration file {main_shown} test failed\n"
            )
        return CommandResult(
            args,
            0,
            "",
            f"nginx: the configuration file {main_shown} syntax is ok\n"
            f"nginx: configuration file {main_shown} test is successful\n",
        )

    # -- certbot ---------------------------------------------------------

    def _certbot(self, args: tuple[str, ...]) -> Any:
        """
        Args:
            args: A certbot argv.

        Returns:
            certbot's answer, or None for a verb this model does not own.
        """
        verb = args[1]
        if verb == "certificates":
            return self._certificates(args)
        if verb == "certonly":
            return self._certonly(args)
        if verb == "renew":
            return self._renew(args)
        if verb == "revoke":
            return self._revoke(args)
        if verb == "delete":
            return self._delete(args)
        return None

    @staticmethod
    def _options(args: tuple[str, ...], flag: str) -> list[str]:
        """
        Args:
            args: The argv.
            flag: An option that takes a value.

        Returns:
            Every value given to it, in order.
        """
        return [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == flag]

    def _certificates(self, args: tuple[str, ...]) -> Any:
        from noust.core.runner import CommandResult

        now = datetime.now()
        blocks = [_DOMAINS_CERTBOT_LOG, "", "-" * 79]
        if not self.lineages:
            blocks = [_DOMAINS_CERTBOT_LOG, "", "No certificates found."]
            return CommandResult(args, 0, "\n".join(blocks) + "\n", "")
        blocks.append("Found the following certs:")
        for name, domains in self.lineages.items():
            expiry = self.expiry[name]
            days = (expiry - now).days
            state = (
                "INVALID: REVOKED"
                if name in self.revoked
                else ("INVALID: EXPIRED" if days < 0 else f"VALID: {days} days")
            )
            blocks += [
                f"  Certificate Name: {name}",
                "    Serial Number: 4a3f9c2e1b7d",
                "    Key Type: ECDSA",
                f"    Domains: {' '.join(domains)}",
                f"    Expiry Date: {expiry.strftime('%Y-%m-%d %H:%M:%S')}+00:00 ({state})",
                f"    Certificate Path: /etc/letsencrypt/live/{name}/fullchain.pem",
                f"    Private Key Path: /etc/letsencrypt/live/{name}/privkey.pem",
            ]
        blocks.append("-" * 79)
        return CommandResult(args, 0, "\n".join(blocks) + "\n", "")

    def _authenticator(self, args: tuple[str, ...]) -> str:
        for flag in ("--nginx", "--apache", "--webroot", "--standalone"):
            if flag in args:
                return flag.removeprefix("--")
        return "nginx"

    def _certonly(self, args: tuple[str, ...]) -> Any:
        from noust.core.runner import CommandResult

        domains = self._options(args, "-d")
        names = self._options(args, "--cert-name")
        name = names[0] if names else (domains[0] if domains else "")
        if not domains:
            return CommandResult(
                args, 1, "", "certbot: error: at least one domain must be given with -d\n"
            )
        requested = (
            f"{domains[0]}"
            if len(domains) == 1
            else f"{domains[0]} and {len(domains) - 1} more domains"
        )
        problems = []
        for domain in domains:
            resolved = _domains_resolve(domain, self.zones)
            if not resolved:
                problems.append(
                    f"  Domain: {domain}\n  Type:   dns\n"
                    f"  Detail: DNS problem: NXDOMAIN looking up A for {domain} - check that "
                    "a DNS record exists for this domain; DNS problem: NXDOMAIN looking up "
                    f"AAAA for {domain} - check that a DNS record exists for this domain"
                )
            elif not _domains_points_here(domain, self.zones):
                problems.append(
                    f"  Domain: {domain}\n  Type:   unauthorized\n"
                    f"  Detail: {resolved[0]}: Invalid response from "
                    f"http://{domain}/.well-known/acme-challenge/"
                    "b3NfUvw9GQeD1Yq8hZk2xJpTcLmR: 404"
                )
        if problems:
            authenticator = self._authenticator(args)
            hint = _DOMAINS_CERTBOT_HINTS.get(authenticator, _DOMAINS_CERTBOT_HINTS["nginx"])
            hint = hint.replace("{server}", authenticator)
            return CommandResult(
                args,
                1,
                "",
                f"{_DOMAINS_CERTBOT_LOG}\nRequesting a certificate for {requested}\n\n"
                f"Certbot failed to authenticate some domains (authenticator: {authenticator})."
                " The Certificate Authority reported these problems:\n"
                + "\n\n".join(problems)
                + f"\n\nHint: {hint}\n\nSome challenges have failed.\nAsk for help or search "
                "for solutions at https://community.letsencrypt.org. See the logfile "
                "/var/log/letsencrypt/letsencrypt.log or re-run Certbot with -v for more "
                "details.\n",
            )
        if "--dry-run" in args:
            return CommandResult(
                args,
                0,
                f"{_DOMAINS_CERTBOT_LOG}\nSimulating a certificate request for {requested}\n"
                "The dry run was successful.\n",
                "",
            )
        expires = datetime.now() + timedelta(days=90)
        self._issue(name, domains, expires)
        return CommandResult(
            args,
            0,
            f"{_DOMAINS_CERTBOT_LOG}\nRequesting a certificate for {requested}\n\n"
            "Successfully received certificate.\n"
            f"Certificate is saved at: /etc/letsencrypt/live/{name}/fullchain.pem\n"
            f"Key is saved at:         /etc/letsencrypt/live/{name}/privkey.pem\n"
            f"This certificate expires on {expires.strftime('%Y-%m-%d')}.\n"
            "These files will be updated when the certificate renews.\n"
            "Certbot has set up a scheduled task to automatically renew this certificate "
            "in the background.\n",
            "",
        )

    def _renew(self, args: tuple[str, ...]) -> Any:
        from noust.core.runner import CommandResult

        names = self._options(args, "--cert-name") or list(self.lineages)
        unknown = [name for name in names if name not in self.lineages]
        if unknown:
            return CommandResult(
                args,
                1,
                "",
                f"No certificate found with name {unknown[0]} (expected "
                f"/etc/letsencrypt/renewal/{unknown[0]}.conf).\n",
            )
        force = "--force-renewal" in args
        dry = "--dry-run" in args
        renewed, skipped = [], []
        for name in names:
            days = (self.expiry[name] - datetime.now()).days
            if force or days < 30:
                if not dry:
                    self._issue(name, self.lineages[name], datetime.now() + timedelta(days=90))
                renewed.append(name)
            else:
                skipped.append(name)
        rule = "- " * 39 + "-"
        lines = [_DOMAINS_CERTBOT_LOG, ""]
        for name in names:
            lines += [rule, f"Processing /etc/letsencrypt/renewal/{name}.conf", rule]
            if name in renewed:
                lines.append(
                    f"Renewing an existing certificate for {' and '.join(self.lineages[name][:2])}"
                )
            else:
                lines.append("Certificate not yet due for renewal")
            lines.append("")
        lines.append(rule)
        if skipped:
            lines.append("The following certificates are not due for renewal yet:")
            lines += [
                f"  /etc/letsencrypt/live/{name}/fullchain.pem expires on "
                f"{self.expiry[name].strftime('%Y-%m-%d')} (skipped)"
                for name in skipped
            ]
        if renewed:
            lines.append(
                "Congratulations, all simulated renewals succeeded:"
                if dry
                else "Congratulations, all renewals succeeded:"
            )
            lines += [f"  /etc/letsencrypt/live/{name}/fullchain.pem (success)" for name in renewed]
        else:
            lines.append("No renewals were attempted.")
        lines.append(rule)
        return CommandResult(args, 0, "\n".join(lines) + "\n", "")

    def _revoke(self, args: tuple[str, ...]) -> Any:
        from noust.core.runner import CommandResult

        paths = self._options(args, "--cert-path")
        name = Path(paths[0]).parent.name if paths else ""
        if name not in self.lineages:
            return CommandResult(
                args,
                1,
                "",
                f"{_DOMAINS_CERTBOT_LOG}\nCould not read the certificate at {paths[:1]}.\n",
            )
        if "--delete-after-revoke" in args:
            self._forget(name)
        else:
            self.revoked.add(name)
        return CommandResult(
            args,
            0,
            f"{_DOMAINS_CERTBOT_LOG}\nCongratulations! You have successfully revoked the "
            f"certificate that was located at /etc/letsencrypt/live/{name}/fullchain.pem.\n",
            "",
        )

    def _delete(self, args: tuple[str, ...]) -> Any:
        from noust.core.runner import CommandResult

        names = self._options(args, "--cert-name")
        name = names[0] if names else ""
        if name not in self.lineages:
            return CommandResult(
                args,
                1,
                "",
                f"No certificate found with name {name} (expected "
                f"/etc/letsencrypt/renewal/{name}.conf).\n",
            )
        self._forget(name)
        return CommandResult(
            args,
            0,
            f"{_DOMAINS_CERTBOT_LOG}\nDeleted all files relating to certificate {name}.\n",
            "",
        )


def _sites_context_problem(
    main: str, files: list[tuple[Path, str]], shown: Callable[[Path], str]
) -> str | None:
    """
    What nginx says of a site that only fails beside the others (spec 3.2, section 2.2).

    The site API tests a candidate inside a copy of nginx.conf with every enabled
    site, so the modelled nginx reads them together the way the real one does: a
    ``limit_req`` zone nobody declared, and an upstream name two files declare.

    Args:
        main: The main configuration's text (the staged copy, or the live one).
        files: Each included file and its text.
        shown: Prints a sandbox path as the modelled machine would.

    Returns:
        nginx's ``[emerg]`` line, or None when the files fit together.
    """
    everything = "\n".join([main, *(text for _, text in files)])
    declared = set(re.findall(r"limit_req_zone\s[^;]*zone=([\w-]+):", everything))
    for zone in re.findall(r"limit_req\s+zone=([\w-]+)", everything):
        if zone not in declared:
            return f'nginx: [emerg] zero size shared memory zone "{zone}"'
    seen: set[str] = set()
    for file, text in files:
        for match in re.finditer(r"^\s*upstream\s+(\S+)\s*\{", text, re.MULTILINE):
            if match.group(1) in seen:
                line = text.count("\n", 0, match.start(1)) + 1
                return (
                    f'nginx: [emerg] duplicate upstream "{match.group(1)}" in {shown(file)}:{line}'
                )
            seen.add(match.group(1))
    return None


def _domains_redirect_managers() -> None:
    """
    Point the nginx and Apache managers' default backends at the sandbox.

    :func:`redirect_system_paths` rebinds the backends on
    ``noust.managers.webserver``, but ``NginxManager`` and ``ApacheManager``
    imported them by name before that, so a manager built without a backend
    (the sites API, a deployer rendering its site) still read and wrote the
    real /etc/nginx.
    """
    import noust.managers.apache_manager as apache_module
    import noust.managers.nginx_manager as nginx_module
    import noust.managers.webserver as webserver_module

    nginx_module.NGINX_BACKEND = webserver_module.NGINX_BACKEND  # type: ignore[attr-defined]
    apache_module.APACHE_BACKEND = webserver_module.APACHE_BACKEND  # type: ignore[attr-defined]
    for manager, backend in (
        (nginx_module.NginxManager, webserver_module.NGINX_BACKEND),
        (apache_module.ApacheManager, webserver_module.APACHE_BACKEND),
    ):
        manager.SITES_AVAILABLE = backend.sites_available
        manager.SITES_ENABLED = backend.sites_enabled


def _domains_site_files(store: Any) -> None:
    """
    Give every seeded site that has no configuration file one, rendered by
    the manager's own template, and enable the ones the store says are.

    Args:
        store: The seeded store.
    """
    from noust.managers.nginx_manager import NginxManager

    manager = NginxManager(verbose=False)
    apps = {app.id: app for app in store.list_apps()}
    for site in store.list_sites():
        if site.webserver != "nginx":
            continue
        app = apps.get(site.app_id)
        static = app is not None and app.app_type == "static"
        if not manager.site_exists(site.domain):
            manager.create_site(
                site.domain,
                "static" if static else "proxy",
                {
                    "port": site.proxy_port or 3000,
                    "ssl": bool(site.ssl_certificate),
                    "app_path": f"/var/www/apps/{site.domain}",
                },
            )
        if site.enabled and not manager.site_enabled(site.domain):
            manager.enable_site(site.domain)


def seed_sites_server_names(domain: str, extra: Sequence[str]) -> None:
    """
    Give an already-written site's configuration more names to answer on.

    Every seeded site otherwise answers on its bare domain only, which never
    exercises ``SiteInfo.server_names`` beyond a single-element list. This
    edits the file ``_domains_site_files`` already wrote, in place, the same
    way an operator's own ``server_name`` edit would: no store row is added,
    since these are extra names of a *site*, not an application's own domains
    (``noust.web.api.domains``, seeded separately).

    Args:
        domain: Domain of an already-written nginx site.
        extra: Names to add beside it, unqualified (``www``, not
            ``www.<domain>``).
    """
    from noust.managers.nginx_manager import NginxManager

    manager = NginxManager(verbose=False)
    if not manager.site_exists(domain):
        return
    config = manager.get_site_config(domain) or ""
    names = " ".join([domain, *(f"{name}.{domain}" for name in extra)])
    updated = config.replace(f"server_name {domain};", f"server_name {names};")
    if updated != config:
        manager.replace_site_config(domain, updated)


#: The http-level settings the Proggest sites rely on, as its server's nginx.conf
#: declares them: the request-rate zones its auth and API locations name.
SITES_NGINX_CONF = """\
user www-data;
worker_processes auto;
pid /run/nginx.pid;

events {
    worker_connections 768;
}

http {
    sendfile on;
    tcp_nopush on;
    types_hash_max_size 2048;
    server_tokens off;

    # Request-rate zones the sites' limit_req directives name.
    limit_req_zone $binary_remote_addr zone=auth_limit:10m rate=5r/s;
    limit_req_zone $binary_remote_addr zone=api_general:10m rate=50r/s;

    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;

    gzip on;

    include sites-enabled/*;
}
"""

#: Proggest's Compose stack, as ``docker ps --format '{{json .}}'`` lists it: the
#: two services its upstreams reach (nextjs_upstream, nestjs_upstream), so the
#: site's diagram names a Compose service as each port's owner. On its server they
#: publish 3001 and 3000; here the seeded applications already hold 3000 and up,
#: so the sites are seeded with each port moved to the one beside it here.
SITES_PROGGEST_CONTAINERS = (
    ("proggest-frontend-1", "frontend", 3001, 3201),
    ("proggest-backend-1", "backend", 3000, 3200),
)


def seed_proggest_site(sandbox: Sandbox, tools: Any) -> None:
    """
    Seed the operator-written Proggest sites, for the site page's structure and diagram.

    ``proggest.es`` and ``modulos.proggest.es`` are the real files the analyzer's
    corpus keeps (tests/fixtures/siteconf/proggest): two upstreams, 25 locations,
    rate limits whose zones live in nginx.conf, and a second site that proxies to
    the first one's upstreams. They are written without Noust's marker, as the
    operator wrote them, and enabled; nginx.conf gains the zones so testing either
    site in its real context passes. The certificate they name is issued, and
    ``docker ps`` answers with the stack whose ports their upstreams reach.

    Args:
        sandbox: The sandbox.
        tools: The modelled web tools (:class:`_DomainsWebTools`), for the certificate.
    """
    from noust.core.runner import CommandResult, get_runner

    nginx = sandbox.etc / "nginx"
    (nginx / "nginx.conf").write_text(SITES_NGINX_CONF, encoding="utf-8")
    corpus = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "siteconf" / "proggest"
    for name in ("proggest.es", "modulos.proggest.es"):
        available = nginx / "sites-available" / name
        text = (corpus / name).read_text(encoding="utf-8")
        for _, _, real, here in SITES_PROGGEST_CONTAINERS:
            text = text.replace(f"127.0.0.1:{real};", f"127.0.0.1:{here};")
        available.write_text(text, encoding="utf-8")
        enabled = nginx / "sites-enabled" / name
        if not enabled.is_symlink():
            enabled.symlink_to(available)
    tools._issue(
        "proggest.es",
        [
            "proggest.es",
            "www.proggest.es",
            "timpora.proggest.es",
            "partes.proggest.es",
            "partestrabajo.proggest.es",
            "gestordoc.proggest.es",
            "uaap.proggest.es",
        ],
        datetime.now() + timedelta(days=75),
    )

    listing = "".join(
        json.dumps(
            {
                "Names": container,
                "Ports": f"127.0.0.1:{port}->{inside}/tcp",
                "Labels": ",".join(
                    (
                        "com.docker.compose.project=proggest",
                        f"com.docker.compose.service={service}",
                        "com.docker.compose.project.working_dir=/opt/proggest",
                    )
                ),
            }
        )
        + "\n"
        for container, service, inside, port in SITES_PROGGEST_CONTAINERS
    )
    runner = get_runner()
    original = runner.run

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        # Only the topology's own listing: Docker stays "not installed" for every
        # page that asks whether it exists first.
        if args == ("docker", "ps", "--format", "{{json .}}"):
            runner.calls.append(args)
            return CommandResult(args, 0, listing, "")
        return original(argv, **kwargs)

    runner.run = run  # type: ignore[method-assign]

    # The stack's ports answer; nothing in the sandbox listens on them for real.
    import noust.core.app_state as app_state_module
    import noust.managers.site_topology as topology_module

    stack_ports = {port for _, _, _, port in SITES_PROGGEST_CONTAINERS}

    def connect(port: int, host: str, timeout: float) -> bool:
        return port in stack_ports or app_state_module.port_answers(port, host, timeout)

    topology_module._DEFAULT = topology_module.TopologyProbe(connect=connect)


def _domains_wizard_sources(sandbox: Sandbox) -> None:
    """
    Write the projects the new-app wizard is pointed at, in /var/www/src.

    ``storefront`` is a Next.js project whose ``.env.example`` has required
    values, defaults and credentials; ``landing`` is a static site.

    Args:
        sandbox: The sandbox.
    """
    root = sandbox.var / "www" / "src"
    storefront = root / WIZARD_SOURCES[0]
    (storefront / "app").mkdir(parents=True, exist_ok=True)
    (storefront / "public" / "uploads").mkdir(parents=True, exist_ok=True)
    (storefront / "package.json").write_text(
        json.dumps(
            {
                "name": "storefront",
                "version": "1.4.2",
                "private": True,
                "scripts": {"dev": "next dev", "build": "next build", "start": "next start"},
                "dependencies": {"next": "15.2.4", "react": "19.0.0", "react-dom": "19.0.0"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (storefront / "package-lock.json").write_text(
        '{\n  "name": "storefront",\n  "lockfileVersion": 3,\n  "requires": true,\n'
        '  "packages": {}\n}\n',
        encoding="utf-8",
    )
    (storefront / "next.config.js").write_text(
        "/** @type {import('next').NextConfig} */\nmodule.exports = { output: 'standalone' };\n",
        encoding="utf-8",
    )
    (storefront / ".env.example").write_text(
        "# Orders and customers\n"
        "DATABASE_URL=\n"
        "# Signs session cookies\n"
        "NEXTAUTH_SECRET=\n"
        "NEXTAUTH_URL=https://example.com\n"
        "STRIPE_SECRET_KEY=\n"
        "SMTP_HOST=smtp.example.com\n"
        "SMTP_PASSWORD=\n"
        "UPLOADS_DIR=public/uploads\n"
        "LOG_LEVEL=info\n",
        encoding="utf-8",
    )
    (storefront / "app" / "page.js").write_text(
        "export default function Home() {\n  return <h1>Storefront</h1>;\n}\n", encoding="utf-8"
    )
    (storefront / "public" / "uploads" / ".gitkeep").write_text("", encoding="utf-8")

    landing = root / WIZARD_SOURCES[1]
    landing.mkdir(parents=True, exist_ok=True)
    (landing / "index.html").write_text(
        '<!doctype html>\n<html lang="en">\n<head><meta charset="utf-8"><title>Landing</title>'
        '<link rel="stylesheet" href="styles.css"></head>\n<body><h1>Coming soon</h1></body>\n'
        "</html>\n",
        encoding="utf-8",
    )
    (landing / "styles.css").write_text("body { font-family: system-ui; }\n", encoding="utf-8")


def seed_domains_and_sources(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    cert_domains: list[str],
    *,
    expired_certificate: bool = False,
    server_names: tuple[str, Sequence[str]] = ("example.net", ("www", "shop", "status")),
    cert_alt_names: dict[str, str] | None = None,
    proggest: bool = True,
) -> None:
    """
    Seed the web server, certificates, DNS and sources the domain pages and the wizard need.

    Temporary files move into the sandbox too: the wizard's inspection clones
    into one, and the web server check stages the configuration it tests in
    another, which the modelled nginx has to be able to read. nginx itself
    runs, as a system unit Noust does not own: a deploy's pre-flight checks
    refuse to start on a machine whose web server is down.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, given the nginx one.
        cert_domains: The domains the store seeded with a certificate.
        expired_certificate: Seed the first certificate as expired.
        server_names: A site's domain, and the extra names to give it (unqualified: "www",
            not "www.<domain>") - see :func:`seed_sites_server_names`. Defaults to the
            default machine's own "example.net"; ``--showcase`` passes one of its own.
        cert_alt_names: See :meth:`_DomainsWebTools.seed`'s own ``alt_names``
            (``--showcase`` only).
        proggest: Seed the operator's Proggest sites (:func:`seed_proggest_site`);
            the showcase's invented agency has none.
    """
    import functools
    import socket as socket_module

    import noust.deployers.domains as domains_core
    import noust.web.api.domains as domains_api
    from noust.core.runner import get_runner

    units.setdefault("nginx", Unit(active="active", pid=880, managed=False))
    scratch = sandbox.root / "tmp"
    scratch.mkdir(mode=0o700, exist_ok=True)
    tempfile.tempdir = str(scratch)
    _domains_redirect_managers()

    zones = frozenset(".".join(app.domain.split(".")[-2:]) for app in store.list_apps())
    tools = _DomainsWebTools(sandbox, zones)
    tools.seed(cert_domains, first_expired=expired_certificate, alt_names=cert_alt_names)

    runner = get_runner()
    original = runner.run

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        answer = tools.answer(args)
        if answer is None:
            return original(argv, **kwargs)
        runner.calls.append(args)
        return answer

    runner.run = run  # type: ignore[method-assign]

    def resolver(host: str, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        addresses = _domains_resolve(host, zones)
        if not addresses:
            raise socket_module.gaierror(socket_module.EAI_NONAME, "Name or service not known")
        return [
            (
                socket_module.AF_INET6 if ":" in address else socket_module.AF_INET,
                socket_module.SOCK_STREAM,
                6,
                "",
                (address, 0),
            )
            for address in addresses
        ]

    # Patched where it is defined, not only where the API imported it: the certificate
    # diagnosis imports it lazily from noust.deployers.domains and must see the same
    # modelled DNS, never this machine's real resolver and addresses.
    modelled = functools.partial(
        domains_core.check_dns,
        resolver=resolver,
        local_addresses=lambda: DOMAINS_MACHINE_ADDRESSES,
    )
    domains_core.check_dns = modelled  # type: ignore[assignment]
    domains_api.check_dns = modelled  # type: ignore[assignment]

    _domains_site_files(store)
    seed_sites_server_names(*server_names)
    _domains_wizard_sources(sandbox)
    if proggest:
        seed_proggest_site(sandbox, tools)


# ---------------------------------------------------------------------------
# Settings (the console's Settings pages)
# ---------------------------------------------------------------------------

#: Seeded API tokens: (name, scope, lifetime in hours or None, revoked).
SETTINGS_API_TOKENS: tuple[tuple[str, str, int | None, bool], ...] = (
    ("ci-deploy", "deploy", 90 * 24, False),
    ("grafana-read", "read", None, False),
    ("old-backup-script", "admin", None, True),
)


def seed_settings_api_tokens() -> None:
    """
    Issue the API tokens the Settings > API tokens page lists.

    A live deploy token, a read token that never expires and a revoked admin
    one, so each state the table draws exists. The clear tokens are discarded:
    nothing in the suite authenticates with them.
    """
    from noust.web.server import get_token_manager

    manager = get_token_manager()
    for name, scope, hours, revoked in SETTINGS_API_TOKENS:
        issued = manager.create_api_token(name, scope, hours)
        if revoked:
            manager.revoke_api_token(int(issued["id"]))


def pin_settings_update_check() -> str:
    """
    Answer the update check from the sandbox instead of GitHub.

    ``GET /api/system/version`` asks GitHub and the installation's package
    source when its cache is older than five minutes, which would make the
    About page depend on the network and on whatever was released last. Both
    answers are pinned to the next minor version of the one installed, so the
    page always shows an update to offer.

    Returns:
        The version the check reports as released.
    """
    from noust import __version__
    from noust.core.update_checker import UpdateChecker

    numbers = [int(part) for part in re.findall(r"\d+", __version__)[:2]] + [0, 0]
    latest = f"{numbers[0]}.{numbers[1] + 1}.0"

    def fetch_latest(cls: type[UpdateChecker], *args: object) -> str:
        """Report the pinned release, both published and installable."""
        return latest

    def detect(cls: type[UpdateChecker]) -> str:
        """Report a pip installation without asking the sandbox's fake runner."""
        return "pip"

    UpdateChecker._fetch_published_version = classmethod(fetch_latest)  # type: ignore[method-assign,assignment]
    UpdateChecker._fetch_installable_version = classmethod(fetch_latest)  # type: ignore[method-assign,assignment]
    UpdateChecker._detect_installation_method = classmethod(detect)  # type: ignore[method-assign,assignment]
    return latest


#: The modelled Telegram bots: token to the chats each has seen. The first has seen a group and
#: a private chat; the second none yet, which is what a bot never written to answers.
TELEGRAM_BOTS: dict[str, list[dict[str, Any]]] = {
    "7000000001:console-sandbox-bot": [
        {"id": -1001987654321, "type": "supergroup", "title": "Noust alerts"},
        {"id": 52345678, "type": "private", "username": "ops_oncall", "first_name": "Ops"},
    ],
    "7000000002:console-sandbox-quiet": [],
}


def model_telegram_bot_api() -> None:
    """
    Answer the Telegram Bot API from :data:`TELEGRAM_BOTS` instead of api.telegram.org.

    Settings > Notifications finds a bot's chats (``getUpdates``) and sends it a test
    (``sendMessage``); neither may leave the machine. The notifier the settings endpoints build
    keeps its real opener - the webhook test posts to a listener the E2E suite runs - but a
    request for the Bot API is answered here, the way Telegram answers: ``{"ok": true, ...}``,
    or an HTTP error whose body carries Telegram's own ``description``. Only the Bot API's
    fixed host is excused from the private-destination guard, since nothing is dialled for it.
    """
    import io
    from email.message import Message
    from urllib.error import HTTPError

    import noust.core.notifier as notifier_module
    import noust.web.api.config as config_api

    api = notifier_module._TELEGRAM_API + "/bot"
    guard = notifier_module._require_public_destination

    def require_public_destination(url: str, config: Any) -> None:
        if not url.startswith(api):
            guard(url, config)

    def refuse(url: str, code: int, reason: str, description: str) -> HTTPError:
        body = json.dumps({"ok": False, "error_code": code, "description": description})
        return HTTPError(url, code, reason, Message(), io.BytesIO(body.encode("utf-8")))

    def answer(request: Any) -> io.BytesIO:
        url = str(request.full_url)
        token, _, method = url[len(api) :].partition("/")
        chats = TELEGRAM_BOTS.get(token)
        if chats is None:
            raise refuse(url, 401, "Unauthorized", "Unauthorized")
        if method == "getUpdates":
            updates = [
                {
                    "update_id": 800 + index,
                    "message": {"message_id": 1, "chat": chat, "text": "/start"},
                }
                for index, chat in enumerate(chats)
            ]
            return io.BytesIO(json.dumps({"ok": True, "result": updates}).encode("utf-8"))
        if method == "sendMessage":
            sent = json.loads(request.data.decode("utf-8"))
            if str(sent.get("chat_id")) not in {str(chat["id"]) for chat in chats}:
                raise refuse(url, 400, "Bad Request", "Bad Request: chat not found")
            return io.BytesIO(b'{"ok": true, "result": {"message_id": 2}}')
        raise refuse(url, 404, "Not Found", "Not Found")

    build = config_api._build_notifier

    def build_notifier() -> Any:
        notifier = build()
        opener = notifier._opener

        def open_or_answer(request: Any, timeout: float | None = None) -> Any:
            if str(request.full_url).startswith(api):
                return answer(request)
            return opener(request, timeout=timeout)

        notifier._opener = open_or_answer
        return notifier

    notifier_module._require_public_destination = require_public_destination  # type: ignore[assignment]
    config_api._build_notifier = build_notifier


# ---------------------------------------------------------------------------
# Console 2.2: blue/green, pull request previews, the GitHub App, backup
# destinations and why an environment variable is hidden. Each screen gets the
# states it draws; every network call they make is answered here, offline.
# ---------------------------------------------------------------------------


def seed_databases(sandbox: Sandbox) -> None:
    """
    Give example_production what its page shows (3.1): the application that uses it, the
    accounts Noust keeps passwords for, a backup policy with its timer, dumps of several ages
    (checked, one test-restored, one sent offsite, one that failed its check), and a month of
    size and connection readings for its charts.

    Everything goes through the managers the API uses, over the modelled runner, so the page
    reads exactly what a real server would have recorded.

    Args:
        sandbox: The sandbox.
    """
    import math

    from noust.core.exceptions import NoustError
    from noust.core.secrets import SecretStore
    from noust.core.store import get_store
    from noust.deployers.helpers.databases import _password_secret
    from noust.managers.database.backup_records import BackupRecords, DumpRecord
    from noust.managers.database.backups import DatabaseBackups
    from noust.managers.database.base import BaseDatabaseManager
    from noust.managers.database.records import DatabaseLink, DatabaseRecords
    from noust.managers.database.service import DatabaseService
    from noust.web.metrics_collector import get_metrics_store
    from tests.panel_factory import DESTINATION_SFTP as DESTINATION_SFTP_NAME

    store = get_store(sandbox.store_file)
    app = store.get_app("example.com")
    records = DatabaseRecords(store)
    secrets = SecretStore()
    if app is not None and app.id is not None:
        records.save_link(
            DatabaseLink(
                app_id=app.id,
                engine="postgresql",
                db_name=_DEMO_DATABASE,
                username=_DEMO_DATABASE,
                env_var="DATABASE_URL",
            )
        )
    shop = store.get_app("shop.example.net")
    if shop is not None and shop.id is not None:
        records.save_link(
            DatabaseLink(
                app_id=shop.id,
                engine="mysql",
                db_name="shop_wp",
                username="shop_wp",
                env_var="DATABASE_URL",
                extra_vars=True,
            )
        )
        secrets.write(_password_secret("mysql", "shop_wp"), "Wp-demo-7Qx2")
    secrets.write(_password_secret("postgresql", _DEMO_DATABASE), "Pr0d-demo-k8Vw")
    records.save_account(
        "postgresql",
        _DEMO_DATABASE,
        db_name=_DEMO_DATABASE,
        profile="read_write",
        password_changed=True,
    )
    records.save_account(
        "postgresql",
        "analytics_reader",
        db_name=_DEMO_DATABASE,
        profile="read_only",
        password_changed=True,
    )
    secrets.write(_password_secret("postgresql", "analytics_reader"), "An4lytics-demo")

    service = DatabaseService()
    backups = DatabaseBackups(service)
    destinations = [{"name": DESTINATION_SFTP_NAME, "retention_count": 30, "retention_days": 90}]
    try:
        backups.set_policy(
            "postgresql",
            _DEMO_DATABASE,
            schedule="daily",
            retention_count=7,
            retention_days=30,
            destinations=destinations,
            verify_restore=True,
        )
    except NoustError as exc:
        # A destination the sandbox did not seed: the policy stays local.
        print(f"seed_databases: {exc}", file=sys.stderr)
        backups.set_policy(
            "postgresql",
            _DEMO_DATABASE,
            schedule="daily",
            retention_count=7,
            retention_days=30,
            verify_restore=True,
        )

    directory = BaseDatabaseManager.BACKUP_DIR
    directory.mkdir(parents=True, exist_ok=True)
    dump_records = BackupRecords(store)
    now = datetime.now()
    taken: list[tuple[str, str]] = []
    for hours, origin, content in (
        (7, "scheduled", _PG_DUMP_BYTES),
        (31, "scheduled", _PG_DUMP_BYTES),
        (55, "scheduled", _PG_DUMP_BYTES),
        (80, "manual", _PG_DUMP_BYTES),
        (130, "safety", _PG_DUMP_BYTES),
        (200, "manual", "not a dump: the disk filled up while it was written"),
    ):
        moment = now - timedelta(hours=hours)
        name = f"postgresql-{_DEMO_DATABASE}-{moment.strftime('%Y%m%d_%H%M%S')}.dump"
        path = directory / name
        path.write_text(content)
        os.utime(path, (moment.timestamp(), moment.timestamp()))
        dump_records.save_dump(
            DumpRecord(
                engine="postgresql",
                file_name=name,
                db_name=_DEMO_DATABASE,
                origin=origin,
                size=path.stat().st_size,
            )
        )
        taken.append((name, origin))
    for index, (name, _origin) in enumerate(taken):
        backups.verify("postgresql", name, restore=index == 0)
    newest = dump_records.dump("postgresql", taken[0][0])
    if newest is not None:
        newest.remote_copies = [
            {
                "destination": DESTINATION_SFTP_NAME,
                "pushed_at": (now - timedelta(hours=6, minutes=58)).astimezone().isoformat(),
                "verified_by": "sha256",
                "folder": f"databases/postgresql/{_DEMO_DATABASE}",
            }
        ]
        dump_records.save_dump(newest)
    dump_records.record_run("postgresql", _DEMO_DATABASE, ok=True, error=None, dump=taken[0][0])

    metrics = get_metrics_store()
    stamp_now = int(time.time())
    stamps = [
        *range(stamp_now - 30 * 86_400, stamp_now - 86_400, 1_800),
        *range(stamp_now - 86_400, stamp_now, 120),
    ]
    base = f"db.postgresql.{_DEMO_DATABASE}"
    for stamp in stamps:
        age_days = (stamp_now - stamp) / 86_400
        daily = math.sin((stamp % 86_400) / 86_400 * 2 * math.pi - math.pi / 2)
        metrics.record_many(
            [
                (
                    f"{base}.size",
                    48_218_931 - age_days * 310_000 + math.sin(stamp / 7_000) * 40_000,
                ),
                (f"{base}.connections", max(1.0, 5 + 3 * daily + math.sin(stamp / 900) * 1.5)),
                (f"{base}.cache_hit", 99.3 + 0.4 * math.sin(stamp / 5_000)),
                (f"{base}.tps", max(0.5, 18 + 12 * daily + math.sin(stamp / 600) * 4)),
            ],
            ts=stamp,
        )
    metrics.consolidate(now=stamp_now)
    seed_detected_use(store)
    model_database_containers()


#: The application whose ``.env`` names ``example_staging`` without Noust having linked it:
#: the database's row offers to record the use (``detected_apps``).
DETECTED_APP = "docs.example.org"

#: The database that application's ``DATABASE_URL`` points at, on the host's PostgreSQL.
DETECTED_DATABASE = "example_staging"


def seed_detected_use(store: Any) -> None:
    """
    Make one application's ``.env`` name a database it has no link to (3.3).

    The use is found the way a real one is: the ``.env`` the layout says (``env_file_for``)
    is read for connection strings, which here points at the host's PostgreSQL on its port.

    Args:
        store: The seeded store.
    """
    from noust.deployers.helpers.layout import env_file_for

    app = store.get_app(DETECTED_APP)
    if app is None:
        return
    env_file = env_file_for(app)
    url = f"postgres://staging_app:Stg-demo-9Fq2@127.0.0.1:5432/{DETECTED_DATABASE}"
    text = env_file.read_text(encoding="utf-8") if env_file.is_file() else "NODE_ENV=production\n"
    if re.search(r"^DATABASE_URL=", text, re.MULTILINE):
        text = re.sub(r"^DATABASE_URL=.*$", f"DATABASE_URL={url}", text, flags=re.MULTILINE)
    else:
        text = f"{text.rstrip(chr(10))}\nDATABASE_URL={url}\n"
    env_file.write_text(text, encoding="utf-8")
    env_file.chmod(0o600)


class _DockerInstalled:
    """
    The runner database discovery asks, for which Docker is installed.

    The modelled machine has no Docker for every other page (the adoption wizard and the
    dependency checks say so, and their suites rely on it), yet it holds a database
    container; discovery gates itself on ``exists("docker")``, so only it is told otherwise.
    Everything else is the runner's own.
    """

    def __init__(self, runner: Any) -> None:
        """
        Args:
            runner: The machine's runner.
        """
        self._runner = runner

    def exists(self, program: str) -> bool:
        """
        Args:
            program: An executable name.

        Returns:
            True for ``docker``, otherwise what the runner says.
        """
        return program == "docker" or bool(self._runner.exists(program))

    def run(self, argv: Sequence[str], **kwargs: Any) -> Any:
        """
        Args:
            argv: The command.
            **kwargs: The runner's options.

        Returns:
            The runner's answer, which for Docker is :class:`_DatabaseContainers`'.
        """
        return self._runner.run(argv, **kwargs)


def model_database_containers() -> None:
    """Show the Databases area the machine's database container (``engines``, ``databases``)."""
    import noust.managers.database.service as service_module
    from noust.core.runner import get_runner

    discover = service_module.discover

    def discover_with_docker(runner: Any = None) -> Any:
        return discover(_DockerInstalled(runner or get_runner()))

    service_module.discover = discover_with_docker


def seed_release_22(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
) -> None:
    """
    Seed what the 2.2 screens need beyond the machine seeded before them.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
    """
    seed_zero_downtime(sandbox, store, units, ports, domains)
    seed_previews(sandbox, store, units, ports, domains)
    seed_github_app(sandbox, store)
    seed_backup_destinations(sandbox, store)
    seed_env_marks(sandbox, store)


# --- 2.2: zero downtime ------------------------------------------------------


def seed_zero_downtime(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
) -> None:
    """
    Seed an application in blue/green mode, turned on by the real engine.

    ``pagos.example.org`` is deployed on releases like the tabs' release app,
    then :func:`noust.deployers.bluegreen.set_zero_downtime` turns the mode on
    exactly as ``noust app zero-downtime`` would: the template written, green
    started and probed, the upstream and the site switched, the old unit
    retired. So what the console reads is what the engine leaves, and turning
    it off and on again from the console runs the same code on the same model.

    Its unit, instances and ports live in the runner's own model rather than
    the maps passed in (see :func:`_zd_model`), because the instances only
    come to exist as the runner starts them. Nothing listens on the instances'
    ports: the health probe and the port check the engine uses answer from
    the model, as :func:`_tabs_port_model` does for an application's state.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units (unused: see above).
        ports: Each unit's port (unused).
        domains: Each unit's domain (unused).
    """
    from noust.core.store import App, ReleaseRecord
    from noust.core.utils import domain_to_app_name
    from noust.deployers.bluegreen import BlueGreen, set_zero_downtime
    from tests.panel_factory import seed_zero_downtime_history

    model_units, model_ports, model_domains = _zd_model()
    _zd_probe_model(model_units, model_ports)

    domain = ZD_APP
    root = sandbox.apps_dir / domain_to_app_name(domain)
    port = _zd_free_port_pair()
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / ".env").write_text(
        "NODE_ENV=production\n"
        "DATABASE_URL=postgres://pagos:Q7m2vK9xLp@127.0.0.1:5432/pagos\n"
        "STRIPE_SECRET_KEY=sk_l" + "ive_51Hx8examplePaymentsSeeded\n"
        "NEXT_PUBLIC_SITE_URL=https://pagos.example.org\n",
        encoding="utf-8",
    )
    (shared / ".env").chmod(0o600)
    (root / "repo").mkdir(parents=True, exist_ok=True)

    app = App(
        domain=domain,
        app_type="nextjs",
        source="https://github.com/example-org/pagos.git",
        branch="main",
        port=port,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
        layout="releases",
        keep_releases=5,
    )
    _tabs_register(
        sandbox,
        store,
        model_units,
        model_ports,
        model_domains,
        app,
        working_directory=root / "current",
    )
    stored = store.get_app(domain)
    if stored is None or stored.id is None:
        raise RuntimeError(f"{domain} was not recorded")

    now = datetime.now()
    serving = ""
    for age_hours, commit, message in _ZD_RELEASES:
        started = now - timedelta(hours=age_hours)
        release_id = _tabs_release_id(started, commit)
        release_dir = root / "releases" / release_id
        (release_dir / ".next").mkdir(parents=True, exist_ok=True)
        (release_dir / "package.json").write_text(
            json.dumps({"name": "pagos", "version": "1.8.0"}) + "\n", encoding="utf-8"
        )
        (release_dir / ".next" / "BUILD_ID").write_text(commit + "\n", encoding="utf-8")
        (release_dir / ".env").symlink_to("../../shared/.env")
        store.record_release(
            ReleaseRecord(
                id=release_id,
                app_id=stored.id,
                git_commit=commit,
                created_at=started.astimezone(timezone.utc).isoformat(),
                activated_at=(started + timedelta(minutes=1)).astimezone(timezone.utc).isoformat(),
                status="superseded",
                path=str(release_dir),
            )
        )
        deployment = store.record_deployment_start(
            domain, "webhook", git_commit=commit, git_branch="main"
        )
        store.annotate_deployment(deployment, commit_message=message, release_id=release_id)
        store.finish_deployment(deployment, "success")
        serving = release_id
    (root / "current").symlink_to(Path("releases") / serving)
    store.mark_release_active(stored.id, serving)

    # The engine drains for real on the console's own switches; here nothing is
    # serving yet, so there is nothing to wait for.
    set_zero_downtime(
        domain,
        True,
        drain_seconds=ZD_DRAIN_SECONDS,
        engine=lambda row, out: BlueGreen(row, logger=out, sleep=lambda _seconds: None),
    )
    seed_zero_downtime_history(store, domain)


#: In blue/green mode: green serves, blue is stopped, the upstream names green.
ZD_APP = "pagos.example.org"

#: Seconds the old instance keeps running after a switch: short, so a test that
#: turns the mode off and on again does not wait long for either.
ZD_DRAIN_SECONDS = 2

#: The releases of the blue/green application: hours ago, commit, subject.
_ZD_RELEASES = (
    (50.0, "4be21c7", "Accept SEPA direct debits"),
    (26.0, "8c03f5a", "Retry webhooks from the payment provider"),
    (2.5, "d7a91e4", "Show the refund status on receipts"),
)


def _zd_model() -> tuple[dict[str, Unit], dict[str, int], dict[str, str]]:
    """
    The runner's own model: its units, their ports and their domains.

    Seeding fills maps of its own that :func:`serve` merges into the runner's
    afterwards; a unit the runner creates while seeding (a blue/green
    instance it is asked to start) lands in the runner's maps directly, so
    the application that has instances is registered there too.

    Returns:
        The maps the runner answers from.
    """
    from noust.core.runner import get_runner

    runner: Any = get_runner()
    return runner.model_units, runner.model_ports, runner.model_domains


def _zd_free_port_pair() -> int:
    """
    Find a port whose next one is free too: blue listens on it, green on the one after.

    The engine checks that green's port is free on this machine before it
    starts green, and the seeded applications' ports are picked around it.

    Returns:
        A port P with P and P + 1 both free on loopback.
    """
    for _attempt in range(64):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            candidate = int(probe.getsockname()[1])
        if candidate >= 65535:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as first:
                first.bind(("127.0.0.1", candidate))
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as second:
                    second.bind(("127.0.0.1", candidate + 1))
        except OSError:
            continue
        return candidate
    raise RuntimeError("no two consecutive free ports on loopback")


def _zd_probe_model(units: dict[str, Unit], ports: dict[str, int]) -> None:
    """
    Answer the engine's health probe and port check from the modelled units.

    A modelled unit that is active answers on its port, and holds it; any
    other port is asked of the machine for real, so a probe of something
    that genuinely listens (the tabs' health servers) still reaches it. The
    application's state reads its port through
    :func:`noust.core.app_state.port_answers`, answered the same way.

    Args:
        units: The runner's units.
        ports: Their ports.
    """
    from urllib.parse import urlsplit

    import noust.core.app_state as app_state_module
    import noust.deployers.bluegreen as bluegreen_module

    def held(port: int | None) -> bool:
        return port is not None and any(
            unit.active == "active" and ports.get(name) == port for name, unit in units.items()
        )

    real_probe = bluegreen_module.wait_until_healthy
    real_free = bluegreen_module.is_port_available
    real_answers = app_state_module.port_answers

    def probe(url: str, **kwargs: Any) -> bool:
        return held(urlsplit(url).port) or real_probe(url, **kwargs)

    def port_free(port: int) -> bool:
        return not held(port) and real_free(port)

    def port_answers(port: int, *args: Any, **kwargs: Any) -> bool:
        return held(port) or real_answers(port, *args, **kwargs)

    bluegreen_module.wait_until_healthy = probe  # type: ignore[assignment]
    bluegreen_module.is_port_available = port_free  # type: ignore[assignment]
    app_state_module.port_answers = port_answers  # type: ignore[assignment]


# --- 2.2: pull request previews ----------------------------------------------


def seed_previews(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
) -> None:
    """
    Seed an application with pull request previews, and one preview deployed beside it.

    ``portal.example.org`` is deployed from GitHub on releases, with previews on
    under ``previews.example.org``: #42 ready (its own application, on releases
    too, marked as the portal's preview), #57 building and #61 failed. The
    sweep timer's units go to the sandboxed systemd directory, so saving the
    settings writes them there; removing a preview deletes its application
    through the fake runner and the sandbox filesystem.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
    """
    import noust.managers.previews as previews_module
    from tests.panel_factory import PREVIEWS_BASE_DOMAIN, seed_previews_records

    # Written when the settings are saved: into the sandbox, like every other unit.
    previews_module.SYSTEMD_DIR = sandbox.systemd_dir

    parent = PREVIEWS_APP
    _previews_release_app(
        sandbox, store, units, ports, domains, parent, commit="4d2a9c1", branch="main"
    )
    # Built from the pull request's branch, as a preview deploy records it.
    _previews_release_app(
        sandbox,
        store,
        units,
        ports,
        domains,
        previews_module.preview_domain_for(parent, 42, PREVIEWS_BASE_DOMAIN),
        commit="8c1f2e7",
        branch="feature/checkout-redesign",
    )
    seed_previews_records(store, parent)


#: The application with pull request previews.
PREVIEWS_APP = "portal.example.org"


def _previews_release_app(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    domain: str,
    *,
    commit: str,
    branch: str,
) -> Path:
    """
    Record a Next.js application on releases with one release on disk, serving.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        domain: The application.
        commit: The commit its one release was built from.
        branch: The branch it deploys.

    Returns:
        Its directory.
    """
    from noust.core.store import App, ReleaseRecord
    from noust.core.utils import domain_to_app_name

    root = sandbox.apps_dir / domain_to_app_name(domain)
    port = _tabs_serve_ok()
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / ".env").write_text(
        "NODE_ENV=production\n"
        f"PORT={port}\n"
        "DATABASE_URL=postgres://portal:Q3n8wLz5@127.0.0.1:5432/portal\n"
        f"NEXT_PUBLIC_SITE_URL=https://{domain}\n",
        encoding="utf-8",
    )
    (shared / ".env").chmod(0o600)
    (root / "repo").mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc) - timedelta(days=1, hours=3)
    release_id = _tabs_release_id(started, commit)
    release = root / "releases" / release_id
    (release / ".next").mkdir(parents=True, exist_ok=True)
    (release / "package.json").write_text(
        json.dumps({"name": "portal", "version": "0.9.0", "private": True}) + "\n",
        encoding="utf-8",
    )
    (release / ".next" / "BUILD_ID").write_text(commit + "\n", encoding="utf-8")
    (release / ".env").symlink_to("../../shared/.env")
    (root / "current").symlink_to(Path("releases") / release_id)

    app = App(
        domain=domain,
        app_type="nextjs",
        source="https://github.com/example-org/portal.git",
        branch=branch,
        port=port,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
        layout="releases",
        keep_releases=5,
    )
    _tabs_register(sandbox, store, units, ports, domains, app, working_directory=root / "current")
    stored = store.get_app(domain)
    if stored is not None and stored.id is not None:
        store.record_release(
            ReleaseRecord(
                id=release_id,
                app_id=stored.id,
                git_commit=commit,
                created_at=started.isoformat(),
                activated_at=(started + timedelta(minutes=2)).isoformat(),
                status="superseded",
                path=str(release),
            )
        )
        store.mark_release_active(stored.id, release_id)
    return root


# --- 2.2: the GitHub App -----------------------------------------------------


#: Where this machine receives code hosts' events (``noust web expose-hooks``).
GITHUB_HOOKS_URL = "https://hooks.example.com/hooks"

#: The one-time code GitHub's manifest flow hands back that it no longer honours
#: (codes last an hour and work once): the conversion is refused with GitHub's 404.
GITHUB_SPENT_CODE = "spent-manifest-code"

_GITHUB_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "Y29uc29sZS1zYW5kYm94LWdpdGh1Yi1hcHAta2V5LW5vdC1hLXJlYWwta2V5\n"
    "-----END RSA PRIVATE KEY-----\n"
)


def seed_github_app(sandbox: Sandbox, store: Any) -> None:
    """
    Connect the machine to its own GitHub App, and answer GitHub from the sandbox.

    The App and its installations are store rows
    (:func:`tests.panel_factory.seed_github_app`); its private key, webhook
    secret and client secret are written where the real secret store keeps
    them, the hooks are exposed at :data:`GITHUB_HOOKS_URL` and the App's
    webhook is recorded active there, so Settings > Integrations shows a
    connected, receiving App. Every call to GitHub - the App's token
    exchange, installations, repositories, branches, the manifest
    conversion - is answered by :func:`_github_opener` instead of
    api.github.com, the JWT is "signed" without openssl, and a clone of one
    of the App's repositories checks out the seeded storefront project, so
    the wizard's "From GitHub" path inspects and deploys offline.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
    """
    from noust.core.config import Config
    from noust.core.secrets import SecretStore
    from noust.integrations.github.app import (
        CLIENT_SECRET,
        PRIVATE_KEY_SECRET,
        WEBHOOK_SECRET,
        write_meta,
    )
    from noust.integrations.github.client import GitHubClient, set_client
    from noust.integrations.hooks_site import HOOKS_URL_KEY
    from tests.panel_factory import GITHUB_APP
    from tests.panel_factory import seed_github_app as record_github_app

    record_github_app(store)
    secrets = SecretStore()
    secrets.write(PRIVATE_KEY_SECRET, _GITHUB_PEM)
    secrets.write(WEBHOOK_SECRET, "console-sandbox-webhook-secret")
    secrets.write(CLIENT_SECRET, "console-sandbox-client-secret")
    config = Config()
    config.set(HOOKS_URL_KEY, GITHUB_HOOKS_URL)
    config.save()
    write_meta(
        owner_type=GITHUB_APP["owner_type"],
        webhook_url=f"{GITHUB_HOOKS_URL}/github",
        webhook_active=True,
    )
    set_client(GitHubClient(opener=_github_opener))
    _github_git_and_openssl(sandbox)


def _github_json(value: Any, status: int = 200) -> Any:
    """
    Answer a GitHub API request the way urllib hands back a response.

    Args:
        value: The JSON body.
        status: The status; anything but 2xx is raised as urllib raises it.

    Returns:
        A readable, closable body.

    Raises:
        HTTPError: For an error status, carrying GitHub's own ``message``.
    """
    import io
    from email.message import Message
    from urllib.error import HTTPError

    body = json.dumps(value).encode("utf-8")
    if status >= 400:
        raise HTTPError("https://api.github.com", status, "error", Message(), io.BytesIO(body))
    return io.BytesIO(body)


def _github_installation(installation_id: int) -> dict[str, Any] | None:
    """
    Describe one installation as GitHub's API does.

    Args:
        installation_id: The installation.

    Returns:
        GitHub's installation object, or None for one the App does not have.
    """
    from tests.panel_factory import GITHUB_INSTALLATIONS

    for known, account, account_type, selection in GITHUB_INSTALLATIONS:
        if known == installation_id:
            return {
                "id": known,
                "account": {"login": account, "type": account_type},
                "repository_selection": selection,
                "target_type": account_type,
            }
    return None


def _github_opener(request: Any, timeout: float | None = None) -> Any:
    """
    Answer the GitHub REST API from the seeded App, installations and repositories.

    Args:
        request: The ``urllib.request.Request`` the client built.
        timeout: Ignored: nothing is dialled.

    Returns:
        The response body.

    Raises:
        HTTPError: Where GitHub would answer an error.
    """
    from urllib.parse import urlsplit

    from tests.panel_factory import (
        GITHUB_APP,
        GITHUB_BRANCHES,
        GITHUB_INSTALLATIONS,
        GITHUB_REPOSITORIES,
    )

    method = request.get_method()
    parts = urlsplit(str(request.full_url))
    path = parts.path
    page = 1
    for pair in parts.query.split("&"):
        key, _, value = pair.partition("=")
        if key == "page" and value.isdigit():
            page = int(value)
    token = str(request.get_header("Authorization") or "")
    not_found = {"message": "Not Found", "documentation_url": "https://docs.github.com/rest"}

    if method == "POST" and (
        match := re.fullmatch(r"/app/installations/(\d+)/access_tokens", path)
    ):
        if _github_installation(int(match.group(1))) is None:
            return _github_json(not_found, 404)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return _github_json({"token": f"ghs_console{match.group(1)}", "expires_at": expires}, 201)
    if method == "GET" and path == "/app/installations":
        items = [_github_installation(known) for known, *_ in GITHUB_INSTALLATIONS]
        return _github_json(items if page == 1 else [])
    if method == "GET" and (match := re.fullmatch(r"/app/installations/(\d+)", path)):
        found = _github_installation(int(match.group(1)))
        return _github_json(found if found is not None else not_found, 200 if found else 404)
    if method == "GET" and path == "/installation/repositories":
        installation = int(token.removeprefix("token ghs_console") or 0)
        repositories = [
            {
                "full_name": name,
                "name": name.split("/")[1],
                "private": private,
                "default_branch": branch,
                "clone_url": f"https://github.com/{name}.git",
            }
            for name, private, branch in GITHUB_REPOSITORIES.get(installation, ())
        ]
        listed = repositories if page == 1 else []
        return _github_json({"total_count": len(repositories), "repositories": listed})
    if method == "GET" and (match := re.fullmatch(r"/repos/([^/]+)/([^/]+)/branches", path)):
        full_name = f"{match.group(1)}/{match.group(2)}"
        known = {name for names in GITHUB_REPOSITORIES.values() for name, *_ in names}
        if full_name not in known:
            return _github_json(not_found, 404)
        branches = [
            {"name": name, "protected": protected, "commit": {"sha": sha}}
            for name, protected, sha in GITHUB_BRANCHES
        ]
        return _github_json(branches if page == 1 else [])
    if method == "POST" and (match := re.fullmatch(r"/app-manifests/([^/]+)/conversions", path)):
        if match.group(1) == GITHUB_SPENT_CODE:
            return _github_json(not_found, 404)
        return _github_json(
            {
                "id": GITHUB_APP["app_id"],
                "slug": GITHUB_APP["slug"],
                "name": GITHUB_APP["name"],
                "owner": {"login": GITHUB_APP["owner"], "type": GITHUB_APP["owner_type"]},
                "html_url": GITHUB_APP["html_url"],
                "client_id": GITHUB_APP["client_id"],
                "client_secret": "console-sandbox-client-secret",
                "webhook_secret": "console-sandbox-webhook-secret",
                "pem": _GITHUB_PEM,
            },
            201,
        )
    if method == "PATCH" and path == "/app/hook/config":
        return _github_json(json.loads(request.data or b"{}"))
    # What a deploy or a preview reports back: a pull request's comment, kept updated, and
    # the deployments and commit statuses GitHub shows. Accepted as GitHub accepts them.
    if method == "POST" and re.fullmatch(r"/repos/[^/]+/[^/]+/issues/\d+/comments", path):
        return _github_json(
            {"id": 7700001, "body": json.loads(request.data or b"{}").get("body")}, 201
        )
    if method in ("PATCH", "DELETE") and re.fullmatch(
        r"/repos/[^/]+/[^/]+/issues/comments/\d+", path
    ):
        return _github_json({"id": int(path.rsplit("/", 1)[1])} if method == "PATCH" else None)
    if method == "POST" and re.fullmatch(
        r"/repos/[^/]+/[^/]+/(deployments(/\d+/statuses)?|statuses/[0-9a-f]{7,40})", path
    ):
        return _github_json({"id": 7800001, "state": "success"}, 201)
    return _github_json(not_found, 404)


def _github_git_and_openssl(sandbox: Sandbox) -> None:
    """
    Answer the two commands the App's work runs besides HTTP.

    ``openssl dgst -sign`` signs the App's JWT: answered with a fixed
    signature, since the fake GitHub checks none. ``git clone`` of one of the
    App's repositories checks out the seeded storefront project (a Next.js
    app), and ``git ls-tree`` in such a checkout lists its files, which is
    what the wizard's inspection reads; every other command goes on to the
    runner as before.

    Args:
        sandbox: The sandbox, whose ``/var/www/src/storefront`` is checked out.
    """
    from noust.core.runner import CommandResult, get_runner
    from tests.panel_factory import GITHUB_REPOSITORIES

    runner = get_runner()
    original = runner.run
    project = sandbox.apps_dir.parent / "src" / "storefront"
    repositories = {name.lower() for names in GITHUB_REPOSITORIES.values() for name, *_ in names}
    checkouts: set[str] = set()

    def repository_of(url: str) -> str | None:
        match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?", url)
        return match.group(1).lower() if match and match.group(1).lower() in repositories else None

    def git_verb(args: tuple[str, ...]) -> tuple[str, ...]:
        """The git command without the ``-c key=value`` pairs every Noust git call starts with."""
        rest = args[1:]
        while rest[:1] == ("-c",):
            rest = rest[2:]
        return rest

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        if args[:2] == ("openssl", "dgst") and "-sign" in args:
            runner.calls.append(args)
            return CommandResult(args, 0, "SHA2-256(stdin)= " + "5a" * 256 + "\n", "")
        verb = git_verb(args) if args[:1] == ("git",) else ()
        if verb[:1] == ("clone",) and "--" in verb:
            url, destination = verb[verb.index("--") + 1 :][:2]
            if repository_of(url) is not None:
                runner.calls.append(args)
                shutil.copytree(project, destination, dirs_exist_ok=True)
                checkouts.add(os.path.abspath(destination))
                return CommandResult(args, 0, "", f"Cloning into '{destination}'...\n")
        cwd = kwargs.get("cwd")
        if verb[:1] == ("ls-tree",) and cwd is not None:
            root = os.path.abspath(cwd)
            if root in checkouts:
                runner.calls.append(args)
                names = sorted(
                    str(path.relative_to(root))
                    for path in Path(root).rglob("*")
                    if path.is_file() and ".git" not in path.parts
                )
                return CommandResult(args, 0, "\0".join(names) + "\0", "")
        return original(argv, **kwargs)

    runner.run = run  # type: ignore[method-assign]


# --- 2.2: backup destinations ------------------------------------------------


#: The folder the SFTP destination writes into, as its operator typed it.
DESTINATIONS_SFTP_PATH = "/srv/backups/web-01"

#: Applications whose local backups the SFTP destination already holds copies of.
DESTINATIONS_SFTP_APPS = ("shop.example.net", "example.com")

#: The application whose backups the encrypted destination holds.
DESTINATIONS_ENCRYPTED_APP = "pedidos.example.org"

#: The application with a schedule that copies to both destinations.
DESTINATIONS_SCHEDULED_APP = "shop.example.net"

#: A host suffix no resolver answers (RFC 6761): a destination pointed at one is
#: unreachable, and rclone's own words say so.
DESTINATIONS_UNREACHABLE = ".invalid"


def seed_showcase_backup_destination() -> None:
    """
    Register one backup destination for ``--showcase``, config only.

    :func:`seed_backup_destinations` (below) models a real rclone remote's contents, on
    disk, so the Backups page's copy and restore flows have something genuine to move -
    none of the documentation screenshots open that page, so this only registers the
    destination itself, through the same :class:`~noust.managers.backup_destinations.
    BackupDestinationManager` a real ``noust backup destination add`` calls, for the
    dataset's own sake (CONTRIBUTING.md rule 4: even a demo goes through the real manager, never
    a hand-written store row).
    """
    from noust.managers.backup_destinations import BackupDestinationManager

    BackupDestinationManager().add(
        "eu-fra-sftp",
        "sftp",
        {
            "host": "backup-fra.kelmoor.dev",
            "user": "kelmoor",
            "port": "22",
            "pass": "Nq37-seeded-sftp-password",
            "path": "/srv/backups/noust",
        },
    )


def seed_showcase_nginx_error_log(sandbox: Sandbox, domain: str) -> None:
    """
    Write one realistic nginx error log line for a failed application (``--showcase``
    only), so the Diagnose page's nginx-log check reads real content instead of skipping
    for want of a file: without this, every showcase machine shows "Could not read
    <path>", which is also a path only :func:`use_showcase_diagnose_paths` cleans up, so a
    check that actually ran is the more convincing fix either way.

    Args:
        sandbox: The sandbox; ``redirect_system_paths`` already pointed
            ``noust.managers.diagnose.NGINX_ERROR_LOG`` at the file this writes.
        domain: The failed application the line is about.
    """
    log_path = sandbox.var / "log" / "nginx" / "error.log"
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    when = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    log_path.write_text(
        f"{when} [error] 41231#41231: *82 connect() failed (111: Connection refused) "
        f"while connecting to upstream, client: 127.0.0.1, server: {domain}, "
        f'request: "GET / HTTP/1.1", upstream: "http://127.0.0.1/", host: "{domain}"\n',
        encoding="utf-8",
    )


def use_showcase_diagnose_paths(sandbox: Sandbox) -> None:
    """
    Clean the sandbox's own temp-directory prefix out of every Diagnose check's text
    (``--showcase`` only).

    Two checks print a raw filesystem path in their summary regardless of outcome - the
    disk-space check's "on the filesystem holding <apps directory>", and the nginx-log
    check's "Could not read <log>" when it has nothing to read - and both paths are the
    sandbox's, since every real path
    (:func:`redirect_system_paths`) is redirected under a ``/tmp/noust-console-...``
    directory a documentation screenshot has no business showing (coordinator review).
    Wrapping every probe, not just those two, is cheap and future-proof: a check added
    later that also happens to print a real path is cleaned the same way without anyone
    having to notice and list it here.

    ``noust.managers.diagnose._PROBES`` is read fresh (``probes = _PROBES``) each time a
    diagnosis runs, so replacing the module attribute here reaches every call after this
    point, the same way :func:`use_fixed_hostname` replaces a module's own ``socket``.

    Args:
        sandbox: The sandbox whose root directory is the prefix to remove.
    """
    import dataclasses

    import noust.managers.diagnose as diagnose_module

    prefix = str(sandbox.root)

    def clean(text: str) -> str:
        return text.replace(prefix, "")

    def wrap(probe: Any) -> Any:
        def wrapped(ctx: Any) -> Any:
            check, facts = probe(ctx)
            return (
                dataclasses.replace(
                    check, summary=clean(check.summary), evidence=clean(check.evidence)
                ),
                facts,
            )

        return wrapped

    diagnose_module._PROBES = tuple((name, wrap(fn)) for name, fn in diagnose_module._PROBES)


def seed_backup_destinations(sandbox: Sandbox, store: Any) -> None:
    """
    Add two backup destinations, copies of backups on them, and a schedule using both.

    The destinations are created through
    :class:`~noust.managers.backup_destinations.BackupDestinationManager`, so their
    secrets are where the manager reads them (the sandboxed secret store beside the
    database) and the encrypted one has real crypt passphrases to show. rclone is
    answered from a directory per remote inside the sandbox (see
    :func:`_destinations_rclone_model`): a copy, a listing, a test and a restore all
    move real files, so an uploaded archive verifies and a downloaded one restores.

    Args:
        sandbox: The sandbox, its local backups already seeded.
        store: The seeded store.
    """
    from noust.core.utils import domain_to_app_name
    from noust.managers.backup_destinations import BackupDestinationManager
    from noust.managers.backup_scheduler import BackupSchedule, BackupScheduler
    from tests.panel_factory import DESTINATION_ENCRYPTED, DESTINATION_SFTP, seed_push_job

    remotes = sandbox.root / "remotes"
    _destinations_rclone_model(sandbox, remotes)

    manager = BackupDestinationManager()
    manager.add(
        DESTINATION_SFTP,
        "sftp",
        {
            "host": "backup.example.org",
            "user": "wasm",
            "port": "22",
            "pass": "Kd82-sftp-seeded-password",
            "path": DESTINATIONS_SFTP_PATH,
        },
    )
    manager.add(
        DESTINATION_ENCRYPTED,
        "s3",
        {
            "provider": "Cloudflare",
            "access_key_id": "4f1c0e9a7b2d6e8f",
            "secret_access_key": "r2-seeded-secret-access-key-not-shown",
            "region": "auto",
            "endpoint": "https://2b7c9e.r2.cloudflarestorage.com",
            "path": "wasm-backups",
        },
        encrypted=True,
    )

    sftp_root = remotes / DESTINATION_SFTP / DESTINATIONS_SFTP_PATH.lstrip("/")
    for domain in DESTINATIONS_SFTP_APPS:
        _destinations_copy_backups(sandbox, domain, sftp_root)
    # One backup only the destination still has: older than anything kept locally.
    _destinations_copy_backups(sandbox, "shop.example.net", sftp_root, remote_only_days=41)
    # rclone's crypt remote carries the wrapped path itself (`vault-r2crypt:`).
    _destinations_copy_backups(
        sandbox, DESTINATIONS_ENCRYPTED_APP, remotes / f"{DESTINATION_ENCRYPTED}crypt"
    )

    BackupScheduler(verbose=False).create_schedule(
        BackupSchedule(
            domain=DESTINATIONS_SCHEDULED_APP,
            app_name=domain_to_app_name(DESTINATIONS_SCHEDULED_APP),
            schedule="daily",
            retention_count=7,
            retention_days=30,
            destinations=[
                {"name": DESTINATION_SFTP, "retention_count": 14, "retention_days": 90},
                {"name": DESTINATION_ENCRYPTED, "retention_count": 30, "retention_days": None},
            ],
        )
    )

    newest = sorted(
        (sandbox.backup_dir / domain_to_app_name(DESTINATIONS_SCHEDULED_APP)).glob("*.json")
    )[-1]
    seed_push_job(
        store,
        backup_id=newest.stem,
        domain=DESTINATIONS_SCHEDULED_APP,
        destination=DESTINATION_SFTP,
    )


def _destinations_copy_backups(
    sandbox: Sandbox, domain: str, root: Path, *, remote_only_days: int | None = None
) -> None:
    """
    Put copies of an application's local backups on a modelled remote.

    Args:
        sandbox: The sandbox holding the local backups.
        domain: The application.
        root: The remote folder; the application's directory goes under it.
        remote_only_days: Instead of copying every local backup, write one more,
            this many days old, that exists nowhere but on the remote.
    """
    from noust.core.utils import domain_to_app_name

    app_name = domain_to_app_name(domain)
    local = sandbox.backup_dir / app_name
    target = root / app_name
    target.mkdir(parents=True, exist_ok=True)
    for metadata_file in sorted(local.glob("*.json")):
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        archive = local / f"{metadata_file.stem}.tar.gz"
        created = datetime.fromisoformat(str(metadata["created_at"]))
        backup_id = metadata_file.stem
        if remote_only_days is not None:
            created = datetime.now() - timedelta(days=remote_only_days, hours=3)
            backup_id = f"{app_name}_{created.strftime('%Y%m%d_%H%M%S')}"
            metadata = {**metadata, "id": backup_id, "created_at": created.isoformat()}
            metadata["description"] = "Nightly backup"
        shutil.copyfile(archive, target / f"{backup_id}.tar.gz")
        (target / f"{backup_id}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        stamp = created.timestamp()
        for name in (f"{backup_id}.tar.gz", f"{backup_id}.json"):
            os.utime(target / name, (stamp, stamp))
        if remote_only_days is not None:
            return


def _destinations_rclone_model(sandbox: Sandbox, remotes: Path) -> None:
    """
    Answer rclone, and the backup timers' systemctl questions, from the sandbox.

    Every remote is a directory under ``remotes`` named after the remote
    (``offsite-sftp``, or ``vault-r2crypt`` for an encrypted destination's crypt
    layer), so ``copyto``, ``lsjson``, ``lsf``, ``mkdir`` and ``deletefile``
    act on real files. A destination whose host or endpoint ends in
    :data:`DESTINATIONS_UNREACHABLE` fails the way rclone fails on a name no
    resolver knows. ``systemctl list-timers noust-backup-*`` and ``show`` of
    such a timer are answered from the unit files the scheduler wrote, so the
    Schedules section lists what is scheduled.

    Args:
        sandbox: The sandbox.
        remotes: Where the remotes' directories live.
    """
    import hashlib

    from noust.core.runner import CommandResult, get_runner

    remotes.mkdir(parents=True, exist_ok=True)
    runner = get_runner()
    original = runner.run
    remote_ref = re.compile(r"^([a-z0-9][a-z0-9-]*):(.*)$")

    def stamp(moment: float) -> str:
        return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")

    def resolve(reference: str) -> tuple[str, Path] | None:
        match = remote_ref.match(reference)
        if match is None:
            return None
        remote, path = match.groups()
        return remote, remotes / remote / path.strip("/")

    def unreachable(remote: str, env: Mapping[str, str]) -> str | None:
        """The name rclone could not resolve, when the remote points at one."""
        base = remote.removesuffix("crypt") if remote.endswith("crypt") else remote
        prefix = f"RCLONE_CONFIG_{base.upper()}_"
        for key in ("HOST", "ENDPOINT", "URL"):
            value = env.get(prefix + key, "")
            host = re.sub(r"^https?://", "", value).split("/")[0]
            if host.endswith(DESTINATIONS_UNREACHABLE):
                return host
        return None

    def failed(args: tuple[str, ...], stderr: str, code: int = 1) -> Any:
        moment = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        return CommandResult(args, code, "", f"{moment} {stderr}\n")

    def entry(path: Path, *, hashes: bool) -> dict[str, Any]:
        info = path.stat()
        listed: dict[str, Any] = {
            "Path": path.name,
            "Name": path.name,
            "Size": -1 if path.is_dir() else info.st_size,
            "MimeType": "inode/directory"
            if path.is_dir()
            else ("application/json" if path.suffix == ".json" else "application/gzip"),
            "ModTime": stamp(info.st_mtime),
            "IsDir": path.is_dir(),
        }
        if hashes and path.is_file():
            listed["Hashes"] = {"md5": hashlib.md5(path.read_bytes()).hexdigest()}  # noqa: S324
        return listed

    def rclone(args: tuple[str, ...], env: Mapping[str, str], stdin: str) -> Any:
        verb = args[1] if len(args) > 1 else ""
        operands: list[str] = []
        skip = False
        for arg in args[2:]:
            if skip:
                skip = False
            elif arg == "--max-depth":
                skip = True  # its value is not an operand
            elif not arg.startswith("-"):
                operands.append(arg)
        if verb in ("version", "--version"):
            return CommandResult(args, 0, "rclone v1.68.2\n- os/version: ubuntu 24.04\n", "")
        if verb == "obscure":
            digest = hashlib.sha256(stdin.encode("utf-8")).hexdigest()[:32]
            return CommandResult(args, 0, f"{digest}\n", "")
        resolved = [resolve(operand) for operand in operands]
        for reference, found in zip(operands, resolved, strict=True):
            if found is None:
                continue
            host = unreachable(found[0], env)
            if host is not None:
                return failed(
                    args,
                    f'CRITICAL: Failed to create file system for "{reference}": NewFs: '
                    f"couldn't connect SSH: dial tcp: lookup {host}: no such host",
                )
        target = resolved[0] if resolved else None
        if verb == "mkdir" and target is not None:
            target[1].mkdir(parents=True, exist_ok=True)
            return CommandResult(args, 0, "", "")
        if verb in ("lsf", "lsjson") and target is not None:
            directory = target[1]
            if not directory.is_dir():
                return failed(
                    args, f"ERROR : : error listing: directory not found: {operands[0]}", 3
                )
            children = sorted(directory.iterdir())
            if verb == "lsf":
                lines = [f"{child.name}/" if child.is_dir() else child.name for child in children]
                return CommandResult(args, 0, "".join(f"{line}\n" for line in lines), "")
            # rclone's crypt layer cannot report the hash of what it encrypted.
            hashes = "--hash" in args and not target[0].endswith("crypt")
            listing = [entry(child, hashes=hashes) for child in children]
            return CommandResult(args, 0, json.dumps(listing), "")
        if verb == "copyto" and len(operands) == 2:
            source = resolved[0][1] if resolved[0] is not None else Path(operands[0])
            destination = resolved[1][1] if resolved[1] is not None else Path(operands[1])
            if not source.is_file():
                return failed(
                    args, f"ERROR : {operands[0]}: error reading source: object not found", 3
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            return CommandResult(args, 0, "", "")
        if verb == "deletefile" and target is not None:
            if not target[1].is_file():
                return failed(args, f"ERROR : {operands[0]}: object not found", 4)
            target[1].unlink()
            return CommandResult(args, 0, "", "")
        return failed(args, f"Fatal error: unknown command {verb!r} in the console model", 2)

    def timers(args: tuple[str, ...]) -> Any:
        now = datetime.now(timezone.utc)
        lines = []
        for timer in sorted(sandbox.systemd_dir.glob("noust-backup-*.timer")):
            next_run = (now + timedelta(hours=15)).strftime("%a %Y-%m-%d %H:%M:%S UTC")
            last_run = (now - timedelta(hours=9)).strftime("%a %Y-%m-%d %H:%M:%S UTC")
            service = timer.name.removesuffix(".timer") + ".service"
            lines.append(f"{next_run} 15h left {last_run} 9h ago {timer.name} {service}")
        return CommandResult(args, 0, "".join(f"{line}\n" for line in lines), "")

    def timer_properties(args: tuple[str, ...], unit: str) -> Any:
        now = datetime.now(timezone.utc)
        text = (sandbox.systemd_dir / unit).read_text(encoding="utf-8")
        values = dict(
            line.split("=", 1) for line in text.splitlines() if "=" in line and line[0] != "#"
        )
        calendar = values.get("OnCalendar", "daily")
        shown = "%a %Y-%m-%d %H:%M:%S UTC"
        return CommandResult(
            args,
            0,
            f"Description={values.get('Description', '')}\n"
            f"TimersCalendar={{ OnCalendar={calendar} ; next_elapse=n/a }}\n"
            f"LastTriggerUSec={(now - timedelta(hours=9)).strftime(shown)}\n"
            f"NextElapseUSecRealtime={(now + timedelta(hours=15)).strftime(shown)}\n"
            # Its file is on disk, so systemd has it loaded: a database policy's view asks.
            "LoadState=loaded\n",
            "",
        )

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        program = args[0] if args else ""
        if program == "rclone":
            runner.calls.append(args)
            return rclone(args, kwargs.get("env") or {}, kwargs.get("input") or "")
        if program == "systemctl" and "list-timers" in args and "noust-backup-*" in args:
            runner.calls.append(args)
            return timers(args)
        if program == "systemctl" and args[1:2] == ("show",) and len(args) > 2:
            unit = args[2]
            if (
                unit.startswith("noust-backup-")
                and unit.endswith(".timer")
                and (sandbox.systemd_dir / unit).is_file()
            ):
                runner.calls.append(args)
                return timer_properties(args, unit)
        return original(argv, **kwargs)

    runner.run = run  # type: ignore[method-assign]


# --- 2.2: environment variable marks -----------------------------------------


def seed_env_marks(sandbox: Sandbox, store: Any) -> None:
    """
    Give one application's variables an operator's own secret / not secret marks.

    The Environment tab says why each value is hidden or shown; with only the
    classifier's verdicts it never shows "marked ... by you". The application
    is one whose ``.env`` the suite never replaces, and the variable marked not
    secret is added to that file first, so both marks name a line it holds.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
    """
    from noust.core.utils import domain_to_app_name
    from tests.panel_factory import ENV_MARKS_APP, ENV_MARKS_PUBLIC
    from tests.panel_factory import seed_env_marks as mark

    env_file = sandbox.apps_dir / domain_to_app_name(ENV_MARKS_APP) / ".env"
    name, value = ENV_MARKS_PUBLIC
    with env_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")
    mark(store, ENV_MARKS_APP)


# ---------------------------------------------------------------------------
# 2.3: recipes, other platforms' configuration, export and import
# ---------------------------------------------------------------------------

#: A static site set up with everything an export carries: an alias, a cron
#: job, variables with a secret among them, a health check and a release
#: retention. Deployed at seed time by the real deploy, so its export is one
#: an import can deploy again in the sandbox.
EXPORT_APP = "lanzamiento.example.org"

#: Its alias, which an import onto another domain renames with it.
EXPORT_ALIAS = "www.lanzamiento.example.org"

#: Its cron job, named after it.
EXPORT_CRON = "lanzamiento-sitemap"

#: Its variables: the first two are shown, the secret one is hidden in an export.
EXPORT_ENV = {
    "SITE_NAME": "Lanzamiento",
    "ANALYTICS_ID": "UA-000000-2",
    "NEWSLETTER_API_KEY": "nl_live_8f2c61d0b7a94e3f",
}

#: The database it is linked to: named by an export, never recreated by an import.
EXPORT_DATABASE = "lanzamiento_newsletter"

#: The wizard's source whose repository carries a Railway configuration.
WIZARD_RAILWAY_SOURCE = "railway-api"

#: Where the recipes' archives come from, answered from files the seed builds:
#: the dev server never reaches the network.
_RECIPE_ARCHIVES = ("https://wordpress.org/latest.tar.gz",)


def _wordpress_archive() -> bytes:
    """
    Build a small stand-in for wordpress.org's latest.tar.gz.

    The layout WordPress ships (one ``wordpress/`` directory, ``index.php``,
    ``wp-content/``, the files the recipe refuses to serve), so the recipe's
    shared paths and refused paths have something to act on.

    Returns:
        The gzipped tarball.
    """
    import io

    files = {
        "wordpress/index.php": "<?php\ndefine('WP_USE_THEMES', true);\nrequire __DIR__ . '/wp-blog-header.php';\n",
        "wordpress/wp-blog-header.php": "<?php\n// WordPress, as the console server models it.\n",
        "wordpress/wp-config-sample.php": "<?php\ndefine('DB_NAME', 'database_name_here');\n",
        "wordpress/readme.html": "<!DOCTYPE html>\n<title>WordPress</title>\n",
        "wordpress/license.txt": "WordPress - Web publishing software\n",
        "wordpress/wp-admin/install.php": "<?php\n// The installer.\n",
        "wordpress/wp-content/index.php": "<?php\n// Silence is golden.\n",
        "wordpress/wp-content/plugins/index.php": "<?php\n// Silence is golden.\n",
        "wordpress/wp-content/themes/index.php": "<?php\n// Silence is golden.\n",
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = int(time.time())
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _recipes_offline() -> None:
    """
    Answer the recipes' downloads from memory, and refuse every other one.

    Patched where the source manager opens a URL, below the checksum check:
    the archive is verified against the published SHA-1 exactly as on a real
    server, only both files come from here instead of wordpress.org.
    """
    import hashlib
    import io
    from urllib.error import URLError

    import noust.managers.source_manager as source_module

    archive = _wordpress_archive()
    answers = {
        _RECIPE_ARCHIVES[0]: archive,
        f"{_RECIPE_ARCHIVES[0]}.sha1": hashlib.sha1(archive, usedforsecurity=False)
        .hexdigest()
        .encode("ascii"),
    }

    def open_url(url: str, timeout: int = 0) -> Any:
        data = answers.get(url)
        if data is None:
            raise URLError(f"the console server does not reach the network ({url})")
        return io.BytesIO(data)

    source_module._open_url = open_url  # type: ignore[assignment]


def _recipes_php_fpm(sandbox: Sandbox) -> None:
    """
    Model PHP 8.3's FPM, so a PHP recipe deploys and its pool is written.

    The pool directory and the binary exist in the sandbox, where the
    deployer is told to look for them; the binary's configuration test and
    the reload go through the runner, which answers them. The pool is asked
    over FastCGI by the health gate, and answers the way WordPress does before
    it is installed: a redirect to its installer.

    Args:
        sandbox: The sandbox.
    """
    import functools

    import noust.deployers.helpers.php_fpm as fpm_helpers
    import noust.deployers.php_fpm as php_module

    (sandbox.etc / "php" / "8.3" / "fpm" / "pool.d").mkdir(parents=True, exist_ok=True)
    binary = sandbox.root / "usr" / "sbin" / "php-fpm8.3"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("", encoding="utf-8")
    binary.chmod(0o755)
    php_module.FPM_ROOT = sandbox.root  # type: ignore[misc]
    # The dependency report asks without a root: the same machine.
    original_find = fpm_helpers.find_fpm
    fpm_helpers.find_fpm = functools.partial(original_find, sandbox.root)  # type: ignore[assignment]

    def installer(_socket: Path, _params: Mapping[str, str], **_kwargs: Any) -> Any:
        return fpm_helpers.FastCgiResponse(
            status=302, headers={"location": "/wp-admin/install.php"}, stderr=""
        )

    php_module.fastcgi_probe = functools.partial(  # type: ignore[assignment]
        fpm_helpers.fastcgi_probe, requester=installer
    )
    # The machine snapshot sorts a PHP app by whether its pool's socket accepts a
    # connection; the modelled FPM runs every pool whose file is in place.
    if hasattr(php_module, "socket_accepts"):
        php_module.socket_accepts = lambda _path, *args, **kwargs: True


def seed_showcase_php_fpm_pools(sandbox: Sandbox, store: Any) -> None:
    """
    Write a pool file for each showcase PHP-FPM application (``--showcase`` only).

    ``_recipes_php_fpm`` above only models the shared FPM installation, so the wizard's own
    WordPress recipe deploy can write its pool for real and be asked over FastCGI - it never
    creates a pool for an application that was seeded directly into the store rather than
    deployed. ``pool_serving()`` (``noust/deployers/php_fpm.py``) reads the pool file's mere
    existence, cheaply, on every apps list request, so without one here a showcase PHP-FPM
    application would read as failed despite its stored status.

    Args:
        sandbox: The sandbox, with ``_recipes_php_fpm`` already run.
        store: The seeded store.
    """
    from noust.core.utils import domain_to_app_name

    pool_dir = sandbox.etc / "php" / "8.3" / "fpm" / "pool.d"
    for app in store.list_apps():
        if app.app_type != "php-fpm":
            continue
        name = domain_to_app_name(app.domain)
        (pool_dir / f"noust-{name}.conf").write_text(
            f"[{name}]\n"
            "user = www-data\n"
            "group = www-data\n"
            f"listen = /run/php/noust-{name}.sock\n"
            "pm = dynamic\n"
            "pm.max_children = 5\n",
            encoding="utf-8",
        )


def seed_showcase_machine_metrics() -> None:
    """
    Write a month of machine-wide history, so the Overview's charts show a curve
    (``--showcase`` only).

    The real :class:`~noust.web.metrics_collector.MetricsCollector` is a background thread
    that samples this process every couple of seconds; a screenshot taken moments after the
    server starts has nothing to chart yet, and every range - "Last hour" through "30d" -
    would read "Collecting samples." Written directly into the same store, over the same
    metric names ``MetricsCollector._system_pairs`` itself records, the way
    :func:`_tabs_metrics` writes an application's own history: denser the more recent, a
    daily wave, noise, and a couple of smooth spikes (a build, a burst of traffic) so the
    chart reads as a real machine's rather than a flat line.

    This only backfills the past: the collector's own ticks, from the moment it starts,
    are real ``psutil`` reads and would read as a machine noisily unlike this one, or
    (every seeded node being the same sandboxed host) unlike each other -
    :func:`use_fixed_machine_stats`, called once ``serve()`` knows which node this is, is
    what keeps those consistent with the history seeded here.
    """
    import math

    from noust.web.metrics_collector import get_metrics_store

    store = get_metrics_store()
    now = int(time.time())
    stamps = [
        *range(now - 30 * 86_400, now - 86_400, 1_800),
        *range(now - 86_400, now - 3_600, 120),
        *range(now - 3_600, now, 10),
    ]
    mem_total = 16 * 1024**3
    disk_total = 480 * 1024**3
    # Smooth bumps (a Gaussian each), not a step: one a few minutes ago, still visible on
    # "Last hour", and two further back that only "24h" and wider ranges will show.
    spike_minutes_ago = (4.0, 55.0, 340.0)
    for stamp in stamps:
        age_days = (now - stamp) / 86_400
        minutes_ago = (now - stamp) / 60.0
        daily = math.sin((stamp % 86_400) / 86_400 * 2 * math.pi - math.pi / 2)
        wobble = math.sin(stamp / 977.0) * 0.6 + math.sin(stamp / 331.0) * 0.4
        spike = max(
            math.exp(-(((minutes_ago - centre) / 2.4) ** 2)) for centre in spike_minutes_ago
        )
        cpu = max(1.0, min(96.0, 13.0 + 9.0 * daily + 4.0 * wobble + 44.0 * spike))
        mem_used = mem_total * max(0.12, min(0.85, 0.29 + 0.05 * daily + 0.02 * wobble))
        disk_used = disk_total * max(0.10, min(0.60, 0.235 + 0.0015 * (30 - age_days)))
        net_rx = max(150.0, 4_200 + 2_600 * wobble + 21_000 * spike)
        net_tx = max(90.0, 1_800 + 900 * wobble + 6_500 * spike)
        store.record_many(
            [
                ("cpu.percent", cpu),
                ("mem.used_bytes", mem_used),
                ("mem.total_bytes", float(mem_total)),
                ("swap.used_bytes", mem_total * 0.015),
                ("disk.used_bytes", disk_used),
                ("disk.total_bytes", float(disk_total)),
                ("net.rx_bytes_s", net_rx),
                ("net.tx_bytes_s", net_tx),
                ("load.1m", max(0.05, 0.5 + 0.3 * wobble)),
            ],
            ts=stamp,
        )
    store.consolidate(now=now)


def seed_showcase_app_metrics(store: Any) -> None:
    """
    Write a month of CPU and memory history for each showcase application that has a real
    unit (``--showcase`` only).

    Reuses :func:`_tabs_metrics`, which already writes over the metric names an
    application's Metrics tab reads. A static site, a PHP-FPM pool and a Docker Compose
    stack are skipped: none of them is sampled by cgroup on a real machine either (see
    ``noust/web/metrics_collector.py``'s module docstring), so seeding history for them
    would chart something that was never measured.

    Args:
        store: The seeded store.
    """
    sampled_types = {"nextjs", "nodejs", "python"}
    base_mb = {"nextjs": 180.0, "nodejs": 120.0, "python": 150.0}
    for app in store.list_apps():
        if app.app_type not in sampled_types:
            continue
        starts = [
            parsed
            for record in store.list_deployments(app.domain)
            if record.started_at and (parsed := _parse_iso(record.started_at)) is not None
        ]
        if not starts:
            continue
        _tabs_metrics(app.domain, starts, base_mb=base_mb[app.app_type])


def _parse_iso(value: str) -> datetime | None:
    """
    Args:
        value: An ISO 8601 timestamp as the store writes one.

    Returns:
        The parsed moment, or None for a value :meth:`datetime.fromisoformat` refuses.
    """
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _platform_sources(sandbox: Sandbox) -> None:
    """
    Write the wizard's source that was configured for Railway.

    A Node API whose ``railway.toml`` names a health check path and timeout,
    a build and a start command: the inspection proposes them, and the
    review carries the health check into the deploy request.

    Args:
        sandbox: The sandbox.
    """
    root = sandbox.var / "www" / "src" / WIZARD_RAILWAY_SOURCE
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "railway-api",
                "version": "0.9.0",
                "private": True,
                "scripts": {"build": "tsc -p .", "start": "node dist/server.js"},
                "dependencies": {"fastify": "5.2.1"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text(
        '{\n  "name": "railway-api",\n  "lockfileVersion": 3,\n  "requires": true,\n'
        '  "packages": {}\n}\n',
        encoding="utf-8",
    )
    (root / "railway.toml").write_text(
        "[build]\n"
        'builder = "NIXPACKS"\n'
        'buildCommand = "npm run build"\n\n'
        "[deploy]\n"
        'startCommand = "npm start"\n'
        'healthcheckPath = "/healthz"\n'
        "healthcheckTimeout = 60\n"
        'restartPolicyType = "ON_FAILURE"\n',
        encoding="utf-8",
    )
    (root / "server.js").write_text(
        "require('http').createServer((q, s) => s.end('ok')).listen(process.env.PORT);\n",
        encoding="utf-8",
    )


def seed_exportable_app(sandbox: Sandbox) -> None:
    """
    Deploy the application the export and import screens work with.

    A static site from the wizard's ``landing`` source, deployed by the real
    job on releases, then given an alias, a cron job, a secret variable and a
    release retention through the same functions the console's own pages
    call. It is also linked to a database, which an export names and an
    import cannot recreate: the import's "not applied" list has an entry.
    A static site has no health check of its own (the web server answers for
    it), so an import of it applies none.

    Args:
        sandbox: The sandbox.
    """
    from noust.core.store import Database, DomainKind, get_store
    from noust.deployers import domains as domain_changes
    from noust.deployers.helpers.app_env import write_app_env
    from noust.deployers.lifecycle import set_release_retention
    from noust.managers.cron_manager import CronJob, CronManager
    from noust.web.jobs import Job, JobContext, JobType, deploy_app_job

    source = sandbox.var / "www" / "src" / WIZARD_SOURCES[1]
    # Run as the job function it is, outside the job manager: seeding must not
    # leave a job in the history the Activity page and its tests count.
    job = Job(id="seed2300", type=JobType.DEPLOY, name=f"Deploy {EXPORT_APP}", description="")
    deploy_app_job(
        job_context=JobContext(job, lambda _job: None),
        domain=EXPORT_APP,
        source=str(source),
        app_type="static",
        ssl=False,
        layout="releases",
    )
    domain_changes.add_domain(EXPORT_APP, EXPORT_ALIAS, DomainKind.ALIAS.value, issue_cert=False)
    set_release_retention(EXPORT_APP, 3)
    CronManager().create_job(
        CronJob(
            name=EXPORT_CRON,
            command="/usr/bin/curl -fsS http://127.0.0.1/sitemap.xml",
            schedule="*-*-* 04:15:00",
            app_domain=EXPORT_APP,
        )
    )
    store = get_store()
    app = store.get_app(EXPORT_APP)
    if app is not None:
        # Written the way the Environment tab writes it: a static deploy takes
        # no variables (nothing runs to read them), but a build-time .env is
        # an operator's to keep, and an export carries it.
        write_app_env(app, EXPORT_ENV)
        store.set_env_secret_marks(EXPORT_APP, {"NEWSLETTER_API_KEY": True})
        store.create_database(
            Database(
                app_id=app.id,
                name=EXPORT_DATABASE,
                engine="postgresql",
                port=5432,
                username=EXPORT_DATABASE,
            )
        )


def seed_fleet_node_app(
    sandbox: Sandbox,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
    domain: str,
) -> None:
    """
    Seed one running application that exists on this server alone.

    Read from :data:`FLEET_NODE_APP_ENV`, set only by the Playwright fixture that
    starts a fleet's node (panel/e2e/fleet.spec.ts): the central's own seed
    (:func:`seed_machine`) never has this domain, so once the console switches to
    this server, seeing it is proof the page just read this server's API, not a
    cache of the one the operator was on before.

    Args:
        sandbox: The sandbox.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
        domain: The application's domain.
    """
    from noust.core.store import App, get_store
    from noust.core.utils import domain_to_app_name

    root = sandbox.apps_dir / domain_to_app_name(domain)
    root.mkdir(parents=True, exist_ok=True)
    app = App(
        domain=domain,
        app_type="nodejs",
        source=f"https://github.com/example-org/{domain_to_app_name(domain)}.git",
        branch="main",
        port=41990,
        app_path=str(root),
        status="running",
        ssl_enabled=True,
        layout="inplace",
    )
    _tabs_register(sandbox, get_store(), units, ports, domains, app, working_directory=root)


def seed_release_23(sandbox: Sandbox) -> None:
    """
    Seed what the 2.3 screens need beyond the machine seeded before them.

    Recipes deploy without the network (their archives are answered from
    here, PHP-FPM is modelled) and the wizard has a repository configured for
    another platform. The application an export is taken from is deployed
    later, by :func:`seed_exportable_app`, once the runner answers for the
    seeded units (a deploy's pre-flight asks whether nginx runs).

    Args:
        sandbox: The sandbox.
    """
    _recipes_offline()
    _recipes_php_fpm(sandbox)
    _platform_sources(sandbox)


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------


def bind(host: str, port: int) -> socket.socket:
    """
    Bind the listening socket before the server starts.

    Binding here, rather than letting uvicorn do it, is what makes ``--port
    0`` race-free: the port printed is the port held.

    Args:
        host: Loopback address.
        port: Port, or 0 for a free one.

    Returns:
        The listening socket.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    sock.set_inheritable(True)
    return sock


def enable_totp(backup_codes: int | None = None) -> tuple[str, list[str]]:
    """
    Turn on two-factor sign-in with a freshly enrolled secret.

    Args:
        backup_codes: How many backup codes to enrol, when not the usual count.

    Returns:
        The secret, so a test can compute codes, and the backup codes.
    """
    from noust.core import totp
    from noust.web import auth
    from noust.web.server import get_token_manager

    manager = get_token_manager()
    secret = manager.begin_totp_enrollment()
    usual = auth.BACKUP_CODE_COUNT
    # Only this enrolment: a later "new backup codes" gives the usual count, as in production.
    if backup_codes is not None:
        auth.BACKUP_CODE_COUNT = backup_codes
    try:
        codes = manager.confirm_totp_enrollment(totp.totp_now(secret))
    finally:
        auth.BACKUP_CODE_COUNT = usual
    if codes is None:
        raise RuntimeError("two-factor enrolment did not accept its own code")
    return secret, codes


def use_console_build(static_dir: Path) -> None:
    """
    Point the server at a console build other than the committed one.

    Development and E2E only: parallel work on the console builds into private
    directories, because two builds racing into ``noust/web/static`` would serve
    each other half-written chunks. Production always serves the committed build.

    Args:
        static_dir: A directory holding a Vite build (``index.html`` + ``assets/``).

    Raises:
        SystemExit: When the directory holds no build.
    """
    from noust.web import server

    static_dir = static_dir.resolve()
    if not (static_dir / "index.html").is_file():
        raise SystemExit(f"{static_dir} holds no console build (no index.html)")
    server.STATIC_DIR = static_dir
    server.ASSETS_DIR = static_dir / "assets"
    server.INDEX_HTML = static_dir / "index.html"


def use_fixed_hostname(hostname: str) -> None:
    """
    Make the console report a fixed hostname instead of this machine's own.

    Development and screenshots only: a recording or a review screenshot should not carry
    the developer's real machine name. The name is read with a bare ``socket.gethostname()``
    in the modules patched below - the machine snapshot, the session answer and TOTP account
    name, the Overview's header and the fleet's default names - none of which uses ``socket``
    for anything else, so each gets a stand-in bound to its own name, and the real module is untouched for
    everything else in the process.

    Args:
        hostname: The name to report.
    """
    import noust.fleet.models as fleet_models
    import noust.managers.overview as overview_module
    import noust.web.api.auth as auth_api
    import noust.web.machine as machine_module

    fixed = SimpleNamespace(gethostname=lambda: hostname)
    machine_module.socket = fixed  # type: ignore[assignment]
    auth_api.socket = fixed  # type: ignore[assignment]
    # The Overview's header (3.1) and the fleet's default names read it too.
    overview_module.socket = fixed  # type: ignore[assignment]
    fleet_models.socket = fixed  # type: ignore[assignment]


def use_fixed_machine_stats(
    *,
    cpu_percent: float,
    memory_percent: float,
    disk_percent: float,
    uptime_s: float,
    net_rx_bytes_s: float = 4_200.0,
    net_tx_bytes_s: float = 1_800.0,
    load_1m: float = 0.8,
) -> None:
    """
    Make the console report fixed CPU, memory, disk, network, load and uptime readings
    instead of this machine's real ones (``--showcase`` only).

    Three independent readers need this, not one:

    - Every server in the showcase's fleet is really the same sandboxed host running
      several processes side by side, so without this, ``psutil`` answers each node's
      machine snapshot (:func:`noust.web.machine.read_machine`, which the REST endpoint
      and the ``/events`` push both go through) with nearly the same numbers - not a fleet
      of different machines, three readings of the one machine running the demo
      (coordinator review, on the fleet page).
    - :class:`~noust.web.metrics_collector.MetricsCollector` samples the same real
      ``psutil`` independently, every couple of seconds, for the Overview's charts - left
      unpatched, its genuinely noisy (often near-idle) readings make a chart's last few
      points visibly disagree with the top bar's own number (coordinator review, CPU
      dropping to 0% beside a top bar reading in the 20-40s). Patching both to the same
      fixed numbers is what makes a chart's tail, the top bar and every node's Fleet row
      agree, the way one real, particular machine would.
    - Both of those read the load average with a bare ``os.getloadavg()``, never through
      ``psutil``, and this sandbox's real one is this whole *development machine's* -
      running every other showcase process, several browsers and whatever else the
      person capturing screenshots is doing at the time - which is how a "Load" reading
      as high as 8 got into a screenshot next to others reading 0.7 (coordinator review).
      ``os.getloadavg`` is patched globally (harmless: nothing else in this sandboxed
      process needs the real figure) rather than on just these two modules' own
      ``import os``, which is the whole standard library module either way.

    ``psutil.net_io_counters()`` is a running total, not a live reading, so the fake counts
    up from zero at whatever rate is given rather than returning a fixed pair - the
    collector's own rate is a delta between two calls, and two identical totals would read
    as no traffic at all.

    Args:
        cpu_percent: CPU utilisation to report, 0-100.
        memory_percent: Memory used, 0-100; used/total are synthesised to match.
        disk_percent: Disk used, 0-100; used/total are synthesised to match.
        uptime_s: Seconds since boot to report.
        net_rx_bytes_s: Inbound network rate to hold steady.
        net_tx_bytes_s: Outbound network rate to hold steady.
        load_1m: The 1-minute load average to hold steady, comfortably under 1 on a
            machine this size; the 5- and 15-minute figures are held a little lower still,
            the shape a load that has been steady for a while actually has.
    """
    import os as os_module

    import noust.monitor.sampler as sampler_module

    os_module.getloadavg = lambda: (load_1m, load_1m * 0.9, load_1m * 0.8)

    mem_total = 16 * 1024**3
    disk_total = 480 * 1024**3
    boot_time = time.time() - uptime_s
    counters_from = time.time()

    def net_io_counters() -> Any:
        elapsed = max(0.0, time.time() - counters_from)
        return SimpleNamespace(
            bytes_recv=int(net_rx_bytes_s * elapsed), bytes_sent=int(net_tx_bytes_s * elapsed)
        )

    fixed = SimpleNamespace(
        cpu_percent=lambda interval=None: cpu_percent,
        virtual_memory=lambda: SimpleNamespace(
            used=int(mem_total * memory_percent / 100), total=mem_total, percent=memory_percent
        ),
        swap_memory=lambda: SimpleNamespace(used=int(mem_total * 0.015)),
        disk_usage=lambda path=None: SimpleNamespace(
            used=int(disk_total * disk_percent / 100), total=disk_total, percent=disk_percent
        ),
        boot_time=lambda: boot_time,
        net_io_counters=net_io_counters,
    )
    # The one place the machine is read: the header strip, the chart collector
    # and the monitor's resource scan all go through the sampler.
    sampler_module.psutil = fixed


# ---------------------------------------------------------------------------
# The fleet: a central and a node as two real processes, without ssh
# ---------------------------------------------------------------------------
#
# Playwright's fleet suite runs two of these servers - a "central" and a "node" -
# and needs the central to reach the node exactly as it would in production: a real
# HTTP request, with the real fleet token, answered by the node's real API. The one
# thing missing on purpose is ssh: the sandbox has no sshd, no `authorized_keys` and
# no root shell to authorize a key against, and none of that is worth modelling to
# prove the fleet works. What follows replaces exactly the two things ssh would
# otherwise be needed for - `noust fleet authorize` reading this machine's own SSH
# host key and installing a key in `authorized_keys`, and the central's tunnel
# turning a node's name into a local port - and nothing else: the token is minted by
# the real TokenManager.create_fleet_token, the join code is the real JoinCode, and
# registration is the real NodeManager.add, which is what a locked central's
# _require_unlocked still refuses, and what a stopped node still fails against with a
# genuine connection error. None of this is reachable from `noust.web.api`; it is
# mounted only here, from this script, and ships in no wheel, deb, rpm or container.

#: Where a node's test-only route mints a join code (see mount_fleet_test_seam).
#: Never a real Noust path: '__test__' names it unmistakably, for anyone reading a
#: request log wondering why a "node" answers something noust.web.api does not have.
FLEET_TEST_MINT_PATH = "/__test__/fleet/mint-join-code"


def mount_fleet_test_seam(app: Any, *, console_port: int) -> None:
    """
    Add the one route that stands in for ``noust fleet authorize`` on a node.

    Real ``noust fleet authorize`` reads this machine's own SSH host key from
    ``/etc/ssh`` and installs a restricted line in the SSH user's
    ``authorized_keys``; a sandboxed, unprivileged test process may do neither. Both
    are about ssh, which this fleet E2E setup does not use at all (see the module
    comment above), so this route does only what is left: mint a fleet token, on this
    node's own store, with the very method production issues one with
    (:meth:`noust.web.auth.TokenManager.create_fleet_token`), and encode it in a real
    :class:`noust.fleet.joincode.JoinCode`, exactly as ``authorize()`` would return one.

    Mounted unconditionally - it is inert until a central's ``--fleet-node`` calls
    it - so a plain ``console_server.py`` run is unaffected.

    Args:
        app: The FastAPI app this node serves.
        console_port: This node's own bound port, embedded in the join code
            exactly as ``noust fleet authorize`` fills in the console's real port.
    """
    from fastapi import Body

    from noust import __version__
    from noust.fleet.authorize import next_token_name
    from noust.fleet.joincode import JoinCode
    from noust.fleet.models import parse_public_key
    from noust.web.server import get_token_manager
    from tests.fleet_support import ed25519_line

    # Not called inline in the signature below (ruff B008): a route's parameter defaults
    # are evaluated once, at import time, same as this is, so there is no difference but
    # the lint rule, which exists for defaults that are NOT meant to run once.
    body_default = Body(...)

    @app.post(FLEET_TEST_MINT_PATH, include_in_schema=False)
    def mint_join_code(body: dict[str, Any] = body_default) -> dict[str, str]:
        """Mint a fleet token and a join code for the central named in the body."""
        central = str(body["central_name"])
        node_name = str(body["node_name"])
        central_key = parse_public_key(str(body["central_key"]), what="central key")
        tokens = get_token_manager()
        issued = tokens.create_fleet_token(next_token_name(tokens, central))
        # A structurally valid host key nothing ever checks: the loopback seam
        # (set_loopback_for_testing) never opens ssh, so nothing ever verifies it
        # against a real one.
        host_key = ed25519_line(hash(node_name) & 0xFFFF, "")
        code = JoinCode(
            ssh_host_key=host_key,
            ssh_user="root",
            ssh_port=22,
            console_port=console_port,
            token=str(issued["token"]),
            noust_version=__version__,
            central_key_fp=central_key.fingerprint,
            node_name=node_name,
            token_name=str(issued["name"]),
            central=central,
        )
        return {"join_code": code.encode()}


def pretend_older_node(app: Any, version: str) -> Any:
    """
    Make this node answer as an older Noust would: its version, and 404 for what it lacks.

    Test-only (the fleet's E2E suite, through :data:`OLDER_NODE_ENV`): a central's fleet
    views must show such a node as "unsupported", with what it does not offer, rather than
    fail. Only the answers change; the rest of the node is the real one.

    Args:
        app: The node's ASGI application, with every route already mounted.
        version: The version it reports.

    Returns:
        The application to serve.
    """
    import noust.web.api.system as system_api

    system_api.__version__ = version
    body = json.dumps({"detail": "Not Found"}).encode()

    async def older(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") in OLDER_NODE_MISSING:
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await app(scope, receive, send)

    return older


def join_fleet_node(entries: list[str], *, display_hosts: dict[str, str] | None = None) -> None:
    """
    Register, on this central, every node named by ``--fleet-node NAME=HOST:PORT``.

    Ties together three pieces of real product code across two real processes, the
    only fake being the one line of ``noust.fleet.tunnels.set_loopback_for_testing``
    that stands in for ssh (see that function):

    1. this central generates its own key pair for the node
       (:meth:`noust.fleet.nodes.NodeManager.central_public_key`; real
       ``ssh-keygen``, faked by :class:`ConsoleRunner` above only because this
       sandbox has no real one to run and never opens the connection it is for);
    2. it asks the node's test seam to mint a join code with that key
       (:data:`FLEET_TEST_MINT_PATH`), over a genuine HTTP request to the node's
       own, already-running API;
    3. it registers the node with :meth:`noust.fleet.nodes.NodeManager.add`,
       exactly as ``noust node add`` or ``POST /api/nodes`` would: it pins the
       (fake) host key, stores the (real) token, saves the record, and then asks
       the node's real API for its version, through the tunnel manager - which,
       with the loopback seam set first, answers with the node's real address
       instead of dialling ssh, so what follows is a genuine HTTP call, with the
       genuine fleet token, straight to that node's own FastAPI process. A locked
       central's ``_require_unlocked`` still refuses every node (checked before
       the seam), and a stopped node still fails this with a real connection error.

    Must run after ``set_runner`` installs the fake ``ssh-keygen`` and before the
    server starts accepting connections that might ask for this node's tunnel.

    Args:
        entries: ``["web-2=127.0.0.1:41234", ...]``, as ``--fleet-node`` collected them.
        display_hosts: Node name to the host to *store and show* as its SSH address
            (``--showcase`` only), in place of the loopback address the tunnel actually
            dials: Settings > Servers otherwise shows ``root@127.0.0.1`` for every node,
            which is this test seam showing through, not something a documentation
            screenshot should carry (coordinator review). The tunnel keeps routing by node
            name (:func:`~noust.fleet.tunnels.set_loopback_for_testing`, below, unaffected
            by this), so the address stored is display-only either way.

    Raises:
        SystemExit: When an entry is not ``NAME=HOST:PORT``, so a typo in a test
            fails at start-up rather than in a request three tests later.
    """
    import httpx

    from noust.fleet.models import central_name
    from noust.fleet.nodes import NodeManager
    from noust.fleet.tunnels import set_loopback_for_testing

    manager = NodeManager()
    this_central = central_name()
    display_hosts = display_hosts or {}

    for entry in entries:
        name, sep, address = entry.partition("=")
        host, hsep, port_text = address.partition(":")
        if not sep or not hsep or not name or not port_text.isdigit():
            raise SystemExit(f"console_server: --fleet-node wants NAME=HOST:PORT, not {entry!r}")
        port = int(port_text)
        set_loopback_for_testing(name, port)
        public_key = manager.central_public_key(name)
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            response = client.post(
                f"http://{host}:{port}{FLEET_TEST_MINT_PATH}",
                json={"central_name": this_central, "central_key": public_key, "node_name": name},
            )
            response.raise_for_status()
            join_code = response.json()["join_code"]
        shown_host = display_hosts.get(name, host)
        manager.add(name, ssh_target=f"root@{shown_host}", join_code=join_code)


def seal_sandbox_before_serving(sandbox: Sandbox) -> None:
    """
    Seal this sandbox's secrets in a throwaway subprocess, before this one serves.

    :func:`noust.core.sealing.unlock` (which :func:`noust.core.sealing.seal_store`
    also calls, on success) remembers the derived keys in the *calling process*, so
    the process that goes on to serve requests has to be a different one for it to
    start genuinely locked - exactly like a central started after ``noust central
    seal`` on a previous run, never given the passphrase this run. The passphrase is
    read from standard input here, and passed to the child the same way, never in
    argv (see ``noust.cli.commands.central._read_passphrase``, which this mirrors).

    Args:
        sandbox: The sandbox, already prepared and with its config written.

    Raises:
        SystemExit: When standard input holds no usable passphrase, or the
            bootstrap subprocess fails (its stderr is included).
    """
    from noust.core.sealing import MIN_PASSPHRASE_LENGTH

    passphrase = sys.stdin.readline().rstrip("\r\n")
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise SystemExit(
            f"console_server: --seal needs a passphrase of {MIN_PASSPHRASE_LENGTH}+ "
            "characters on standard input"
        )
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--internal-seal", str(sandbox.root)],
        input=passphrase,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise SystemExit(f"console_server: sealing the sandbox failed:\n{result.stderr}")


def run_internal_seal(sandbox_root: Path) -> int:
    """
    Seal an already-prepared sandbox's secrets: the ``--internal-seal`` entry point.

    Never invoked directly; :func:`seal_sandbox_before_serving` runs this same
    script with this flag as a short-lived child, precisely so the sealing happens
    outside the process that goes on to serve. See that function's docstring.

    Args:
        sandbox_root: The sandbox the parent process already created and prepared.

    Returns:
        0.
    """
    sandbox = Sandbox(sandbox_root)
    prepare_environment(sandbox)
    redirect_system_paths(sandbox)
    from noust.core import sealing
    from noust.core.secrets import secrets_dir

    passphrase = sys.stdin.readline().rstrip("\r\n")
    sealing.seal_store(secrets_dir(), passphrase)
    return 0


# ---------------------------------------------------------------------------------------
# The machine behind the Server area (/api/server): an Ubuntu 24.04 VPS with work to do.
#
# The server managers read files below a root (HostPaths) and ask the machine through the
# runner; both are answered here, from a small host tree in the sandbox and canned output
# in each tool's own format (apt-get -s, sshd -T, ss, ufw, fail2ban-client, timedatectl,
# hostnamectl, journalctl --output=json...), so every tab renders the way it does on a real
# server: security updates pending, a reboot due, passwords on in SSH, PostgreSQL answering
# the internet through a forgotten rule, two addresses banned. Nothing is executed.

#: Programs the modelled server has for the Server area, beside INSTALLED_PROGRAMS.
SERVER_PROGRAMS = (
    "apt-get",
    "apt-mark",
    "apt-config",
    "dpkg",
    "dpkg-query",
    "needrestart",
    "sshd",
    "ss",
    "ufw",
    "fail2ban-client",
    "hostnamectl",
    "timedatectl",
    "swapon",
    "findmnt",
    "du",
    "ionice",
    "nice",
    "shutdown",
    "systemd-detect-virt",
)

#: What Debian's postgresql-common writes for a cluster: the settings Noust edits that it
#: sets itself, and the include_dir that makes Noust's own conf.d file count.
_SERVER_POSTGRESQL_CONF = """\
# -----------------------------
# PostgreSQL configuration file
# -----------------------------
data_directory = '/var/lib/postgresql/16/main'
hba_file = '/etc/postgresql/16/main/pg_hba.conf'
ident_file = '/etc/postgresql/16/main/pg_ident.conf'
external_pid_file = '/var/run/postgresql/16-main.pid'
port = 5432
max_connections = 100
unix_socket_directories = '/var/run/postgresql'
ssl = on
shared_buffers = 128MB
dynamic_shared_memory_type = posix
max_wal_size = 1GB
min_wal_size = 80MB
log_line_prefix = '%m [%p] %q%u@%d '
cluster_name = '16/main'
datestyle = 'iso, dmy'
timezone = 'Europe/Madrid'
lc_messages = 'en_US.UTF-8'
default_text_search_config = 'pg_catalog.spanish'
include_dir = 'conf.d'
"""

#: Ubuntu's mysqld.cnf: the include directory Noust writes 99-noust.cnf into is the one
#: /etc/mysql/my.cnf already reads.
_SERVER_MYSQLD_CNF = """\
[mysqld]
user            = mysql
# bind-address  = 127.0.0.1
mysqlx-bind-address = 127.0.0.1
key_buffer_size         = 16M
myisam-recover-options  = BACKUP
log_error = /var/log/mysql/error.log
max_binlog_size   = 100M
"""

_SERVER_OS_RELEASE = """PRETTY_NAME="Ubuntu 24.04.1 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.1 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
UBUNTU_CODENAME=noble
"""

#: ``apt-get -s upgrade``: two security updates (one a kernel), five others, one kept back.
_SERVER_APT_UPGRADE = """NOTE: This is only a simulation!
      apt-get needs root privileges for real execution.
Reading package lists...
Building dependency tree...
Reading state information...
Calculating upgrade...
The following packages have been kept back:
  linux-generic
The following packages will be upgraded:
  curl libcurl4t64 libssl3t64 linux-image-6.8.0-47-generic nginx nginx-common openssl python3-urllib3
8 upgraded, 1 newly installed, 0 to remove and 1 not upgraded.
Inst libssl3t64 [3.0.13-0ubuntu3.3] (3.0.13-0ubuntu3.4 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst openssl [3.0.13-0ubuntu3.3] (3.0.13-0ubuntu3.4 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst linux-image-6.8.0-47-generic (6.8.0-47.47 Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])
Inst curl [8.5.0-2ubuntu10.3] (8.5.0-2ubuntu10.4 Ubuntu:24.04/noble-updates [amd64])
Inst libcurl4t64 [8.5.0-2ubuntu10.3] (8.5.0-2ubuntu10.4 Ubuntu:24.04/noble-updates [amd64])
Inst nginx-common [1.24.0-2ubuntu7] (1.24.0-2ubuntu7.1 Ubuntu:24.04/noble-updates [all])
Inst nginx [1.24.0-2ubuntu7] (1.24.0-2ubuntu7.1 Ubuntu:24.04/noble-updates [amd64])
Inst python3-urllib3 [2.0.7-1] (2.0.7-1ubuntu0.1 Ubuntu:24.04/noble-updates [all])
"""

#: ``apt-get -s full-upgrade``: the same, plus what was kept back and the kernel it retires.
_SERVER_APT_FULL = (
    _SERVER_APT_UPGRADE.replace(
        "The following packages have been kept back:\n  linux-generic\n", ""
    )
    + "Remv linux-image-6.8.0-31-generic [6.8.0-31.31]\n"
    + "Inst linux-generic [6.8.0-45.45] (6.8.0-47.47 Ubuntu:24.04/noble-updates [amd64])\n"
)

_SERVER_SSHD_T = """port 22
addressfamily any
listenaddress [::]:22
listenaddress 0.0.0.0:22
permitrootlogin prohibit-password
pubkeyauthentication yes
passwordauthentication yes
kbdinteractiveauthentication no
permitemptypasswords no
maxauthtries 6
maxsessions 10
logingracetime 120
clientaliveinterval 0
x11forwarding yes
allowtcpforwarding yes
allowagentforwarding yes
loglevel INFO
usepam yes
authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2
"""

_SERVER_UFW_STATUS = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
80/tcp                     ALLOW IN    Anywhere
443/tcp                    ALLOW IN    Anywhere
5432/tcp                   ALLOW IN    Anywhere                   # temporary: migration from the old host
22/tcp (v6)                ALLOW IN    Anywhere (v6)
80/tcp (v6)                ALLOW IN    Anywhere (v6)
443/tcp (v6)               ALLOW IN    Anywhere (v6)
"""

_SERVER_UFW_ADDED = """Added user rules (see 'ufw status' for running firewall):
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 5432/tcp comment 'temporary: migration from the old host'
"""

#: What the modelled machine spends its disk on, for ``du -sx -B1``, by the end of the path
#: (the sandbox moves Noust's own directories, not their names); the first match wins.
_SERVER_DU = (
    ("/var/cache/apt/archives", 642 * 1024**2),
    ("/var/lib/postgresql", 2_400 * 1024**2),
    ("/var/lib/mysql", 1_100 * 1024**2),
    ("/var/tmp", 12 * 1024**2),  # noqa: S108 - a path of the modelled host, matched, never used
    ("/tmp", 96 * 1024**2),  # noqa: S108 - a path of the modelled host, matched, never used
    ("/log/noust", 180 * 1024**2),
)


def _server_key(comment: str, seed: int) -> tuple[str, str]:
    """
    An ed25519 public key line, in the SSH wire format, and its SHA256 fingerprint.

    Args:
        comment: The key's comment.
        seed: Makes each key distinct.

    Returns:
        The ``authorized_keys`` line and ``SHA256:...``, as ``ssh-keygen -l`` prints it.
    """
    import base64
    import hashlib

    def field(data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + data

    blob = field(b"ssh-ed25519") + field(hashlib.sha256(f"noust-console-{seed}".encode()).digest())
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}", fingerprint


class ServerHost:
    """The modelled VPS: a host tree below ``root`` and the tools' answers."""

    def __init__(self, root: Path, hostname: str, units: dict[str, Unit]) -> None:
        """
        Args:
            root: Where the host tree is written (HostPaths' root).
            hostname: The name the rest of the console reports.
            units: The runner's unit model, for ``systemctl --failed``.
        """
        self.root = root
        self.hostname = hostname
        self.units = units
        self.boot_id = "3f1c2a4b5d6e7f8091a2b3c4d5e6f708"
        self.operator_key, self.operator_fingerprint = _server_key("yago@laptop", 1)
        self.deploy_key, self.deploy_fingerprint = _server_key("github-actions@deploy", 2)

    # -- the host tree ----------------------------------------------------------------

    def write(self, relative: str, content: str = "", *, age: timedelta | None = None) -> Path:
        """Write a file of the host tree, dated ``age`` ago when given."""
        path = self.root / relative.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if age is not None:
            moment = (datetime.now() - age).timestamp()
            os.utime(path, (moment, moment))
        return path

    def build(self) -> None:
        """Write the files the server managers read."""
        self.write("/etc/os-release", _SERVER_OS_RELEASE)
        self.write("/etc/hostname", f"{self.hostname}\n")
        self.write("/etc/fstab", "LABEL=cloudimg-rootfs / ext4 discard,errors=remount-ro 0 1\n")
        self.write("/proc/uptime", "1051234.56 2020000.12\n")
        self.write("/proc/sys/kernel/random/boot_id", f"{self.boot_id}\n")
        self.write("/proc/sys/vm/swappiness", "60\n")
        self.write(
            "/proc/meminfo",
            "MemTotal:        2030612 kB\nMemFree:          210044 kB\nMemAvailable:     918332 kB\n",
        )
        (self.root / "run/systemd/system").mkdir(parents=True, exist_ok=True)
        (self.root / "var/log/journal").mkdir(parents=True, exist_ok=True)
        self.write(
            "/var/run/reboot-required", "*** System restart required ***\n", age=timedelta(days=9)
        )
        self.write(
            "/var/run/reboot-required.pkgs",
            "linux-image-6.8.0-45-generic\nlibc6\n",
            age=timedelta(days=9),
        )
        self.write("/var/lib/apt/periodic/update-success-stamp", age=timedelta(hours=3))
        for directory in (
            "var/lib/apt/lists",
            "var/cache/apt/archives",
            "var/lib/postgresql",
            "var/lib/mysql",
            "tmp",
            "var/tmp",
        ):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.write(
            "/var/log/unattended-upgrades/unattended-upgrades.log", "", age=timedelta(hours=20)
        )
        self.write(
            "/etc/apt/apt.conf.d/20auto-upgrades",
            'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n',
        )
        for name in (
            "vmlinuz-6.8.0-45-generic",
            "initrd.img-6.8.0-45-generic",
            "vmlinuz-6.8.0-47-generic",
            "initrd.img-6.8.0-47-generic",
        ):
            self.write(f"/boot/{name}")
        self.write(
            "/etc/ssh/sshd_config",
            "Include /etc/ssh/sshd_config.d/*.conf\nKbdInteractiveAuthentication no\nUsePAM yes\nX11Forwarding yes\n",
        )
        (self.root / "etc/ssh/sshd_config.d").mkdir(parents=True, exist_ok=True)
        self.write(
            "/etc/passwd",
            "root:x:0:0:root:/root:/bin/bash\n"
            "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\n"
            "deploy:x:1000:1000:Deploy,,,:/home/deploy:/bin/bash\n"
            "postgres:x:113:120:PostgreSQL administrator,,,:/var/lib/postgresql:/bin/bash\n",
        )
        self.write("/etc/group", "root:x:0:\nsudo:x:27:deploy\nwww-data:x:33:\ndeploy:x:1000:\n")
        self.write(
            "/etc/shadow",
            "root:!:19990:0:99999:7:::\n"
            "deploy:$y$j9T$Zm9vYmFyYmF6$6Y1o0t9mW2m6c6c0nY2i1m3s8m5o2Q5c4J8m1v0n2s1:19990:0:99999:7:::\n"
            "www-data:*:19990:0:99999:7:::\npostgres:*:19990:0:99999:7:::\n",
        )
        self.write("/etc/shells", "/bin/sh\n/bin/bash\n/usr/bin/bash\n")
        self.write(
            "/etc/sudoers", "Defaults env_reset\nroot ALL=(ALL:ALL) ALL\n%sudo ALL=(ALL:ALL) ALL\n"
        )
        self.write(f"{_PG_CONFIG_DIR}/postgresql.conf", _SERVER_POSTGRESQL_CONF)
        (self.root / _PG_CONFIG_DIR.lstrip("/") / "conf.d").mkdir(parents=True, exist_ok=True)
        self.write("/etc/mysql/mysql.conf.d/mysqld.cnf", _SERVER_MYSQLD_CNF)
        self.write("/root/.ssh/authorized_keys", f"{self.operator_key}\n")
        self.write("/home/deploy/.ssh/authorized_keys", f"{self.deploy_key}\n")
        for path in ("root/.ssh", "home/deploy/.ssh"):
            (self.root / path).chmod(0o700)
        for path in ("root/.ssh/authorized_keys", "home/deploy/.ssh/authorized_keys"):
            (self.root / path).chmod(0o600)

    # -- the tools' answers ---------------------------------------------------------------

    def answer(self, args: tuple[str, ...]) -> Any:
        """
        Answer a command of the Server area, or None to leave it to the rest of the runner.

        Args:
            args: The argv.

        Returns:
            A CommandResult, or None.
        """
        from noust.core.runner import CommandResult

        def ok(stdout: str = "", code: int = 0) -> CommandResult:
            return CommandResult(argv=args, exit_code=code, stdout=stdout, stderr="")

        program, rest = (args[0], args[1:]) if args else ("", ())
        if program == "systemd-detect-virt":
            return ok("none\n", 1)
        if program == "hostnamectl" and rest[:1] == ("--json=short",):
            return ok(
                json.dumps(
                    {
                        "Hostname": self.hostname,
                        "StaticHostname": self.hostname,
                        "PrettyHostname": None,
                        "Chassis": "vm",
                        "MachineID": "5d3e2f1a9b8c7d6e5f4a3b2c1d0e9f8a",
                        "BootID": self.boot_id,
                        "OperatingSystemPrettyName": "Ubuntu 24.04.1 LTS",
                    }
                )
                + "\n"
            )
        if program == "timedatectl" and rest[:1] == ("show",):
            now = datetime.now().astimezone()
            return ok(
                "Timezone=Europe/Madrid\nLocalRTC=no\nCanNTP=yes\nNTP=yes\nNTPSynchronized=yes\n"
                f"TimeUSec={now.strftime('%a %Y-%m-%d %H:%M:%S %Z')}\n"
            )
        if program == "swapon":
            return ok("")
        if program == "systemctl" and rest[:1] == ("is-system-running",):
            failed = any(unit.active == "failed" for unit in self.units.values())
            return ok("degraded\n" if failed else "running\n", 1 if failed else 0)
        if program == "systemctl" and "--failed" in rest:
            return ok(
                "".join(
                    f"{name}.service loaded failed failed {name}\n"
                    for name, unit in sorted(self.units.items())
                    if unit.active == "failed"
                )
            )
        if program == "systemctl" and rest[:1] == ("is-active",) and "fail2ban.service" in rest:
            return ok("active\n")
        if program == "apt-get" and "-s" in rest:
            return ok(
                _SERVER_APT_FULL
                if "full-upgrade" in rest or "dist-upgrade" in rest
                else _SERVER_APT_UPGRADE
            )
        if program == "apt-mark" or (program == "dpkg" and rest[:1] == ("--audit",)):
            return ok("")
        if program == "dpkg-query" and "unattended-upgrades" in rest:
            return ok("ii ")
        if program == "apt-config" and rest[:1] == ("dump",):
            return ok(
                'APT::Periodic::Update-Package-Lists "1";\n'
                'APT::Periodic::Unattended-Upgrade "1";\n'
                'Unattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}";\n'
                'Unattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}-security";\n'
                'Unattended-Upgrade::Automatic-Reboot "false";\n'
            )
        if program == "needrestart":
            return ok(
                "NEEDRESTART-VER: 3.6\nNEEDRESTART-KCUR: 6.8.0-45-generic\nNEEDRESTART-KEXP: 6.8.0-47-generic\n"
                "NEEDRESTART-KSTA: 3\nNEEDRESTART-SVC: nginx.service\nNEEDRESTART-SVC: cron.service\n"
                "NEEDRESTART-SVC: ssh.service\n"
            )
        if program == "findmnt" and "--verify" in rest:
            return ok("Success, no errors or warnings detected\n")
        if program == "journalctl" and "--disk-usage" in rest:
            return ok("Archived and active journal files take up 1.2G in the file system.\n")
        if program == "journalctl" and "--list-boots" in rest:
            earlier = (datetime.now() - timedelta(days=12, hours=4)).strftime(
                "%a %Y-%m-%d %H:%M:%S UTC"
            )
            before = (datetime.now() - timedelta(days=40)).strftime("%a %Y-%m-%d %H:%M:%S UTC")
            latest = datetime.now().strftime("%a %Y-%m-%d %H:%M:%S UTC")
            return ok(
                f" -1 9a8b7c6d5e4f30211a2b3c4d5e6f7081 {before} {earlier}\n"
                f"  0 {self.boot_id} {earlier} {latest}\n"
            )
        if program == "journalctl" and "--output=json" in rest:
            return ok(self._journal_json(rest))
        if program == "journalctl" and any(arg.startswith("_COMM=sshd") for arg in rest):
            return ok(self._ssh_logins())
        if program == "ionice" and "du" in args:
            path = args[-1]
            size = next(
                (size for suffix, size in _SERVER_DU if path.endswith(suffix)), 48 * 1024**2
            )
            return ok(f"{size}\t{path}\n")
        if program == "sshd" and rest[:1] == ("-T",):
            return ok(_SERVER_SSHD_T)
        if program == "sshd" and rest[:1] == ("-t",):
            return ok("")
        if program == "ss" and rest[:1] == ("-Hltnup",):
            return ok(
                'tcp LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=812,fd=3))\n'
                'tcp LISTEN 0 4096 [::]:22 [::]:* users:(("sshd",pid=812,fd=4))\n'
                'tcp LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=1021,fd=6))\n'
                'tcp LISTEN 0 511 0.0.0.0:443 0.0.0.0:* users:(("nginx",pid=1021,fd=7))\n'
                'tcp LISTEN 0 244 0.0.0.0:5432 0.0.0.0:* users:(("postgres",pid=990,fd=5))\n'
                'tcp LISTEN 0 151 127.0.0.1:3306 0.0.0.0:* users:(("mysqld",pid=1003,fd=21))\n'
                'tcp LISTEN 0 511 127.0.0.1:6379 0.0.0.0:* users:(("redis-server",pid=977,fd=6))\n'
                'tcp LISTEN 0 2048 127.0.0.1:8080 0.0.0.0:* users:(("python3",pid=1300,fd=9))\n'
                'tcp LISTEN 0 511 127.0.0.1:3004 0.0.0.0:* users:(("node",pid=2204,fd=19))\n'
                'udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=610,fd=13))\n'
            )
        if program == "ss" and "established" in rest:
            return ok('0 0 10.0.0.5:22 203.0.113.7:51234 users:(("sshd",pid=41022,fd=4))\n')
        if program == "ufw" and rest == ("status", "verbose"):
            return ok(_SERVER_UFW_STATUS)
        if program == "ufw" and rest == ("show", "added"):
            return ok(_SERVER_UFW_ADDED)
        if program == "fail2ban-client":
            return ok(self._fail2ban(rest))
        if program == "shutdown":
            return self._shutdown(rest, ok)
        return None

    def _journal_json(self, rest: tuple[str, ...]) -> str:
        """``journalctl --output=json``: a morning on the server, filtered as asked."""
        unit = next((arg.split("=", 1)[1] for arg in rest if arg.startswith("--unit=")), None)
        priority = next(
            (int(arg.split("=", 1)[1]) for arg in rest if arg.startswith("--priority=")), 7
        )
        kernel = "--dmesg" in rest
        entries = [
            (
                "kernel",
                6,
                None,
                "Linux version 6.8.0-45-generic (buildd@lcy02-amd64-075) #45-Ubuntu SMP PREEMPT_DYNAMIC",
            ),
            ("systemd-journald.service", 6, 301, "Journal started"),
            (
                "ssh.service",
                6,
                41022,
                "Accepted publickey for root from 203.0.113.7 port 51234 ssh2: ED25519 "
                + self.operator_fingerprint,
            ),
            ("ssh.service", 6, 41110, "Invalid user admin from 185.220.101.4 port 40022"),
            (
                "ssh.service",
                6,
                41111,
                "Failed password for invalid user admin from 185.220.101.4 port 40022 ssh2",
            ),
            ("fail2ban.service", 5, 700, "NOTICE  [sshd] Ban 185.220.101.4"),
            (
                "nginx.service",
                4,
                1021,
                "2026/09/29 09:12:44 [warn] 1021#1021: *88 an upstream response is buffered to a temporary file",
            ),
            (
                "postgresql@16-main.service",
                6,
                990,
                "LOG:  checkpoint complete: wrote 412 buffers (2.5%)",
            ),
            (
                "cron.service",
                6,
                880,
                "(root) CMD (test -x /usr/sbin/anacron || { cd / && run-parts --report /etc/cron.daily; })",
            ),
            (
                "unattended-upgrades.service",
                6,
                1502,
                "Packages that will be upgraded: libssl3t64 openssl",
            ),
            ("noust-web.service", 6, 1300, "Console listening on 127.0.0.1:8080"),
            (
                "mysql.service",
                3,
                1003,
                "[ERROR] [MY-012574] [InnoDB] Unable to lock ./ibdata1 error: 11",
            ),
            (
                "nginx.service",
                3,
                1021,
                '2026/09/29 09:40:02 [error] 1021#1021: *131 connect() failed (111: Connection refused) while connecting to upstream, upstream: "http://127.0.0.1:3007/"',
            ),
            (
                "ssh.service",
                6,
                41200,
                "Received disconnect from 203.0.113.7 port 51234:11: disconnected by user",
            ),
        ]
        start = datetime.now(timezone.utc) - timedelta(minutes=len(entries) * 3)
        lines = []
        for index, (name, level, pid, message) in enumerate(entries):
            if (
                level > priority
                or (kernel and name != "kernel")
                or (
                    unit is not None
                    and name.removesuffix(".service") != unit.removesuffix(".service")
                )
            ):
                continue
            micros = int((start + timedelta(minutes=index * 3)).timestamp() * 1_000_000)
            record = {
                "__CURSOR": f"s=4b1c;i={index + 1:x};b={self.boot_id};m={micros:x}",
                "__REALTIME_TIMESTAMP": str(micros),
                "PRIORITY": str(level),
                "MESSAGE": message,
                **(
                    {"_SYSTEMD_UNIT": name} if name != "kernel" else {"SYSLOG_IDENTIFIER": "kernel"}
                ),
                **({"_PID": str(pid)} if pid is not None else {}),
            }
            lines.append(json.dumps(record))
        return "\n".join(lines) + ("\n" if lines else "")

    def _ssh_logins(self) -> str:
        """``journalctl _COMM=sshd -o short-unix``: the logins sshd recorded."""
        now = time.time()
        return (
            f"{now - 3 * 86400:.6f} {self.hostname} sshd[39001]: Accepted publickey for deploy from 198.51.100.20 port 40210 ssh2: ED25519 {self.deploy_fingerprint}\n"
            f"{now - 2 * 86400:.6f} {self.hostname} sshd[40012]: Accepted publickey for root from 203.0.113.7 port 50122 ssh2: ED25519 {self.operator_fingerprint}\n"
            f"{now - 1800:.6f} {self.hostname} sshd[41022]: Accepted publickey for root from 203.0.113.7 port 51234 ssh2: ED25519 {self.operator_fingerprint}\n"
        )

    @staticmethod
    def _fail2ban(rest: tuple[str, ...]) -> str:
        """``fail2ban-client``: one jail, for sshd, with two addresses banned now."""
        if rest[:1] == ("ping",):
            return "Server replied: pong\n"
        if rest == ("status",):
            return "Status\n|- Number of jail:\t1\n`- Jail list:\tsshd\n"
        if rest == ("status", "sshd"):
            return (
                "Status for the jail: sshd\n"
                "|- Filter\n"
                "|  |- Currently failed:\t3\n"
                "|  |- Total failed:\t1284\n"
                "|  `- Journal matches:\t_SYSTEMD_UNIT=sshd.service + _COMM=sshd\n"
                "`- Actions\n"
                "   |- Currently banned:\t2\n"
                "   |- Total banned:\t97\n"
                "   `- Banned IP list:\t185.220.101.4 45.148.10.182\n"
            )
        return ""

    def _shutdown(self, rest: tuple[str, ...], ok: Any) -> Any:
        """``shutdown -r +N`` leaves the schedule for logind; ``shutdown -c`` removes it."""
        scheduled = self.root / "run/systemd/shutdown/scheduled"
        if rest[:1] == ("-c",):
            scheduled.unlink(missing_ok=True)
            return ok("")
        minutes = next(
            (int(arg[1:]) for arg in rest if arg.startswith("+") and arg[1:].isdigit()), 1
        )
        mode = "reboot" if "-r" in rest else "poweroff"
        due = int((time.time() + minutes * 60) * 1_000_000)
        scheduled.parent.mkdir(parents=True, exist_ok=True)
        scheduled.write_text(f"USEC={due}\nWARN_WALL=1\nMODE={mode}\n", encoding="utf-8")
        return ok("")


#: The modelled server, once serve() has built it; the runner asks it first.
_SERVER_HOST: ServerHost | None = None


def model_server_host(sandbox: Sandbox, hostname: str, units: dict[str, Unit]) -> ServerHost:
    """
    Build the VPS the Server area manages, and point its managers at it.

    Every ``HostPaths()`` in the process resolves below the sandbox's host tree from here on,
    the disks are the modelled VPS's rather than this machine's, and the last scan of what
    takes space is already there, as it is on a server someone measured yesterday.

    Args:
        sandbox: The sandbox.
        hostname: The name the rest of the console reports.
        units: The runner's unit model.

    Returns:
        The model, which the runner consults for every command.
    """
    global _SERVER_HOST
    from noust.managers.server.host import HostPaths
    from noust.managers.server.storage import Mount, StorageManager

    host = ServerHost(sandbox.root / "host", hostname, units)
    host.build()
    HostPaths.__init__.__defaults__ = (host.root,)

    # The database area keeps its own HostPaths, made when the module was imported: without
    # this the engines' settings (and the distribution the install catalog reads) would be
    # this machine's, and a change made in the console would be a write to its /etc.
    import noust.managers.database.settings as database_settings
    from noust.managers.database import flavours

    flavours.HOST = HostPaths(root=host.root)
    # What the recommendations are computed from: the modelled server, not this machine.
    database_settings.server_resources = lambda: database_settings.Resources(
        memory_bytes=_MODELLED_MEMORY_BYTES, cpus=_MODELLED_CPUS
    )

    gib = 1024**3

    def mounts(self: Any) -> list[Any]:
        rows = [
            ("/", "/dev/vda1", "ext4", 78 * gib, 55.6 * gib, 5_111_808, 4_900_112),
            ("/srv", "/dev/vdb", "xfs", 40 * gib, 35.1 * gib, 20_971_520, 20_100_000),
            ("/boot", "/dev/vda16", "ext4", 881 * 1024**2, 118 * 1024**2, 58_496, 58_100),
        ]
        found = []
        for mount_point, device, fstype, total, used, inodes, inodes_free in rows:
            percent = round(used / total * 100, 1)
            inode_percent = round((inodes - inodes_free) / inodes * 100, 1)
            found.append(
                Mount(
                    mount_point=mount_point,
                    device=device,
                    fstype=fstype,
                    total_bytes=int(total),
                    used_bytes=int(used),
                    free_bytes=int(total - used),
                    percent_used=percent,
                    inodes_total=inodes,
                    inodes_free=inodes_free,
                    inodes_percent=inode_percent,
                    readonly=False,
                    status="critical" if percent >= 95 else "warn" if percent >= 85 else "ok",
                )
            )
        return sorted(found, key=lambda mount: mount.percent_used, reverse=True)

    StorageManager.mounts = mounts  # type: ignore[method-assign]

    # The host tree belongs to whoever runs this script; on the modelled VPS the key files
    # are root's, and StrictModes has nothing to say about their owner.
    import noust.managers.server.security_keys as keys_module
    import noust.managers.server.security_probe as probe_module
    import noust.managers.server.security_ssh as ssh_module

    strict = keys_module.strict_mode_problems
    owner = f"belongs to uid {os.getuid()},"

    def owned_by_root(paths: Any, account: Any, path: str) -> list[str]:
        return [problem for problem in strict(paths, account, path) if owner not in problem]

    for module in (keys_module, probe_module, ssh_module):
        module.strict_mode_problems = owned_by_root  # type: ignore[attr-defined]
    _SERVER_HOST = host
    return host


def seed_server_analysis() -> None:
    """Measure the known places and run the hardening checks once, and keep both."""
    from noust.managers.server.security import ServerSecurity
    from noust.web.api.server.common import get_server_context
    from noust.web.api.server.storage import ANALYSIS_KEY

    context = get_server_context()
    context.cache.put(ANALYSIS_KEY, context.storage.analyze())
    # The hardening checks ran this morning, as they do on a server someone looks after: the
    # Overview and the Security tab read their last run, and never probe on their own.
    ServerSecurity(actor="console-server").checks(refresh=True)


def serve(args: argparse.Namespace, sandbox: Sandbox) -> None:
    """
    Seed the machine and serve the panel until interrupted.

    Args:
        args: The parsed options.
        sandbox: The sandbox, already prepared.
    """
    import uvicorn

    from noust.core.fs import set_fs
    from noust.core.runner import set_runner
    from noust.web.auth import SecurityConfig
    from noust.web.server import create_app, get_token_manager

    redirect_system_paths(sandbox)
    if args.showcase:
        from tests.showcase import AGENCY_DOMAIN

        ssl_email = f"ops@{AGENCY_DOMAIN}"
    else:
        ssl_email = "ops@example.com"
    write_config(sandbox, central_role=args.central_role, ssl_email=ssl_email)
    forbid_real_processes()
    set_fs(make_sandbox_filesystem(sandbox))
    if args.showcase:
        use_showcase_diagnose_paths(sandbox)

    # The runner has to exist before seeding (cron enables its timer through
    # it) and needs the seeded units to answer for, so it is built over empty
    # maps that seeding fills in place.
    units: dict[str, Unit] = {}
    ports: dict[str, int] = {}
    domains: dict[str, str] = {}
    certs: list[str] = []
    set_runner(make_runner(units, ports, domains, certs, sandbox.systemd_dir))
    # Managers report progress on stdout, which belongs to the one JSON line
    # the caller parses.
    with contextlib.redirect_stdout(sys.stderr):
        if args.central_role == "hub":
            # A hub deploys nothing itself: seed_machine's seed is a server's - an
            # application, cron jobs, backups - and creating any of it would hit the
            # same noust.central.require_server_role guard a real hub answers with
            # (RoleError). Nothing the fleet's E2E coverage needs of a hub reads it;
            # the store still has to exist, which is otherwise seed_machine's doing.
            from noust.core.store import get_store

            get_store(sandbox.store_file)
            seeded_units: dict[str, Unit] = {}
            seeded_ports: dict[str, int] = {}
            seeded_domains: dict[str, str] = {}
            seeded_certs: list[str] = []
        else:
            seeded_units, seeded_ports, seeded_domains, seeded_certs = seed_machine(
                sandbox,
                expired_certificate=args.expired_certificate,
                showcase=args.showcase,
                hostname=args.hostname,
            )
            if args.misplaced_backups:
                seed_misplaced_backups(sandbox)
            if args.showcase:
                seed_showcase_backup_destination()
    model_telegram_bot_api()
    units.update(seeded_units)
    ports.update(seeded_ports)
    domains.update(seeded_domains)
    certs.extend(seeded_certs)
    # The VPS the Server area manages (/api/server): see model_server_host.
    model_server_host(sandbox, args.hostname or socket.gethostname(), units)
    with contextlib.redirect_stdout(sys.stderr):
        seed_server_analysis()
    if args.central_role != "hub" and not args.showcase:
        with contextlib.redirect_stdout(sys.stderr):
            seed_databases(sandbox)
    if args.central_role != "hub" and not args.showcase:
        # Deploys tests.panel_factory's own "lanzamiento.example.org": nothing the
        # documentation screenshots need, and exactly the example.org the showcase exists
        # to keep out of them.
        with contextlib.redirect_stdout(sys.stderr):
            seed_exportable_app(sandbox)
    fleet_node_app = os.environ.get(FLEET_NODE_APP_ENV)
    if fleet_node_app:
        with contextlib.redirect_stdout(sys.stderr):
            seed_fleet_node_app(sandbox, units, ports, domains, fleet_node_app)

    # Sealed last, once every seed above has finished writing whatever secrets it
    # needed to (a GitHub app's private key, and so on): sealing before seeding, the
    # way an earlier version of this function did, left seeding itself refused by the
    # very store it was trying to write to. A real central seals only after it holds
    # the secrets it will manage, which this now matches - see seal_sandbox_before_serving.
    if args.seal:
        seal_sandbox_before_serving(sandbox)

    config = SecurityConfig(
        host=args.host,
        port=args.port,
        state_dir=sandbox.state_dir,
        # One browser drives every page of the suite from one address; the
        # production limit would throttle the tests, not an attacker.
        rate_limit_requests=100_000,
    )
    if args.static_dir is not None:
        use_console_build(args.static_dir)
    if args.hostname is not None:
        use_fixed_hostname(args.hostname)
    if args.showcase:
        from tests.showcase import NODE_READINGS

        cpu_percent, memory_percent, disk_percent, uptime_s = NODE_READINGS.get(
            args.hostname or "", NODE_READINGS["fra-1"]
        )
        use_fixed_machine_stats(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            disk_percent=disk_percent,
            uptime_s=uptime_s,
        )
    app = create_app(config)
    token = get_token_manager().generate_master_token()
    totp_secret, backup_codes = enable_totp(args.backup_codes) if args.totp else (None, [])
    seed_settings_api_tokens()
    pin_settings_update_check()

    sock = bind(args.host, args.port)
    host, port = sock.getsockname()[:2]
    display_host = f"[{host}]" if ":" in host else host
    # Inert unless a central's --fleet-node calls it: see the module comment above
    # mount_fleet_test_seam for why this - and only this - is mounted unconditionally.
    mount_fleet_test_seam(app, console_port=port)
    older_version = os.environ.get(OLDER_NODE_ENV)
    served: Any = pretend_older_node(app, older_version) if older_version else app
    if args.fleet_node:
        display_hosts = None
        if args.showcase:
            from tests.showcase import NODE_SSH_HOSTS

            display_hosts = NODE_SSH_HOSTS
        join_fleet_node(args.fleet_node, display_hosts=display_hosts)
    server = uvicorn.Server(
        uvicorn.Config(
            served,
            log_level="warning",
            access_log=False,
            server_header=False,
            timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
        )
    )

    def announce() -> None:
        """Print the connection details once the server accepts requests."""
        while not server.started and not server.should_exit:
            time.sleep(0.02)
        if server.started:
            line: dict[str, object] = {
                "url": f"http://{display_host}:{port}",
                "token": token,
                "totp_secret": totp_secret,
            }
            if args.backup_codes is not None:
                line["backup_codes"] = backup_codes
            if args.keep:
                line["sandbox"] = str(sandbox.root)
            print(json.dumps(line), flush=True)

    threading.Thread(target=announce, name="announce", daemon=True).start()
    # uvicorn shuts down gracefully on SIGTERM and then re-raises it against
    # the previous handler; the default one would kill the process before
    # main() removes the sandbox. Exiting normally instead runs the cleanup.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    server.run(sockets=[sock])


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the console server.

    Args:
        argv: Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The exit status.
    """
    args = parse_args(argv)
    if args.internal_seal is not None:
        return run_internal_seal(Path(args.internal_seal))
    sandbox = Sandbox(Path(tempfile.mkdtemp(prefix="noust-console-")).resolve())
    try:
        prepare_environment(sandbox)
        serve(args, sandbox)
    finally:
        if args.keep:
            print(f"console_server: sandbox kept at {sandbox.root}", file=sys.stderr)
        else:
            shutil.rmtree(sandbox.root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
