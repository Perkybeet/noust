# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What each operation of the API is: a read, a change, or something that asks for a person (item 184).

The approval policy for a caller that is not a person (:mod:`noust.core.caller_policy`) and the built-in
assistant's three classes of action (item 171) need one answer to "what kind of operation is this", and
it is computed here, from what the API already publishes about each operation and never from the verb: a
``POST`` of ``databases.read`` runs a ``SELECT`` or plans a change and writes nothing, and a ``GET`` of
``secrets.reveal`` is a read of the most sensitive thing there is, guarded by its permission.

The classes:

- ``read``: changes nothing on the server. Reading is not approved.
- ``action``: changes something, with the permission of a token. This is what the policy table is about.
- ``strong``: an ``action`` that asks for sudo mode (``x-noust-requires-elevation``) or for a second
  person every time (``x-noust-requires-approval`` that always applies, and ``root_equivalent``). A
  person does it from the console, in their own session; a caller that is not a person is never given it
  by a table.
- ``own``: the caller's own session, second factor, password and tokens. Outside the table.
- ``public``: needs no credential (sign-in, the forge webhooks), so there is no caller to judge.

The three inputs of a class are the three things the schema already says (``x-noust-permission``,
``x-noust-requires-elevation``, ``x-noust-requires-approval``), so the console, a client and the assistant
read the same, and :func:`classify_parts` is also what the guard of the API calls with the very same
values: there is one definition (rule 3). A permission and an operation nobody classified fail
``tests/test_operation_classes.py`` before they are served.

A few read permissions have a ``POST`` that only reads (a plan, a preview, a query). Those are listed in
:data:`READS_BY_POST` with their reason, because a ``POST`` of a read permission that is not on the list is
not assumed to be harmless: that would be the verb deciding, with the permission as an afterthought.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.permission_catalog import ALL_PERMISSIONS, PUBLIC, Permission

READ = "read"
ACTION = "action"
STRONG = "strong"
OWN = "own"
PUBLIC_CLASS = "public"

#: Every class, from the one that changes nothing to the one a person has to do.
CLASSES: tuple[str, ...] = (READ, ACTION, STRONG, OWN, PUBLIC_CLASS)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"})

#: What the API publishes about an operation, which is everything a class is computed from.
PERMISSION_EXTENSION = "x-noust-permission"
ELEVATION_EXTENSION = "x-noust-requires-elevation"
APPROVAL_EXTENSION = "x-noust-requires-approval"

#: What a permission that is only about reading is called.
READ_PERMISSIONS: frozenset[str] = frozenset(
    name for name in ALL_PERMISSIONS if name.endswith(".read")
)

#: The permissions that change something: the rows of the approval policy. A new permission is one or
#: the other, and the test that walks the catalog fails until it is listed here or among the neutral ones.
CHANGE_PERMISSIONS: frozenset[str] = frozenset(
    {
        Permission.APPS_OPERATE,
        Permission.APPS_DEPLOY,
        Permission.APPS_MANAGE,
        Permission.APPS_TERMINAL,
        Permission.SECRETS_REVEAL,
        Permission.ROOT_EQUIVALENT,
        Permission.SERVER_MANAGE,
        Permission.SERVER_HOST_ACCESS,
        Permission.SERVER_TERMINAL,
        Permission.DOCKER_MANAGE,
        Permission.DATABASES_WRITE,
        Permission.DATABASES_MANAGE,
        Permission.BACKUPS_RUN,
        Permission.BACKUPS_MANAGE,
        # Not a ``.read`` on purpose (see the catalogue): a central takes the copies away with it.
        Permission.BACKUPS_COLLECT,
        Permission.FLEET_MANAGE,
        Permission.SETTINGS_MANAGE,
        Permission.SECURITY_MANAGE,
        Permission.ACCOUNTS_MANAGE,
        Permission.AUDIT_MANAGE,
        Permission.TEAMS_MANAGE,
    }
)

