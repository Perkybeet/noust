# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Backup and rollback commands.

The work itself lives in the private ``_backup_*`` helpers. The Click commands
and the argparse-shaped handlers below are both thin adapters over them, so the
two entry points cannot drift. ``wasm.cli.parser`` is gone and nothing calls
these handlers in production anymore; they are kept, and tested directly, for
the same reason.

Two things the argparse tree got wrong and this module does not:

- ``wasm backup new`` and ``wasm backup ls`` reached the handler with the alias
  as the action name, which fell through to "Unknown backup action". The alias
  table is now one constant, used by the Click group and by the handler.
- ``--include-docker-volumes``, ``--schemas``, ``--redis-method``,
  ``--retention-count`` and ``--retention-days`` were declared, documented and
  then never passed to :class:`BackupManager`. They are wired through now.

Backups are self-contained since archive format 2.0.0: the archive carries the
application directory plus, when asked for, the database dumps and Docker
volumes, so it restores on a machine that knows nothing about this one. The
help text says so because it is now true.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import click

from wasm.cli.app import Context, WasmGroup, global_flags, json_option, pass_context
from wasm.cli.panel_links import open_in_panel
from wasm.core.exceptions import WASMError
from wasm.core.logger import Logger
from wasm.managers.backup_manager import BackupManager, BackupMetadata, RollbackManager

#: Alternative spellings for the actions under ``wasm backup``. They are in
#: scripts and in muscle memory, so they resolve rather than fail.
BACKUP_ALIASES: dict[str, str] = {
    "check": "verify",
    "ls": "list",
    "new": "create",
    "remove": "delete",
    "rm": "delete",
    "show": "info",
}

#: Alternative spellings for the actions under ``wasm backup schedule``.
SCHEDULE_ALIASES: dict[str, str] = {
    "ls": "list",
    "remove": "delete",
    "rm": "delete",
}

#: Ways to capture a Redis instance into a backup.
REDIS_METHODS: tuple[str, ...] = ("rdb", "aof")


class AliasedGroup(WasmGroup):
    """
    A group that answers to the alternative spellings of its subcommands.

    Only the canonical names are listed in ``--help``: an alias is there so an
    old script keeps working, not so the help page grows a second copy of every
    command.
    """

    def __init__(self, *args: Any, aliases: dict[str, str] | None = None, **kwargs: Any) -> None:
        """
        Args:
            *args: Passed to click.Group.
            aliases: Alternative spelling to the canonical subcommand name.
            **kwargs: Passed to click.Group.
        """
        super().__init__(*args, **kwargs)
        self.aliases = aliases or {}

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        """
        Look a subcommand up, resolving an alias first.

        Args:
            ctx: Click context.
            name: Name or alias the user typed.

        Returns:
            The command, or None if there is no such subcommand.
        """
        return super().get_command(ctx, self.aliases.get(name, name))

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        """
        Resolve the command to run, reporting the canonical name.

        Args:
            ctx: Click context.
            args: Remaining arguments.

        Returns:
            The command name, the command and the arguments left for it.
        """
        _, command, remaining = super().resolve_command(ctx, args)
        return (command.name if command else None), command, remaining


def _finish(code: int) -> None:
    """
    Leave the current command with an exit code Click will propagate.

    Returning the code is not enough: Click only forwards a callback's return
    value when it is driven with ``standalone_mode=False``.

    Args:
        code: Process exit code.
    """
    click.get_current_context().exit(code)


def _parse_tags(raw: str | None) -> list[str]:
    """
    Split a comma-separated tag list.

    Args:
        raw: The value of ``--tags``, or None.

    Returns:
        The tags, without surrounding whitespace and without empty entries.
    """
    if not raw:
        return []
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def _human_bytes(size_bytes: float) -> str:
    """
    Render a byte count in the largest unit that keeps it under 1024.

    Args:
        size_bytes: Size in bytes.

    Returns:
        A human-readable size, for example "1.4 GB".
    """
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _read_stdin_value() -> str:
    """
    Read a value piped to standard input.

    A secret typed on the command line lands in shell history and is visible
    in ``ps`` to every local user for as long as the process runs; piping it
    in instead keeps it out of both.

    Returns:
        The bytes read, decoded as text, with one trailing newline removed.
    """
    raw = sys.stdin.read()
    if raw.endswith("\n"):
        raw = raw[:-1]
    return raw


def _parse_field_options(pairs: Sequence[str]) -> dict[str, str]:
    """
    Parse repeated ``--field KEY=VALUE`` options into a mapping.

    Args:
        pairs: The raw ``KEY=VALUE`` strings.

    Returns:
        The fields, keyed by name.

    Raises:
        click.UsageError: When an entry has no ``=``.
    """
    fields: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise click.UsageError(f"--field must be KEY=VALUE, got: {pair!r}")
        fields[key.strip()] = value
    return fields


def _read_secret_field(backend: str, *, from_stdin: bool, from_prompt: bool) -> dict[str, str]:
    """
    Read a backend's one secret field, from stdin, a hidden prompt, or neither.

    Every backend WASM supports declares at most one secret field (a
    password, an application key, an OAuth token), so ``--stdin`` and
    ``--prompt`` need not name which field they are for.

    Args:
        backend: The destination's backend.
        from_stdin: Read the value from standard input.
        from_prompt: Prompt for the value without echoing it, twice.

    Returns:
        ``{field_key: value}``, or empty when neither flag was given and the
        backend has no secret field.

    Raises:
        click.UsageError: When both flags are given, or the backend has no
            secret field to fill.
    """
    from wasm.managers.backup_destinations import backend_fields

    if from_stdin and from_prompt:
        raise click.UsageError("Use --stdin or --prompt, not both.")
    if not (from_stdin or from_prompt):
        return {}

    secret_specs = [spec for spec in backend_fields(backend) if spec.secret]
    if not secret_specs:
        raise click.UsageError(f"Backend {backend!r} has no secret field to read.")
    spec = secret_specs[0]

    value = (
        _read_stdin_value()
        if from_stdin
        else click.prompt(spec.label, hide_input=True, confirmation_prompt=True)
    )
    return {spec.key: value}


def _parse_destination_spec(value: str) -> dict[str, Any]:
    """
    Parse a ``--destination NAME[:COUNT[:DAYS]]`` value.

    Args:
        value: The raw option value.

    Returns:
        ``{"name", "retention_count", "retention_days"}``, the shape a
        schedule stores a destination reference as.

    Raises:
        click.UsageError: When no name is given, or a count/days segment is
            not an integer.
    """
    parts = value.split(":")
    name = parts[0].strip()
    if not name:
        raise click.UsageError(f"--destination must name a destination: {value!r}")

    def _optional_int(segment: str | None, what: str) -> int | None:
        if not segment:
            return None
        try:
            return int(segment)
        except ValueError as exc:
            raise click.UsageError(f"--destination {what} must be a number: {value!r}") from exc

    return {
        "name": name,
        "retention_count": _optional_int(parts[1] if len(parts) > 1 else None, "retention count"),
        "retention_days": _optional_int(parts[2] if len(parts) > 2 else None, "retention days"),
    }


