# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The pull requests a repository has open, read through this server's GitHub App.

A preview an operator makes by hand from a pull request needs the pull request's
branch and head commit, and listing the open ones is how the console offers
them. Both are questions only the code host can answer, and the only one Noust
holds a credential for is GitHub (through its App): GitLab, Gitea and Bitbucket
deliveries reach an application's own webhook, which has none to ask with, so a
preview there is made from the branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from noust.core.exceptions import IntegrationError
from noust.integrations.github.app import GitHubApp, installation_for, load_app
from noust.integrations.github.client import GitHubAPIError, json_object
from noust.validators.source import GITHUB_SHORTHAND_PATTERN

#: Most open pull requests listed: a repository with more has a queue nobody previews.
MAX_PULL_REQUESTS = 200


@dataclass(frozen=True)
class OpenPullRequest:
    """
    One open pull request.

    Attributes:
        number: Its number.
        title: Its title.
        branch: Its source branch.
        base_branch: The branch it targets.
        head_sha: The commit at the tip of its branch.
        author: Who opened it.
        author_association: GitHub's relation of the author to the repository.
        draft: Whether it is a draft.
        from_fork: Whether its branch lives in another repository.
        url: Its page on GitHub.
    """

    number: int
    title: str
    branch: str
    base_branch: str
    head_sha: str
    author: str
    author_association: str
    draft: bool
    from_fork: bool
    url: str


def _read(item: object, repository: str) -> OpenPullRequest | None:
    """
    Args:
        item: One pull request as GitHub describes it.
        repository: ``owner/repo`` it was asked of.

    Returns:
        It; None when GitHub left out what a preview needs.
    """
    data = json_object(item)
    head = json_object(data.get("head"))
    base = json_object(data.get("base"))
    head_repo = json_object(head.get("repo"))
    number = data.get("number")
    branch = head.get("ref")
    sha = head.get("sha")
    if not isinstance(number, int) or not isinstance(branch, str) or not isinstance(sha, str):
        return None
    full = str(head_repo.get("full_name") or "")
    return OpenPullRequest(
        number=number,
        title=str(data.get("title") or ""),
        branch=branch,
        base_branch=str(base.get("ref") or ""),
        head_sha=sha,
        author=str(json_object(data.get("user")).get("login") or ""),
        author_association=str(data.get("author_association") or ""),
        draft=bool(data.get("draft")),
        from_fork=bool(full) and full.lower() != repository.lower(),
        url=str(data.get("html_url") or ""),
    )


def _app_for(repository: str, installation_id: int | None) -> tuple[GitHubApp, int]:
    """
    Args:
        repository: ``owner/repo``.
        installation_id: The installation the application is linked to, if any.

    Returns:
        This server's GitHub App and the installation that reaches the repository.

    Raises:
        IntegrationError: There is no App, or none of its installations covers
            the repository; the message says what to do.
    """
    if not GITHUB_SHORTHAND_PATTERN.match(f"github:{repository}"):
        raise IntegrationError(
            f"{repository} is not a GitHub repository",
            details="Only GitHub's pull requests can be read; make a preview from a branch "
            "or a tag instead.",
        )
    app = load_app()
    installation = installation_for(repository, installation_id) if app is not None else None
    if app is None or installation is None:
        raise IntegrationError(
            "This server has no GitHub App that reaches " + repository,
            details="Connect the GitHub App in the console's integrations and install it on the "
            "repository, or make the preview from a branch (--branch) or a tag (--tag).",
        )
    return app, installation


def list_open(repository: str, installation_id: int | None = None) -> list[OpenPullRequest]:
    """
    List the pull requests a repository has open.

    Args:
        repository: ``owner/repo``.
        installation_id: The installation the application previewed is linked to.

    Returns:
        Them, newest first, at most :data:`MAX_PULL_REQUESTS`.

    Raises:
        IntegrationError: There is no App that reaches the repository, or GitHub
            refused or could not be reached.
    """
    app, installation = _app_for(repository, installation_id)
    owner, _, name = repository.partition("/")
    path = f"/repos/{quote(owner)}/{quote(name)}/pulls?state=open&sort=created&direction=desc"
    items = app.paginate_as_installation(installation, path)
    found = [pull for pull in (_read(item, repository) for item in items) if pull is not None]
    return found[:MAX_PULL_REQUESTS]


def get(repository: str, number: int, installation_id: int | None = None) -> OpenPullRequest | None:
    """
    Read one pull request, if it is open.

    Args:
        repository: ``owner/repo``.
        number: The pull request's number.
        installation_id: The installation the application previewed is linked to.

    Returns:
        It; None when it is not open (closed, merged).

    Raises:
        IntegrationError: There is no App that reaches the repository; there is
            no such pull request; or GitHub refused or could not be reached.
    """
    app, installation = _app_for(repository, installation_id)
    owner, _, name = repository.partition("/")
    try:
        answer = app.as_installation(
            installation, "GET", f"/repos/{quote(owner)}/{quote(name)}/pulls/{int(number)}"
        )
    except GitHubAPIError as exc:
        if exc.status == 404:
            raise IntegrationError(
                f"{repository} has no pull request #{number}",
                details="Check the number; GitHub also shows nothing the App cannot reach.",
            ) from exc
        raise
    data = json_object(answer)
    if data.get("state") != "open":
        return None
    return _read(data, repository)


def changed_files(repository: str, number: int, installation_id: int | None = None) -> list[str]:
    """
    List the files a pull request changes, renames included.

    What decides, for an application that deploys one directory of a monorepo, whether a
    pull request is a change to it at all (the watch paths of the application, weighed
    against the whole pull request and not against its last push).

    Args:
        repository: ``owner/repo``.
        number: The pull request's number.
        installation_id: The installation the application previewed is linked to.

    Returns:
        The paths, as GitHub names them: the new one of a rename and the old one with it,
        since a file that left a directory changes it too.

    Raises:
        IntegrationError: There is no App that reaches the repository, or GitHub refused
            or could not be reached.
    """
    app, installation = _app_for(repository, installation_id)
    owner, _, name = repository.partition("/")
    path = f"/repos/{quote(owner)}/{quote(name)}/pulls/{int(number)}/files"
    files: list[str] = []
    for item in app.paginate_as_installation(installation, path):
        data = json_object(item)
        for key in ("filename", "previous_filename"):
            value = data.get(key)
            if isinstance(value, str) and value and value not in files:
                files.append(value)
    return files
