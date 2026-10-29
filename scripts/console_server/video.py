# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The machine the product video is filmed on: ``--showcase --profile video``.

:mod:`tests.support.showcase_video` says what the agency's servers hold; this is what has to be
true of the sandbox for the video's actions to happen in it for real rather than be pictured:

- the applications on releases have their releases on disk, ``current`` pointing at the active
  one and their ``.env`` in ``shared/``, so an update builds, activates and goes back;
- the agency's repositories on GitHub answer ``git`` (:func:`model_code_host` with the
  agency's :class:`CodeHost`): a new site is a Next.js project, a push to the shop is the
  commit whose release never starts, and a started release listens unless it is that one;
- ``npm`` prints what it prints, a line at a time, so a deploy can be watched while it builds;
- the paths a log or an error shows are the server's own (``/var/www/apps/...``), not the
  sandbox's directory under ``/tmp``;
- the nightly backups exist at a believable size, scheduled at 03:00 to two destinations.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .backup_destinations import _destinations_copy_backups, _destinations_rclone_model
from .code_hosts import CodeHost, _code_host_commit, _code_host_tree, model_code_host
from .sandbox import Sandbox

#: The organisation the agency's repositories live under (tests.support.showcase.GITHUB_ORG).
_ORG = "kelmoor-labs"

#: A file the release of a commit that never starts carries, so the model of its unit can tell.
CRASH_MARKER = ".exits-on-start"

#: How long a Next.js deploy of one of the agency's projects takes in the sandbox, roughly what a
#: real one takes on a small server (half a minute): long enough to be watched while it builds.
NPM_PACE = 1.0

#: The repositories that are Next.js projects; any other of the agency's is a Node service.
_NEXTJS_REPOSITORIES = frozenset(
    {"tidewick-site", "brumaria-shop", "wrenfield-site", "wrenfield-portal", "thornfield-site"}
)


def _next_commit(repository: str) -> Any:
    from tests.support.showcase_video import NEXT_COMMITS

    owner, _, name = repository.partition("/")
    return NEXT_COMMITS.get(name) if owner == _ORG else None


def _video_commit(repository: str, branch: str) -> str:
    """
    Returns:
        The full commit a branch of one of the agency's repositories points at: on ``main`` of
        a repository the video pushes to, the commit of :data:`NEXT_COMMITS`, padded to forty
        hexadecimal digits the same way every run.
    """
    upcoming = _next_commit(repository)
    if upcoming is None or branch != "main":
        return _code_host_commit(repository, branch)
    tail = hashlib.sha1(repository.encode(), usedforsecurity=False).hexdigest()
    return (upcoming.commit.sha + tail)[:40]


def _video_subject(repository: str, branch: str) -> str:
    upcoming = _next_commit(repository)
    return upcoming.commit.message if upcoming is not None else "Update dependencies"