#: The rows where most of what a permission lets one do asks for sudo mode or for a second person every
#: time (the key of the policy's table: it asks you to confirm it is you). ``alone`` is never offered there.
#: Listed, and pinned by ``tests/test_operation_classes.py`` to the rule that makes it (more than half of the
#: permission's operations are ``strong``), so a route that moves a permission across the line fails a test
#: instead of changing the table behind anyone's back.
SUDO_ROWS: frozenset[str] = frozenset(
    {
        Permission.ACCOUNTS_MANAGE,
        Permission.APPS_MANAGE,
        Permission.APPS_TERMINAL,
        Permission.BACKUPS_MANAGE,
        Permission.DATABASES_MANAGE,
        Permission.DOCKER_MANAGE,
        Permission.FLEET_MANAGE,
        Permission.ROOT_EQUIVALENT,
        Permission.SECRETS_REVEAL,
        Permission.SECURITY_MANAGE,
        Permission.SERVER_HOST_ACCESS,
        Permission.SERVER_MANAGE,
        Permission.SERVER_TERMINAL,
        Permission.SETTINGS_MANAGE,
        Permission.TEAMS_MANAGE,
    }
)

#: Permissions that are neither a read nor a change of the server: one's own business, and the right to
#: use the built-in assistant (which spends money and sends data out, decided by its own settings).
NEUTRAL_PERMISSIONS: frozenset[str] = frozenset({Permission.SELF, "assistant.use"})

#: A ``POST``, ``PUT`` or ``DELETE`` of a read permission that changes nothing on the server, with why.
#: Anything of a read permission that is not here is not classified (and fails the test), instead of
#: being taken for a read because of the permission it needs.
READS_BY_POST: dict[tuple[str, str], str] = {
    ("POST", "/api/audit/reviews"): (
        "Records that the log was reviewed (ENS evidence): it writes one audit event and changes nothing else."
    ),
    ("POST", "/api/cron/preview"): "Says when a schedule would run; nothing is created.",
    ("DELETE", "/api/databases/console/history"): (
        "Clears the console's own history of one's queries, which is not the server's."
    ),
    (
        "POST",
        "/api/databases/console/saved",
    ): "Saves a query in the console's own list, not on the server.",
    ("DELETE", "/api/databases/console/saved/{saved_id}"): (
        "Removes a query from the console's own list, not from the server."
    ),
    ("PUT", "/api/databases/console/saved/{saved_id}"): (
        "Edits a query in the console's own list, not on the server."
    ),
    (
        "POST",
        "/api/databases/engines/{engine}/tls/plan",
    ): "Plans a change and says what it would do.",
    ("POST", "/api/databases/instances/migrate/plan"): "Plans a change and says what it would do.",
    ("POST", "/api/databases/instances/plan"): "Plans a change and says what it would do.",
    (
        "POST",
        "/api/databases/instances/{name}/launch/plan",
    ): "Plans a change and says what it would do.",
    (
        "POST",
        "/api/databases/instances/{name}/tls/plan",
    ): "Plans a change and says what it would do.",
    (
        "POST",
        "/api/databases/instances/{name}/tuning/plan",
    ): "Plans a change and says what it would do.",
    ("POST", "/api/databases/moves/plan"): "Plans a change and says what it would do.",
    ("POST", "/api/databases/pitr/enable/plan"): "Plans a change and says what it would do.",
    ("POST", "/api/databases/pitr/restore/plan"): "Plans a change and says what it would do.",
    ("POST", "/api/databases/public/plan"): "Plans a change and says what it would do.",
    ("POST", "/api/databases/replicas/{engine}/plan"): "Plans a change and says what it would do.",
    ("POST", "/api/databases/query"): (
        "Runs a statement in read mode, where the database server itself refuses a write; a body that asks "
        "for write mode is judged by the permission of writing (see ESCALATIONS)."
    ),
    ("POST", "/api/databases/query/explain"): (
        "Explains a statement without running it; ANALYZE runs it, and is judged by the permission of "
        "writing (see ESCALATIONS)."
    ),
    ("POST", "/api/databases/query/export"): "Exports the rows a read-mode statement returns.",
    (
        "POST",
        "/api/manifest/plan",
    ): "Plans a manifest against the server and says what would change.",
    ("POST", "/api/nodes/{node}/test"): "Tries the connection to a server of the fleet.",
    ("POST", "/api/server/security/checks/refresh"): "Runs the read-only security checks again.",
    ("POST", "/api/server/storage/analyze"): "Measures what the disk holds; nothing is removed.",
    ("POST", "/api/sites/{domain}/config/edit"): (
        "Computes the text a visual edit produces; it is saved by the one PUT of the configuration."
    ),
    ("POST", "/api/sites/{domain}/route"): "Says which location of a site would answer a request.",
    ("POST", "/api/sites/{domain}/structure"): "Parses a site's configuration into its structure.",
    ("POST", "/api/streams/plan"): "Plans a change and says what it would do.",
}

