# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What renewing a certificate would do (item 183).

The renewal is :meth:`noust.managers.cert_manager.CertManager.renew_and_report`, the call of the
renewal job, rehearsed: the ``certbot`` command for each lineage and what it runs afterwards. Whether
the certificate authority accepts it is the one thing a plan cannot ask without renewing.
"""

from __future__ import annotations

from noust.core.exceptions import NoustError
from noust.core.plans.shape import PlanOut
from noust.core.server_text import say
from noust.deployers.plans import common
from noust.managers.cert_manager import CertManager
from noust.validators.domain import validate_domain

OPERATION = "cert.renew"


def plan_cert_renew(domain: str, *, force: bool = False) -> PlanOut:
    """
    Say what renewing a certificate would do.

    Args:
        domain: The certificate's name.
        force: Renew it although it is not near its expiry.

    Returns:
        The plan.

    Raises:
        NoustError: There is no such certificate.
    """
    name = validate_domain(domain)
    manager = CertManager(verbose=False)
    if not manager.cert_exists(name):
        raise NoustError(
            f"Certificate not found: {name}",
            details="Run 'noust cert list' to see the certificates of this server.",
        )
    effect = say("plans.cert.renew_forced" if force else "plans.cert.renew", domain=name)
    return common.plan_call(
        OPERATION,
        name,
        effect,
        lambda: CertManager(verbose=False).renew_and_report(domain=name, force=force),
        domain=None,
        unknown=[say("plans.unknown.authority", domain=name)],
    )