def _video_tree(repository: str, branch: str, destination: Path) -> None:
    """
    Write the project one of the agency's repositories holds.

    A Next.js repository is a Next.js 15 project with its lock file (so it installs with
    ``npm ci``) and a standalone build; the commit that never starts also carries
    :data:`CRASH_MARKER`. Anything else is the code host's default project.

    Args:
        repository: ``owner/name``.
        branch: The branch checked out.
        destination: The directory the clone writes into.
    """
    owner, _, name = repository.partition("/")
    if owner != _ORG or name not in _NEXTJS_REPOSITORIES:
        _code_host_tree(repository, branch, destination)
        return
    _code_host_tree(repository, branch, destination)
    for leftover in ("server.js", "BRANCH"):
        (destination / leftover).unlink(missing_ok=True)
    package = {
        "name": name,
        "version": "1.4.0",
        "private": True,
        "scripts": {"dev": "next dev", "build": "next build", "start": "next start"},
        "dependencies": {"next": "15.2.4", "react": "19.0.0", "react-dom": "19.0.0"},
    }
    (destination / "package.json").write_text(
        json.dumps(package, indent=2) + "\n", encoding="utf-8"
    )
    (destination / "package-lock.json").write_text(
        json.dumps({"name": name, "lockfileVersion": 3, "requires": True, "packages": {}}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (destination / "next.config.mjs").write_text(
        "/** @type {import('next').NextConfig} */\nexport default { output: 'standalone' };\n",
        encoding="utf-8",
    )
    (destination / "app").mkdir(exist_ok=True)
    (destination / "app" / "page.tsx").write_text(
        "export default function Home() {\n  return <main>Tidewick</main>;\n}\n", encoding="utf-8"
    )
    upcoming = _next_commit(repository)
    if upcoming is not None and upcoming.crashes and branch == "main":
        (destination / CRASH_MARKER).write_text(
            "\n".join(upcoming.journal) + "\n", encoding="utf-8"
        )


def _crashing_release(sandbox: Sandbox, unit: str) -> Path | None:
    """
    Args:
        sandbox: The sandbox, whose unit files say where each unit runs from.
        unit: A unit's name, a blue/green instance's included.

    Returns:
        The marker of the release the unit runs, when that release never starts; else None.
    """
    prefix, at, _instance = unit.partition("@")
    unit_file = sandbox.systemd_dir / (f"{prefix}@.service" if at else f"{unit}.service")
    try:
        text = unit_file.read_text(encoding="utf-8")
    except OSError:
        return None
    found = re.search(r"^WorkingDirectory=(\S+)", text, re.M)
    if found is None:
        return None
    marker = Path(found.group(1)).resolve() / CRASH_MARKER
    return marker if marker.exists() else None


def model_agency_code_host(sandbox: Sandbox, units: dict[str, Any]) -> None:
    """
    Answer ``git`` for the agency's repositories on GitHub, start their releases, and print
    what a crashing release prints to its journal.

    Args:
        sandbox: The sandbox.
        units: The modelled units: a release that exits as soon as it starts leaves its unit
            failed, which is what ``systemctl`` and the journal then say.
    """
    from noust.core.runner import CommandResult, get_runner

    def starts(unit: str) -> bool:
        return _crashing_release(sandbox, unit) is None

    model_code_host(
        sandbox,
        CodeHost(
            name="github.com",
            tree=_video_tree,
            commit=_video_commit,
            subject=_video_subject,
            starts=starts,
        ),
    )

    runner = get_runner()
    original = runner.run

    def run(argv: Sequence[str], **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        if args[:1] == ("journalctl",) and "-u" in args and "-k" not in args:
            unit = args[args.index("-u") + 1].removesuffix(".service")
            marker = _crashing_release(sandbox, unit)
            if marker is not None:
                runner.calls.append(args)
                stamp = datetime.now().astimezone().strftime("%b %d %H:%M:%S")
                lines = [
                    f"{stamp} fra-1 systemd[1]: Started {unit}.service.",
                    *(
                        f"{stamp} fra-1 {unit}[51207]: {line}"
                        for line in marker.read_text().split("\n")
                        if line
                    ),
                    f"{stamp} fra-1 systemd[1]: {unit}.service: Main process exited, code=exited, status=1/FAILURE",
                    f"{stamp} fra-1 systemd[1]: {unit}.service: Failed with result 'exit-code'.",
                ]
                return CommandResult(args, 0, "\n".join(lines) + "\n", "")
        return original(argv, **kwargs)

    runner.run = run  # type: ignore[method-assign]


#: What ``npm ci`` prints for one of the agency's Next.js projects, each line with the seconds
#: before it: the download is the long wait.
_NPM_CI: tuple[tuple[str, float], ...] = (
    ("npm warn deprecated inflight@1.0.6: This module is not supported, and leaks memory.", 2.2),
    ("", 0.1),
    ("added 412 packages, and audited 413 packages in 9s", 6.8),
    ("", 0.1),
    ("143 packages are looking for funding", 0.1),
    ("  run `npm fund` for details", 0.1),
    ("", 0.1),
    ("found 0 vulnerabilities", 0.2),
)


def _next_build(name: str) -> tuple[tuple[str, float], ...]:
    """
    Args:
        name: The project's name, as its package.json says it.

    Returns:
        What ``npm run build`` prints for it, each line with the seconds before it.
    """
    return (
        ("", 0.1),
        (f"> {name}@1.4.0 build", 0.1),
        ("> next build", 0.1),
        ("", 0.3),
        ("   ▲ Next.js 15.2.4", 0.4),
        ("", 0.1),
        ("   Creating an optimized production build ...", 0.4),
        (" ✓ Compiled successfully in 17.8s", 12.5),
        ("   Linting and checking validity of types ...", 0.3),
        ("   Collecting page data ...", 0.9),
        (" ✓ Generating static pages (14/14)", 0.8),
        ("   Finalizing page optimization ...", 0.4),
        ("", 0.1),
        ("Route (app)                                 Size  First Load JS", 0.2),
        ("┌ ○ /                                    5.21 kB         112 kB", 0.05),
        ("├ ○ /_not-found                            979 B         102 kB", 0.05),
        ("├ ○ /collections                         3.84 kB         110 kB", 0.05),
        ("├ ƒ /collections/[slug]                  2.67 kB         109 kB", 0.05),
        ("└ ○ /journal                             1.92 kB         108 kB", 0.05),
        ("+ First Load JS shared by all             101 kB", 0.05),
        ("", 0.05),
        ("○  (Static)   prerendered as static content", 0.05),
        ("ƒ  (Dynamic)  server-rendered on demand", 0.05),
    )


def stream_npm_output(sandbox: Sandbox) -> None:
    """
    Print npm's output for a Next.js build of one of the agency's projects, a line at a time.

    The runner answers every command at once and with nothing; a deploy would be over before
    it could be watched, and its log would say "Running: npm ci" and nothing more. Only npm in
    a project under the applications' directory is answered this way.

    Args:
        sandbox: The sandbox.
    """
    from noust.core.runner import CommandResult, get_runner

    runner = get_runner()
    original = runner.stream

    def stream(argv: Sequence[str], *, on_line: Any, cwd: Path | None = None, **kwargs: Any) -> Any:
        args = tuple(str(a) for a in argv)
        npm = [a for a in args if a == "npm" or a.endswith("/npm")]
        inside = cwd is not None and sandbox.apps_dir in Path(cwd).resolve().parents
        package = Path(cwd) / "package.json" if cwd is not None else None
        if not (npm and inside and package is not None and package.exists()):
            return original(argv, on_line=on_line, cwd=cwd, **kwargs)
        try:
            name = str(json.loads(package.read_text(encoding="utf-8")).get("name", "app"))
        except (OSError, ValueError):
            name = "app"
        runner.calls.append(args)
        lines = _next_build(name) if "build" in args else _NPM_CI
        for line, delay in lines:
            time.sleep(delay * NPM_PACE)
            on_line(line)
        output = "\n".join(line for line, _ in lines) + "\n"
        return CommandResult(argv=args, exit_code=0, stdout=output, stderr="")

    runner.stream = stream  # type: ignore[method-assign]


def show_server_paths(sandbox: Sandbox) -> None:
    """
    Write the server's own paths in deployment logs and errors, not the sandbox's.

    Every path under the sandbox is the path a real server has with the sandbox's directory in
    front (``/tmp/noust-console-x/var/www/apps/shop`` is ``/var/www/apps/shop``): the captured
    log of a deploy, and the error a failed one records, drop that directory.

    Args:
        sandbox: The sandbox.
    """
    from noust.deployers.recorder import DeploymentRecorder

    root = str(sandbox.root)
    scrub = DeploymentRecorder._scrub

    def server_paths(self: DeploymentRecorder, text: str) -> str:
        return scrub(self, text).replace(root, "")

    DeploymentRecorder._scrub = server_paths  # type: ignore[method-assign]


def seed_release_trees(sandbox: Sandbox, store: Any) -> None:
    """
    Put every application on releases on disk the way a deploy leaves it.

    Its releases still kept (the active one and the two before it) under ``releases/``,
    ``current`` pointing at the active one, and its ``.env`` in ``shared/``. No ``repo/``: the
    first update clones the repository again, as a server whose cache was cleaned does.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
    """
    for app in store.list_apps():
        root = Path(app.app_path)
        if not sandbox.contains(root):
            continue
        root.mkdir(parents=True, exist_ok=True)
        if app.layout != "releases" or app.id is None:
            continue
        releases = [r for r in store.list_releases(app.id) if r.status in ("active", "superseded")]
        releases.sort(key=lambda r: r.created_at or "")
        shared = root / "shared"
        shared.mkdir(exist_ok=True)
        env = shared / ".env"
        env.write_text(
            f"NODE_ENV=production\nPORT={app.port or 8080}\n"
            f"NEXT_PUBLIC_SITE_URL=https://{app.domain}\nLOG_LEVEL=info\n",
            encoding="utf-8",
        )
        env.chmod(0o600)
        for number, release in enumerate(releases[-3:], start=1):
            directory = root / "releases" / release.id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "package.json").write_text(
                json.dumps({"name": app.domain, "version": f"1.{number}.0"}) + "\n",
                encoding="utf-8",
            )
            link = directory / ".env"
            if not link.is_symlink():
                link.symlink_to("../../shared/.env")
        active = next((r for r in releases if r.status == "active"), None)
        current = root / "current"
        if active is not None and not current.is_symlink():
            current.symlink_to(Path("releases") / active.id)


def seed_nightly_backups(sandbox: Sandbox, store: Any, domains: list[str]) -> None:
    """
    The nightly backups: three nights of archives per application, at a believable size,
    a 03:00 schedule for each, and the copies on the two destinations.

    The destinations go through the real manager, the schedules through the real scheduler
    (which writes their timers), and rclone is answered from directories in the sandbox.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        domains: The applications backed up every night.
    """
    from noust.core.utils import domain_to_app_name
    from noust.managers.backup_destinations import BackupDestinationManager
    from noust.managers.backup_manager import BackupMetadata
    from noust.managers.backup_scheduler import BackupSchedule, BackupScheduler
    from tests.support.showcase import AGENCY_DOMAIN
    from tests.support.showcase_video import BACKUP_DESTINATIONS, BACKUP_TIME

    sftp, vault = BACKUP_DESTINATIONS
    remotes = sandbox.root / "remotes"
    _destinations_rclone_model(sandbox, remotes)
    (remotes / vault / "kelmoor-vault").mkdir(parents=True, exist_ok=True)
    manager = BackupDestinationManager()
    manager.add(
        sftp,
        "sftp",
        {
            "host": f"backup.{AGENCY_DOMAIN}",
            "user": "noust",
            "port": "22",
            "pass": "Qv58-seeded-sftp-password",
            "path": "/srv/backups/fra-1",
        },
    )
    manager.add(
        vault,
        "s3",
        {
            "provider": "Cloudflare",
            "access_key_id": "7d2e9c4b1a6f3e80",
            "secret_access_key": "r2-seeded-secret-access-key-not-shown",
            "region": "auto",
            "endpoint": "https://8c41e2.r2.cloudflarestorage.com",
            "path": "kelmoor-vault",
        },
        encrypted=True,
    )

    hour, minute = (int(part) for part in BACKUP_TIME.split(":"))
    today = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    if today > datetime.now():
        today -= timedelta(days=1)
    apps = {app.domain: app for app in store.list_apps()}
    for index, domain in enumerate(domains):
        app = apps.get(domain)
        app_name = domain_to_app_name(domain)
        directory = sandbox.backup_dir / app_name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # A site with a database dumps it too: a few hundred megabytes, growing a little a night.
        base = 180_000_000 + (
            int(hashlib.sha1(domain.encode(), usedforsecurity=False).hexdigest()[:6], 16)
            % 260_000_000
        )
        for night in range(3):
            created = today - timedelta(days=night, seconds=-(index * 41 + 7))
            backup_id = f"{app_name}_{created.strftime('%Y%m%d_%H%M%S')}"
            archive = directory / f"{backup_id}.tar.gz"
            payload = directory / f"{backup_id}.README"
            payload.write_text(f"Nightly backup of {domain}\n", encoding="utf-8")
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(payload, arcname="app/README")
            payload.unlink()
            # Sparse: the size a reader sees, none of it on the disk of whoever runs this.
            os.truncate(archive, base - night * 1_700_000)
            metadata = BackupMetadata(
                id=backup_id,
                domain=domain,
                app_name=app_name,
                created_at=created.isoformat(),
                size_bytes=archive.stat().st_size,
                app_type=app.app_type if app is not None else "nextjs",
                version="4.1.0",
                description="Nightly backup",
                includes_env=True,
                includes_node_modules=False,
                git_commit=None,
                git_branch=app.branch if app is not None else None,
                tags=["scheduled", "auto"],
            )
            (directory / f"{backup_id}.json").write_text(
                json.dumps(metadata.to_dict(), indent=2), encoding="utf-8"
            )
        _destinations_copy_backups(sandbox, domain, remotes / sftp / "srv/backups/fra-1", link=True)
        _destinations_copy_backups(sandbox, domain, remotes / f"{vault}crypt", link=True)
        BackupScheduler(verbose=False).create_schedule(
            BackupSchedule(
                domain=domain,
                app_name=app_name,
                schedule=f"*-*-* {BACKUP_TIME}:00",
                retention_count=14,
                retention_days=30,
                destinations=[
                    {"name": sftp, "retention_count": 14, "retention_days": 90},
                    {"name": vault, "retention_count": 30, "retention_days": None},
                ],
            )
        )


def model_agency_databases(node: str) -> None:
    """
    Make the modelled database servers hold the agency's databases instead of the default
    machine's examples: the Databases page lists them at their size, and the dump taken before
    an update finds the database it dumps.

    Args:
        node: Which server of the fleet this is.
    """
    from noust.core.runner import get_runner
    from tests.support.showcase_video import VIDEO_DATABASES

    runner: Any = get_runner()
    postgres = {"postgres": ("postgres", 7_553_827)}
    mysql: dict[str, tuple[int, int]] = {}
    for name, engine, _port, _owner, size in VIDEO_DATABASES.get(node, ()):
        if engine == "postgresql":
            postgres[name] = (name, size)
        elif engine == "mysql":
            mysql[name] = (size, 12 + size % 40)
    runner._pg_databases = postgres
    runner._mysql_databases = mysql


def settle_server_host(root: Path) -> None:
    """
    Leave the modelled VPS with nothing to restart for: the video's servers were patched and
    rebooted already, so no "restart required" competes with what the video shows.

    Args:
        root: The modelled host's root (:class:`ServerHost`'s).
    """
    for name in ("reboot-required", "reboot-required.pkgs"):
        (root / "var" / "run" / name).unlink(missing_ok=True)
