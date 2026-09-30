# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The deploy webhook of one application, as the operator has to set it up.

A push to the repository deploys the application when the forge signs a
delivery with the application's secret and Noust checks it
(:mod:`noust.web.api.hooks`). Setting that up takes five things that live in
five places - the public hooks URL, the secret, the forge's settings page, the
branch, the GitHub App that may already do the job - and none of them said
whether it was done. :func:`status` gathers them into one answer, which the
console's guided setup and ``noust app webhook show`` both draw from, so the
CLI and the API cannot disagree about what "connected" means. The secret
itself is created, rotated and shown here too, for the same reason.

This module is outside :mod:`noust.web` on purpose: the CLI must work on a
server that runs no console, and imports nothing that needs FastAPI.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from noust.core import webhook_deliveries
from noust.core.exceptions import DeploymentError, NoustError
from noust.core.store import App, get_store
from noust.core.webhook_deliveries import WebhookDelivery
from noust.validators.source import github_repository

logger = logging.getLogger(__name__)

#: What the forge must be told to send: the body is the event as JSON.
CONTENT_TYPE = "application/json"

#: The events worth sending. A push is the only one that deploys; a forge that
#: sends everything only fills the delivery list with what is ignored.
EVENTS = ["push"]

#: Forges whose settings page Noust can link to, by host (or a word in it).
_GITLAB_WORD = "gitlab"
_GITEA_HOSTS = ("codeberg.org", "gitea.com")
_GITEA_WORD = "gitea"


@dataclass
class HooksInfo:
    """
    Where the forge delivers.

    Attributes:
        exposed: Whether ``noust web expose-hooks`` published ``/hooks/``.
        base_url: The public base, ``https://hooks.example.com/hooks``.
        hook_url: The exact payload URL of this application: the public one
            when exposed, else the caller's own address when it gave one.
        hook_url_public: Whether ``hook_url`` is one a forge can reach.
        content_type: What the forge must send.
        events: The events to enable at the forge.
    """

    exposed: bool
    base_url: str | None
    hook_url: str | None
    hook_url_public: bool
    content_type: str = CONTENT_TYPE
    events: list[str] = field(default_factory=lambda: list(EVENTS))


@dataclass
class BranchInfo:
    """
    Which pushes deploy.

    Attributes:
        tracked: The branch the application deploys, None when none is pinned.
        pinned: Whether one is.
        any_push_deploys: True when none is pinned: a push to any branch of
            the repository then deploys this application.
    """

    tracked: str | None
    pinned: bool
    any_push_deploys: bool


@dataclass
class ForgeInfo:
    """
    The forge side of the setup.

    Attributes:
        forge: ``github``, ``gitlab`` or ``gitea``; None for another host or a
            source that is not a remote repository.
        host: The forge's host name. Never a credential: a source may carry a
            token in its URL, and nothing here repeats it.
        repository: ``owner/repo`` (``group/subgroup/repo`` on GitLab).
        settings_url: Where to add the webhook, when the forge is known.
    """

    forge: str | None
    host: str | None
    repository: str | None
    settings_url: str | None


@dataclass
class GitHubAppInfo:
    """
    Whether this server's GitHub App already deploys the repository.

    Attributes:
        configured: Whether this server has an App.
        hooks_active: Whether the App's webhook points at this server.
        covers_repository: Whether a push to the repository reaches this
            application through the App - in which case a webhook of its own
            would deploy twice.
        account: The account the covering installation belongs to.
        repository_selection: ``all`` or ``selected``: on ``selected`` the
            App covers only the repositories that were picked, and the
            application is covered only when it is linked to the installation.
        settings_url: Where to change which repositories the installation has.
    """

    configured: bool
    hooks_active: bool = False
    covers_repository: bool = False
    account: str | None = None
    repository_selection: str | None = None
    settings_url: str | None = None


@dataclass
class DeliveriesInfo:
    """
    What the forge has been sending.

    Attributes:
        total: Deliveries kept.
        refused_since_last_verified: Wrong signatures and lockouts since the
            last delivery that verified.
        last: The newest delivery.
        last_verified_at: When the newest delivery that verified arrived.
        last_push_at: When the newest push that deployed arrived.
    """

    total: int
    refused_since_last_verified: int
    last: WebhookDelivery | None
    last_verified_at: str | None
    last_push_at: str | None


