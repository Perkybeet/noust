# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Custom roles and role bindings (I-03, I-07 of the 3.4 checklist).

A role is held at a scope: the whole server, a team, a project or an
environment. The same person can administer one project and only read another,
or run the staging environment of a project they cannot touch in production.
A custom role is a set of the permissions that exist, defined by an
administrator and bound exactly like a built-in one.

The pieces, each stated once:

- :mod:`~noust.core.access.model`: the shapes, the names, the errors.
- :mod:`~noust.core.access.store`: :class:`AccessStore`, the one reader and
  writer of the two tables.
- :mod:`noust.web.permissions.access`: what a role may hold, who may be given
  it, and what a principal ends up holding where (the permission catalogue is
  the console's, so the rules that need it live there).

Importing this package starts neither the store nor the accounts.
"""

from noust.core.access.model import (
    BUILT_IN_ROLE_NAMES,
    INSTANCE_SCOPE_ID,
    SCOPE_ENVIRONMENT,
    SCOPE_INSTANCE,
    SCOPE_KINDS,
    SCOPE_PROJECT,
    SCOPE_TEAM,
    AccessConflictError,
    AccessError,
    AccessNotFoundError,
    Binding,
    CustomRole,
)
from noust.core.access.store import AccessStore

__all__ = [
    "BUILT_IN_ROLE_NAMES",
    "INSTANCE_SCOPE_ID",
    "SCOPE_ENVIRONMENT",
    "SCOPE_INSTANCE",
    "SCOPE_KINDS",
    "SCOPE_PROJECT",
    "SCOPE_TEAM",
    "AccessConflictError",
    "AccessError",
    "AccessNotFoundError",
    "AccessStore",
    "Binding",
    "CustomRole",
]
