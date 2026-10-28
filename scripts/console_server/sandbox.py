# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The sandbox: the temporary directory every path Noust uses is redirected into, and the file
system that refuses a write outside it."""

from __future__ import annotations

import asyncio
import os
import sys
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .constants import CALL_HISTORY, REPO

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
    # The repository's own code, never an installed copy, and tests.support.panel_factory.
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
    ``NGINX_UPSTREAMS_DIR``...). Patched after the fact, any of those would
    keep resolving against the real ``/etc`` and ``/var`` - exactly the
    developer's machine this script promises never to touch.

    Module constants are rebound on the module that reads them; class
    attributes on the class. A path this misses is still covered by
    :class:`SandboxFileSystem` for writes, and is only ever read.

    Args:
        sandbox: The sandbox.
    """
    import noust.core.paths as paths_module

    etc, var = sandbox.etc, sandbox.var

    paths_module.CONFIG_DIR = sandbox.state_dir
    paths_module.STATE_DIR = sandbox.store_file.parent
    paths_module.BACKUP_DIR = sandbox.backup_dir
    paths_module.LOG_DIR = var / "log" / "noust"
    paths_module.NGINX_UPSTREAMS_DIR = etc / "nginx" / "noust-upstreams"

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
    # A backup is restored where the store says the application is, and the
    # seeded store records the paths a real server has (/var/www/apps/...):
    # outside the sandbox, a restore would write to the machine's own /var.
    # The same path under the sandbox's root instead.
    import noust.managers.backup_manager as backup_module
    import noust.managers.rollback_manager as rollback_module

    deployed_at = backup_module._deployed_at

    def sandboxed_deployed_at(domain: str, app_name: str, config: Any) -> Path:
        path = deployed_at(domain, app_name, config)
        return path if sandbox.contains(path) else sandbox.root / path.relative_to(path.anchor)

    # A rollback imports the function by name, so it needs the same replacement or it would restore
    # outside the sandbox.
    backup_module._deployed_at = sandboxed_deployed_at
    rollback_module._deployed_at = sandboxed_deployed_at
    # How long the journal keeps the logs is a drop-in under /etc; the sandbox's own, so the page
    # that sets it writes into the temporary machine and never into the developer's journald.
    import noust.managers.log_retention as retention_module

    retention_defaults = retention_module.LogRetention.__init__.__kwdefaults__
    if retention_defaults is not None:
        retention_defaults["dropin"] = (
            etc / "systemd" / "journald.conf.d" / "50-noust-retention.conf"
        )
        retention_defaults["journal_dir"] = var / "log" / "journal"
    timeseries_module.SYSTEM_DB_PATH = var / "lib" / "noust" / "metrics.db"
    observations_module.SYSTEM_DB_PATH = var / "lib" / "noust" / "observations.db"
    diagnose_module.NGINX_ERROR_LOG = var / "log" / "nginx" / "error.log"
    # The web server's own logs for a site, which the Logs tab reads: the sandbox's, never the
    # machine's, so what a spec sees does not depend on what is installed where it runs.
    import noust.monitor.plan as plan_module

    plan_module.ACCESS_LOG_DIRS = {
        "nginx": (var / "log" / "nginx",),
        "apache": (var / "log" / "apache2",),
        "apache2": (var / "log" / "apache2",),
    }

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
    # The console's own unit names the noust that runs it. The modelled machine has one at
    # /usr/bin/noust, whatever PATH this process was given (the E2E suite runs without one).
    import noust.managers.console_service as console_service_module

    console_service_module.find_noust_executable = lambda: "/usr/bin/noust"
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


#: The public address an ``--empty`` server declares. A server in a sandbox has none of its own
#: (its interfaces are private) and the temporary address is made of one; this one is a real
#: public address that nothing here ever connects to.
EMPTY_PUBLIC_ADDRESS = "93.184.216.34"


def write_config(
    sandbox: Sandbox,
    *,
    central_role: str | None = None,
    ssl_email: str = "ops@example.com",
    public_address: str | None = None,
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
        public_address: ``dns.public_addresses`` to declare, for ``--empty``: the address a
            temporary name is made of. Left out, the sandbox has none.
    """
    import yaml

    config: dict[str, Any] = {
        "apps_directory": str(sandbox.apps_dir),
        "webserver": "nginx",
        "service_user": "www-data",
        "service_group": "www-data",
        "ssl": {"enabled": True, "provider": "certbot", "email": ssl_email},
        "backup": {"directory": str(sandbox.backup_dir), "max_per_app": 10},
        # The terminal's root shell on the server is off on a real machine until an
        # administrator turns it on; here it is on, so the console's E2E can open it. What
        # opens is the fake runner's shell that echoes (see ConsoleRunner), not a real one.
        "terminal": {"server": {"enabled": True}},
    }
    if central_role is not None:
        config["central"] = {"role": central_role}
    if public_address is not None:
        config["dns"] = {"public_addresses": [public_address]}
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