@dataclass
class WebhookStatus:
    """
    Everything the guided setup shows about one application's webhook.

    Attributes:
        domain: The application.
        enabled: Whether a secret exists. The secret is never in this answer.
        state: ``disabled`` (no secret), ``waiting`` (a secret and no delivery
            yet), ``connected`` (the forge reaches Noust and the last delivery
            was not refused) or ``problem`` (the last delivery was refused:
            the secret at the forge is wrong, or someone is guessing).
        layout: ``releases`` or ``inplace``.
        inplace_warning: True when an in-place application deploys on push:
            each one rebuilds the live tree, without the instant way back a
            release gives.
        hooks: Where the forge delivers.
        branch: Which pushes deploy.
        forge: The forge side.
        github_app: Whether the GitHub App already covers the repository.
        deliveries: What the forge has been sending.
    """

    domain: str
    enabled: bool
    state: str
    layout: str
    inplace_warning: bool
    hooks: HooksInfo
    branch: BranchInfo
    forge: ForgeInfo
    github_app: GitHubAppInfo
    deliveries: DeliveriesInfo

    def to_dict(self) -> dict[str, Any]:
        """
        Serialise for JSON.

        Returns:
            The state as plain data; the newest delivery is a mapping too.
        """
        return asdict(self)


def mint_secret(domain: str) -> str:
    """
    Generate, store and return a fresh webhook secret for an application.

    Creating and rotating are the same operation: the old secret is replaced in
    the same motion, so the forge's copy stops working the moment this returns.

    Args:
        domain: Application domain, already validated.

    Returns:
        The secret in clear.

    Raises:
        DeploymentError: When no application is deployed at the domain.
    """
    secret = secrets.token_urlsafe(32)
    if not get_store().set_webhook_secret(domain, secret):
        raise DeploymentError(
            f"Application not found: {domain}",
            details="Deploy it first, or check 'noust list' for the exact domain.",
        )
    return secret


def disable_secret(domain: str) -> None:
    """
    Discard an application's webhook secret: deliveries answer 404 from now on.

    Args:
        domain: Application domain, already validated.

    Raises:
        DeploymentError: When no application is deployed at the domain.
    """
    if not get_store().set_webhook_secret(domain, None):
        raise DeploymentError(
            f"Application not found: {domain}",
            details="Check 'noust list' for the exact domain.",
        )


def reveal_secret(domain: str) -> str:
    """
    Read an application's webhook secret back, in clear.

    It is stored in clear because verifying a signature needs it; the callers
    are the ones that decide who may see it (sudo mode and ``secrets.reveal``
    in the console, root in the CLI).

    Args:
        domain: Application domain, already validated.

    Returns:
        The secret.

    Raises:
        DeploymentError: When the application has no secret.
    """
    secret = get_store().get_webhook_secret(domain)
    if not secret:
        raise DeploymentError(
            f"{domain} has no webhook secret",
            details=f"Create one with 'noust app webhook rotate {domain}'.",
        )
    return secret


def _forge_of(source: str | None) -> ForgeInfo:
    """
    Read the forge side of an application's source.

    Args:
        source: The source as stored; it may carry a token in its URL.

    Returns:
        The forge, host, repository and the page to add the webhook on. Only
        what the source names publicly: never the userinfo of its URL.
    """
    text = (source or "").strip()
    repository = github_repository(text)
    if repository is not None:
        return ForgeInfo(
            forge="github",
            host="github.com",
            repository=repository,
            settings_url=f"https://github.com/{repository}/settings/hooks/new",
        )

    host: str | None = None
    path = ""
    if text.startswith("git@") and ":" in text:
        host, _, path = text[len("git@") :].partition(":")
    else:
        parts = urlsplit(text.split("#", 1)[0])
        if parts.scheme in ("http", "https", "ssh", "git") and parts.hostname:
            host, path = parts.hostname, parts.path
    if not host:
        return ForgeInfo(forge=None, host=None, repository=None, settings_url=None)

    host = host.lower()
    repo = path.strip("/").removesuffix(".git")
    repository = repo or None
    forge: str | None = None
    settings_url: str | None = None
    if _GITLAB_WORD in host:
        forge = "gitlab"
        settings_url = f"https://{host}/{repo}/-/hooks" if repo else None
    elif host in _GITEA_HOSTS or _GITEA_WORD in host:
        forge = "gitea"
        settings_url = f"https://{host}/{repo}/settings/hooks/gitea/new" if repo else None
    return ForgeInfo(forge=forge, host=host, repository=repository, settings_url=settings_url)