def _add_destination(
    *,
    logger: Logger,
    name: str,
    backend: str,
    fields_raw: Sequence[str],
    from_stdin: bool,
    from_prompt: bool,
    encrypted: bool,
) -> int:
    """
    Create a backup destination and report it.

    Args:
        logger: Logger to report through.
        name: Destination name.
        backend: Backend type.
        fields_raw: Raw ``--field KEY=VALUE`` options.
        from_stdin: Read the backend's secret field from standard input.
        from_prompt: Prompt for the backend's secret field.
        encrypted: Wrap the remote in an rclone crypt backend.

    Returns:
        0 on success, 1 if the destination could not be created.
    """
    from wasm.managers.backup_destinations import BackupDestinationManager

    try:
        fields = _parse_field_options(fields_raw)
        fields.update(_read_secret_field(backend, from_stdin=from_stdin, from_prompt=from_prompt))
        destination = BackupDestinationManager().add(name, backend, fields, encrypted=encrypted)
    except WASMError as exc:
        logger.error(f"Could not create backup destination: {exc}")
        return 1

    logger.success(f"Backup destination created: {destination.name}")
    logger.info(f"  Backend: {destination.backend}")
    if destination.encrypted:
        logger.warning(
            "Encryption keys were generated and stored on this server only. Run "
            f"'wasm backup destination show-key {destination.name}' now and keep them "
            "somewhere safe: losing them makes every backup on this destination unrecoverable."
        )
    return 0


def _update_destination(
    *,
    logger: Logger,
    name: str,
    fields_raw: Sequence[str],
    from_stdin: bool,
    from_prompt: bool,
    encrypted: bool | None,
) -> int:
    """
    Change a backup destination's fields and report it.

    Args:
        logger: Logger to report through.
        name: Destination name.
        fields_raw: Raw ``--field KEY=VALUE`` options; a blank secret field
            keeps its stored value.
        from_stdin: Read the backend's secret field from standard input.
        from_prompt: Prompt for the backend's secret field.
        encrypted: Turn encryption on or off; None leaves it as it was.

    Returns:
        0 on success, 1 if the destination could not be changed.
    """
    from wasm.managers.backup_destinations import BackupDestinationManager

    manager = BackupDestinationManager()
    try:
        existing = manager.get(name)
        if existing is None:
            logger.error(f"No such backup destination: {name}")
            return 1
        fields = _parse_field_options(fields_raw)
        fields.update(
            _read_secret_field(existing.backend, from_stdin=from_stdin, from_prompt=from_prompt)
        )
        destination = manager.update(name, fields, encrypted=encrypted)
    except WASMError as exc:
        logger.error(f"Could not update backup destination: {exc}")
        return 1

    logger.success(f"Backup destination updated: {destination.name}")
    return 0


def _list_destinations(*, logger: Logger, json_output: bool = False) -> int:
    """
    List the configured backup destinations.

    Args:
        logger: Logger to report through.
        json_output: Print the destinations as JSON instead of a listing.

    Returns:
        0 on success, 1 if the destinations could not be read.
    """
    from wasm.managers.backup_destinations import BackupDestinationManager

    try:
        manager = BackupDestinationManager()
        destinations = manager.list_destinations()
    except WASMError as exc:
        logger.error(f"Error listing backup destinations: {exc}")
        return 1

    if json_output:
        click.echo(
            json.dumps(
                [
                    {
                        "name": d.name,
                        "backend": d.backend,
                        "encrypted": d.encrypted,
                        "settings": d.settings,
                        "configured_secret_fields": manager.configured_secret_fields(d.name),
                    }
                    for d in destinations
                ],
                indent=2,
            )
        )
        return 0

    if not destinations:
        logger.info("No backup destinations configured")
        return 0

    logger.header("Backup Destinations")
    for destination in destinations:
        marker = " [encrypted]" if destination.encrypted else ""
        logger.info(f"  {destination.name}: {destination.backend}{marker}")

    return 0


def _test_destination(*, logger: Logger, name: str) -> int:
    """
    Check that a backup destination can be reached.

    Args:
        logger: Logger to report through.
        name: Destination name.

    Returns:
        0 if the destination was reached, 1 otherwise.
    """
    from wasm.managers.backup_destinations import BackupDestinationManager

    try:
        result = BackupDestinationManager().test(name)
    except WASMError as exc:
        logger.error(f"Could not reach {name}: {exc}")
        return 1

    logger.success(f"Reached backup destination: {name}")
    for entry in result["entries"]:
        logger.info(f"  {entry}")
    return 0


def _remove_destination(*, logger: Logger, name: str, force: bool = False) -> int:
    """
    Remove a backup destination and its secrets.

    Args:
        logger: Logger to report through.
        name: Destination name.
        force: Do not ask for confirmation, and remove it even when a
            schedule still references it.

    Returns:
        0 on success, 1 if the destination could not be removed.
    """
    from wasm.managers.backup_destinations import BackupDestinationManager

    if not force and not click.confirm(
        f"Remove backup destination {name!r}? Backups already sent there are not deleted.",
        default=False,
    ):
        logger.info("Cancelled")
        return 0

    try:
        BackupDestinationManager().remove(name, force=force)
    except WASMError as exc:
        logger.error(f"Could not remove {name}: {exc}")
        return 1

    logger.success(f"Backup destination removed: {name}")
    return 0


def _show_destination_key(*, logger: Logger, name: str, json_output: bool = False) -> int:
    """
    Reveal a destination's encryption passphrases.

    Args:
        logger: Logger to report through.
        name: Destination name.
        json_output: Print the keys as JSON instead of a listing.

    Returns:
        0 on success, 1 if the destination is unknown or not encrypted.
    """
    from wasm.managers.backup_destinations import BackupDestinationManager

    try:
        keys = BackupDestinationManager().show_key(name)
    except WASMError as exc:
        logger.error(f"Could not show the key: {exc}")
        return 1

    if json_output:
        click.echo(json.dumps(keys, indent=2))
        return 0

    logger.warning(
        f"Encryption keys for {name}. Store them somewhere safe; they are not shown "
        "again automatically."
    )
    logger.info(f"  password:  {keys['password']}")
    logger.info(f"  password2: {keys['password2']}")
    return 0


def _push_backup(*, logger: Logger, backup_id: str, destination: str) -> int:
    """
    Upload a local backup to a remote destination.

    Args:
        logger: Logger to report through.
        backup_id: Backup to upload.
        destination: Destination to upload it to.

    Returns:
        0 on success, 1 if the backup or destination is unknown, or the
        upload failed.
    """
    from wasm.managers.backup_destinations import BackupDestinationManager

    try:
        manager = BackupManager(verbose=logger.verbose)
        metadata = manager.get_backup(backup_id)
        if not metadata:
            logger.error(f"Backup not found: {backup_id}")
            return 1
        summary = BackupDestinationManager().push(metadata, destination, backup_manager=manager)
    except WASMError as exc:
        logger.error(f"Upload failed: {exc}")
        return 1

    logger.success(f"Uploaded {backup_id} to {destination}")
    if summary.get("retention_deleted"):
        logger.info(f"  Removed by retention: {', '.join(summary['retention_deleted'])}")
    return 0


