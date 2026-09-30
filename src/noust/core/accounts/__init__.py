# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Accounts: people who sign in to the console, each with one role.

Until the first account exists Noust behaves as 3.0 did, with the master token
as the one credential. Once one does, the master token becomes the break-glass
credential and every action is taken by a named person. See
:class:`~noust.core.accounts.manager.AccountManager` for the rules and
:mod:`noust.core.accounts.policy` for the timeouts and the ENS profile.
"""

from noust.core.accounts.manager import INVITATION_PREFIX, AccountManager
from noust.core.accounts.model import (
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_OPERATOR,
    ROLE_SECURITY,
    ROLE_VIEWER,
    ROLES,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_INVITED,
    STATUS_LOCKED,
    STATUSES,
    Account,
    AccountError,
    AccountNotFoundError,
    AuthenticationFailed,
    LoginRecord,
    SodException,
    incompatible_roles,
)
from noust.core.accounts.policy import (
    PROFILE_ENS_MEDIUM,
    PROFILE_STANDARD,
    AuthPolicy,
    load_policy,
)

__all__ = [
    "INVITATION_PREFIX",
    "PROFILE_ENS_MEDIUM",
    "PROFILE_STANDARD",
    "ROLES",
    "ROLE_ADMIN",
    "ROLE_AUDITOR",
    "ROLE_OPERATOR",
    "ROLE_SECURITY",
    "ROLE_VIEWER",
    "STATUSES",
    "STATUS_ACTIVE",
    "STATUS_DISABLED",
    "STATUS_INVITED",
    "STATUS_LOCKED",
    "Account",
    "AccountError",
    "AccountManager",
    "AccountNotFoundError",
    "AuthPolicy",
    "AuthenticationFailed",
    "LoginRecord",
    "SodException",
    "incompatible_roles",
    "load_policy",
]
