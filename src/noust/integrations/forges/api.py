# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One small client per kind of code host, with the same calls: a status on a commit, a comment on a pull request.

The four hosts say the same things in four ways. A commit has a state (pending, running,
success, failure, cancelled) that GitLab, Gitea/Forgejo and Bitbucket Cloud each spell their
own; a pull request (a merge request, on GitLab) has one comment of Noust's that is edited in
place rather than a new one at every push, and a comment somebody deleted is made again.

Every request goes through :class:`~noust.deployers.importers.remote.RemoteClient` (https
only, behind the SSRF guard, no redirect followed, a timeout and a size limit), with the
credential the operator already gave for cloning the repository: it signs in here, it is
never in an argument, a log line or an answer. A token that can clone may not be allowed to
write a status (GitLab asks for the ``api`` scope, Bitbucket for ``repository:write``), and
the refusal says so.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from noust.core.exceptions import IntegrationError
from noust.deployers.importers.remote import Opener, RemoteClient, RemoteRefused
from noust.integrations.forges.hosts import BITBUCKET, GITLAB, GOGS, api_family

#: The states a deployment is reported in, whatever the host calls them.
PENDING = "pending"
RUNNING = "running"
SUCCESS = "success"
FAILURE = "failure"
CANCELLED = "cancelled"
STATES: tuple[str, ...] = (PENDING, RUNNING, SUCCESS, FAILURE, CANCELLED)

#: Every host cuts a status description short somewhere; this is under the lowest.
MAX_DESCRIPTION = 140

#: What GitLab says to an operator whose token can clone but not write.
_GITLAB_HINTS = {
    401: "GitLab did not accept the credential. Give the repository a token with the api scope "
    "(noust app git-credential) and try again.",
    403: "The token may not write a commit status. It needs the api scope; the connection's "
    "read_api and read_repository are not enough. Give the repository a token of its own "
    "(noust app git-credential).",
    404: "GitLab has no such project, or the token cannot see it.",
}
_GITEA_HINTS = {
    401: "The host did not accept the token. Create one with the repository scope "
    "(write:repository) and give it to the repository (noust app git-credential).",
    403: "The token may not write here. It needs write access to the repository.",
    404: "The host has no such repository or pull request, or the token cannot see it.",
}
_BITBUCKET_HINTS = {
    401: "Bitbucket did not accept the credential. Use a repository access token "
    "(user x-token-auth) with the repository:write scope, or an API token.",
    403: "The credential may not write. It needs the repository:write scope "
    "(and pullrequest:write for comments).",
    404: "Bitbucket has no such repository or pull request, or the credential cannot see it.",
}


@dataclass(frozen=True)
class ForgeRepo:
    """
    A repository on a code host that is not GitHub.

    Attributes:
        kind: ``gitlab``, ``gitea``, ``forgejo``, ``gogs`` or ``bitbucket``.
        authority: The host, with its port when the source names one.
        repository: ``owner/repo`` (``group/subgroup/repo`` on GitLab).
    """

    kind: str
    authority: str
    repository: str

    @property
    def host(self) -> str:
        """The host name without a port."""
        return urlsplit(f"//{self.authority}").hostname or self.authority

    @property
    def family(self) -> str:
        """The API the host speaks: ``gitea`` for Gitea, Forgejo and Gogs."""
        return api_family(self.kind) or self.kind


class ForgeAPI:
    """
    The two things Noust writes to a code host.

    Args:
        client: The host's API.
        repository: ``owner/repo``.
        supports_statuses: Whether the host has commit statuses at all (Gogs has none).
    """

    supports_statuses = True

    def __init__(self, client: RemoteClient, repository: str) -> None:
        self.client = client
        self.repository = repository

    def set_status(self, sha: str, state: str, *, description: str, url: str, context: str) -> None:
        """
        Mark a commit.

        Args:
            sha: The commit.
            state: One of :data:`STATES`.
            description: A line shown beside it.
            url: Where "details" leads.
            context: The name of the check, so a new state replaces the last of it.

        Raises:
            IntegrationError: The host refused or could not be reached.
        """
        raise NotImplementedError

    def upsert_comment(self, number: int, body: str, comment_ref: str | None) -> str | None:
        """
        Create Noust's comment on a pull request, or edit the one it made.

        Args:
            number: The pull (merge) request number.
            body: Markdown.
            comment_ref: The id the last call returned, to edit that comment.

        Returns:
            The comment's id.

        Raises:
            IntegrationError: The host refused or could not be reached.
        """
        raise NotImplementedError


class GitLabAPI(ForgeAPI):
    """GitLab API v4: ``/projects/:id/statuses/:sha`` and a merge request's notes."""

    _STATES = {
        PENDING: "pending",
        RUNNING: "running",
        SUCCESS: "success",
        FAILURE: "failed",
        CANCELLED: "canceled",
    }

    @property
    def _project(self) -> str:
        return quote(self.repository, safe="")

    def set_status(self, sha: str, state: str, *, description: str, url: str, context: str) -> None:
        self.client.post_form(
            f"/projects/{self._project}/statuses/{quote(sha, safe='')}",
            {
                "state": self._STATES[state],
                "name": context,
                "target_url": url,
                "description": description[:MAX_DESCRIPTION],
            },
        )

    def upsert_comment(self, number: int, body: str, comment_ref: str | None) -> str | None:
        notes = f"/projects/{self._project}/merge_requests/{int(number)}/notes"
        if comment_ref and comment_ref.isdigit():
            try:
                answer = self.client.put_form(f"{notes}/{comment_ref}", {"body": body})
            except RemoteRefused as exc:
                # Deleted by someone on GitLab: say it again in a new one.
                if exc.status != 404:
                    raise
            else:
                return (
                    str(answer.get("id", comment_ref))
                    if isinstance(answer, Mapping)
                    else comment_ref
                )
        created = self.client.post_form(notes, {"body": body})
        return _id_of(created)