def _remote_list(
    *, logger: Logger, destination: str, app_name: str | None, json_output: bool = False
) -> int:
    """
    List what a remote backup destination holds.

    Args:
        logger: Logger to report through.
        destination: Destination to list.
        app_name: List this application's backups; without it, list the
            application directories found at the destination's own path.
        json_output: Print the listing as JSON instead of a table.

    Returns:
        0 on success, 1 if the destination could not be listed.
    """
    from wasm.managers.backup_destinations import BackupDestinationManager

    try:
        result = BackupDestinationManager().remote_list(destination, app_name)
    except WASMError as exc:
        logger.error(f"Could not list {destination}: {exc}")
        return 1

    if json_output:
        click.echo(json.dumps(result, indent=2))
        return 0

    if app_name is None:
        if not result["apps"]:
            logger.info(f"No applications found on {destination}")
            return 0
        logger.header(f"Applications on {destination}")
        for app in result["apps"]:
            logger.info(f"  {app}")
        return 0

    if not result["backups"]:
        logger.info(f"No backups found for {app_name} on {destination}")
        return 0

    logger.header(f"Backups for {app_name} on {destination}")
    for entry in result["backups"]:
        size = _human_bytes(entry["size"]) if entry.get("size") is not None else "?"
        logger.info(f"  {entry['backup_id']}: {size} (modified {entry.get('modified') or '?'})")
    return 0


def _restore_from_destination(
    *,
    logger: Logger,
    destination: str,
    backup_id: str,
    app_name: str | None,
    target_domain: str | None,
    restore_env: bool = True,
    force: bool = False,
) -> int:
    """
    Download a backup from a remote destination and restore it.

    Args:
        logger: Logger to report through.
        destination: Destination to download from.
        backup_id: Backup to restore.
        app_name: Application the backup belongs to on the destination;
            derived from the backup id when not given.
        target_domain: Restore into this domain instead of the one recorded
            in the downloaded backup.
        restore_env: Restore the ``.env`` files from the archive.
        force: Do not ask for confirmation.

    Returns:
        0 on success, 1 if the application cannot be determined, or the
        download or restore fails.
    """
    import tempfile

    from wasm.managers.backup_destinations import BackupDestinationManager
    from wasm.managers.backup_manager import BackupMetadata, app_name_of_backup_id

    resolved_app = app_name or app_name_of_backup_id(backup_id)
    if not resolved_app:
        logger.error(f"Cannot determine the application {backup_id!r} belongs to; pass --app.")
        return 1

    where = f" into {target_domain}" if target_domain else ""
    if not force and not click.confirm(
        f"Download {backup_id} from {destination} and restore it{where}. "
        "Anything deployed there now is overwritten. Continue?",
        default=False,
    ):
        logger.info("Cancelled")
        return 0

    try:
        destination_manager = BackupDestinationManager()
        backup_manager = BackupManager(verbose=logger.verbose)
        with tempfile.TemporaryDirectory(prefix="wasm-restore-remote-") as staging:
            staging_path = Path(staging)
            logger.step(1, 2, f"Downloading {backup_id} from {destination}")
            archive_path, metadata_path = destination_manager.download(
                destination, backup_id, resolved_app, staging_path
            )
            fallback = BackupMetadata.from_dict(json.loads(metadata_path.read_text()))
            target = target_domain or fallback.domain

            logger.step(2, 2, f"Restoring to {target}")
            backup_manager.restore_archive(
                archive_path,
                target_domain=target,
                restore_env=restore_env,
                expected_checksum=fallback.checksum,
                fallback=fallback,
            )
    except WASMError as exc:
        logger.error(f"Restore failed: {exc}")
        return 1

    logger.success(f"Successfully restored {target} from {backup_id} ({destination})")
    return 0


def _run_schedule(*, logger: Logger, domain: str) -> int:
    """
    Run an application's backup schedule now.

    What a scheduled timer's service unit runs: the local backup with the
    schedule's own retention, then a push to every configured destination.

    Args:
        logger: Logger to report through.
        domain: Application domain.

    Returns:
        0 when the local backup and every destination succeeded, 1 when the
        local backup failed or at least one destination could not be sent
        the backup (which is kept locally either way).
    """
    from wasm.managers.backup_scheduler import run_schedule

    try:
        result = run_schedule(domain, verbose=logger.verbose)
    except WASMError as exc:
        logger.error(f"Scheduled backup failed: {exc}")
        return 1

    logger.success(f"Backup created: {result['backup_id']}")
    failed = False
    for name, outcome in result.get("destinations", {}).items():
        if outcome.get("ok"):
            logger.info(f"  Sent to {name}")
        else:
            failed = True
            logger.warning(f"  Failed to send to {name}: {outcome.get('error')}")
    return 1 if failed else 0


def _print_backup_table(
    backups: Sequence[BackupMetadata], logger: Logger, indent: bool = False
) -> None:
    """
    Print one line per backup.

    Args:
        backups: Backups to print, newest first.
        logger: Logger to print through.
        indent: Indent the lines, for use under a domain heading.
    """
    prefix = "  " if indent else ""

    for backup in backups:
        tags_str = f" [{', '.join(backup.tags)}]" if backup.tags else ""
        commit_str = f" ({backup.git_commit})" if backup.git_commit else ""
        desc_str = f" - {backup.description}" if backup.description else ""

        logger.info(
            f"{prefix}- {backup.id}: {backup.size_human}, "
            f"{backup.age}{commit_str}{tags_str}{desc_str}"
        )


def _create_backup(
    *,
    logger: Logger,
    domain: str,
    description: str = "",
    include_env: bool = True,
    include_node_modules: bool = False,
    include_build: bool = False,
    include_databases: bool = False,
    include_docker_volumes: bool = False,
    schemas: Sequence[str] | None = None,
    redis_method: str = "rdb",
    retention_count: int | None = None,
    retention_days: int | None = None,
    tags: str | None = None,
) -> int:
    """
    Create a backup of an application and report what went into it.

    Args:
        logger: Logger to report through.
        domain: Domain of the application to back up.
        description: Note to store with the backup.
        include_env: Put the ``.env`` files in the archive.
        include_node_modules: Put ``node_modules`` in the archive.
        include_build: Put the build output in the archive.
        include_databases: Dump the application databases into the archive.
        include_docker_volumes: Copy the Docker volumes into the archive.
        schemas: PostgreSQL schemas to dump instead of whole databases.
        redis_method: How to capture Redis, "rdb" or "aof".
        retention_count: Keep at most this many backups of the application.
        retention_days: Delete backups older than this many days.
        tags: Comma-separated tags to file the backup under.

    Returns:
        0 on success, 1 if the backup could not be created.
    """
    try:
        manager = BackupManager(verbose=logger.verbose)

        logger.step(1, 2, f"Creating backup for {domain}")
        metadata = manager.create(
            domain=domain,
            description=description,
            include_env=include_env,
            include_node_modules=include_node_modules,
            include_build=include_build,
            include_databases=include_databases,
            include_docker_volumes=include_docker_volumes,
            schemas=list(schemas) if schemas else None,
            redis_method=redis_method,
            retention_count=retention_count,
            retention_days=retention_days,
            tags=_parse_tags(tags),
        )
    except WASMError as exc:
        logger.error(f"Backup failed: {exc}")
        return 1

    logger.step(2, 2, "Backup complete")
    logger.success(f"Created backup: {metadata.id}")
    logger.info(f"  Size: {metadata.size_human}")
    if metadata.git_commit:
        logger.info(f"  Commit: {metadata.git_commit} ({metadata.git_branch})")
    if metadata.database_backups:
        logger.info(f"  Databases: {len(metadata.database_backups)} dumped into the archive")
        for db_info in metadata.database_backups:
            size = _human_bytes(db_info.get("size_bytes", 0))
            logger.info(f"    - {db_info['engine']}/{db_info['name']} ({size})")
    if metadata.docker_volume_backups:
        logger.info(f"  Volumes: {len(metadata.docker_volume_backups)} copied into the archive")
        for volume in metadata.docker_volume_backups:
            logger.info(f"    - {volume.get('name', '?')}")

    return 0


