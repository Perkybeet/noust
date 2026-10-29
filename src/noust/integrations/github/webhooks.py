# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a delivery to ``/hooks/github`` means, in Noust's terms.

The router verifies and answers; this module reads GitHub's payloads into
:mod:`noust.core.forge_events` records and decides which applications a push
concerns. Kept apart from HTTP so every decision is testable with a dict.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Sequence
from typing import Any

from noust.core.forge_events import Forge, PushEvent, TagEvent, parse_push_change
from noust.core.github_host import configured_host, integration_repository
from noust.core.store import App, get_store
from noust.integrations.github.app import forget_tokens
from noust.integrations.github.client import json_object
from noust.integrations.github.service import installation_record
from noust.validators.source import github_repository, parse_git_url

logger = logging.getLogger(__name__)

_SIGNATURE_PREFIX = "sha256="

#: GitHub's pull request actions, reduced to what a preview does about them.
#: Anything else (labeled, edited, review_requested...) changes no code.


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """
    Check GitHub's ``X-Hub-Signature-256`` in constant time.

    Args:
        secret: The App's webhook secret.
        body: The raw body, exactly as delivered.
        header: The header's value, or None when absent.

    Returns:
        True when the header is the HMAC-SHA256 of the body under the secret.
    """
    if not header or not header.startswith(_SIGNATURE_PREFIX) or not secret:
        return False
    presented = header[len(_SIGNATURE_PREFIX) :].strip().lower()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, presented)


def _installation_id(payload: dict[str, Any]) -> int | None:
    """
    Read the installation that delivered an event.

    Args:
        payload: The event.

    Returns:
        Its id, or None.
    """
    installation = payload.get("installation")
    if isinstance(installation, dict) and isinstance(installation.get("id"), int):
        return int(installation["id"])
    return None


def parse_push(payload: dict[str, Any]) -> PushEvent | None:
    """
    Read a ``push`` event.

    Args:
        payload: GitHub's payload.

    Returns:
        The push, or None for a push that deploys nothing: a tag, a deleted
        branch, or a payload missing what a push has.
    """
    ref = payload.get("ref")
    if not isinstance(ref, str) or not ref.startswith("refs/heads/") or payload.get("deleted"):
        return None
    repository = json_object(payload.get("repository"))
    full_name = repository.get("full_name")
    head = payload.get("after")
    if not isinstance(full_name, str) or not isinstance(head, str):
        return None
    return PushEvent(
        forge=Forge.GITHUB,
        repository=full_name,
        clone_url=str(
            repository.get("clone_url") or f"https://{configured_host()}/{full_name}.git"
        ),
        branch=ref.removeprefix("refs/heads/"),
        head_sha=head,
        installation_id=_installation_id(payload),
        change=parse_push_change("github", payload),
    )


def repository_of(source: str | None, hosts: Sequence[str] | None) -> str | None:
    """
    Args:
        source: An application's source.
        hosts: The hosts whose repositories count; the integration's own by default.

    Returns:
        ``owner/repo`` in lower case, or None when the source is not a repository of those hosts.
    """
    named = integration_repository(source) if hosts is None else github_repository(source, hosts)
    return named.lower() if named else None


def follows_push(
    *,
    source: str | None,
    branch: str | None,
    follow_tags: str | None,
    preview_parent: str | None,
    push: PushEvent,
    default_branch: str | None,
    hosts: Sequence[str] | None = None,
) -> bool:
    """
    Decide whether an application is updated by a push: the one rule, whoever asks.

    An application follows a push when its source is the pushed repository and the branch it
    deploys is the one pushed to: its own branch, the ``#branch`` of its source, or else the
    repository's default branch. Previews are left out, and so are the applications that follow
    tags: a branch is news about something they do not deploy. This server's own applications
    and the ones a central finds on its servers are asked the same question with the same
    fields, so they cannot disagree.

    Args:
        source: The application's source.
        branch: The branch it is set to deploy, when it was set.
        follow_tags: The tag pattern it follows instead of a branch.
        preview_parent: The application it previews, when it is a preview.
        push: The push.
        default_branch: The repository's default branch, from the payload.
        hosts: The hosts whose repositories count; the integration's own by default (a server
            told of a push on another host by its central names it).

    Returns:
        True when the push updates it.
    """
    if preview_parent or follow_tags:
        return False
    if repository_of(source, hosts) != push.repository.lower():
        return False
    wanted = branch or parse_git_url(source or "")["branch"] or default_branch
    return wanted == push.branch


def follows_tags(
    *,
    source: str | None,
    follow_tags: str | None,
    preview_parent: str | None,
    repository: str,
    hosts: Sequence[str] | None = None,
) -> bool:
    """
    Decide whether an application is concerned by a release or a tag push.

    Whether the tag is one of its own (the pattern, the order) is the application's to decide, so
    one that will ignore it is still concerned and the delivery log can say why.

    Args:
        source: The application's source.
        follow_tags: The tag pattern it follows; none means it does not follow tags.
        preview_parent: The application it previews, when it is a preview.
        repository: ``owner/repo`` the tag is in.
        hosts: As for :func:`follows_push`.

    Returns:
        True when it follows tags of that repository.
    """
    if preview_parent or not follow_tags:
        return False
    return repository_of(source, hosts) == repository.lower()


def apps_following(
    push: PushEvent, default_branch: str | None, hosts: Sequence[str] | None = None
) -> list[App]:
    """
    Find the applications a push updates.

    Args:
        push: The push.
        default_branch: The repository's default branch, from the payload.
        hosts: The hosts whose repositories count; the integration's own by default.

    Returns:
        The applications, by domain.
    """
    following = [
        app
        for app in get_store().list_apps()
        if follows_push(
            source=app.source,
            branch=app.branch,
            follow_tags=app.follow_tags,
            preview_parent=app.preview_parent,
            push=push,
            default_branch=default_branch,
            hosts=hosts,
        )
    ]
    return sorted(following, key=lambda a: a.domain)


def apps_following_tags(event: TagEvent, hosts: Sequence[str] | None = None) -> list[App]:
    """
    Find the applications a release or a tag push concerns.

    Args:
        event: The tag announced.
        hosts: The hosts whose repositories count; the integration's own by default.

    Returns:
        The applications, by domain.
    """
    following = [
        app
        for app in get_store().list_apps()
        if follows_tags(
            source=app.source,
            follow_tags=app.follow_tags,
            preview_parent=app.preview_parent,
            repository=event.repository,
            hosts=hosts,
        )
    ]
    return sorted(following, key=lambda a: a.domain)


def apply_installation_event(event: str, payload: dict[str, Any]) -> str:
    """
    Keep the stored installations in step with an ``installation`` or
    ``installation_repositories`` event.

    Args:
        event: The ``X-GitHub-Event`` name.
        payload: GitHub's payload.

    Returns:
        What was done: ``saved``, ``deleted`` or ``ignored``.
    """
    installation = json_object(payload.get("installation"))
    if not installation.get("id"):
        return "ignored"
    action = str(payload.get("action") or "")
    store = get_store()
    if event == "installation" and action == "deleted":
        installation_id = int(installation["id"])
        store.delete_github_installation(installation_id)
        forget_tokens(installation_id)
        return "deleted"
    if event == "installation" and action == "suspend":
        # Kept: an unsuspend brings it back, and its applications keep
        # their link meanwhile. Its tokens stop working now.
        forget_tokens(int(installation["id"]))
        return "ignored"
    store.save_github_installation(installation_record(installation))
    return "saved"
