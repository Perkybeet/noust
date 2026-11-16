# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``Strict-Transport-Security`` says, and when the console says it (item 198).

The header makes a browser refuse plain HTTP, and any certificate it cannot trust, for a *name*
in every port and (with ``includeSubDomains``) under every subdomain. That is a promise only
whoever owns the name can make. A central in a container on a NAS shares its name with the NAS's
own portal on other ports, so a console that said it everywhere left the browser unable to open
the NAS. The sites Noust writes for an application send the same value, a year and nothing else,
when ``ssl.hsts`` asks for it (:mod:`noust.managers.webserver`): there is one value.

The decision is a pure function so it is tested without a server; the console feeds it the
request and the name ``noust console expose`` published (:mod:`noust.web.console_hsts`).
"""

from __future__ import annotations

from typing import Any

__all__ = ["ALWAYS", "AUTO", "DEFAULT_MODE", "MODES", "NEVER", "VALUE", "header_for"]

#: A year, without ``includeSubDomains`` or ``preload``: those bind every subdomain of the name,
#: which an operator hosting other sites under it never asked for.
VALUE = "max-age=31536000"

#: Only on the name the console was published on (``noust console expose``).
AUTO = "auto"
#: On every response over TLS, whatever the name: the operator owns it and says so.
ALWAYS = "always"
#: Never.
NEVER = "never"

#: The words ``web.hsts`` takes, the factory one first.
MODES: tuple[str, ...] = (AUTO, ALWAYS, NEVER)
DEFAULT_MODE = AUTO


def header_for(mode: Any, *, secure: bool, host: str | None, published: str | None) -> str | None:
    """
    Decide the ``Strict-Transport-Security`` of one response of the console.

    Args:
        mode: ``web.hsts``. Anything that is not one of :data:`MODES` (a file edited by hand) is
            read as :data:`AUTO`, the one that promises least.
        secure: Whether the request arrived over TLS, directly or through a declared proxy.
            The header is never sent in cleartext, where a browser ignores it.
        host: The name the request was made for, without a port.
        published: The name ``noust console expose`` published the console on, if it did.

    Returns:
        :data:`VALUE`, or None to send nothing.
    """
    if not secure or mode == NEVER:
        return None
    if mode == ALWAYS:
        return VALUE
    if host and published and host.lower().rstrip(".") == published.lower().rstrip("."):
        return VALUE
    return None