def _list_backups(
    *,
    logger: Logger,
    domain: str | None = None,
    tags: str | None = None,
    limit: int | None = None,
    json_output: bool = False,
) -> int:
    """
    List the backups WASM knows about.

    Args:
        logger: Logger to report through.
        domain: Only list backups of this application.
        tags: Comma-separated tags to filter by.
        limit: Maximum number of backups to show.
        json_output: Print the backups as JSON instead of a table.

    Returns:
        0 on success, 1 if the backups could not be read.
    """
    try:
        manager = BackupManager(verbose=logger.verbose)
        backups = manager.list_backups(
            domain=domain,
            tags=_parse_tags(tags) or None,
            limit=limit,
        )
    except WASMError as exc:
        logger.error(f"Error listing backups: {exc}")
        return 1

    if json_output:
        click.echo(json.dumps([backup.to_dict() for backup in backups], indent=2))
        return 0

    if not backups:
        logger.info(f"No backups found for {domain}" if domain else "No backups found")
        return 0

    if domain:
        _print_backup_table(backups, logger)
        return 0

    by_domain: dict[str, list[BackupMetadata]] = {}
    for backup in backups:
        by_domain.setdefault(backup.domain, []).append(backup)

    for dom, dom_backups in by_domain.items():
        logger.info(f"\n[{dom}]")
        _print_backup_table(dom_backups, logger, indent=True)

    return 0


def _restore_backup(
    *,
    logger: Logger,
    backup_id: str,
    target_domain: str | None = None,
    restore_env: bool = True,
    verify: bool = True,
    force: bool = False,
) -> int:
    """
    Restore an application from a backup.

    Args:
        logger: Logger to report through.
        backup_id: Backup to restore.
        target_domain: Restore into this domain instead of the original one.
        restore_env: Restore the ``.env`` files from the archive.
        verify: Check the archive against its recorded checksum first.
        force: Do not ask for confirmation.

    Returns:
        0 on success, 1 if the restore failed or the backup is unknown.
    """
    try:
        manager = BackupManager(verbose=logger.verbose)

        metadata = manager.get_backup(backup_id)
        if not metadata:
            logger.error(f"Backup not found: {backup_id}")
            return 1

        target = target_domain or metadata.domain

        if not force and not click.confirm(
            f"Replace the files of {target} with backup {backup_id} "
            f"({metadata.size_human}, taken {metadata.age}). "
            "Anything deployed there now is overwritten. Continue?",
            default=False,
        ):
            logger.info("Cancelled")
            return 0

        logger.step(1, 3, "Verifying backup")
        if verify:
            verify_result = manager.verify(backup_id)
            if not verify_result["valid"]:
                logger.error("Backup verification failed:")
                for err in verify_result["errors"]:
                    logger.error(f"  - {err}")
                return 1

        logger.step(2, 3, f"Restoring to {target}")
        manager.restore(
            backup_id=backup_id,
            target_domain=target_domain,
            restore_env=restore_env,
            verify_checksum=verify,
        )
    except WASMError as exc:
        logger.error(f"Restore failed: {exc}")
        return 1

    logger.step(3, 3, "Restore complete")
    logger.success(f"Successfully restored {target} from {backup_id}")
    return 0


def _delete_backup(*, logger: Logger, backup_id: str, force: bool = False) -> int:
    """
    Delete a backup and its archive.

    Args:
        logger: Logger to report through.
        backup_id: Backup to delete.
        force: Do not ask for confirmation.

    Returns:
        0 on success, 1 if the backup is unknown or could not be deleted.
    """
    try:
        manager = BackupManager(verbose=logger.verbose)

        metadata = manager.get_backup(backup_id)
        if not metadata:
            logger.error(f"Backup not found: {backup_id}")
            return 1

        if not force and not click.confirm(
            f"Permanently delete backup {backup_id} of {metadata.domain} "
            f"({metadata.size_human}, taken {metadata.age}). "
            "The archive is removed from disk and cannot be recovered. Continue?",
            default=False,
        ):
            logger.info("Cancelled")
            return 0

        manager.delete(backup_id)
    except WASMError as exc:
        logger.error(f"Delete failed: {exc}")
        return 1

    logger.success(f"Deleted backup: {backup_id}")
    return 0


def _verify_backup(*, logger: Logger, backup_id: str) -> int:
    """
    Check that a backup archive is intact.

    Args:
        logger: Logger to report through.
        backup_id: Backup to verify.

    Returns:
        0 if the backup is valid, 1 otherwise.
    """
    try:
        manager = BackupManager(verbose=logger.verbose)

        logger.info(f"Verifying backup: {backup_id}")
        result = manager.verify(backup_id)
    except WASMError as exc:
        logger.error(f"Verification failed: {exc}")
        return 1

    if result["valid"]:
        logger.success("Backup is valid")
        if result.get("checksum_ok"):
            logger.info("  [OK] Checksum verified")
        if result.get("files_ok"):
            logger.info(f"  [OK] Archive valid ({result.get('file_count', '?')} files)")
    else:
        logger.error("Backup is invalid")
        for err in result["errors"]:
            logger.error(f"  [ERROR] {err}")

    for warn in result.get("warnings", []):
        logger.warning(f"  [WARN] {warn}")

    return 0 if result["valid"] else 1


def _show_backup(*, logger: Logger, backup_id: str, json_output: bool = False) -> int:
    """
    Show everything recorded about a backup.

    Args:
        logger: Logger to report through.
        backup_id: Backup to describe.
        json_output: Print the metadata as JSON instead of a listing.

    Returns:
        0 on success, 1 if the backup is unknown.
    """
    try:
        manager = BackupManager(verbose=logger.verbose)
        metadata = manager.get_backup(backup_id)
    except WASMError as exc:
        logger.error(f"Error: {exc}")
        return 1

    if not metadata:
        logger.error(f"Backup not found: {backup_id}")
        return 1

    if json_output:
        click.echo(json.dumps(metadata.to_dict(), indent=2))
        return 0

    logger.info(f"Backup: {metadata.id}")
    logger.info(f"  Domain:      {metadata.domain}")
    logger.info(f"  App Name:    {metadata.app_name}")
    logger.info(f"  App Type:    {metadata.app_type}")
    logger.info(f"  Size:        {metadata.size_human}")
    logger.info(f"  Created:     {metadata.created_at} ({metadata.age})")
    logger.info(f"  Format:      {metadata.version}")

    if metadata.description:
        logger.info(f"  Description: {metadata.description}")

    if metadata.git_commit:
        logger.info(f"  Git Commit:  {metadata.git_commit}")
        logger.info(f"  Git Branch:  {metadata.git_branch}")

    if metadata.tags:
        logger.info(f"  Tags:        {', '.join(metadata.tags)}")

    logger.info("  Archive contains:")
    logger.info(f"    - .env files:     {'Yes' if metadata.includes_env else 'No'}")
    logger.info(f"    - node_modules:   {'Yes' if metadata.includes_node_modules else 'No'}")
    logger.info(f"    - build output:   {'Yes' if metadata.includes_build else 'No'}")
    logger.info(f"    - databases:      {len(metadata.database_backups)}")
    logger.info(f"    - docker volumes: {len(metadata.docker_volume_backups)}")

    for db_info in metadata.database_backups:
        logger.info(f"        {db_info.get('engine', '?')}/{db_info.get('name', '?')}")

    if metadata.checksum:
        logger.info(f"  Checksum:    {metadata.checksum[:16]}...")

    return 0


