# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a code host told us, in one shape whichever host it was.

GitHub (through this server's GitHub App or an application's own webhook),
GitLab, Gitea and Bitbucket Cloud describe a push and a pull request each in
their own JSON.
The webhook endpoints translate those payloads into these records once, and
everything after them - an update, a preview - reads only these.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

from noust.core.tags import tag_from_ref


class Forge(str, Enum):
    """The code hosts Noust understands."""

    GITHUB = "github"
    GITLAB = "gitlab"
    GITEA = "gitea"
    BITBUCKET = "bitbucket"


class PullRequestAction(str, Enum):
    """What happened to a pull request, reduced to what a preview needs."""

    # Opened, reopened, or marked ready: build a preview.
    OPENED = "opened"
    # New commits on its branch: rebuild the preview.
    UPDATED = "updated"
    # Closed or merged: remove the preview.
    CLOSED = "closed"


@dataclass(frozen=True)
class PushChange:
    """
    What the commits of a push say and touch, as far as the delivery tells.

    Attributes:
        messages: The message of every commit the delivery lists.
        files: Every path those commits added, modified or removed; None when
            the delivery does not say (a forge that sends no file lists, a
            list cut short, a push of no commits). Unknown is not empty: an
            application with watch paths deploys on unknown.
        messages_complete: False when the delivery may list only some of the
            commits, so a marker in all of the ones shown proves nothing about
            the rest.
    """

    messages: tuple[str, ...] = ()
    files: tuple[str, ...] | None = None
    messages_complete: bool = True


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
        change: What its commits say and touch.
    """

    forge: Forge
    repository: str
    clone_url: str
    branch: str
    head_sha: str
    installation_id: int | None = None
    change: PushChange = PushChange()


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
        head_message: The message of the commit at the tip of its branch,
            for the forges whose payload carries it (GitLab); empty otherwise.
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
    head_message: str = ""


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
    if by_suffix and (name.endswith("bot") or _GITLAB_TOKEN_USER.fullmatch(name) is not None):
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


def _family(provider: str) -> str:
    """
    Args:
        provider: A host as the webhook named it.

    Returns:
        ``gitea`` for Forgejo, which is a fork of Gitea and sends the same payloads; the
        name itself for any other (Gogs, from which Gitea was forked, differs in its pull
        requests and keeps its own).
    """
    return "gitea" if provider == "forgejo" else provider


def parse_pull_request(
    provider: str, payload: dict[str, Any], event_key: str | None = None
) -> PullRequestEvent | None:
    """
    Translate a pull (merge) request delivery into a :class:`PullRequestEvent`.

    GitHub, Gitea and Forgejo send ``pull_request`` payloads of the same shape; GitLab
    sends ``merge_request`` with ``object_attributes``; Bitbucket Cloud sends a
    ``pullrequest`` and names what happened in a header, not in the body; Gogs sends a
    ``pull_request`` whose branches and repositories are flat fields (``head_branch``,
    ``head_repo``) and which names no commit. A
    branch is from a fork when the repository it lives in is not the one the
    request targets (GitHub: a deleted fork has no head repository at all, and
    counts).

    Args:
        provider: ``github``, ``gitea``, ``forgejo``, ``gogs``, ``gitlab`` or ``bitbucket``.
        payload: The parsed body.
        event_key: What the delivery says it is, for the forge that says it
            outside the body: Bitbucket's ``X-Event-Key``
            (``pullrequest:created``...).

    Returns:
        The event, or None for an action a preview does not act on (labels,
        reviews, a merge request edit that pushed nothing) or a payload that
        lacks what an event needs.
    """
    provider = _family(provider)
    if provider == "gitlab":
        return _merge_request_event(payload)
    if provider == "bitbucket":
        return _bitbucket_pull_request(payload, event_key)
    if provider == "gogs":
        return _gogs_pull_request(payload)
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


def _gogs_pull_request(payload: dict[str, Any]) -> PullRequestEvent | None:
    """
    Translate a Gogs pull request delivery.

    Gogs names the branches and repositories of a pull request as flat fields and gives no
    commit: the preview is built from the branch, as a push to it would be.

    Args:
        payload: The parsed body.

    Returns:
        The event, or None (see :func:`parse_pull_request`).
    """
    action = _PR_ACTIONS.get(_text(payload.get("action")))
    pull = _section(payload.get("pull_request"))
    number = payload.get("number", pull.get("number"))
    base_repo = _section(pull.get("base_repo")) or _section(payload.get("repository"))
    head_repo = _section(pull.get("head_repo"))
    repository = _text(base_repo.get("full_name"))
    branch = _text(pull.get("head_branch"))
    if action is None or not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    if not repository or not branch:
        return None
    head_name = _text(head_repo.get("full_name"))
    from_fork = not head_name or head_name.lower() != repository.lower()
    user = _section(pull.get("user"))
    sender = _section(payload.get("sender"))
    author = _text(user.get("login")) or _text(sender.get("login"))
    sender_login = _text(sender.get("login")) or author
    bot = _is_bot(author, by_suffix=True) or _is_bot(sender_login, by_suffix=True)
    return PullRequestEvent(
        forge=Forge.GITEA,
        action=action,
        repository=repository,
        clone_url=_text(head_repo.get("clone_url")) or _text(base_repo.get("clone_url")),
        number=number,
        title=_text(pull.get("title")),
        branch=branch,
        base_branch=_text(pull.get("base_branch")),
        head_sha="",
        from_fork=from_fork,
        installation_id=None,
        author=author,
        sender=sender_login,
        bot=bot,
        author_association=None,
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
        head_message=_text(_section(attributes.get("last_commit")).get("message")),
    )


#: Bitbucket Cloud's pull request event keys a preview acts on. A merge
#: (``fulfilled``) and a decline (``rejected``) both end the request.
_BITBUCKET_PR_EVENTS = {
    "pullrequest:created": PullRequestAction.OPENED,
    "pullrequest:updated": PullRequestAction.UPDATED,
    "pullrequest:fulfilled": PullRequestAction.CLOSED,
    "pullrequest:rejected": PullRequestAction.CLOSED,
}

#: Where Bitbucket Cloud's repositories are cloned from when a payload does not
#: say which host sent it.
_BITBUCKET_HOST = "bitbucket.org"


def _bitbucket_repository(repository: dict[str, Any]) -> tuple[str, str]:
    """
    Read the name and HTTPS clone URL of a Bitbucket repository object.

    Args:
        repository: A ``repository`` object of a delivery.

    Returns:
        ``(workspace/repo, clone URL)``, each empty when the payload lacks it.
    """
    full_name = _text(repository.get("full_name"))
    html = _text(_section(_section(repository.get("links")).get("html")).get("href"))
    host = urlsplit(html).hostname or _BITBUCKET_HOST
    return full_name, (f"https://{host}/{full_name}.git" if full_name else "")


def _bitbucket_login(account: dict[str, Any]) -> str:
    """
    Name a Bitbucket account the way a person recognises it.

    Args:
        account: An ``actor`` or ``author`` object.

    Returns:
        Its nickname, else its display name, else empty.
    """
    return _text(account.get("nickname")) or _text(account.get("display_name"))


def _bitbucket_pull_request(
    payload: dict[str, Any], event_key: str | None
) -> PullRequestEvent | None:
    """
    Translate a Bitbucket Cloud ``pullrequest`` delivery.

    ``pullrequest:updated`` is sent for a new title as well as for new commits;
    what tells the two apart is the commit at the tip of the branch, which the
    previews compare with the one they built.

    Args:
        payload: The parsed body.
        event_key: The ``X-Event-Key`` header.

    Returns:
        The event, or None (see :func:`parse_pull_request`).
    """
    action = _BITBUCKET_PR_EVENTS.get(_text(event_key).lower())
    pull = _section(payload.get("pullrequest"))
    number = pull.get("id")
    source = _section(pull.get("source"))
    destination = _section(pull.get("destination"))
    branch = _text(_section(source.get("branch")).get("name"))
    if action is None or not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    if action is PullRequestAction.UPDATED and _text(pull.get("state")).upper() not in ("", "OPEN"):
        return None
    target_name, target_url = _bitbucket_repository(_section(destination.get("repository")))
    if not target_name:
        target_name, target_url = _bitbucket_repository(_section(payload.get("repository")))
    if not target_name or not branch:
        return None
    source_name, source_url = _bitbucket_repository(_section(source.get("repository")))
    # A request from a deleted fork has no source repository, and counts as one.
    from_fork = not source_name or source_name.lower() != target_name.lower()
    author = _bitbucket_login(_section(pull.get("author")))
    sender = _bitbucket_login(_section(payload.get("actor"))) or author
    return PullRequestEvent(
        forge=Forge.BITBUCKET,
        action=action,
        repository=target_name,
        clone_url=source_url or target_url,
        number=number,
        title=_text(pull.get("title")),
        branch=branch,
        base_branch=_text(_section(destination.get("branch")).get("name")),
        head_sha=_text(_section(source.get("commit")).get("hash")),
        from_fork=from_fork,
        author=author or sender,
        sender=sender,
        bot=_is_bot(author, by_suffix=True) or _is_bot(sender, by_suffix=True),
    )


def _bitbucket_changes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    List the ref changes of a Bitbucket ``repo:push`` delivery.

    A deleted branch has no ``new``; a push can carry several changes (a
    branch and a tag together).

    Args:
        payload: The parsed body.

    Returns:
        The ``new`` object of every change that created or moved a ref.
    """
    changes = _section(payload.get("push")).get("changes")
    if not isinstance(changes, list):
        return []
    return [new for change in changes if (new := _section(_section(change).get("new")))]


