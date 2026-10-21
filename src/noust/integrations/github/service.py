# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the console and ``noust github`` do with this server's GitHub App.

One implementation for both front ends: status, installations, the
repositories and branches the wizard offers, the webhook URL, and removal.
The API router and the CLI translate to and from these calls and hold no
logic of their own.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote

from noust.core.config import Config
from noust.core.exceptions import IntegrationError, ValidationError
from noust.core.github_host import GITHUB_COM, configured_host, source_for, web_url
from noust.core.secrets import SecretStore
from noust.core.store import GitHubInstallationRecord, get_store
from noust.integrations.github.app import (
    SECRET_NAMESPACE,
    WEBHOOK_SECRET,
    GitHubApp,
    forget_tokens,
    installation_for,
    load_app,
    read_meta,
    write_meta,
)
from noust.integrations.github.client import json_object
from noust.integrations.hooks_site import public_hooks_url
from noust.validators.source import GITHUB_SHORTHAND_PATTERN

logger = logging.getLogger(__name__)


def github_hooks_url() -> str | None:
    """
    Return where GitHub should deliver the App's events.

    Returns:
        ``<public hooks URL>/github``, or None when there is no public URL.
    """
    base = public_hooks_url()
    return f"{base}/github" if base else None


@dataclass
class InstallationInfo:
    """
    An account the App is installed on.

    Attributes:
        installation_id: GitHub's id of the installation.
        account: The account's login.
        account_type: ``User`` or ``Organization``.
        repository_selection: ``all`` or ``selected``.
        settings_url: Where to change which repositories it covers.
    """

    installation_id: int
    account: str
    account_type: str | None = None
    repository_selection: str | None = None
    settings_url: str | None = None


@dataclass
class GitHubStatus:
    """
    The integration as the console shows it.

    Attributes:
        configured: Whether this server has an App.
        app_id: The App's id.
        slug: The App's URL name.
        name: The App's display name.
        owner: The account that owns the App.
        html_url: The App's public page.
        settings_url: The App's settings page, where it is deleted.
        install_url: Where to install it on another account.
        installations: Where it is installed.
        hooks_url: Where GitHub delivers events, when this server has a
            public URL for them.
        hooks_active: Whether the App's webhook is set to that URL.
        host: The GitHub the integration talks to: github.com, or the Enterprise
            host (``github.host``).
    """

    configured: bool
    app_id: int | None = None
    slug: str | None = None
    name: str | None = None
    owner: str | None = None
    html_url: str | None = None
    settings_url: str | None = None
    install_url: str | None = None
    installations: list[InstallationInfo] = field(default_factory=list)
    hooks_url: str | None = None
    hooks_active: bool = False
    host: str = "github.com"

    def to_dict(self) -> dict[str, Any]:
        """
        Serialise for JSON.

        Returns:
            The status as plain data.
        """
        return asdict(self)


def _require_app() -> GitHubApp:
    """
    Load the App or say how to create one.

    Returns:
        The App.

    Raises:
        IntegrationError: This server has no GitHub App.
    """
    app = load_app()
    if app is None:
        raise IntegrationError(
            "This server has no GitHub App",
            details="Create it in the console: Integrations, GitHub, Create the App.",
        )
    return app


def settings_url(slug: str, owner: str | None, owner_type: str | None) -> str:
    """
    Name the App's settings page on GitHub.

    Args:
        slug: The App's URL name.
        owner: The owning account.
        owner_type: ``Organization`` or ``User``.

    Returns:
        The page where the App is edited and, at the bottom, deleted.
    """
    if owner and owner_type == "Organization":
        return f"{web_url()}/organizations/{quote(owner)}/settings/apps/{quote(slug)}"
    return f"{web_url()}/settings/apps/{quote(slug)}"


def _installation_settings(record: GitHubInstallationRecord) -> str:
    """
    Name an installation's settings page on GitHub.

    Args:
        record: The installation.

    Returns:
        The page where its repositories are chosen or it is uninstalled.
    """
    if record.account_type == "Organization":
        return (
            f"{web_url()}/organizations/{quote(record.account)}/settings/installations/"
            f"{record.installation_id}"
        )
    return f"{web_url()}/settings/installations/{record.installation_id}"


def _info(record: GitHubInstallationRecord) -> InstallationInfo:
    """
    Describe a stored installation.

    Args:
        record: The installation.

    Returns:
        Its description.
    """
    return InstallationInfo(
        installation_id=record.installation_id,
        account=record.account,
        account_type=record.account_type,
        repository_selection=record.repository_selection,
        settings_url=_installation_settings(record),
    )


