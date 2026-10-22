# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The commands of the runtime area under ``noust app`` (3.4), gathered in one place.

``noust app`` mounts each of these once, so the area adds its commands without
touching the shared group's list in more than one line.
"""

from __future__ import annotations

import click

from noust.cli.commands.app_deploylog import deploy_log_command
from noust.cli.commands.app_disk import disk_command
from noust.cli.commands.app_files import files_group
from noust.cli.commands.app_hardening import hardening_group
from noust.cli.commands.app_mount import mount_group
from noust.cli.commands.app_pending import pending_command
from noust.cli.commands.app_process import process_group
from noust.cli.commands.app_replicas import replicas_command
from noust.cli.commands.app_replication import replication_command
from noust.cli.commands.app_tuning import tuning_group

#: Every command and group the area adds to ``noust app``.
COMMANDS: tuple[click.Command, ...] = (
    deploy_log_command,
    disk_command,
    files_group,
    hardening_group,
    mount_group,
    pending_command,
    process_group,
    replicas_command,
    replication_command,
    tuning_group,
)