def _show_storage(*, logger: Logger, json_output: bool = False) -> int:
    """
    Show how much disk the backups take.

    Args:
        logger: Logger to report through.
        json_output: Print the usage as JSON instead of a listing.

    Returns:
        0 on success, 1 if the usage could not be read.
    """
    try:
        manager = BackupManager(verbose=logger.verbose)
        usage = manager.get_storage_usage()
        misplaced = manager.find_misplaced_backups()
    except WASMError as exc:
        logger.error(f"Error: {exc}")
        return 1

    if json_output:
        usage["directory"] = str(manager.backup_dir)
        usage["misplaced"] = [
            {"directory": str(found.directory), "count": found.count} for found in misplaced
        ]
        click.echo(json.dumps(usage, indent=2))
        return 0

    logger.info("Backup Storage Usage")
    logger.info(f"  Directory: {manager.backup_dir}")
    logger.info(
        f"  Total: {_human_bytes(usage['total_size_bytes'])} ({usage['total_backups']} backups)"
    )
    logger.info("")

    for app_name, app_usage in usage["by_app"].items():
        logger.info(
            f"  {app_name}: {_human_bytes(app_usage['size_bytes'])} ({app_usage['count']} backups)"
        )

    for found in misplaced:
        logger.warning(
            f"{found.count} backup(s) found outside the backup directory, in {found.directory}"
        )
        logger.info(f"  Move them into {manager.backup_dir} with: {found.command}")

    return 0


def _import_backups(*, logger: Logger, source: str) -> int:
    """
    Move misplaced WASM backups into the configured backup directory.

    Args:
        logger: Logger to report through.
        source: Directory holding the misplaced ``<app>/`` backup directories.

    Returns:
        0 when everything that looked like a backup was moved (or would be,
        under ``--dry-run``), 1 when something was left behind or the import
        could not start.
    """
    try:
        manager = BackupManager(verbose=logger.verbose)
        report = manager.import_backups(Path(source))
    except WASMError as exc:
        logger.error(f"Import failed: {exc}")
        return 1

    verb = "Would move" if report.rehearsal else "Moved"
    for moved_from, moved_to in report.moved:
        logger.info(f"  {moved_from} -> {moved_to}")
    for left, reason in report.left:
        logger.warning(f"Left {left}: {reason}")

    if report.moved:
        logger.success(
            f"{verb} {len(report.moved)} backup(s) from {report.source} into {report.destination}"
        )
    else:
        logger.info(f"No backups to move from {report.source}")

    return 1 if report.left else 0


def _create_schedule(
    *,
    logger: Logger,
    domain: str,
    schedule: str = "daily",
    retention_count: int = 7,
    retention_days: int = 30,
    destinations: Sequence[str] = (),
    verb: str = "created",
) -> int:
    """
    Create or replace the systemd timer that backs an application up on its own.

    ``BackupScheduler.create_schedule`` is itself an upsert, so this is also
    how a schedule is changed - the schedule and destination update commands
    call this same helper.

    Args:
        logger: Logger to report through.
        domain: Domain of the application to back up.
        schedule: hourly, daily, weekly, monthly or a systemd OnCalendar value.
        retention_count: Keep at most this many local backups.
        retention_days: Delete local backups older than this many days.
        destinations: Raw ``NAME[:COUNT[:DAYS]]`` destination specs.
        verb: Past-tense verb for the success message.

    Returns:
        0 on success, 1 if the schedule could not be installed.
    """
    from wasm.core.utils import domain_to_app_name
    from wasm.managers.backup_scheduler import BackupSchedule, BackupScheduler

    try:
        parsed_destinations = [_parse_destination_spec(value) for value in destinations]
        scheduler = BackupScheduler(verbose=logger.verbose)
        backup_schedule = BackupSchedule(
            domain=domain,
            app_name=domain_to_app_name(domain),
            schedule=schedule,
            retention_count=retention_count,
            retention_days=retention_days,
            destinations=parsed_destinations,
        )
        scheduler.create_schedule(backup_schedule)
    except WASMError as exc:
        logger.error(f"Failed to save schedule: {exc}")
        return 1

    logger.success(f"Backup schedule {verb} for {domain}")
    logger.info(f"  Schedule: {backup_schedule.on_calendar}")
    logger.info(f"  Retention: {retention_count} backups / {retention_days} days")
    if parsed_destinations:
        logger.info(f"  Destinations: {', '.join(d['name'] for d in parsed_destinations)}")
    return 0


def _list_schedules(*, logger: Logger) -> int:
    """
    List the applications that back themselves up on a timer.

    Args:
        logger: Logger to report through.

    Returns:
        0 on success, 1 if the schedules could not be read.
    """
    from wasm.managers.backup_scheduler import BackupScheduler

    try:
        scheduler = BackupScheduler(verbose=logger.verbose)
        schedules = scheduler.list_schedules()
    except WASMError as exc:
        logger.error(f"Failed to list schedules: {exc}")
        return 1

    if not schedules:
        logger.info("No backup schedules found")
        return 0

    logger.header("Backup Schedules")
    for sched in schedules:
        retention = sched.get("retention_count") or "default"
        logger.info(
            f"  {sched['app_name']}: "
            f"next={sched.get('next_run', '?')} "
            f"last={sched.get('last_run', 'never')} "
            f"retention={retention}"
        )

    return 0


def _delete_schedule(*, logger: Logger, domain: str) -> int:
    """
    Stop backing an application up on a timer.

    Args:
        logger: Logger to report through.
        domain: Domain whose schedule is removed.

    Returns:
        0 on success, 1 if the schedule could not be removed.
    """
    from wasm.managers.backup_scheduler import BackupScheduler

    try:
        scheduler = BackupScheduler(verbose=logger.verbose)
        scheduler.remove_schedule(domain)
    except WASMError as exc:
        logger.error(f"Failed to remove schedule: {exc}")
        return 1

    logger.success(f"Backup schedule removed for {domain}")
    return 0


