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
from typing import Any


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


#: Pull request actions of GitHub and Gitea that matter to a preview.
_PR_ACTIONS = {
    "opened": PullRequestAction.OPENED,
    "reopened": PullRequestAction.OPENED,
    "ready_for_review": PullRequestAction.OPENED,
    "synchronize": PullRequestAction.UPDATED,
    "synchronized": PullRequestAction.UPDATED,
    "closed": PullRequestAction.CLOSED,
}

#: GitLab's merge request actions. ``update`` is also sent for a new title
#: or label; only an update that carries ``oldrev`` moved the branch.
_MR_ACTIONS = {
    "open": PullRequestAction.OPENED,
    "reopen": PullRequestAction.OPENED,
    "update": PullRequestAction.UPDATED,
    "close": PullRequestAction.CLOSED,
    "merge": PullRequestAction.CLOSED,
}


def _text(value: Any) -> str:
    """
    Read a payload field that should be text.

    Args:
        value: The field.

    Returns:
        It, stripped, or an empty string when it is not text.
    """
    return value.strip() if isinstance(value, str) else ""


def _section(value: Any) -> dict[str, Any]:
    """
    Read a payload field that should be an object.

    Args:
        value: The field.

    Returns:
        It, or an empty object when it is not one.
    """
    return value if isinstance(value, dict) else {}


def parse_pull_request(provider: str, payload: dict[str, Any]) -> PullRequestEvent | None:
    """
    Translate a pull (merge) request delivery into a :class:`PullRequestEvent`.

    GitHub and Gitea send ``pull_request`` payloads of the same shape; GitLab
    sends ``merge_request`` with ``object_attributes``. A branch is from a
    fork when the repository it lives in is not the one the request targets
    (GitHub: a deleted fork has no head repository at all, and counts).

    Args:
        provider: ``github``, ``gitea`` or ``gitlab``.
        payload: The parsed body.

    Returns:
        The event, or None for an action a preview does not act on (labels,
        reviews, a merge request edit that pushed nothing) or a payload that
        lacks what an event needs.
    """
    if provider == "gitlab":
        return _merge_request_event(payload)
    forge = Forge.GITHUB if provider == "github" else Forge.GITEA
    action = _PR_ACTIONS.get(_text(payload.get("action")))
    pull = _section(payload.get("pull_request"))
    number = payload.get("number", pull.get("number"))
    head = _section(pull.get("head"))
    base = _section(pull.get("base"))
    base_repo = _section(base.get("repo")) or _section(payload.get("repository"))
    head_repo = _section(head.get("repo"))
    repository = _text(base_repo.get("full_name")) or _text(
        _section(payload.get("repository")).get("full_name")
    )
    branch = _text(head.get("ref"))
    if action is None or not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    if not repository or not branch:
        return None
    head_name = _text(head_repo.get("full_name"))
    from_fork = not head_name or head_name.lower() != repository.lower()
    installation = _section(payload.get("installation")).get("id")
    return PullRequestEvent(
        forge=forge,
        action=action,
        repository=repository,
        clone_url=_text(head_repo.get("clone_url")) or _text(base_repo.get("clone_url")),
        number=number,
        title=_text(pull.get("title")),
        branch=branch,
        base_branch=_text(base.get("ref")),
        head_sha=_text(head.get("sha")),
        from_fork=from_fork,
        installation_id=installation if isinstance(installation, int) else None,
    )


def _merge_request_event(payload: dict[str, Any]) -> PullRequestEvent | None:
    """
    Translate a GitLab merge request delivery.

    Args:
        payload: The parsed body.

    Returns:
        The event, or None (see :func:`parse_pull_request`).
    """
    attributes = _section(payload.get("object_attributes"))
    action = _MR_ACTIONS.get(_text(attributes.get("action")))
    if action is PullRequestAction.UPDATED and not attributes.get("oldrev"):
        return None
    number = attributes.get("iid")
    project = _section(payload.get("project"))
    target = _section(attributes.get("target"))
    source = _section(attributes.get("source"))
    repository = _text(project.get("path_with_namespace")) or _text(
        target.get("path_with_namespace")
    )
    branch = _text(attributes.get("source_branch"))
    if action is None or not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    if not repository or not branch:
        return None
    source_project = attributes.get("source_project_id")
    target_project = attributes.get("target_project_id")
    from_fork = source_project is None or source_project != target_project
    return PullRequestEvent(
        forge=Forge.GITLAB,
        action=action,
        repository=repository,
        clone_url=_text(source.get("git_http_url")) or _text(project.get("git_http_url")),
        number=number,
        title=_text(attributes.get("title")),
        branch=branch,
        base_branch=_text(attributes.get("target_branch")),
        head_sha=_text(_section(attributes.get("last_commit")).get("id")),
        from_fork=from_fork,
    )