#: A route of a read permission whose body can make it a write is judged, when it does, by the permission
#: of the write. Keyed by the four-eyes rule that spots that body (:mod:`noust.core.accounts.approvals`).
ESCALATIONS: dict[str, str] = {
    "db.query.write": Permission.DATABASES_WRITE,
    "db.query.analyze": Permission.DATABASES_WRITE,
}


class Unclassified(ValueError):
    """An operation, or a permission, that nobody decided what kind it is."""


class UnclassifiedOperations(Unclassified):
    """
    Operations of a schema that could not be classified.

    Attributes:
        operations: ``METHOD path`` of each one, with why.
    """

    def __init__(self, operations: list[str]) -> None:
        """
        Args:
            operations: One line for each operation, with the reason.
        """
        super().__init__(
            "Operations nobody classified; list the read ones in READS_BY_POST of "
            "noust/core/operation_classes.py, or give the route a permission:\n  "
            + "\n  ".join(operations)
        )
        self.operations = operations


@dataclass(frozen=True)
class OperationClass:
    """
    What one operation is.

    Attributes:
        method: The HTTP method, upper case.
        path: The route's template.
        permission: What it needs (``x-noust-permission``).
        kind: One of :data:`CLASSES`.
        elevation: It asks for sudo mode.
        approval: When a second person is asked (``always``, or the condition on the body), or None.
    """

    method: str
    path: str
    permission: str
    kind: str
    elevation: bool = False
    approval: str | None = None

    @property
    def changes(self) -> bool:
        """Whether it changes something on the server."""
        return self.kind in (ACTION, STRONG)

    @property
    def strong(self) -> bool:
        """Whether a person has to do it, in their own session."""
        return self.kind == STRONG


def classify_parts(
    method: str,
    path: str,
    permission: str | None,
    *,
    elevation: bool = False,
    approval: str | None = None,
) -> OperationClass:
    """
    Classify one operation from the three things the API says of it.

    Args:
        method: The HTTP method.
        path: The route's template.
        permission: Its ``x-noust-permission``.
        elevation: Whether it asks for sudo mode.
        approval: When a four-eyes rule covers it: ``always`` or the condition on the body; None when none.

    Returns:
        The operation's class.

    Raises:
        Unclassified: When the permission is missing or unknown, or the operation is a ``POST``, ``PUT``
            or ``DELETE`` of a read permission that nobody listed as a read.
    """
    verb = method.upper()
    if verb not in HTTP_METHODS:
        raise Unclassified(f"{verb} {path}: not an HTTP method of the API")
    if permission is None:
        raise Unclassified(f"{verb} {path}: it publishes no permission")
    held = elevation or approval == "always"
    if permission == PUBLIC:
        return OperationClass(verb, path, permission, PUBLIC_CLASS)
    if permission not in ALL_PERMISSIONS and permission not in NEUTRAL_PERMISSIONS:
        raise Unclassified(f"{verb} {path}: {permission!r} is not a permission")
    if verb in SAFE_METHODS:
        return OperationClass(verb, path, permission, READ, elevation, approval)
    if permission in NEUTRAL_PERMISSIONS:
        return OperationClass(verb, path, permission, OWN, elevation, approval)
    if permission in READ_PERMISSIONS:
        if (verb, path) in READS_BY_POST:
            return OperationClass(verb, path, permission, READ, elevation, approval)
        raise Unclassified(
            f"{verb} {path}: changes something under a read permission, or is a read nobody listed"
        )
    if permission not in CHANGE_PERMISSIONS:
        raise Unclassified(f"{verb} {path}: {permission!r} is in no list of operation_classes")
    return OperationClass(verb, path, permission, STRONG if held else ACTION, elevation, approval)


