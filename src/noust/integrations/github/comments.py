# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The comment Noust keeps on a pull request about its preview.

One comment per pull request, edited as the preview changes, rather than a
new comment for every push: the caller keeps the id the first call returned
and hands it back. A comment someone deleted is created again.
"""

from __future__ import annotations

from urllib.parse import quote

from noust.integrations.github.app import installation_for, load_app
from noust.integrations.github.client import GitHubAPIError
from noust.validators.source import GITHUB_SHORTHAND_PATTERN


def upsert_pr_comment(
    repository: str, number: int, body: str, comment_ref: str | None = None
) -> str | None:
    """
    Create, or update, Noust's comment on a pull request.

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
    if not GITHUB_SHORTHAND_PATTERN.match(f"github:{repository}"):
        return None
    app = load_app()
    if app is None:
        return None
    installation = installation_for(repository)
    if installation is None:
        return None
    owner, _, name = repository.partition("/")
    base = f"/repos/{quote(owner)}/{quote(name)}"

    if comment_ref and comment_ref.isdigit():
        try:
            answer = app.as_installation(
                installation, "PATCH", f"{base}/issues/comments/{comment_ref}", {"body": body}
            )
        except GitHubAPIError as exc:
            # Deleted by someone on GitHub: say it again in a new one.
            if exc.status != 404:
                raise
        else:
            return str(answer.get("id", comment_ref)) if isinstance(answer, dict) else comment_ref

    answer = app.as_installation(
        installation, "POST", f"{base}/issues/{int(number)}/comments", {"body": body}
    )
    if isinstance(answer, dict) and answer.get("id") is not None:
        return str(answer["id"])
    return None
