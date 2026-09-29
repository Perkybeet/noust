"""CLI commands package for Noust."""

from noust.cli.commands.cert import handle_cert
from noust.cli.commands.db import handle_db
from noust.cli.commands.service import handle_service
from noust.cli.commands.setup import handle_setup
from noust.cli.commands.site import handle_site
from noust.cli.commands.web import handle_web
from noust.cli.commands.webapp import handle_webapp

__all__ = [
    "handle_cert",
    "handle_db",
    "handle_service",
    "handle_setup",
    "handle_site",
    "handle_web",
    "handle_webapp",
]
