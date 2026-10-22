# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether the names of previews reach this server (checklist E-25).

A preview answers on a name of its own, and the name works only when a DNS record points it at the
server: with the default naming, one wildcard record (``*.previews.example.com``) covers every
preview. Nothing checked that. Turning previews on said "point a wildcard record at this server" and
the first pull request built, deployed and then failed to get a certificate, with certbot's error,
minutes later.

This asks the one DNS check every other feature uses (:func:`noust.deployers.domains.check_dns`):

- :func:`check_preview_dns` resolves the name the next preview would get, with a number no pull
  request has (0) and the application's own template, and says which wildcard record covers it. It
  answers ``noust preview dns`` and ``GET /api/apps/{domain}/previews/dns``, and is what
  ``noust preview enable`` prints beside the settings.
- :func:`problem_with` is asked before a preview is built: a name that points elsewhere is said in
  the job's log, once, so the failure that follows is not a mystery. It never refuses: DNS may be
  in flight, and a resolver that cannot be asked is no reason to stop a build. ``dns.check_previews:
  false`` turns it off.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from noust.core.config import Config
from noust.core.exceptions import NoustError, ValidationError
from noust.core.preview_names import preview_domain_for
from noust.core.preview_store import PreviewStore
from noust.core.store import get_store
from noust.validators.domain import validate_domain

log = logging.getLogger(__name__)

#: The number the probe name is made with: no pull request has it, so no record made for a real
#: preview answers for it, only the wildcard does.
PROBE_NUMBER = 0

#: What ``{{random}}`` stands for in the probe name.
PROBE_RANDOM = "dnsprobe0"

#: What turns the check of a preview about to be built off.
CONFIG_KEY = "dns.check_previews"

#: A verdict that reaches this server (``unknown``: a server behind NAT cannot say which address
#: is its own, and a record that looks wrong from inside may be right).
REACHES = ("here", "cdn", "unknown")

Check = Callable[..., Any]


def _default_check() -> Check:
    """
    Returns:
        :func:`noust.deployers.domains.check_dns`, imported here because that module imports the
        deployers, which import the managers.
    """
    from noust.deployers.domains import check_dns

    return check_dns


@dataclass(frozen=True)
class PreviewDns:
    """
    What the DNS says about the names previews will have.

    Attributes:
        app_domain: The application previewed.
        base_domain: The domain previews answer under.
        probe: A name shaped like a preview's, which was resolved.
        wildcard: The record that covers it (``*.previews.example.com``).
        verdict: ``here``, ``cdn``, ``elsewhere``, ``none`` or ``unknown`` (see
            :class:`~noust.deployers.domains.DnsCheck`).
        cdn: The CDN every address belongs to, when the verdict is ``cdn``.
        resolved_addresses: What the probe resolves to.
        expected_addresses: This server's addresses.
        ok: Whether a preview would be reached.
        advice: The record to add or change, when it would not.
    """

    app_domain: str
    base_domain: str
    probe: str
    wildcard: str
    verdict: str
    cdn: str | None
    resolved_addresses: tuple[str, ...]
    expected_addresses: tuple[str, ...]
    ok: bool
    advice: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        data = asdict(self)
        data["resolved_addresses"] = list(self.resolved_addresses)
        data["expected_addresses"] = list(self.expected_addresses)
        return data


def advice_for(
    verdict: str, wildcard: str, resolved: tuple[str, ...], expected: tuple[str, ...]
) -> str | None:
    """
    Say which record a preview's name lacks.

    Args:
        verdict: The DNS check's verdict.
        wildcard: The record that covers the names.
        resolved: What the name resolves to.
        expected: This server's addresses.

    Returns:
        A sentence; None when the name reaches the server.
    """
    server = ", ".join(expected) or "this server"
    if verdict in REACHES:
        return None
    if verdict == "none":
        return f"Add a record {wildcard} (A, and AAAA if the server has an IPv6 address) pointing at {server}."
    return (
        f"{wildcard} points at {', '.join(resolved) or 'another address'}, not at {server}: "
        "correct the record, or Let's Encrypt will not reach the preview."
    )


def check_preview_dns(
    app_domain: str,
    *,
    check: Check | None = None,
) -> PreviewDns:
    """
    Resolve the name the next preview of an application would get.

    Args:
        app_domain: The application previewed.
        check: The DNS check; the shared one by default. Tests pass their own.

    Returns:
        The verdict and the record to add.

    Raises:
        ValidationError: The application is not deployed, or its previews are off.
    """
    domain = validate_domain(app_domain)
    store = get_store()
    if store.get_app(domain) is None:
        raise ValidationError(
            f"Application not found: {domain}", details="Run 'noust list' for the exact domain."
        )
    settings = store.get_preview_settings(domain)
    if settings is None:
        raise ValidationError(
            f"Previews are off for {domain}",
            details=f"Turn them on first: noust preview enable {domain} --domain BASE",
        )
    options = PreviewStore(store).options(domain)
    probe = preview_domain_for(
        domain,
        PROBE_NUMBER,
        settings.base_domain,
        template=options.domain_template,
        random=PROBE_RANDOM,
    )
    wildcard = f"*.{probe.partition('.')[2]}"
    found = (check or _default_check())(probe)
    resolved = tuple(found.resolved_addresses)
    expected = tuple(found.expected_addresses)
    return PreviewDns(
        app_domain=domain,
        base_domain=settings.base_domain,
        probe=probe,
        wildcard=wildcard,
        verdict=found.verdict,
        cdn=found.cdn,
        resolved_addresses=resolved,
        expected_addresses=expected,
        ok=found.verdict in REACHES,
        advice=advice_for(found.verdict, wildcard, resolved, expected),
    )


def problem_with(preview_domain: str, *, check: Check | None = None) -> str | None:
    """
    Say, before a preview is built, that its name does not reach this server.

    Args:
        preview_domain: The preview's domain.
        check: The DNS check; the shared one by default. Tests pass their own.

    Returns:
        A sentence for the job's log; None when the name reaches the server, when the check is
        off, or when it could not be made.
    """
    if not bool(Config().get(CONFIG_KEY, True)):
        return None
    try:
        found = (check or _default_check())(preview_domain)
    except (NoustError, OSError) as exc:
        log.debug("The DNS of %s was not checked: %s", preview_domain, exc)
        return None
    if found.verdict in REACHES:
        return None
    where = ", ".join(found.resolved_addresses)
    if found.verdict == "none":
        return (
            f"{preview_domain} does not resolve: its certificate will not be issued. "
            "Check the wildcard record of the previews (noust preview dns)."
        )
    return (
        f"{preview_domain} resolves to {where}, which is not this server: its certificate "
        "will not be issued. Check the wildcard record of the previews (noust preview dns)."
    )