def _github_app_of(app: App, repository: str | None, forge: str | None) -> GitHubAppInfo:
    """
    Decide whether the GitHub App already deploys an application's repository.

    Args:
        app: The application.
        repository: Its ``owner/repo`` on GitHub, None off GitHub.
        forge: The forge its source is on.

    Returns:
        The App's state and whether it covers the repository. An App that
        cannot be asked (sealed secrets, GitHub down) is reported as not
        configured rather than failing the whole answer.
    """
    from noust.integrations.github import service

    try:
        github = service.status()
    except NoustError as exc:
        logger.warning("Could not read the GitHub App's state: %s", exc)
        return GitHubAppInfo(configured=False)
    if not github.configured:
        return GitHubAppInfo(configured=False)
    info = GitHubAppInfo(configured=True, hooks_active=github.hooks_active)
    if forge != "github" or repository is None:
        return info

    owner = repository.split("/", 1)[0].lower()
    for installation in github.installations:
        linked = app.github_installation_id == installation.installation_id
        if not linked and installation.account.lower() != owner:
            continue
        info.account = installation.account
        info.repository_selection = installation.repository_selection
        info.settings_url = installation.settings_url
        # "selected" is only known to cover what the application was linked to:
        # the list of picked repositories is GitHub's, not stored here.
        info.covers_repository = github.hooks_active and (
            linked or installation.repository_selection == "all"
        )
        break
    return info


def _state_of(enabled: bool, last: WebhookDelivery | None) -> str:
    """
    Name where the setup stands.

    Args:
        enabled: Whether a secret exists.
        last: The newest delivery.

    Returns:
        ``disabled``, ``waiting``, ``problem`` or ``connected``.
    """
    if not enabled:
        return "disabled"
    if last is None:
        return "waiting"
    if last.outcome in webhook_deliveries.REFUSALS:
        return "problem"
    return "connected"


def status(app: App, *, fallback_base: str | None = None) -> WebhookStatus:
    """
    Gather what the guided setup needs to show about an application's webhook.

    Args:
        app: The application, as stored.
        fallback_base: The base of ``/hooks`` at an address the caller has,
            offered as the payload URL when nothing public was exposed. The
            console passes the address it was opened at; the CLI passes none.

    Returns:
        The state. Reads only: nothing is created or changed.
    """
    from noust.integrations.hooks_site import public_hooks_url

    store = get_store()
    enabled = bool(store.get_webhook_secret(app.domain))
    public = public_hooks_url()
    base = public or (fallback_base.rstrip("/") if fallback_base else None)
    hooks = HooksInfo(
        exposed=public is not None,
        base_url=public,
        hook_url=f"{base}/deploy/{app.domain}" if base else None,
        hook_url_public=public is not None,
    )

    pinned = bool(app.branch)
    branch = BranchInfo(tracked=app.branch or None, pinned=pinned, any_push_deploys=not pinned)

    forge = _forge_of(app.source)
    summary = (
        webhook_deliveries.summarize(app.id)
        if app.id is not None
        else webhook_deliveries.DeliverySummary(0, None, None, None, 0)
    )
    deliveries = DeliveriesInfo(
        total=summary.total,
        refused_since_last_verified=summary.refused_since_last_verified,
        last=summary.last,
        last_verified_at=summary.last_verified.received_at if summary.last_verified else None,
        last_push_at=summary.last_push.received_at if summary.last_push else None,
    )

    return WebhookStatus(
        domain=app.domain,
        enabled=enabled,
        state=_state_of(enabled, summary.last),
        layout=app.layout,
        inplace_warning=enabled and app.layout == "inplace",
        hooks=hooks,
        branch=branch,
        forge=forge,
        github_app=_github_app_of(app, forge.repository, forge.forge),
        deliveries=deliveries,
    )