def _rollback_app(
    *,
    logger: Logger,
    domain: str,
    backup_id: str | None = None,
    rebuild: bool = True,
) -> int:
    """
    Roll an application back to a backup.

    ``RollbackManager.rollback`` takes its own safety backup of the current
    state before restoring, so the CLI and the panel both get one; this used
    to be a step only the CLI performed itself.

    Args:
        logger: Logger to report through.
        domain: Domain of the application to roll back.
        backup_id: Backup to return to, defaulting to the most recent one.
        rebuild: Rebuild the application after the files are back.

    Returns:
        0 on success, 1 if there is nothing to roll back to or the restore
        failed.
    """
    try:
        rollback_manager = RollbackManager(verbose=logger.verbose)

        if not backup_id:
            backups = rollback_manager.list_rollback_points(domain)
            if not backups:
                logger.error(f"No backups found for {domain}")
                return 1

            logger.info(f"Rolling back to latest backup: {backups[0].id}")
            logger.info(f"  Created: {backups[0].age}")
            if backups[0].description:
                logger.info(f"  Description: {backups[0].description}")

        logger.step(1, 2, "Restoring from backup")
        rollback_manager.rollback(domain=domain, backup_id=backup_id, rebuild=rebuild)
    except WASMError as exc:
        logger.error(f"Rollback failed: {exc}")
        return 1

    logger.step(2, 2, "Rollback complete")
    logger.success(f"Successfully rolled back {domain}")
    return 0


@click.group(cls=WasmGroup)
def cli() -> None:
    """
    Container for the commands this module defines.

    ``wasm.cli.app`` picks ``backup`` or ``rollback`` out of it by name; the
    group itself is never typed by anyone.
    """


@cli.group(
    "backup",
    cls=AliasedGroup,
    aliases=BACKUP_ALIASES,
    invoke_without_command=True,
)
@click.pass_context
def backup(ctx: click.Context) -> None:
    """
    Create, inspect and restore application backups.

    A backup is a single self-contained archive: the application directory
    plus, when you ask for them, its database dumps and Docker volumes. Run
    without an action to list the backups on this server.
    """
    if ctx.invoked_subcommand is not None:
        return

    # `wasm backup` has always listed the backups, and scripts rely on it.
    state = ctx.ensure_object(Context)
    _finish(_list_backups(logger=state.logger, json_output=state.json_output))


@backup.command("create")
@click.argument("domain")
@click.option("-m", "--description", default="", help="Note to store with the backup.")
@click.option("--no-env", is_flag=True, help="Leave the .env files out of the archive.")
@click.option(
    "--include-node-modules",
    is_flag=True,
    help="Include node_modules. The archive gets much bigger.",
)
@click.option(
    "--include-build",
    is_flag=True,
    help="Include the build output (.next, dist, build).",
)
@click.option(
    "--include-databases",
    "--include-db",
    "include_databases",
    is_flag=True,
    help="Dump the application databases into the archive.",
)
@click.option(
    "--include-docker-volumes",
    is_flag=True,
    help="Copy the application's Docker volumes into the archive.",
)
@click.option(
    "--schemas",
    metavar="SCHEMA",
    multiple=True,
    help="Dump only this PostgreSQL schema. Repeat for several.",
)
@click.option(
    "--redis-method",
    type=click.Choice(REDIS_METHODS),
    default="rdb",
    show_default=True,
    help="How to capture Redis: a point-in-time rdb or the aof log.",
)
@click.option(
    "--retention-count",
    type=click.INT,
    help="Keep at most this many backups of the application.",
)
@click.option(
    "--retention-days",
    type=click.INT,
    help="Delete backups of the application older than this many days.",
)
@click.option("-t", "--tags", help="Comma-separated tags to file the backup under.")
@pass_context
def backup_create(
    state: Context,
    domain: str,
    description: str,
    no_env: bool,
    include_node_modules: bool,
    include_build: bool,
    include_databases: bool,
    include_docker_volumes: bool,
    schemas: tuple[str, ...],
    redis_method: str,
    retention_count: int | None,
    retention_days: int | None,
    tags: str | None,
) -> None:
    """
    Back an application up into one restorable archive.

    The archive holds the application directory and, with --include-databases
    or --include-docker-volumes, its data as well, so it can be restored on a
    server that knows nothing about this one.
    """
    _finish(
        _create_backup(
            logger=state.logger,
            domain=domain,
            description=description,
            include_env=not no_env,
            include_node_modules=include_node_modules,
            include_build=include_build,
            include_databases=include_databases,
            include_docker_volumes=include_docker_volumes,
            schemas=schemas,
            redis_method=redis_method,
            retention_count=retention_count,
            retention_days=retention_days,
            tags=tags,
        )
    )


@backup.command("list")
@click.argument("domain", required=False)
@click.option("-t", "--tags", help="Only show backups carrying one of these tags.")
@click.option("-n", "--limit", type=click.INT, help="Show at most this many backups.")
@click.option(
    "--open",
    "open_panel",
    is_flag=True,
    help="Print the panel URL for the backup list, opening it if a display is available.",
)
@json_option("Print the backup list as JSON.")
@pass_context
def backup_list(
    state: Context,
    domain: str | None,
    tags: str | None,
    limit: int | None,
    open_panel: bool,
) -> None:
    """
    List the backups on this server, newest first.

    Give a domain to see only that application's backups.
    """
    code = _list_backups(
        logger=state.logger,
        domain=domain,
        tags=tags,
        limit=limit,
        json_output=state.json_output,
    )
    if open_panel and code == 0:
        open_in_panel("/backups", logger=state.logger)
    _finish(code)