def classify(method: str, path: str, operation: Mapping[str, Any]) -> OperationClass:
    """
    Classify one operation of the OpenAPI document.

    Args:
        method: The HTTP method.
        path: The route's template.
        operation: The operation object, with the extensions the API adds.

    Returns:
        The operation's class.

    Raises:
        Unclassified: See :func:`classify_parts`.
    """
    requirement = operation.get(APPROVAL_EXTENSION)
    when = requirement.get("when") if isinstance(requirement, Mapping) else None
    return classify_parts(
        method,
        path,
        operation.get(PERMISSION_EXTENSION),
        elevation=bool(operation.get(ELEVATION_EXTENSION)),
        approval=str(when) if when is not None else None,
    )


def classify_schema(schema: Mapping[str, Any]) -> dict[tuple[str, str], OperationClass]:
    """
    Classify every operation of the OpenAPI document.

    Args:
        schema: ``app.openapi()``.

    Returns:
        ``(METHOD, path)`` to its class.

    Raises:
        UnclassifiedOperations: Naming every operation that could not be, so one run says them all.
    """
    found: dict[tuple[str, str], OperationClass] = {}
    failed: list[str] = []
    for path, item in schema.get("paths", {}).items():
        for method, operation in item.items():
            if method.upper() not in HTTP_METHODS or not isinstance(operation, Mapping):
                continue
            try:
                found[(method.upper(), path)] = classify(method, path, operation)
            except Unclassified as exc:
                failed.append(str(exc))
    if failed:
        raise UnclassifiedOperations(sorted(failed))
    return found


def sudo_rows(classes: Mapping[tuple[str, str], OperationClass]) -> frozenset[str]:
    """
    Compute which permissions are key rows: most of their operations ask for sudo mode or a person.

    Args:
        classes: ``classify_schema`` of the application.

    Returns:
        The permissions of :data:`CHANGE_PERMISSIONS` for which more than half of the operations that change
        something are ``strong``. :data:`SUDO_ROWS` is this, written down.
    """
    total: dict[str, int] = {}
    strong: dict[str, int] = {}
    for found in classes.values():
        if found.changes and found.permission in CHANGE_PERMISSIONS:
            total[found.permission] = total.get(found.permission, 0) + 1
            strong[found.permission] = strong.get(found.permission, 0) + int(found.strong)
    return frozenset(p for p, count in total.items() if strong.get(p, 0) * 2 > count)


def escalated_permission(rule_action: str | None) -> str | None:
    """
    Name the permission a read route is judged by when its body makes it a write.

    Args:
        rule_action: The action of the four-eyes rule that matched the call's body, or None.

    Returns:
        The permission of the write, or None when the rule is no read route's escalation.
    """
    return ESCALATIONS.get(rule_action) if rule_action else None


__all__ = [
    "ACTION",
    "CHANGE_PERMISSIONS",
    "CLASSES",
    "ESCALATIONS",
    "HTTP_METHODS",
    "NEUTRAL_PERMISSIONS",
    "OWN",
    "PUBLIC_CLASS",
    "READ",
    "READS_BY_POST",
    "READ_PERMISSIONS",
    "SAFE_METHODS",
    "STRONG",
    "SUDO_ROWS",
    "OperationClass",
    "Unclassified",
    "UnclassifiedOperations",
    "classify",
    "classify_parts",
    "classify_schema",
    "escalated_permission",
    "sudo_rows",
]