def bitbucket_pushed_branches(payload: dict[str, Any]) -> list[str]:
    """
    Name the branches a Bitbucket ``repo:push`` delivery moved.

    Args:
        payload: The parsed body.

    Returns:
        The branch names, in the order the delivery lists them; empty for a
        push of tags only, a deleted branch, or a body that is not a push.
    """
    return [
        name
        for new in _bitbucket_changes(payload)
        if _text(new.get("type")) in ("branch", "named_branch") and (name := _text(new.get("name")))
    ]


#: The most commits a forge is trusted to list whole. GitHub documents 2048 for
#: its timeline and none for deliveries; a list this long is not assumed to be all.
_FORGE_COMMIT_CAP = 2048


def _paths(commit: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Read the files a commit object of GitHub, Gitea or GitLab changed.

    Args:
        commit: One entry of ``commits``.

    Returns:
        Whether the commit carried any of the three lists, and the paths.
    """
    present = False
    paths: list[str] = []
    for key in ("added", "modified", "removed"):
        listed = commit.get(key)
        if isinstance(listed, list):
            present = True
            paths.extend(item for item in listed if isinstance(item, str) and item)
    return present, paths


def parse_push_change(provider: str, payload: dict[str, Any]) -> PushChange:
    """
    Read what the commits of a push delivery say and touch.

    GitHub, Gitea and GitLab list the commits with the files each added,
    modified or removed; Bitbucket Cloud lists them (up to five per change)
    with their messages and no files. A list that may be cut short, or that
    carries no file list at all, reports the files as unknown rather than
    empty, because a deployment skipped on a guess is lost.

    Args:
        provider: ``github``, ``gitea``, ``forgejo``, ``gogs``, ``gitlab`` or ``bitbucket``.
        payload: The parsed body.

    Returns:
        The change; empty and unknown for a provider that is not one of those.
    """
    provider = _family(provider)
    if provider == "bitbucket":
        messages: list[str] = []
        complete = True
        changes = _section(payload.get("push")).get("changes")
        for change in changes if isinstance(changes, list) else []:
            entry = _section(change)
            if entry.get("truncated"):
                complete = False
            commits = entry.get("commits")
            for commit in commits if isinstance(commits, list) else []:
                message = _section(commit).get("message")
                if isinstance(message, str):
                    messages.append(message)
        return PushChange(messages=tuple(messages), files=None, messages_complete=complete)
    if provider not in ("github", "gitea", "gogs", "gitlab"):
        return PushChange()
    listed = payload.get("commits")
    commits = (
        [item for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []
    )
    if not commits and isinstance(payload.get("head_commit"), dict):
        commits = [payload["head_commit"]]
    messages = [c["message"] for c in commits if isinstance(c.get("message"), str)]
    total = payload.get("total_commits_count")
    complete = len(commits) < _FORGE_COMMIT_CAP and not (
        isinstance(total, int) and total > len(commits)
    )
    seen_lists = False
    files: dict[str, None] = {}
    for commit in commits:
        present, paths = _paths(commit)
        seen_lists = seen_lists or present
        files.update(dict.fromkeys(paths))
    known = complete and seen_lists
    return PushChange(
        messages=tuple(messages),
        files=tuple(sorted(files)) if known else None,
        messages_complete=complete,
    )


def _bitbucket_tag_event(payload: dict[str, Any]) -> TagEvent | None:
    """
    Translate the first tag a Bitbucket ``repo:push`` delivery created or moved.

    Args:
        payload: The parsed body.

    Returns:
        The event, or None when the push names no tag.
    """
    for new in _bitbucket_changes(payload):
        if _text(new.get("type")) != "tag" or not _text(new.get("name")):
            continue
        name, clone_url = _bitbucket_repository(_section(payload.get("repository")))
        return TagEvent(
            forge=Forge.BITBUCKET,
            tag=_text(new.get("name")),
            source="push",
            repository=name,
            clone_url=clone_url,
            sha=_text(_section(new.get("target")).get("hash")) or None,
        )
    return None


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
        provider: ``github``, ``gitea``, ``forgejo``, ``gogs``, ``gitlab`` or ``bitbucket``.
        kind: ``push`` or ``release``, as the delivery says it is.
        payload: The parsed body.

    Returns:
        The event; None for anything that names no tag to deploy: a branch
        push, a deleted tag, a release action that publishes nothing, a body
        without what an event needs. A draft or a pre-release is returned with
        ``skip`` set, so the delivery log can say that is why it was ignored.
    """
    forge = {
        "github": Forge.GITHUB,
        "gitea": Forge.GITEA,
        "gogs": Forge.GITEA,
        "gitlab": Forge.GITLAB,
    }.get(_family(provider))
    if provider == "bitbucket":
        # Bitbucket has no release event, and names a pushed tag in its changes.
        return _bitbucket_tag_event(payload) if kind == "push" else None
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
