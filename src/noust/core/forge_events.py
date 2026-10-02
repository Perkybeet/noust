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

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from noust.core.tags import tag_from_ref


class Forge(str, Enum):
    """The code hosts Noust understands."""

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
class TagEvent:
    """
    A tag a code host announced: pushed, or published as a release.

    Attributes:
        forge: Which host sent it.
        tag: The tag's name.
        source: ``push`` for a pushed tag, ``release`` for a published release.
        repository: ``owner/repo``; empty when the payload does not say, which
            is all an application's own webhook needs.
        clone_url: The repository's HTTPS clone URL, or empty.
        sha: The commit the tag points at, when the payload says (a release
            does not).
        installation_id: The GitHub App installation that delivered it, for
            deliveries to ``/hooks/github``; None otherwise.
        skip: Why this must not deploy though it names a tag: a draft or a
            pre-release. None for one that may.
    """

    forge: Forge
    tag: str
    source: str
    repository: str = ""
    clone_url: str = ""
    sha: str | None = None
    installation_id: int | None = None
    skip: str | None = None


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
        author: Login of the account that opened it (GitLab: of the account
            that caused the event; GitLab does not say who opened it).
        sender: Login of the account whose action sent this delivery: for a
            push to the branch, whoever pushed.
        bot: True when the author or the sender is a bot account (GitHub
            ``type: Bot`` or a ``[bot]`` login; on GitLab and Gitea, a
            username ending in ``bot``). A bot's pull request runs code a
            dependency update or an automation chose, not a person.
        author_association: GitHub's relation of the author to the
            repository (``OWNER``, ``MEMBER``, ``COLLABORATOR``,
            ``CONTRIBUTOR``, ``NONE``...); None elsewhere, since GitLab and
            Gitea send no role.
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
    author: str = ""
    sender: str = ""
    bot: bool = False
    author_association: str | None = None


#: GitLab project and group access tokens act as users named
#: ``project_<id>_bot_<hash>`` and ``group_<id>_bot_<hash>``.
_GITLAB_TOKEN_USER = re.compile(r"^(?:project|group)_\d+_bot(?:_[0-9a-f]+)?$", re.IGNORECASE)


def _is_bot(login: str, account_type: str = "", *, by_suffix: bool = False) -> bool:
    """
    Tell a bot account from a person.

    Args:
        login: The account's login.
        account_type: What the host says it is (GitHub: ``User``, ``Bot``,
            ``Organization``); empty when it says nothing.
        by_suffix: Also count a login that merely ends in ``bot``, for the
            hosts that have no account type (GitLab, Gitea): that is how
            their bots are named (``renovate-bot``, ``dependabot``).

    Returns:
        True for a bot.
    """
    name = login.lower()
    if account_type.lower() == "bot" or name.endswith("[bot]"):
        return True
    if by_suffix and (name.endswith("bot") or _GITLAB_TOKEN_USER.match(name) is not None):
        return True
    return False


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
    user = _section(pull.get("user"))
    sender = _section(payload.get("sender"))
    author = _text(user.get("login")) or _text(sender.get("login"))
    sender_login = _text(sender.get("login")) or author
    # Gitea's accounts carry no type; its bots are only told by their name.
    by_suffix = forge is Forge.GITEA
    bot = _is_bot(author, _text(user.get("type")), by_suffix=by_suffix) or _is_bot(
        sender_login, _text(sender.get("type")), by_suffix=by_suffix
    )
    association = _text(pull.get("author_association")).upper()
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
        author=author,
        sender=sender_login,
        bot=bot,
        author_association=association if forge is Forge.GITHUB and association else None,
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
    # GitLab names the account that caused the event, not the one that
    # opened the merge request, and says nothing of its role.
    user = _text(_section(payload.get("user")).get("username"))
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
        author=user,
        sender=user,
        bot=_is_bot(user, by_suffix=True),
    )


#: Release actions that put a release in front of users: published, or a
#: pre-release promoted. The rest (created, edited, deleted...) publish nothing.
_RELEASE_ACTIONS = frozenset({"published", "released"})


def parse_tag_event(provider: str, kind: str, payload: dict[str, Any]) -> TagEvent | None:
    """
    Translate a tag push or a published release into a :class:`TagEvent`.

    GitHub and Gitea send ``release`` payloads of the same shape and push
    payloads with ``refs/tags/<tag>`` in ``ref``; GitLab's tag push has the
    same ``ref`` and puts the repository in ``project``. Deleting a tag is a
    push too, and deploys nothing.

    Args:
        provider: ``github``, ``gitea`` or ``gitlab``.
        kind: ``push`` or ``release``, as the delivery says it is.
        payload: The parsed body.

    Returns:
        The event; None for anything that names no tag to deploy: a branch
        push, a deleted tag, a release action that publishes nothing, a body
        without what an event needs. A draft or a pre-release is returned with
        ``skip`` set, so the delivery log can say that is why it was ignored.
    """
    forge = {"github": Forge.GITHUB, "gitea": Forge.GITEA, "gitlab": Forge.GITLAB}.get(provider)
    if forge is None:
        return None
    installation = _section(payload.get("installation")).get("id")
    installation_id = installation if isinstance(installation, int) else None
    if kind == "release":
        return _release_event(forge, payload, installation_id)
    if kind != "push":
        return None
    tag = tag_from_ref(payload.get("ref"))
    sha = _text(payload.get("after")) or _text(payload.get("checkout_sha"))
    if tag is None or payload.get("deleted") or (sha and set(sha) == {"0"}):
        return None
    repository = _section(payload.get("repository"))
    project = _section(payload.get("project"))
    return TagEvent(
        forge=forge,
        tag=tag,
        source="push",
        repository=_text(repository.get("full_name")) or _text(project.get("path_with_namespace")),
        clone_url=_text(repository.get("clone_url")) or _text(project.get("git_http_url")),
        sha=sha or None,
        installation_id=installation_id,
    )


def _release_event(
    forge: Forge, payload: dict[str, Any], installation_id: int | None
) -> TagEvent | None:
    """
    Translate a GitHub or Gitea ``release`` delivery.

    Args:
        forge: Which host sent it.
        payload: The parsed body.
        installation_id: The App installation that delivered it, if any.

    Returns:
        The event, or None (see :func:`parse_tag_event`).
    """
    if _text(payload.get("action")) not in _RELEASE_ACTIONS:
        return None
    published = _section(payload.get("release"))
    tag = _text(published.get("tag_name"))
    if not tag:
        return None
    skip = None
    if published.get("draft"):
        skip = "it is a draft release"
    elif published.get("prerelease"):
        skip = "it is marked as a pre-release"
    repository = _section(payload.get("repository"))
    return TagEvent(
        forge=forge,
        tag=tag,
        source="release",
        repository=_text(repository.get("full_name")),
        clone_url=_text(repository.get("clone_url")),
        installation_id=installation_id,
        skip=skip,
    )
