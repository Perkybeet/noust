# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
CLI commands package for Noust.

The handlers are loaded when they are read. Importing any module of this package runs this file,
so importing them here would make ``noust list --help`` load the certificate, database, site,
service, setup, console and application commands (several hundred modules) to print a page that
needs one of them; ``LazyGroup`` exists so that a command costs its own imports only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from noust.cli.commands.cert import handle_cert
    from noust.cli.commands.db import handle_db
    from noust.cli.commands.service import handle_service
    from noust.cli.commands.setup import handle_setup
    from noust.cli.commands.site import handle_site
    from noust.cli.commands.web import handle_web
    from noust.cli.commands.webapp import handle_webapp

__getattr__, __dir__ = lazy_exports(
    globals(),
    {
        "handle_cert": "noust.cli.commands.cert",
        "handle_db": "noust.cli.commands.db",
        "handle_service": "noust.cli.commands.service",
        "handle_setup": "noust.cli.commands.setup",
        "handle_site": "noust.cli.commands.site",
        "handle_web": "noust.cli.commands.web",
        "handle_webapp": "noust.cli.commands.webapp",
    },
)

__all__ = [
    "handle_cert",
    "handle_db",
    "handle_service",
    "handle_setup",
    "handle_site",
    "handle_web",
    "handle_webapp",
]
