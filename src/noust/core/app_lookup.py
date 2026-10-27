# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
"Is an application deployed at this domain", for the code that runs as the instance.

The command line, a job and the deployers read the store as root: there is no caller whose teams
could hide an application, so the answer is a plain lookup that says where to look when it fails.
The console's answer is scoped to the caller and reads as a 404
(``noust.web.api.deps.known_app``); this is not it, and a route must not use this one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.exceptions import NoustError

if TYPE_CHECKING:
    from noust.core.store import App, NoustStore


def require_app(store: NoustStore, domain: str) -> App:
    """
    Read an application's row, or say it is not deployed.

    Args:
        store: The store.
        domain: A validated domain.

    Returns:
        The row.

    Raises:
        NoustError: Nothing is deployed there.
    """
    app = store.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    return app
