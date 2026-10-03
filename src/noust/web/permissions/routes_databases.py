# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/databases`` (:mod:`noust.web.api.databases`).

A query that writes also needs sudo mode, asked by the handler because only
the body says whether it writes. So does the permission: ``POST
/api/databases/query`` and ``/query/explain`` are mapped to
``databases.read``, what a read needs, and the handler asks a write (or
``EXPLAIN ANALYZE``) for ``databases.write`` too, and for ``root_equivalent``
under the ``ens-medium`` profile, because it runs as the engine's superuser.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/databases/engines"): Permission.DATABASES_READ,
    ("GET", "/api/databases/engines/{engine}/status"): Permission.DATABASES_READ,
    ("GET", "/api/databases/engines/{engine}/privileges"): Permission.DATABASES_READ,
    # A container's log also needs databases.manage, asked by the handler:
    # only the engine says whether it names a container.
    ("GET", "/api/databases/engines/{engine}/logs"): Permission.DATABASES_READ,
    # 3.3 (item 71): what can be installed here, and each engine's settings.
    ("GET", "/api/databases/engines/catalog"): Permission.DATABASES_READ,
    ("GET", "/api/databases/engines/{engine}/settings"): Permission.DATABASES_READ,
    ("PUT", "/api/databases/engines/{engine}/settings"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/engines/{engine}/install"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/engines/{engine}/uninstall"): Permission.DATABASES_MANAGE,
    ("PUT", "/api/databases/engines/{engine}/credentials"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/engines/{engine}/start"): Permission.APPS_OPERATE,
    ("POST", "/api/databases/engines/{engine}/stop"): Permission.APPS_OPERATE,
    ("POST", "/api/databases/engines/{engine}/restart"): Permission.APPS_OPERATE,
    ("GET", "/api/databases/databases"): Permission.DATABASES_READ,
    ("POST", "/api/databases/databases"): Permission.DATABASES_WRITE,
    ("GET", "/api/databases/databases/{engine}/{name}"): Permission.DATABASES_READ,
    ("DELETE", "/api/databases/databases/{engine}/{name}"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/users"): Permission.DATABASES_WRITE,
    ("POST", "/api/databases/users/grant"): Permission.DATABASES_WRITE,
    ("POST", "/api/databases/users/revoke"): Permission.DATABASES_WRITE,
    ("GET", "/api/databases/users/{engine}"): Permission.DATABASES_READ,
    ("DELETE", "/api/databases/users/{engine}/{username}"): Permission.DATABASES_MANAGE,
    ("GET", "/api/databases/backups"): Permission.DATABASES_READ,
    ("POST", "/api/databases/backups"): Permission.BACKUPS_RUN,
    ("POST", "/api/databases/backups/restore"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/query"): Permission.DATABASES_READ,
    ("POST", "/api/databases/connection-string"): Permission.SECRETS_REVEAL,
    # 3.1 (B6a): one service behind the CLI and the API; per-application links.
    ("GET", "/api/databases/engines/{engine}/exposure"): Permission.DATABASES_READ,
    ("GET", "/api/databases/exposure"): Permission.DATABASES_READ,
    ("POST", "/api/databases/databases/adopt"): Permission.DATABASES_WRITE,
    ("POST", "/api/databases/databases/{engine}/{name}/links/detected"): Permission.DATABASES_WRITE,
    ("GET", "/api/databases/provisioning/plan"): Permission.DATABASES_READ,
    ("GET", "/api/databases/databases/{engine}/{name}/overview"): Permission.DATABASES_READ,
    ("POST", "/api/databases/databases/{engine}/{name}/forget"): Permission.DATABASES_MANAGE,
    ("GET", "/api/databases/databases/{engine}/{name}/fix-owner"): Permission.DATABASES_READ,
    ("POST", "/api/databases/databases/{engine}/{name}/fix-owner"): Permission.DATABASES_MANAGE,
    ("GET", "/api/databases/databases/{engine}/{name}/access"): Permission.DATABASES_READ,
    (
        "PUT",
        "/api/databases/databases/{engine}/{name}/access/{username}",
    ): Permission.DATABASES_WRITE,
    ("GET", "/api/databases/databases/{engine}/{name}/connect"): Permission.DATABASES_READ,
    ("POST", "/api/databases/users/{engine}/{username}/password"): Permission.DATABASES_MANAGE,
    (
        "POST",
        "/api/databases/users/{engine}/{username}/password/reveal",
    ): Permission.SECRETS_REVEAL,
    ("GET", "/api/apps/{domain}/databases"): Permission.DATABASES_READ,
    ("POST", "/api/apps/{domain}/databases"): Permission.DATABASES_WRITE,
    ("POST", "/api/apps/{domain}/databases/link"): Permission.DATABASES_WRITE,
    ("DELETE", "/api/apps/{domain}/databases/{engine}/{name}"): Permission.DATABASES_MANAGE,
    ("POST", "/api/apps/{domain}/databases/{engine}/{name}/url"): Permission.SECRETS_REVEAL,
    # 3.1 (B6b): backup policies, verification, offsite copies, downloads.
    ("GET", "/api/databases/backup-policies"): Permission.BACKUPS_READ,
    ("GET", "/api/databases/backup-policies/{engine}/{database}"): Permission.BACKUPS_READ,
    ("PUT", "/api/databases/backup-policies/{engine}/{database}"): Permission.BACKUPS_MANAGE,
    ("DELETE", "/api/databases/backup-policies/{engine}/{database}"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/databases/backup-policies/{engine}/{database}/run"): Permission.BACKUPS_RUN,
    ("POST", "/api/databases/backups/{name}/verify"): Permission.BACKUPS_RUN,
    ("POST", "/api/databases/backups/{name}/push"): Permission.BACKUPS_MANAGE,
    ("GET", "/api/databases/backups/{name}/download"): Permission.DATABASES_MANAGE,
    ("DELETE", "/api/databases/backups/{name}"): Permission.BACKUPS_MANAGE,
    ("GET", "/api/databases/backups/remote"): Permission.BACKUPS_READ,
    ("GET", "/api/databases/backups/remote/databases"): Permission.BACKUPS_READ,
    ("GET", "/api/databases/backups/suggest-name"): Permission.DATABASES_READ,
    ("POST", "/api/databases/backups/restore-remote"): Permission.DATABASES_MANAGE,
    # 3.1 (B6c): the data explorer, the row editor, the console's second
    # version, metrics. Reads are databases.read; a row edit is databases.write
    # and sudo mode (require_elevated).
    ("GET", "/api/databases/databases/{engine}/{name}/schemas"): Permission.DATABASES_READ,
    ("GET", "/api/databases/databases/{engine}/{name}/relations"): Permission.DATABASES_READ,
    ("GET", "/api/databases/databases/{engine}/{name}/relation"): Permission.DATABASES_READ,
    ("GET", "/api/databases/databases/{engine}/{name}/rows"): Permission.DATABASES_READ,
    ("POST", "/api/databases/databases/{engine}/{name}/rows"): Permission.DATABASES_WRITE,
    ("PATCH", "/api/databases/databases/{engine}/{name}/rows"): Permission.DATABASES_WRITE,
    ("POST", "/api/databases/databases/{engine}/{name}/rows/delete"): Permission.DATABASES_WRITE,
    ("GET", "/api/databases/databases/{engine}/{name}/keys"): Permission.DATABASES_READ,
    ("GET", "/api/databases/databases/{engine}/{name}/key"): Permission.DATABASES_READ,
    ("POST", "/api/databases/query/explain"): Permission.DATABASES_READ,
    ("POST", "/api/databases/query/export"): Permission.DATABASES_READ,
    ("GET", "/api/databases/console/history"): Permission.DATABASES_READ,
    ("DELETE", "/api/databases/console/history"): Permission.DATABASES_READ,
    ("GET", "/api/databases/console/saved"): Permission.DATABASES_READ,
    ("POST", "/api/databases/console/saved"): Permission.DATABASES_READ,
    ("PUT", "/api/databases/console/saved/{saved_id}"): Permission.DATABASES_READ,
    ("DELETE", "/api/databases/console/saved/{saved_id}"): Permission.DATABASES_READ,
    ("GET", "/api/databases/engines/{engine}/metrics"): Permission.DATABASES_READ,
    ("GET", "/api/databases/databases/{engine}/{name}/metrics"): Permission.DATABASES_READ,
    ("GET", "/api/databases/databases/{engine}/{name}/slow-queries"): Permission.DATABASES_READ,
}
