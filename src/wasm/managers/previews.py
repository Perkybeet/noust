# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pull request previews (2.2).

Interface fixed for 2.2; the previews feature implements it.
"""

from __future__ import annotations

from wasm.core.forge_events import PullRequestEvent


def handle_pull_request(event: PullRequestEvent, *, app_domain: str | None = None) -> list[str]:
    """
    Act on a pull request event for every application that previews it.

    Args:
        event: The pull request event.
        app_domain: Only this application (a per-application webhook knows
            which application it is for); None to find every application
            whose source is ``event.repository`` and has previews on.

    Returns:
        The ids of the jobs queued (one per preview built, rebuilt or
        removed); empty when nothing previews this repository.
    """
    return []
