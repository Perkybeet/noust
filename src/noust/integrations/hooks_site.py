# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A public address for the console's webhooks, and for nothing else.

GitHub (and GitLab, and Gitea) must reach ``/hooks/...`` to deliver pushes and
pull requests, but the console itself should stay on loopback, reached through
an SSH tunnel. ``wasm web expose-hooks DOMAIN`` puts an nginx site of WASM's
own on a dedicated name that forwards ``/hooks/`` to the console on
``127.0.0.1`` and answers 404 to every other path, with a certificate obtained
the way every site gets one. The public URL is written to ``web.hooks_url``,
which the GitHub App's manifest and status read.

The name must be dedicated: a name that belongs to an application is refused,
because the hooks site would take it over, and the site of an application is
never edited to add a route. A site already on that name that WASM did not
write for this purpose is refused too.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from wasm.core.config import Config
from wasm.core.exceptions import (
    CertificateError,
    DomainError,
    IntegrationError,
    SiteError,
    WASMError,
)
from wasm.core.store import get_store
from wasm.managers.cert_manager import CertManager
from wasm.managers.webserver import NGINX_BACKEND, WebServerManager
from wasm.validators.domain import is_valid_domain

logger = logging.getLogger(__name__)

#: The template, under ``templates/nginx``.
TEMPLATE = "hooks"

#: The first line of every hooks site: how WASM recognises its own.
MARKER = "# WASM hooks site for "

#: The configuration key the public URL of ``/hooks`` is kept in:
#: ``https://hooks.example.com/hooks``.
HOOKS_URL_KEY = "web.hooks_url"


def public_hooks_url() -> str | None:
    """
    Read the public URL of the console's ``/hooks`` surface.

    Returns:
        ``https://<domain>/hooks``, or None when none was exposed.
    """
    value = Config().get(HOOKS_URL_KEY)
    return str(value).rstrip("/") if value else None