def set_host(host: str | None, *, force: bool = False, config: Config | None = None) -> str:
    """
    Choose the GitHub the integration talks to: github.com, or an Enterprise host.

    The App lives on the host that created it, so a server that has one keeps its host: changing
    it would leave an App, its key and its installations pointing at a GitHub that does not know
    them. Remove the App first (``noust github remove``), or pass ``force`` to change the host
    anyway.

    Args:
        host: A host name, or None (or empty) for github.com.
        force: Change the host although an App exists.
        config: The configuration to change; the process-wide one when None.

    Returns:
        The host now in force.

    Raises:
        ConfigError: Not a host name, or an address inside this machine's networks that is
            not allowed.
        ValidationError: An App exists on the current host.
    """
    from noust.core import audit
    from noust.core.github_host import require_reachable, validate_host

    settings = config or Config()
    chosen = validate_host(host)
    target = chosen or GITHUB_COM
    if target != configured_host(settings):
        if get_store().get_github_app() is not None and not force:
            raise ValidationError(
                f"This server has a GitHub App on {configured_host(settings)}",
                details="It cannot move to another GitHub: remove it first with "
                "'noust github remove' and create it again on the new host.",
                field="host",
            )
        require_reachable(target)
    settings.set("github.host", chosen)
    settings.save()
    forget_tokens()
    audit.record("integration.github.host", target="integration:github", details={"host": target})
    return target


def status() -> GitHubStatus:
    """
    Describe the integration, without calling GitHub.

    Returns:
        The status.
    """
    hooks = github_hooks_url()
    record = get_store().get_github_app()
    if record is None:
        return GitHubStatus(configured=False, hooks_url=hooks, host=configured_host())
    meta = read_meta()
    active = bool(
        hooks
        and meta.get("webhook_active")
        and meta.get("webhook_url") == hooks
        and SecretStore().read(WEBHOOK_SECRET)
    )
    return GitHubStatus(
        configured=True,
        app_id=record.app_id,
        slug=record.slug,
        name=record.name,
        owner=record.owner,
        html_url=record.html_url,
        settings_url=settings_url(record.slug, record.owner, meta.get("owner_type")),
        install_url=f"{web_url()}/apps/{quote(record.slug)}/installations/new",
        installations=[_info(r) for r in get_store().list_github_installations()],
        hooks_url=hooks,
        hooks_active=active,
        host=configured_host(),
    )


def installation_record(payload: dict[str, Any]) -> GitHubInstallationRecord:
    """
    Read an installation as GitHub describes it.

    Args:
        payload: GitHub's installation object.

    Returns:
        The record to store.

    Raises:
        IntegrationError: The object lacks its id or account.
    """
    account = json_object(payload.get("account"))
    try:
        installation_id = int(payload["id"])
    except (KeyError, TypeError, ValueError):
        raise IntegrationError("GitHub described an installation without an id") from None
    login = account.get("login") or account.get("slug") or account.get("name")
    if not login:
        raise IntegrationError(
            f"GitHub described installation {installation_id} without an account"
        )
    return GitHubInstallationRecord(
        installation_id=installation_id,
        account=str(login),
        account_type=account.get("type"),
        repository_selection=payload.get("repository_selection"),
    )


def add_installation(installation_id: int) -> InstallationInfo:
    """
    Record an installation GitHub's setup redirect named, after asking GitHub about it.

    The id comes from a browser's query string, so it is only believed once
    GitHub confirms the installation belongs to this App.

    Args:
        installation_id: The ``installation_id`` of the redirect.

    Returns:
        The installation as stored.

    Raises:
        IntegrationError: There is no App, or GitHub does not know the
            installation for this App.
    """
    app = _require_app()
    answer = app.app_request("GET", f"/app/installations/{int(installation_id)}")
    if not isinstance(answer, dict):
        raise IntegrationError(f"GitHub answered nothing about installation {installation_id}")
    record = get_store().save_github_installation(installation_record(answer))
    if record.account_type and record.account == app.record.owner:
        write_meta(owner_type=record.account_type)
    return _info(record)


