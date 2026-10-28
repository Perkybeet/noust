# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the session store and the token manager share about an API token's row.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Mapping
from typing import Any

from noust.core.exceptions import SecurityError
from noust.web.permissions import Permission
from noust.web.permissions.roles import ADMIN

#: The API token table, named by ``{table}`` so the rebuild that widens an old
#: CHECK (:meth:`SessionStore._allow_fleet_scope`) declares exactly what a
#: fresh database does. The token itself is never stored: only its salted
#: hash, exactly like the master token. Names stay unique across revocations
#: so an audit line naming a token always names one thing.
API_TOKENS_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS {table} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        token_hash TEXT NOT NULL UNIQUE,
        scope TEXT NOT NULL CHECK (scope IN ('read', 'deploy', 'admin', 'fleet')),
        created_at REAL NOT NULL,
        expires_at REAL,
        last_used_at REAL,
        revoked_at REAL,
        owner_account_id INTEGER,
        permissions TEXT,
        allowed_cidrs TEXT,
        allow_elevated INTEGER,
        created_by TEXT,
        last_used_ip TEXT,
        bound_kind TEXT,
        bound_id INTEGER,
        legacy_prefix_used_at REAL
    )
"""

#: Columns 3.1 added to ``api_tokens``, for a database a 3.0 console created.
#: ``owner_account_id`` NULL is a token issued before its server had
#: accounts: it keeps the power of its scope until the first ``admin``
#: account adopts it. ``permissions`` NULL means "what the scope says".
#: ``allow_elevated`` NULL keeps 3.0's rule, where a token was never asked
#: for sudo mode; a token issued since says so explicitly.
API_TOKEN_COLUMNS_3_1 = (
    ("owner_account_id", "INTEGER"),
    ("permissions", "TEXT"),
    ("allowed_cidrs", "TEXT"),
    ("allow_elevated", "INTEGER"),
    ("created_by", "TEXT"),
    ("last_used_ip", "TEXT"),
)


#: Columns 4.0 added to ``api_tokens`` (I-34): a token bound to one team, one
#: project or one environment sees nothing else, whoever owns it. ``bound_kind``
#: is ``team``, ``project`` or ``environment`` and ``bound_id`` its row in the
#: store; NULL for both is a token that reaches whatever its owner does. The
#: binding is not a foreign key because the tokens live in the sessions
#: database; a scope that was deleted since leaves a token that reaches nothing.
API_TOKEN_BINDING_COLUMNS = (
    ("bound_kind", "TEXT"),
    ("bound_id", "INTEGER"),
)

#: Column 4.0 added to ``api_tokens``: when a token last signed in with the prefix of before
#: 3.0. The token is stored hashed, so its prefix is only known while it is presented; this
#: is how ``noust health`` can name the scripts still holding one.
API_TOKEN_COLUMNS_4_0 = (("legacy_prefix_used_at", "REAL"),)

#: How recently a token of the old kind must have been used to count as still in use.
LEGACY_TOKEN_WINDOW_DAYS = 30.0


#: What no API token holds where the restricted_tokens control is on, whoever owns it: a token is
#: a standing credential nobody watches, and root-equivalent changes or
#: governing accounts and security settings need a person (ens.md §4.2.6).
#: The most a token issued before accounts holds once accounts exist and no
#: admin adopted it: an admin's permissions, never the governance of
#: accounts, security settings or the audit trail.
LEGACY_TOKEN_CEILING = ADMIN - {
    Permission.SECURITY_MANAGE,
    Permission.ACCOUNTS_MANAGE,
    Permission.AUDIT_MANAGE,
}

ENS_TOKEN_EXCLUDED = frozenset(
    {Permission.ROOT_EQUIVALENT, Permission.ACCOUNTS_MANAGE, Permission.SECURITY_MANAGE}
)


def _api_enabled() -> bool:
    """
    Returns:
        Whether API tokens are accepted (``web.api_enabled``), read on every request so
        that a change applies to the next one. An unreadable value leaves it on: failing
        closed would lock a server's scripts out over a typo.
    """
    from noust.core.config import Config

    raw = Config().get("web.api_enabled", True)
    return str(raw).strip().lower() not in ("false", "no", "off", "0")


def _validated_networks(entries: list[str] | None) -> list[str] | None:
    """
    Check the networks a token is restricted to.

    Args:
        entries: Addresses or CIDRs, or None for no restriction.

    Returns:
        The networks in canonical form, or None when there are none.

    Raises:
        SecurityError: When an entry is not an address or a network.
    """
    if not entries:
        return None
    networks = []
    for entry in entries:
        try:
            networks.append(str(ipaddress.ip_network(str(entry).strip(), strict=False)))
        except ValueError as exc:
            raise SecurityError(
                f"Not an address or a network: {entry!r}",
                details="Use addresses or CIDRs, such as 203.0.113.7 or 10.0.0.0/8.",
            ) from exc
    return networks


def _json_list(raw: Any) -> list[str] | None:
    """
    Args:
        raw: A JSON list as stored, or NULL.

    Returns:
        Its items as strings, or None for NULL or anything unreadable.
    """
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return [str(item) for item in value] if isinstance(value, list) else None


def _token_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """
    Turn an ``api_tokens`` row into the record callers see, hash excluded.

    Args:
        row: A row of ``api_tokens``.

    Returns:
        The record, with its JSON columns decoded and ``allow_elevated`` as a
        boolean or None.
    """
    record = dict(row)
    record.pop("token_hash", None)
    record["permissions"] = _json_list(record.get("permissions"))
    record["allowed_cidrs"] = _json_list(record.get("allowed_cidrs"))
    elevated = record.get("allow_elevated")
    record["allow_elevated"] = None if elevated is None else bool(elevated)
    return record