@dataclass
class HooksExposure:
    """
    What exposing the hooks did.

    Attributes:
        domain: The dedicated name.
        hooks_url: The public URL of ``/hooks``, as written to the
            configuration; None when it was not written.
        ssl_enabled: Whether the site serves TLS.
        certificate_reused: Whether an existing certificate covered it.
        github_webhook: ``updated`` when the GitHub App's webhook now points
            here, ``inactive`` when it does but must still be switched on
            at GitHub, ``failed`` when GitHub refused, None without an App.
        notes: What the operator should know, in order.
    """

    domain: str
    hooks_url: str | None = None
    ssl_enabled: bool = False
    certificate_reused: bool = False
    github_webhook: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Serialise for JSON.

        Returns:
            The record as plain data.
        """
        return asdict(self)


def save_hooks_url(url: str | None) -> None:
    """
    Record the public hooks URL in the configuration file.

    Args:
        url: ``https://<domain>/hooks``, or None to forget it.
    """
    config = Config()
    config.set(HOOKS_URL_KEY, url or "")
    config.write()


def _check_domain(domain: str) -> str:
    """
    Validate the dedicated name and refuse one an application uses.

    Args:
        domain: The name, as typed.

    Returns:
        The name, normalised.

    Raises:
        DomainError: It is not a domain, or it belongs to an application.
    """
    name = domain.strip().lower().rstrip(".")
    valid, reason = is_valid_domain(name)
    if not valid:
        raise DomainError(f"Invalid domain: {domain!r}", details=reason)
    owner = get_store().domain_owner(name)
    if owner is not None:
        raise DomainError(
            f"{name} belongs to the application {owner[0]}",
            details="The hooks site needs a name of its own (hooks.example.com, say) "
            "pointing at this server; an application's site is never edited to add it.",
        )
    return name


def _is_ours(manager: WebServerManager, domain: str) -> bool:
    """
    Tell whether the site on a name is a hooks site WASM wrote.

    Args:
        manager: The nginx manager.
        domain: The name.

    Returns:
        True when there is a site and it starts with :data:`MARKER`.
    """
    text = manager.get_site_config(domain)
    return text is not None and text.lstrip().startswith(MARKER)


def _write(manager: WebServerManager, domain: str, context: dict[str, Any], exists: bool) -> None:
    """
    Write the site and reload nginx, or say verbatim why nginx refused.

    Args:
        manager: The nginx manager.
        domain: The name.
        context: The template's variables.
        exists: Whether the site is already there.

    Raises:
        SiteError: nginx refused the configuration.
    """
    if exists:
        manager.update_site(domain, template=TEMPLATE, context=context)
    else:
        manager.create_site(domain, template=TEMPLATE, context=context)
        manager.enable_site(domain)
    if not manager.reload():
        raise SiteError(
            f"nginx refused the configuration of {domain}",
            details="Fix what nginx says, then run the command again.",
            output=manager.config_errors(),
        )


def expose(
    domain: str,
    *,
    port: int,
    scheme: str = "http",
    ssl: bool = True,
    manager: WebServerManager | None = None,
    cert_manager: CertManager | None = None,
    verbose: bool = False,
) -> HooksExposure:
    """
    Serve the console's ``/hooks/`` on a dedicated public name.

    Args:
        domain: The name, already pointing at this server.
        port: The console's port on loopback.
        scheme: ``https`` when the console serves TLS itself.
        ssl: Obtain a certificate; False serves plain HTTP, which a forge
            accepts but which shows every payload to the network.
        manager: The nginx manager (tests).
        cert_manager: The certificate manager (tests).
        verbose: Verbose logging on managers built here.

    Returns:
        What was done.

    Raises:
        DomainError: The name is invalid or an application's.
        SiteError: A site not written for this is already on the name, or
            nginx refused the configuration.
        CertificateError: No certificate could be obtained; the site stays,
            over HTTP, and nothing is recorded, so running again retries.
    """
    name = _check_domain(domain)
    manager = manager or WebServerManager(NGINX_BACKEND, verbose=verbose)
    exists = manager.site_exists(name)
    if exists and not _is_ours(manager, name):
        raise SiteError(
            f"{name} already has an nginx site WASM did not write for the hooks",
            details=f"Choose another name, or remove {manager.config_path(name)} first.",
        )

    context: dict[str, Any] = {"port": int(port), "upstream_scheme": scheme, "ssl": False}
    _write(manager, name, context, exists)
    result = HooksExposure(domain=name)

    if ssl:
        cert_manager = cert_manager or CertManager(verbose=verbose)
        if not cert_manager.is_installed():
            raise CertificateError(
                "certbot is not installed",
                details=f"Install certbot, or run with --no-ssl to serve {name} over HTTP.",
            )
        if cert_manager.cert_exists(name) and cert_manager.test_cert(name).get("valid"):
            result.certificate_reused = cert_manager.cert_covers_domains(name, [name])
        if not result.certificate_reused:
            cert_manager.obtain(name, nginx=True)
        paths = cert_manager.get_cert_path(name)
        context.update(
            ssl=True,
            ssl_certificate=str(paths["fullchain"]),
            ssl_certificate_key=str(paths["privkey"]),
        )
        _write(manager, name, context, True)
        result.ssl_enabled = True
    else:
        result.notes.append(
            "Served over plain HTTP: deliveries are signed, but anyone on the path reads them."
        )

    result.hooks_url = f"{'https' if ssl else 'http'}://{name}/hooks"
    save_hooks_url(result.hooks_url)
    result.github_webhook = _point_github_webhook(f"{result.hooks_url}/github", result)
    return result


def _point_github_webhook(url: str, result: HooksExposure) -> str | None:
    """
    Point this server's GitHub App's webhook at the new URL, when there is an App.

    Args:
        url: The public URL of ``/hooks/github``.
        result: Where to add notes.

    Returns:
        As :attr:`HooksExposure.github_webhook`.
    """
    from wasm.integrations.github import service
    from wasm.integrations.github.app import github_app_configured

    if not github_app_configured():
        return None
    try:
        active = service.configure_webhook(url)
    except IntegrationError as exc:
        result.notes.append(f"The GitHub App's webhook was not updated: {exc.message}")
        return "failed"
    if not active:
        status = service.status()
        result.notes.append(
            "Switch the GitHub App's webhook on: on "
            f"{status.settings_url}, tick 'Active' under Webhook and save; then under "
            "'Permissions & events', subscribe to Push and Pull request and save. "
            "An App created before this server had a public hooks URL has neither."
        )
        return "inactive"
    return "updated"


def unexpose(
    domain: str,
    *,
    manager: WebServerManager | None = None,
    cert_manager: CertManager | None = None,
    verbose: bool = False,
) -> bool:
    """
    Remove a hooks site, its certificate, and the recorded URL when it named it.

    Args:
        domain: The name.
        manager: The nginx manager (tests).
        cert_manager: The certificate manager (tests).
        verbose: Verbose logging on managers built here.

    Returns:
        True when a hooks site was removed.

    Raises:
        SiteError: The site on the name is not a hooks site.
    """
    name = domain.strip().lower().rstrip(".")
    manager = manager or WebServerManager(NGINX_BACKEND, verbose=verbose)
    removed = False
    if manager.site_exists(name):
        if not _is_ours(manager, name):
            raise SiteError(
                f"The nginx site of {name} is not a hooks site",
                details="'wasm web expose-hooks --remove' only removes what it created.",
            )
        manager.delete_site(name)
        manager.reload()
        removed = True
        cert_manager = cert_manager or CertManager(verbose=verbose)
        if cert_manager.is_installed() and cert_manager.cert_exists(name):
            try:
                cert_manager.delete(name)
            except WASMError as exc:
                logger.warning("Could not remove the certificate of %s: %s", name, exc)
    current = public_hooks_url() or ""
    if current.split("://", 1)[-1].split("/", 1)[0] == name:
        save_hooks_url(None)
    return removed