class GiteaAPI(ForgeAPI):
    """Gitea and Forgejo: ``/api/v1/repos/{owner}/{repo}/statuses/{sha}`` and issue comments."""

    _STATES = {
        PENDING: "pending",
        RUNNING: "pending",
        SUCCESS: "success",
        FAILURE: "failure",
        CANCELLED: "error",
    }

    @property
    def _base(self) -> str:
        owner, _, name = self.repository.partition("/")
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    def set_status(self, sha: str, state: str, *, description: str, url: str, context: str) -> None:
        self.client.post_json(
            f"{self._base}/statuses/{quote(sha, safe='')}",
            {
                "state": self._STATES[state],
                "target_url": url,
                "description": description[:MAX_DESCRIPTION],
                "context": context,
            },
        )

    def upsert_comment(self, number: int, body: str, comment_ref: str | None) -> str | None:
        if comment_ref and comment_ref.isdigit():
            try:
                answer = self.client.patch_json(
                    f"{self._base}/issues/comments/{comment_ref}", {"body": body}
                )
            except RemoteRefused as exc:
                if exc.status != 404:
                    raise
            else:
                return (
                    str(answer.get("id", comment_ref))
                    if isinstance(answer, Mapping)
                    else comment_ref
                )
        created = self.client.post_json(
            f"{self._base}/issues/{int(number)}/comments", {"body": body}
        )
        return _id_of(created)


class GogsAPI(GiteaAPI):
    """Gogs: Gitea's comments; it has no commit statuses."""

    supports_statuses = False

    def set_status(self, sha: str, state: str, *, description: str, url: str, context: str) -> None:
        return None


class BitbucketAPI(ForgeAPI):
    """Bitbucket Cloud API 2.0: ``commit/{sha}/statuses/build`` and a pull request's comments."""

    _STATES = {
        PENDING: "INPROGRESS",
        RUNNING: "INPROGRESS",
        SUCCESS: "SUCCESSFUL",
        FAILURE: "FAILED",
        CANCELLED: "STOPPED",
    }

    @property
    def _base(self) -> str:
        workspace, _, name = self.repository.partition("/")
        return f"/repositories/{quote(workspace, safe='')}/{quote(name, safe='')}"

    def set_status(self, sha: str, state: str, *, description: str, url: str, context: str) -> None:
        self.client.post_json(
            f"{self._base}/commit/{quote(sha, safe='')}/statuses/build",
            {
                # A key is at most 40 characters, and is what a later state replaces.
                "key": context[:40],
                "state": self._STATES[state],
                "name": context[:255],
                "url": url,
                "description": description[:MAX_DESCRIPTION],
            },
        )

    def upsert_comment(self, number: int, body: str, comment_ref: str | None) -> str | None:
        comments = f"{self._base}/pullrequests/{int(number)}/comments"
        content = {"content": {"raw": body}}
        if comment_ref and comment_ref.isdigit():
            try:
                answer = self.client.put_json(f"{comments}/{comment_ref}", content)
            except RemoteRefused as exc:
                if exc.status != 404:
                    raise
            else:
                return (
                    str(answer.get("id", comment_ref))
                    if isinstance(answer, Mapping)
                    else comment_ref
                )
        created = self.client.post_json(comments, content)
        return _id_of(created)


def _id_of(answer: Any) -> str | None:
    """
    Args:
        answer: What the host answered to a new comment.

    Returns:
        Its id as text; None when the host named none.
    """
    if isinstance(answer, Mapping) and answer.get("id") is not None:
        return str(answer["id"])
    return None


def _bitbucket_header(user: str, secret: str) -> tuple[str, str]:
    """
    Args:
        user: The user of the credential.
        secret: Its token or password.

    Returns:
        The header that signs the REST API in: a repository or workspace access token
        (user ``x-token-auth``) is a bearer; anything else is HTTP Basic.
    """
    if user == "x-token-auth":
        return ("Authorization", f"Bearer {secret}")
    pair = base64.b64encode(f"{user}:{secret}".encode()).decode("ascii")
    return ("Authorization", f"Basic {pair}")


def api_for(
    repo: ForgeRepo, credential: tuple[str, str], *, opener: Opener | None = None
) -> ForgeAPI:
    """
    Build the client of one repository's host, signed in with a credential.

    Args:
        repo: The repository.
        credential: ``(user, secret)``, as :meth:`GitCredentialManager.credential_for` finds it.
        opener: Replacement for the HTTP opener (tests).

    Returns:
        The client.

    Raises:
        IntegrationError: A kind this package does not write to.
    """
    user, secret = credential
    if repo.kind == GITLAB:
        client = RemoteClient(
            f"https://{repo.authority}/api/v4",
            ("Authorization", f"Bearer {secret}"),
            platform="GitLab",
            opener=opener,
            hints=_GITLAB_HINTS,
        )
        return GitLabAPI(client, repo.repository)
    if repo.kind == BITBUCKET:
        client = RemoteClient(
            "https://api.bitbucket.org/2.0",
            _bitbucket_header(user, secret),
            platform="Bitbucket",
            opener=opener,
            hints=_BITBUCKET_HINTS,
        )
        return BitbucketAPI(client, repo.repository)
    if repo.family == "gitea":
        client = RemoteClient(
            f"https://{repo.authority}/api/v1",
            ("Authorization", f"token {secret}"),
            platform=repo.kind.capitalize(),
            opener=opener,
            hints=_GITEA_HINTS,
        )
        return (GogsAPI if repo.kind == GOGS else GiteaAPI)(client, repo.repository)
    raise IntegrationError(f"Noust does not write to a {repo.kind} host")
