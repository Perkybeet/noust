# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The periodic access review (ENS op.acc.4.4: "los permisos de acceso se revisarán de forma periódica").

The review is a person's attestation that the list of who may do what is
right: every account with its role, state, second factor and last sign-in,
the API tokens each one owns, the people holding incompatible roles and the
exceptions recorded for them. :func:`build_review` assembles that list with a
SHA-256 of it; :func:`attest` records the attestation as an ``access.review``
audit event carrying the digest, the reviewer and their notes, which is what
``noust ens check`` (ENS-ACC-05) looks for and what an auditor asks to see.

Attesting is the security officer's (``accounts.manage`` in the console); the
CLI is root's, recorded with the operating system identity.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from noust.core.audit import Actor, record

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: The longest note kept with an attestation.
MAX_NOTES = 2000


def build_review(
    store: NoustStore | None = None, tokens: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """
    Assemble the list a reviewer attests.

    Args:
        store: The store; the process-wide one by default.
        tokens: The console's API token records, when it can be reached.

    Returns:
        ``accounts``, ``conflicts``, ``exceptions``, ``tokens_by_owner``,
        ``generated_at`` and ``digest`` (SHA-256 of everything else,
        canonical JSON, so an attestation names exactly what was reviewed).
    """
    from noust.core.accounts import AccountManager

    manager = AccountManager(store)
    live_tokens = [t for t in tokens or [] if not t.get("revoked_at") and t.get("scope") != "fleet"]
    by_owner: dict[str, int] = {}
    for token in live_tokens:
        key = str(token.get("owner_account_id") or "none")
        by_owner[key] = by_owner.get(key, 0) + 1
    accounts = [
        {
            "id": account.id,
            "username": account.username,
            "role": account.role,
            "status": account.effective_status(),
            "person_ref": account.person_ref,
            "mfa": account.has_mfa,
            "last_login_at": account.last_login_at,
            "created_at": account.created_at,
            "tokens": by_owner.get(str(account.id), 0),
        }
        for account in manager.list_all()
    ]
    body: dict[str, Any] = {
        "accounts": accounts,
        "conflicts": manager.separation_conflicts(),
        "exceptions": [
            {
                "id": item.id,
                "person_ref": item.person_ref,
                "reason": item.reason,
                "expires_at": item.expires_at,
            }
            for item in manager.list_exceptions()
        ],
        "tokens_without_owner": by_owner.get("none", 0) if tokens is not None else None,
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return {
        **body,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "digest": digest,
    }


def attest(review: dict[str, Any], *, actor: Actor | None, notes: str = "") -> dict[str, Any]:
    """
    Record that a person reviewed the list and found it right, or what they changed.

    Args:
        review: What :func:`build_review` returned, as it was shown.
        actor: Who attests; the bound actor when None.
        notes: What was looked at and what was done about it.

    Returns:
        What was recorded.
    """
    details = {
        "digest": review.get("digest"),
        "accounts": len(review.get("accounts") or []),
        "conflicts": len(review.get("conflicts") or []),
        "exceptions": len(review.get("exceptions") or []),
        "notes": (notes or "").strip()[:MAX_NOTES],
    }
    record("access.review", actor=actor, target="accounts", details=details)
    return {**details, "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def last_reviews(limit: int = 20) -> list[dict[str, Any]]:
    """
    Args:
        limit: How many.

    Returns:
        The attestations on record, newest first.
    """
    from noust.core.audit import get_log

    return get_log().read(action="access.review", limit=limit)
