# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What installs the components whose install is more than ``apt-get install``.

cloudflared comes from a repository whose key is pinned, the WAF is a module and a rule set that
nginx must then load, fail2ban never bans the person connected, Knot comes from EPEL on the RHEL
family, Docker is a package and a daemon to enable, and a database engine has flavours and versions.
Each of those already has an installer that knows it. The software manager calls them as they are
(rule 3): this module is only the table that says which installer is whose, with the arguments each
one takes, and it is imported when one of them is needed and not before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.audit.context import current_actor
from noust.managers.server.components import Component, Installer

if TYPE_CHECKING:
    from noust.managers.server.software import Adapter, InstallOptions, SoftwareManager

__all__ = ["ADAPTERS"]


def _cloudflared(manager: SoftwareManager, component: Component, options: InstallOptions) -> None:
    """
    Install cloudflared from Cloudflare's repository, with its pinned key.

    Args:
        manager: The software manager.
        component: The component.
        options: Unused: nothing here asks for more.
    """
    from noust.managers.cloudflared_install import CloudflaredInstaller

    CloudflaredInstaller(platform=manager.platform, host=manager.host).install()


def _waf(manager: SoftwareManager, component: Component, options: InstallOptions) -> None:
    """
    Install ModSecurity and the Core Rule Set for nginx.

    Args:
        manager: The software manager.
        component: The component.
        options: Unused: nothing here asks for more.
    """
    from noust.managers.unknown_hosts import web_manager
    from noust.managers.waf import Waf

    Waf(web_manager("nginx")).install()


def _fail2ban(manager: SoftwareManager, component: Component, options: InstallOptions) -> None:
    """
    Install fail2ban with an sshd jail that never bans who is connected now.

    Args:
        manager: The software manager.
        component: The component.
        options: ``epel`` to confirm EPEL and ``ignore``, the addresses never to ban.
    """
    from noust.managers.server.security import ServerSecurity

    actor = current_actor()
    ServerSecurity(
        actor=actor.label if actor else "system", runner=None, fs=None, host=manager.host
    ).fail2ban_install(epel=options.epel, ignore=list(options.ignore))


def _knot(manager: SoftwareManager, component: Component, options: InstallOptions) -> None:
    """
    Install Knot DNS, enabling EPEL where it comes from there and the operator agreed.

    Args:
        manager: The software manager.
        component: The component.
        options: ``epel``.
    """
    from noust.managers.authdns.engine import KnotEngine

    KnotEngine().install(epel=options.epel)


def _docker(manager: SoftwareManager, component: Component, options: InstallOptions) -> None:
    """
    Install Docker and its Compose plugin, and start the daemon.

    Args:
        manager: The software manager.
        component: The component.
        options: Unused: nothing here asks for more.
    """
    from noust.managers.server_provision import ServerProvisioner

    ServerProvisioner(
        family=manager.platform.family, os_release=manager.platform.os
    ).ensure_docker()


def _database_engine(
    manager: SoftwareManager, component: Component, options: InstallOptions
) -> None:
    """
    Install a database engine as ``noust db install`` does: the distribution's package.

    Args:
        manager: The software manager.
        component: The component, whose ``engine`` names the manager.
        options: Unused: a flavour and a version are chosen where engines are chosen.
    """
    from noust.managers.database.service import DatabaseService

    DatabaseService().install_engine(component.engine or "")


#: Each installer other than the package manager's own, by the name the registry gives it.
ADAPTERS: dict[Installer, Adapter] = {
    Installer.CLOUDFLARED: _cloudflared,
    Installer.WAF: _waf,
    Installer.FAIL2BAN: _fail2ban,
    Installer.KNOT: _knot,
    Installer.DOCKER: _docker,
    Installer.DATABASE_ENGINE: _database_engine,
}
