# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a code host told us, in one shape whichever host it was.

GitHub (through this server's GitHub App or an application's own webhook),
GitLab and Gitea describe a push and a pull request each in their own JSON.
The webhook endpoints translate those payloads into these records once, and
everything after them - an update, a preview - reads only these.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Forge(str, Enum):
    """The code hosts WASM understands."""

    GITHUB = "github"
    GITLAB = "gitlab"
    GITEA = "gitea"


class PullRequestAction(str, Enum):
    """What happened to a pull request, reduced to what a preview needs."""

    # Opened, reopened, or marked ready: build a preview.
    OPENED = "opened"
    # New commits on its branch: rebuild the preview.
    UPDATED = "updated"
    # Closed or merged: remove the preview.
    CLOSED = "closed"


@dataclass(frozen=True)
class PushEvent:
    """
    Commits pushed to a branch.

    Attributes:
        forge: Which host sent it.
        repository: ``owner/repo`` (GitLab: the project's full path).
        clone_url: The repository's HTTPS clone URL.
        branch: The branch pushed to.
        head_sha: The commit the branch points at now.
        installation_id: The GitHub App installation that delivered it, for
            deliveries to ``/hooks/github``; None otherwise.
    """

    forge: Forge
    repository: str
    clone_url: str
    branch: str
    head_sha: str
    installation_id: int | None = None


@dataclass(frozen=True)
class PullRequestEvent:
    """
    A pull (merge) request opened, updated or closed.

    Attributes:
        forge: Which host sent it.
        action: Opened, updated or closed.
        repository: ``owner/repo`` of the repository the request targets.
        clone_url: HTTPS clone URL of the repository its branch lives in.
            For a request from a fork that is the fork, which previews refuse:
            a fork's code must not run on this server with this application's
            environment.
        number: The request's number.
        title: Its title, for messages.
        branch: Its source branch.
        base_branch: The branch it targets.
        head_sha: The commit at the tip of its branch.
        from_fork: True when its branch lives in another repository.
        installation_id: As for :class:`PushEvent`.
    """

    forge: Forge
    action: PullRequestAction
    repository: str
    clone_url: str
    number: int
    title: str
    branch: str
    base_branch: str
    head_sha: str
    from_fork: bool = False
    installation_id: int | None = None