@backup.command("restore")
@click.argument("backup_id")
@click.option(
    "--from",
    "from_destination",
    metavar="DESTINATION",
    help="Download the backup from this remote destination first, instead of using a local one.",
)
@click.option(
    "--app",
    "app_name",
    help="Application the backup belongs to on the destination (with --from); derived from "
    "the backup id when omitted.",
)
@click.option("--target-domain", help="Restore into this domain instead of the original one.")
@click.option("--no-env", is_flag=True, help="Keep the current .env files.")
@click.option("--no-verify", is_flag=True, help="Skip the checksum check before restoring.")
@click.option("-f", "--force", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def backup_restore(
    state: Context,
    backup_id: str,
    from_destination: str | None,
    app_name: str | None,
    target_domain: str | None,
    no_env: bool,
    no_verify: bool,
    force: bool,
) -> None:
    """
    Put an application back the way a backup left it.

    The files under the target domain are replaced by the ones in the archive.
    With --from, the backup is downloaded from a remote destination first.
    """
    if from_destination:
        _finish(
            _restore_from_destination(
                logger=state.logger,
                destination=from_destination,
                backup_id=backup_id,
                app_name=app_name,
                target_domain=target_domain,
                restore_env=not no_env,
                force=force,
            )
        )
        return

    _finish(
        _restore_backup(
            logger=state.logger,
            backup_id=backup_id,
            target_domain=target_domain,
            restore_env=not no_env,
            verify=not no_verify,
            force=force,
        )
    )


@backup.command("delete")
@click.argument("backup_id")
@click.option("-f", "-y", "--force", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def backup_delete(state: Context, backup_id: str, force: bool) -> None:
    """
    Delete a backup and remove its archive from disk.
    """
    _finish(_delete_backup(logger=state.logger, backup_id=backup_id, force=force))


@backup.command("verify")
@click.argument("backup_id")
@pass_context
def backup_verify(state: Context, backup_id: str) -> None:
    """
    Check that a backup archive is intact and restorable.

    Compares the archive against its recorded checksum and reads the contents
    without writing anything.
    """
    _finish(_verify_backup(logger=state.logger, backup_id=backup_id))


@backup.command("info")
@click.argument("backup_id")
@json_option("Print the backup's metadata as JSON.")
@pass_context
def backup_info(state: Context, backup_id: str) -> None:
    """
    Show what a backup contains and where it came from.
    """
    _finish(_show_backup(logger=state.logger, backup_id=backup_id, json_output=state.json_output))


@backup.command("storage")
@json_option("Print the storage usage as JSON.")
@pass_context
def backup_storage(state: Context) -> None:
    """
    Show how much disk the backups take, per application.
    """
    _finish(_show_storage(logger=state.logger, json_output=state.json_output))


@backup.command("import")
@click.argument("directory", type=click.Path(file_okay=False, resolve_path=True))
@global_flags
@pass_context
def backup_import(state: Context, directory: str) -> None:
    """
    Move WASM backups found in DIRECTORY into the backup directory.

    For backups written to the wrong place, such as /root/<app>/ or /<app>/
    while backup.directory was empty. Only complete backups move (the
    .tar.gz and its .json); nothing else in DIRECTORY is touched and nothing
    in the backup directory is overwritten. Use --dry-run to see what would
    move.
    """
    _finish(_import_backups(logger=state.logger, source=directory))


@backup.command("push")
@click.argument("backup_id")
@click.argument("destination")
@pass_context
def backup_push(state: Context, backup_id: str, destination: str) -> None:
    """
    Upload a local backup to a remote destination.

    Verifies the upload (size, and hash when the destination supports one)
    and applies no retention: retention is a schedule's own concern, applied
    by 'wasm backup run-schedule'.
    """
    _finish(_push_backup(logger=state.logger, backup_id=backup_id, destination=destination))


@backup.command("remote-list")
@click.argument("destination")
@click.option(
    "--app", "app_name", help="List this application's backups instead of the destination's."
)
@json_option("Print the listing as JSON.")
@pass_context
def backup_remote_list(state: Context, destination: str, app_name: str | None) -> None:
    """
    List what a remote backup destination holds.
    """
    _finish(
        _remote_list(
            logger=state.logger,
            destination=destination,
            app_name=app_name,
            json_output=state.json_output,
        )
    )


@backup.command("run-schedule", hidden=True)
@click.argument("domain")
@pass_context
def backup_run_schedule(state: Context, domain: str) -> None:
    """
    Run an application's backup schedule now (local backup, retention, destinations).

    This is what a scheduled timer's service unit runs. An operator wants
    'wasm backup create' or 'wasm backup schedule create' instead.
    """
    _finish(_run_schedule(logger=state.logger, domain=domain))


@backup.group("destination", cls=WasmGroup)
def backup_destination() -> None:
    """
    Manage remote backup destinations (rclone).
    """


@backup_destination.command("add")
@click.argument("name")
@click.option(
    "--type",
    "backend",
    required=True,
    help="Backend: sftp, smb, webdav, s3, b2, drive, onedrive, dropbox, pcloud, local.",
)
@click.option(
    "--field",
    "fields_raw",
    metavar="KEY=VALUE",
    multiple=True,
    help="A non-secret field, repeatable (host, user, port, path...).",
)
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    help="Read the backend's secret field (password, key or token) from standard input.",
)
@click.option(
    "--prompt",
    "from_prompt",
    is_flag=True,
    help="Prompt for the backend's secret field without echoing it, twice.",
)
@click.option(
    "--encrypt",
    is_flag=True,
    help="Wrap the remote in an rclone crypt backend; run 'show-key' once to keep a copy.",
)
@pass_context
def destination_add(
    state: Context,
    name: str,
    backend: str,
    fields_raw: tuple[str, ...],
    from_stdin: bool,
    from_prompt: bool,
    encrypt: bool,
) -> None:
    """
    Create a backup destination.

    Non-secret fields (host, user, port, region, path...) are given with
    repeated --field KEY=VALUE. The backend's one secret field (a password,
    an application key or an OAuth token) is read with --stdin or --prompt,
    never as a KEY=VALUE pair, since that would put it in this shell's
    history.
    """
    _finish(
        _add_destination(
            logger=state.logger,
            name=name,
            backend=backend,
            fields_raw=fields_raw,
            from_stdin=from_stdin,
            from_prompt=from_prompt,
            encrypted=encrypt,
        )
    )


@backup_destination.command("update")
@click.argument("name")
@click.option(
    "--field", "fields_raw", metavar="KEY=VALUE", multiple=True, help="A non-secret field."
)
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    help="Read the backend's secret field from standard input.",
)
@click.option(
    "--prompt", "from_prompt", is_flag=True, help="Prompt for the backend's secret field."
)
@click.option("--encrypt/--no-encrypt", "encrypt", default=None, help="Turn encryption on or off.")
@pass_context
def destination_update(
    state: Context,
    name: str,
    fields_raw: tuple[str, ...],
    from_stdin: bool,
    from_prompt: bool,
    encrypt: bool | None,
) -> None:
    """
    Change a backup destination's fields. A blank secret field keeps its stored value.
    """
    _finish(
        _update_destination(
            logger=state.logger,
            name=name,
            fields_raw=fields_raw,
            from_stdin=from_stdin,
            from_prompt=from_prompt,
            encrypted=encrypt,
        )
    )


@backup_destination.command("list")
@json_option("Print the destinations as JSON.")
@pass_context
def destination_list(state: Context) -> None:
    """
    List the configured backup destinations.
    """
    _finish(_list_destinations(logger=state.logger, json_output=state.json_output))


@backup_destination.command("test")
@click.argument("name")
@pass_context
def destination_test(state: Context, name: str) -> None:
    """
    Check that a backup destination can be reached.
    """
    _finish(_test_destination(logger=state.logger, name=name))


@backup_destination.command("remove")
@click.argument("name")
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Do not ask for confirmation, and remove it even if a schedule references it.",
)
@pass_context
def destination_remove(state: Context, name: str, force: bool) -> None:
    """
    Remove a backup destination and its secrets. Backups already sent there are kept.
    """
    _finish(_remove_destination(logger=state.logger, name=name, force=force))


@backup_destination.command("show-key")
@click.argument("name")
@json_option("Print the keys as JSON.")
@pass_context
def destination_show_key(state: Context, name: str) -> None:
    """
    Reveal a destination's encryption passphrases, for safekeeping.

    Losing them makes every backup on that destination unrecoverable.
    """
    _finish(_show_destination_key(logger=state.logger, name=name, json_output=state.json_output))


@backup.group("schedule", cls=AliasedGroup, aliases=SCHEDULE_ALIASES)
def backup_schedule() -> None:
    """
    Back applications up automatically on a timer.
    """