def sync_installations() -> list[InstallationInfo]:
    """
    Make the stored installations exactly the App's installations on GitHub.

    Returns:
        The installations now stored.

    Raises:
        IntegrationError: There is no App, or GitHub refused.
    """
    app = _require_app()
    answer = app.app_paginate("/app/installations")
    seen: set[int] = set()
    store = get_store()
    for payload in answer:
        if isinstance(payload, dict):
            record = store.save_github_installation(installation_record(payload))
            seen.add(record.installation_id)
            if record.account_type and record.account == app.record.owner:
                write_meta(owner_type=record.account_type)
    for existing in store.list_github_installations():
        if existing.installation_id not in seen:
            store.delete_github_installation(existing.installation_id)
            forget_tokens(existing.installation_id)
    return [_info(r) for r in store.list_github_installations()]


def list_repositories() -> list[dict[str, Any]]:
    """
    List every repository the App's installations cover.

    Returns:
        ``full_name``, ``private``, ``default_branch``, ``clone_url``,
        ``source`` (the ``github:`` shorthand to deploy it) and
        ``installation_id`` of each, sorted by name.

    Raises:
        IntegrationError: There is no App, or GitHub refused.
    """
    app = _require_app()
    repositories: list[dict[str, Any]] = []
    for installation in get_store().list_github_installations():
        items = app.paginate_as_installation(
            installation.installation_id, "/installation/repositories", key="repositories"
        )
        for item in items:
            if not isinstance(item, dict) or not item.get("full_name"):
                continue
            full_name = str(item["full_name"])
            repositories.append(
                {
                    "full_name": full_name,
                    "private": bool(item.get("private")),
                    "default_branch": item.get("default_branch"),
                    "clone_url": item.get("clone_url"),
                    "source": source_for(full_name),
                    "installation_id": installation.installation_id,
                }
            )
    return sorted(repositories, key=lambda r: r["full_name"].lower())


def list_branches(owner: str, repo: str) -> list[dict[str, Any]]:
    """
    List a repository's branches, as the installation that covers it sees them.

    Args:
        owner: The repository's owner.
        repo: The repository's name.

    Returns:
        ``name``, ``protected`` and ``commit`` (its head) of each branch.

    Raises:
        ValidationError: The names are not a GitHub repository.
        IntegrationError: No installation covers the repository, or GitHub
            refused.
    """
    full_name = f"{owner}/{repo}"
    if not GITHUB_SHORTHAND_PATTERN.match(f"github:{full_name}"):
        raise ValidationError(f"Not a GitHub repository: {full_name!r}", field="repository")
    app = _require_app()
    installation = installation_for(full_name)
    if installation is None:
        raise IntegrationError(
            f"No installation of the App covers {full_name}",
            details=f"Install the App on the {owner} account, or add the repository to "
            "its installation.",
        )
    items = app.paginate_as_installation(
        installation, f"/repos/{quote(owner)}/{quote(repo)}/branches"
    )
    branches = []
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        commit = json_object(item.get("commit"))
        branches.append(
            {
                "name": str(item["name"]),
                "protected": bool(item.get("protected")),
                "commit": commit.get("sha"),
            }
        )
    return branches


def configure_webhook(url: str) -> bool:
    """
    Point the App's webhook at a URL, with a secret this server holds.

    GitHub lets an App change its webhook's URL and secret but not switch it
    on, so an App created before the server had a public URL still needs its
    webhook marked active once on GitHub; the return value says whether that
    is the case.

    Args:
        url: Where GitHub should deliver.

    Returns:
        True when the webhook is known to be active; False when the operator
        must still tick "Active" on the App's settings page.

    Raises:
        IntegrationError: There is no App, or GitHub refused.
    """
    app = _require_app()
    store = SecretStore()
    secret = store.read(WEBHOOK_SECRET)
    if not secret:
        secret = secrets.token_urlsafe(32)
    app.app_request(
        "PATCH",
        "/app/hook/config",
        {"url": url, "content_type": "json", "secret": secret, "insecure_ssl": "0"},
    )
    store.write(WEBHOOK_SECRET, secret)
    meta = read_meta()
    active = bool(meta.get("webhook_active"))
    write_meta(webhook_url=url, webhook_active=active)
    return active


def remove() -> dict[str, Any]:
    """
    Forget the App: its credentials, its installations, every link to them.

    GitHub has no API for an App to delete itself, so what this returns is
    where the operator does it.

    Returns:
        ``removed`` (whether there was one) and ``settings_url``, the App's
        page on GitHub, where it is deleted (or None when there was none).
    """
    store = get_store()
    record = store.get_github_app()
    url = None
    if record is not None:
        url = settings_url(record.slug, record.owner, read_meta().get("owner_type"))
    removed = store.delete_github_app()
    SecretStore().delete_namespace(SECRET_NAMESPACE)
    forget_tokens()
    return {"removed": removed, "settings_url": url}
