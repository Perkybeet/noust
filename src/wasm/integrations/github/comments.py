# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The comment WASM keeps on a pull request about its preview.

Interface fixed for 2.2; the GitHub integration implements it.
"""

from __future__ import annotations


def upsert_pr_comment(
    repository: str, number: int, body: str, comment_ref: str | None = None
) -> str | None:
    """
    Create, or update, WASM's comment on a pull request.

    Args:
        repository: ``owner/repo``.
        number: The pull request number.
        body: Markdown to show.
        comment_ref: The id returned by an earlier call, to update that
            comment instead of adding one.

    Returns:
        The comment's id, or None when no installation of this server's
        GitHub App covers the repository (nothing is sent then).

    Raises:
        IntegrationError: GitHub refused or could not be reached.
    """
    return None
