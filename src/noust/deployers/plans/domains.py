# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What adding a domain to an application, or removing one, would do (item 183).

Both are the functions the console calls (:func:`noust.deployers.domains.add_domain` and
:func:`~noust.deployers.domains.remove_domain`), rehearsed with the arguments the console gives them
(the certificate is a job of its own, so it is not part of the call): the site rewritten, the web
server tested and reloaded. Whether a name points at this server is the one thing the plan leaves to
the DNS, and says so.
"""

from __future__ import annotations

from noust.core.plans.shape import PlanOut
from noust.core.server_text import say
from noust.core.store import DomainKind
from noust.deployers import domains
from noust.deployers.plans import common
from noust.validators.domain import validate_domain


def plan_add_domain(
    app_domain: str,
    name: str,
    *,
    kind: str = DomainKind.ALIAS.value,
    force: bool = False,
    dns: bool = True,
    port: int | None = None,
    certificate: str | None = None,
    challenge: str | None = None,
    dns_provider: str | None = None,
) -> PlanOut:
    """
    Say what making an application answer on another name would do.

    Args:
        app_domain: The application's primary domain.
        name: The name to add.
        kind: ``alias`` or ``redirect``.
        force: Add it although another site already answers on it.
        dns: Point it at this server in the DNS account that holds its zone.
        port: The port an alias reaches, when it is not the application's.
        certificate: ``shared`` (the default) or ``own``.
        challenge: ``http-01`` or ``dns-01``.
        dns_provider: The DNS account to use.

    Returns:
        The plan.
    """
    app = validate_domain(app_domain)
    return common.plan_call(
        "app.domain.add",
        app,
        say("plans.domain.add", domain=app, name=name),
        lambda: domains.add_domain(
            app,
            name,
            kind,
            issue_cert=False,
            force=force,
            dns=dns,
            port=port,
            certificate=certificate or domains.SHARED,
            challenge=challenge,
            dns_provider=dns_provider,
            logger=common.quiet_logger(),
        ),
        domain=app,
        unknown=[say("plans.unknown.dns", name=name)],
    )


def plan_remove_domain(app_domain: str, name: str) -> PlanOut:
    """
    Say what stopping an application answering on a name would do.

    Args:
        app_domain: The application's primary domain.
        name: The alias or redirect to remove.

    Returns:
        The plan.
    """
    app = validate_domain(app_domain)
    return common.plan_call(
        "app.domain.remove",
        app,
        say("plans.domain.remove", domain=app, name=name),
        lambda: domains.remove_domain(app, name, reduce_cert=False, logger=common.quiet_logger()),
        domain=app,
    )
