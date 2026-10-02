# Copyright (c) 2024-2026 Yago Lopez Prado
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
from typing import Any

from noust.core.forge_events import Forge, PushEvent, TagEvent
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
        clone_url=str(repository.get("clone_url") or f"https://github.com/{full_name}.git"),
        branch=ref.removeprefix("refs/heads/"),
        head_sha=head,
        installation_id=_installation_id(payload),
    )


def apps_following(push: PushEvent, default_branch: str | None) -> list[App]:
    """
    Find the applications a push updates.

    An application follows a push when its source is the pushed repository
    and the branch it deploys is the one pushed to: its own branch, the
    ``#branch`` of its source, or else the repository's default branch.
    Previews are left out; their pull request's events rebuild them, and so
    are the applications that follow tags (:func:`apps_following_tags`): a
    branch is news about something they do not deploy.

    Args:
        push: The push.
        default_branch: The repository's default branch, from the payload.

    Returns:
        The applications, by domain.
    """
    wanted = push.repository.lower()
    following: list[App] = []
    for app in get_store().list_apps():
        if app.preview_parent or app.follow_tags:
            continue
        repository = github_repository(app.source)
        if repository is None or repository.lower() != wanted:
            continue
        branch = app.branch or parse_git_url(app.source)["branch"] or default_branch
        if branch == push.branch:
            following.append(app)
    return sorted(following, key=lambda a: a.domain)


def apps_following_tags(event: TagEvent) -> list[App]:
    """
    Find the applications a release or a tag push concerns.

    Those whose source is the repository the tag is in and that follow tags.
    Whether the tag is one of theirs (the pattern, the order) is each one's to
    decide, so an application that will ignore it is still returned and the
    delivery log can say why.

    Args:
        event: The tag announced.

    Returns:
        The applications, by domain.
    """
    wanted = event.repository.lower()
    following: list[App] = []
    for app in get_store().list_apps():
        if app.preview_parent or not app.follow_tags:
            continue
        repository = github_repository(app.source)
        if repository is not None and repository.lower() == wanted:
            following.append(app)
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