@backup_schedule.command("create")
@click.argument("domain")
@click.option(
    "--schedule",
    default="daily",
    show_default=True,
    help="hourly, daily, weekly, monthly, or a systemd OnCalendar expression.",
)
@click.option(
    "--retention-count",
    type=click.INT,
    default=7,
    show_default=True,
    help="Keep at most this many local backups of the application.",
)
@click.option(
    "--retention-days",
    type=click.INT,
    default=30,
    show_default=True,
    help="Delete local backups of the application older than this many days.",
)
@click.option(
    "--destination",
    "destinations",
    metavar="NAME[:COUNT[:DAYS]]",
    multiple=True,
    help="Push each backup to this remote destination, with its own retention. Repeatable.",
)
@pass_context
def schedule_create(
    state: Context,
    domain: str,
    schedule: str,
    retention_count: int,
    retention_days: int,
    destinations: tuple[str, ...],
) -> None:
    """
    Back an application up on a timer and drop the old backups.
    """
    _finish(
        _create_schedule(
            logger=state.logger,
            domain=domain,
            schedule=schedule,
            retention_count=retention_count,
            retention_days=retention_days,
            destinations=destinations,
            verb="created",
        )
    )


@backup_schedule.command("update")
@click.argument("domain")
@click.option(
    "--schedule",
    default="daily",
    show_default=True,
    help="hourly, daily, weekly, monthly, or a systemd OnCalendar expression.",
)
@click.option(
    "--retention-count",
    type=click.INT,
    default=7,
    show_default=True,
    help="Keep at most this many local backups of the application.",
)
@click.option(
    "--retention-days",
    type=click.INT,
    default=30,
    show_default=True,
    help="Delete local backups of the application older than this many days.",
)
@click.option(
    "--destination",
    "destinations",
    metavar="NAME[:COUNT[:DAYS]]",
    multiple=True,
    help="Push each backup to this remote destination, with its own retention. Repeatable.",
)
@pass_context
def schedule_update(
    state: Context,
    domain: str,
    schedule: str,
    retention_count: int,
    retention_days: int,
    destinations: tuple[str, ...],
) -> None:
    """
    Replace an application's backup schedule.

    Every option is given again, as with 'create': this replaces the whole
    schedule, it does not patch one field of it.
    """
    _finish(
        _create_schedule(
            logger=state.logger,
            domain=domain,
            schedule=schedule,
            retention_count=retention_count,
            retention_days=retention_days,
            destinations=destinations,
            verb="updated",
        )
    )


@backup_schedule.command("list")
@pass_context
def schedule_list(state: Context) -> None:
    """
    Show which applications back themselves up, and when they last did.
    """
    _finish(_list_schedules(logger=state.logger))


@backup_schedule.command("delete")
@click.argument("domain")
@pass_context
def schedule_delete(state: Context, domain: str) -> None:
    """
    Stop backing an application up automatically.

    The backups already taken are kept.
    """
    _finish(_delete_schedule(logger=state.logger, domain=domain))


@cli.command("rollback")
@click.argument("domain")
@click.argument("backup_id", required=False)
@click.option("--no-rebuild", is_flag=True, help="Do not rebuild after the files are back.")
@pass_context
def rollback(state: Context, domain: str, backup_id: str | None, no_rebuild: bool) -> None:
    """
    Return an application to its most recent backup.

    Takes a safety backup of the current state first, then restores. Name a
    backup id to go somewhere other than the latest one.
    """
    _finish(
        _rollback_app(
            logger=state.logger,
            domain=domain,
            backup_id=backup_id,
            rebuild=not no_rebuild,
        )
    )


def handle_backup(args: Namespace) -> int:
    """
    Handle ``wasm backup <action>`` on the argparse path.

    ``wasm.cli.parser`` is gone and nothing calls this in production; it is
    kept, and tested directly, sharing every helper with the Click commands
    rather than repeating them.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.
    """
    verbose = getattr(args, "verbose", False)
    logger = Logger(verbose=verbose)
    action = BACKUP_ALIASES.get(
        getattr(args, "action", None) or "list", getattr(args, "action", None) or "list"
    )

    if action == "create":
        return _create_backup(
            logger=logger,
            domain=getattr(args, "domain", ""),
            description=getattr(args, "description", ""),
            include_env=not getattr(args, "no_env", False),
            include_node_modules=getattr(args, "include_node_modules", False),
            include_build=getattr(args, "include_build", False),
            include_databases=getattr(args, "include_databases", False),
            include_docker_volumes=getattr(args, "include_docker_volumes", False),
            schemas=getattr(args, "schemas", None),
            redis_method=getattr(args, "redis_method", "rdb"),
            retention_count=getattr(args, "retention_count", None),
            retention_days=getattr(args, "retention_days", None),
            tags=getattr(args, "tags", None),
        )
    if action == "list":
        return _list_backups(
            logger=logger,
            domain=getattr(args, "domain", None),
            tags=getattr(args, "tags", None),
            limit=getattr(args, "limit", None),
            json_output=getattr(args, "json", False),
        )
    if action == "restore":
        return _restore_backup(
            logger=logger,
            backup_id=getattr(args, "backup_id", ""),
            target_domain=getattr(args, "target_domain", None),
            restore_env=not getattr(args, "no_env", False),
            verify=not getattr(args, "no_verify", False),
            force=getattr(args, "force", False),
        )
    if action == "delete":
        return _delete_backup(
            logger=logger,
            backup_id=getattr(args, "backup_id", ""),
            force=getattr(args, "force", False),
        )
    if action == "verify":
        return _verify_backup(logger=logger, backup_id=getattr(args, "backup_id", ""))
    if action == "info":
        return _show_backup(
            logger=logger,
            backup_id=getattr(args, "backup_id", ""),
            json_output=getattr(args, "json", False),
        )
    if action == "storage":
        return _show_storage(logger=logger, json_output=getattr(args, "json", False))
    if action == "import":
        return _import_backups(logger=logger, source=getattr(args, "directory", ""))
    if action == "schedule":
        return _handle_backup_schedule(args, logger)

    logger.error(f"Unknown backup action: {action}")
    return 1


def _handle_backup_schedule(args: Namespace, logger: Logger) -> int:
    """
    Handle ``wasm backup schedule <action>`` on the argparse path.

    Args:
        args: Parsed arguments.
        logger: Logger to report through.

    Returns:
        Process exit code.
    """
    raw_action = getattr(args, "schedule_action", None)
    if not raw_action:
        logger.error("Schedule requires an action: create, list, or delete")
        return 1

    action = SCHEDULE_ALIASES.get(raw_action, raw_action)

    if action == "create":
        return _create_schedule(
            logger=logger,
            domain=getattr(args, "domain", ""),
            schedule=getattr(args, "schedule", "daily"),
            retention_count=getattr(args, "retention_count", 7),
            retention_days=getattr(args, "retention_days", 30),
        )
    if action == "list":
        return _list_schedules(logger=logger)
    if action == "delete":
        return _delete_schedule(logger=logger, domain=getattr(args, "domain", ""))

    logger.error(f"Unknown schedule action: {action}")
    return 1


def handle_rollback(args: Namespace) -> int:
    """
    Handle ``wasm rollback`` on the argparse path.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.
    """
    verbose = getattr(args, "verbose", False)
    logger = Logger(verbose=verbose)

    domain = getattr(args, "domain", None)
    if not domain:
        logger.error("Domain is required")
        return 1

    return _rollback_app(
        logger=logger,
        domain=domain,
        backup_id=getattr(args, "backup_id", None),
        rebuild=not getattr(args, "no_rebuild", False),
    )
