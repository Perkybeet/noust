# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a person is called where something they did is listed.

A job, a deployment and an audit line record who acted as a label (:func:`noust.web.auth.actor_label`):
for a person it is the login they signed in with, which is not what anybody calls them. The console
greets an account by its display name, and the places that list what happened say the same name, so
"Started by" and the recent activity read "Marta Ruiz" and not "Console" or "Session marta".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from noust.core.accounts.manager import AccountManager

if TYPE_CHECKING:
    from noust.core.store import NoustStore


def display_names(
    labels: Iterable[str | None], *, store: NoustStore | None = None
) -> dict[str, str]:
    """
    Name the people behind a list of actor labels.

    Args:
        labels: Actor labels as a job or a deployment records them (``marta``, ``master``,
            ``token:ci``, ``webhook``...); None and empty ones are skipped.
        store: The store holding the accounts (the active one by default).

    Returns:
        For each label that is an account's login, the name its owner is greeted by (the login
        itself for an account that has no display name). A label that is not an account, such as a
        token, the master token or a webhook, is not in the answer: it names no person.
    """
    manager = AccountManager(store)
    found: dict[str, str] = {}
    for label in dict.fromkeys(label for label in labels if label):
        account = manager.find(label)
        if account is not None:
            found[label] = account.display_name.strip() or account.username
    return found
