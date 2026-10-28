# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Console 2.2, backup destinations: an SFTP folder, an encrypted bucket and the rclone that
answers for them."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .sandbox import Sandbox

# --- 2.2: backup destinations ------------------------------------------------


#: The folder the SFTP destination writes into, as its operator typed it.
DESTINATIONS_SFTP_PATH = "/srv/backups/web-01"
DESTINATIONS_ENCRYPTED_BUCKET = "noust-backups"

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


def seed_site_logs(sandbox: Sandbox) -> None:
    """
    Write the web server's logs for two of the seeded sites, so the Logs tab has real lines.

    ``tienda.example.org`` has both: an access log with requests of every kind (one carries a
    token in its query string, which the console must show masked) and an error log with a
    refused upstream. ``taller.example.com`` is a static site with an error log only, so the
    Logs tab of a site that has written nothing to its access log is the one seeded too.

    Args:
        sandbox: The sandbox; ``redirect_system_paths`` already pointed the web server's log
            directory at ``var/log/nginx`` under it.
    """
    directory = sandbox.var / "log" / "nginx"
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    now = datetime.now()

    def stamp(seconds_ago: int) -> str:
        return (now - timedelta(seconds=seconds_ago)).strftime("%d/%b/%Y:%H:%M:%S +0000")

    requests = [
        ("GET / HTTP/1.1", 200, 5120),
        ("GET /assets/app.js HTTP/1.1", 200, 183204),
        ("GET /assets/app.css HTTP/1.1", 304, 0),
        ("GET /favicon.ico HTTP/1.1", 404, 153),
        ("GET /api/orders HTTP/1.1", 200, 2210),
        ("POST /api/orders HTTP/1.1", 502, 157),
        ("GET /callback?code=1&token=ghp_" + "t" * 36 + " HTTP/1.1", 200, 312),
    ]
    lines = [
        f'203.0.113.{7 + index % 5} - - [{stamp(3_600 - index * 45)}] "{request}" {status} {size} "-" "Mozilla/5.0"'
        for index, (request, status, size) in enumerate(requests * 6)
    ]
    (directory / "tienda.example.org.access.log").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    when = now.strftime("%Y/%m/%d %H:%M:%S")
    (directory / "tienda.example.org.error.log").write_text(
        f"{when} [error] 41231#41231: *82 connect() failed (111: Connection refused) while connecting "
        'to upstream, client: 203.0.113.7, server: tienda.example.org, request: "POST /api/orders '
        'HTTP/1.1", upstream: "http://127.0.0.1:3000/api/orders", host: "tienda.example.org"\n',
        encoding="utf-8",
    )
    (directory / "taller.example.com.error.log").write_text(
        f'{when} [error] 41231#41231: *91 open() "/var/www/taller/missing.html" failed (2: No such file '
        'or directory), client: 203.0.113.9, server: taller.example.com, request: "GET /missing.html '
        'HTTP/1.1", host: "taller.example.com"\n',
        encoding="utf-8",
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
    from tests.support.panel_factory import DESTINATION_ENCRYPTED, DESTINATION_SFTP, seed_push_job

    remotes = sandbox.root / "remotes"
    _destinations_rclone_model(sandbox, remotes)

    # Adding an object store looks at its bucket first and never creates one, so the bucket
    # the encrypted destination names exists on its modelled remote before it is added.
    (remotes / DESTINATION_ENCRYPTED / DESTINATIONS_ENCRYPTED_BUCKET).mkdir(
        parents=True, exist_ok=True
    )

    manager = BackupDestinationManager()
    manager.add(
        DESTINATION_SFTP,
        "sftp",
        {
            "host": "backup.example.org",
            "user": "backup",
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
            "path": DESTINATIONS_ENCRYPTED_BUCKET,
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
        if verb == "rcat" and target is not None:
            # The record that travels beside an archive (its sidecar) is written from stdin.
            target[1].parent.mkdir(parents=True, exist_ok=True)
            target[1].write_text(stdin, encoding="utf-8")
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
