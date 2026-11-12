# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every permission there is, and what each one means.

The catalogue sits in ``core`` and not with the routes because the roles do: a role is rows in the
store (:mod:`noust.core.access.roles`), seeded from the permissions that exist, and the code that
keeps them (accounts, the compliance report, the ENS review) is below the console. The names are
an interface that other areas of the product and a central's forwarded requests rely on: add new
ones, never rename one. :mod:`noust.web.permissions` re-exports everything here, so the routes
keep importing it from where they always did.
"""

from __future__ import annotations


class Permission:
    """
    Every permission, as the string it travels as.

    Grouped by what they govern. ``SELF`` is what every signed-in principal
    holds: its own session, second factor, password, tokens and notice.
    """

    SELF = "self"

    APPS_READ = "apps.read"
    APPS_OPERATE = "apps.operate"
    APPS_DEPLOY = "apps.deploy"
    APPS_MANAGE = "apps.manage"
    APPS_TERMINAL = "apps.terminal"

    SECRETS_REVEAL = "secrets.reveal"
    ROOT_EQUIVALENT = "root_equivalent"

    SERVER_READ = "server.read"
    SERVER_MANAGE = "server.manage"
    SERVER_HOST_ACCESS = "server.host_access"
    SERVER_TERMINAL = "server.terminal"

    # Docker is the machine's: what a container, an image, a volume or a network is, whoever made
    # it, and the few things that change one (4.3, item 189).
    DOCKER_READ = "docker.read"
    DOCKER_MANAGE = "docker.manage"

    DATABASES_READ = "databases.read"
    DATABASES_WRITE = "databases.write"
    DATABASES_MANAGE = "databases.manage"

    BACKUPS_READ = "backups.read"
    BACKUPS_RUN = "backups.run"
    BACKUPS_MANAGE = "backups.manage"

    FLEET_READ = "fleet.read"
    FLEET_MANAGE = "fleet.manage"

    SETTINGS_READ = "settings.read"
    SETTINGS_MANAGE = "settings.manage"
    SECURITY_MANAGE = "security.manage"

    ACCOUNTS_READ = "accounts.read"
    ACCOUNTS_MANAGE = "accounts.manage"

    AUDIT_READ = "audit.read"
    AUDIT_MANAGE = "audit.manage"
    COMPLIANCE_READ = "compliance.read"

    TEAMS_READ = "teams.read"
    TEAMS_MANAGE = "teams.manage"


#: Every permission there is.
ALL_PERMISSIONS: frozenset[str] = frozenset(
    value
    for name, value in vars(Permission).items()
    if not name.startswith("_") and isinstance(value, str)
)

#: What a route that needs no credential is mapped to: the sign-in, the
#: invitation pages, ``/health``, the console's static shell and the forge
#: webhooks, which verify their own signature. Not a permission: nothing
#: holds it, and nothing needs to.
PUBLIC = "public"

#: Human-readable description of each permission, for the console and --help.
DESCRIPTIONS: dict[str, str] = {
    Permission.SELF: "Own session, second factor, password, tokens and usage notice",
    Permission.APPS_READ: "See applications, sites, certificates, jobs, logs and deployments",
    Permission.APPS_OPERATE: "Start, stop and restart; renew certificates; run cron jobs",
    Permission.APPS_DEPLOY: "Update, roll back, activate a release, rebuild",
    Permission.APPS_MANAGE: "Create, configure and delete applications, sites and domains",
    Permission.APPS_TERMINAL: (
        "A terminal and one-off commands as an application's own account, with its secrets"
    ),
    Permission.SECRETS_REVEAL: "Read secrets in clear: .env values, exports, keys",
    Permission.ROOT_EQUIVALENT: "Raw units, cron commands, backup hooks, raw site configuration",
    Permission.SERVER_READ: "See the server: system, services, metrics, monitor",
    Permission.SERVER_MANAGE: "Manage the server: services, the monitor",
    Permission.SERVER_HOST_ACCESS: "Change the host itself: SSH, firewall, packages",
    Permission.SERVER_TERMINAL: "A root shell on the server itself, in the browser",
    Permission.DOCKER_READ: (
        "See everything Docker has on the server and whose it is: containers, images, volumes, "
        "networks, their logs and consumption"
    ),
    Permission.DOCKER_MANAGE: (
        "Start, stop, restart and remove what Docker has, open a console in a container and "
        "clean up Docker, for what no application or database engine owns"
    ),
    Permission.DATABASES_READ: "See database engines, databases and users",
    Permission.DATABASES_WRITE: "Create databases and users, run queries",
    Permission.DATABASES_MANAGE: "Install engines, drop databases, restore dumps",
    Permission.BACKUPS_READ: "See backups, schedules and destinations",
    Permission.BACKUPS_RUN: "Create and verify backups",
    Permission.BACKUPS_MANAGE: "Restore, delete, and configure destinations",
    Permission.FLEET_READ: "See the servers this central manages",
    Permission.FLEET_MANAGE: "Add and remove servers",
    Permission.SETTINGS_READ: "Read the configuration, secrets redacted",
    Permission.SETTINGS_MANAGE: "Change the configuration outside security settings",
    Permission.SECURITY_MANAGE: "Change security settings: console access, sign-in policy",
    Permission.ACCOUNTS_READ: "See accounts and separation-of-duties exceptions",
    Permission.ACCOUNTS_MANAGE: "Create, change, lock and remove accounts; others' sessions",
    Permission.AUDIT_READ: "Read the whole audit log",
    Permission.AUDIT_MANAGE: "Manage the audit log and activity history",
    Permission.COMPLIANCE_READ: "Read the ENS compliance check and its evidence report",
    Permission.TEAMS_READ: "See the teams, projects and environments one belongs to",
    Permission.TEAMS_MANAGE: "Create and delete teams, projects and environments, and their members",
}

__all__ = ["ALL_PERMISSIONS", "DESCRIPTIONS", "PUBLIC", "Permission"]
