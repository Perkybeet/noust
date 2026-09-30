# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who is speaking, in which language, and where the console is.

Every composer needs the same three things and none of them belongs to an
event: the language of Noust's own words (``notifications.language``), the
name of the server raising it (``server.name``, the machine's short hostname
when unset) and the base of the links (``web.public_url``).
:class:`NotificationContext` is those three, read once from the configuration.

On a fleet each node notifies from its own process, so ``server.name`` must be
set on the node and the message says which server it is about. A node's
console is normally reachable only through the central's tunnel; setting the
node's ``web.public_url`` to ``https://<central>/n/<node>`` (the console
accepts that form) makes every link in its messages open the right page.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from noust.core.messages import DEFAULT_LOCALE, Locale, message, normalize_locale
from noust.core.notifications.model import Link, clean_inline

#: The most characters of a server name any message will carry.
MAX_SERVER_NAME = 64

#: Console pages that belong to the central whatever server is selected: a
#: link to one never carries a node (``panel/src/app/nodeRoute.ts`` keeps the
#: same list).
CENTRAL_ONLY_PATHS: tuple[str, ...] = ("/settings", "/fleet", "/servers", "/integrations")


def default_server_name() -> str:
    """
    Name the machine for a message when nobody set ``server.name``.

    Returns:
        The short hostname (``web-1`` for ``web-1.example.com``), or
        ``"server"`` when the machine cannot say.
    """
    try:
        host = socket.gethostname()
    except OSError:
        return "server"
    return clean_inline(host).split(".")[0][:MAX_SERVER_NAME] or "server"


def server_name(config: Any) -> str:
    """
    Read the server's name for notifications.

    Args:
        config: Anything with ``get(key, default)``: the shared
            :class:`~noust.core.config.Config` or a snapshot of it.

    Returns:
        ``server.name`` when set, else :func:`default_server_name`; never
        longer than :data:`MAX_SERVER_NAME`.
    """
    configured = clean_inline(str(config.get("server.name", "") or ""))
    return configured[:MAX_SERVER_NAME] or default_server_name()


def _is_central_only(path: str) -> bool:
    """
    Args:
        path: A console path.

    Returns:
        Whether it is a page of the central itself.
    """
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in CENTRAL_ONLY_PATHS)


@dataclass(frozen=True)
class NotificationContext:
    """
    What every composer needs besides the event itself.

    Attributes:
        locale: The language of Noust's own words.
        server: The name of the server that raised the event.
        public_url: The console's public base URL without a trailing slash,
            or empty when it has none (then a message carries no link).
    """

    locale: Locale = DEFAULT_LOCALE
    server: str = "server"
    public_url: str = ""

    @classmethod
    def from_config(cls, config: Any) -> NotificationContext:
        """
        Read the three values from a configuration.

        Args:
            config: The shared configuration or a snapshot of it.

        Returns:
            The context.
        """
        return cls(
            locale=normalize_locale(config.get("notifications.language")),
            server=server_name(config),
            public_url=str(config.get("web.public_url", "") or "").rstrip("/"),
        )

    def link(self, path: str, *, node: str | None = None) -> Link | None:
        """
        Build the link to a console page.

        Args:
            path: The page, starting with ``/`` (``/apps/shop.example.com``).
            node: The managed server the page is about, when this process is a
                central speaking about one; the page then lives under
                ``/n/<node>``. Ignored for a page of the central's own.

        Returns:
            The console link, or None when no public URL is configured.
        """
        if not self.public_url:
            return None
        prefix = ""
        if node and not _is_central_only(path):
            prefix = f"/n/{quote(node, safe='')}"
        return Link(
            rel="console",
            label=message("ui.open_console", self.locale),
            # A segment is whatever an application or a unit is called; it must
            # not be able to add a space, a query or a fragment to the address.
            url=f"{self.public_url}{prefix}{quote(path, safe='/-._~')}",
        )
