# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The units that are never restarted because an update replaced their libraries, and why.

A restart of any of them ends sessions, drops the network, stops every container or virtual machine,
or is systemd's own plumbing. They live here and not in :mod:`noust.managers.service_manager`, whose
module is past the length the project allows and does not grow; the service manager still answers for
them (:meth:`~noust.managers.service_manager.ServiceManager.restart_outdated`).
"""

from __future__ import annotations

import re

#: Units never restarted because an update replaced their libraries, each with
#: why: restarting them ends sessions, drops the network, stops every container
#: or virtual machine, or is systemd's own plumbing. The list needrestart keeps
#: for itself (its ``override_rc``), and what a server with clients' containers
#: adds. A reboot is how these take the new libraries.
NEVER_RESTARTED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), reason)
    for pattern, reason in (
        (r"^dbus(-broker)?\.service$", "restarting D-Bus breaks systemd and every session"),
        (r"^systemd-logind\.service$", "restarting logind ends every login session"),
        (
            r"^(serial-|console-|container-)?getty@.*\.service$",
            "restarting a getty ends a console login",
        ),
        (r"^user@\d+\.service$", "restarting it ends a user's session"),
        (r"^user-runtime-dir@\d+\.service$", "restarting it ends a user's session"),
        (r"^(emergency|rescue)\.service$", "it is a recovery shell"),
        (
            r"^(systemd-networkd|systemd-resolved|NetworkManager|networking|network"
            r"|wpa_supplicant|wicked|systemd-udevd)\.service$",
            "restarting it drops the network, and the connection to this page with it",
        ),
        (
            r"^(docker|containerd|podman|crio|snapd)\.service$",
            "restarting it stops every container on this server",
        ),
        (
            r"^(libvirtd|virtqemud|qemu-kvm|xen.*|lxc|lxd)\.service$",
            "restarting it stops every virtual machine on this server",
        ),
        (
            r"^(gdm|gdm3|lightdm|sddm|xdm|kdm|lxdm|slim)\.service$",
            "restarting it ends the graphical session",
        ),
        (r"^(open-)?iscsid\.service$", "restarting it can drop the disks it serves"),
        (r"^multipathd\.service$", "restarting it can drop the disks it serves"),
    )
)


def outdated_restart_refusal(unit: str) -> str | None:
    """
    Say why a unit running replaced libraries is not restarted from Noust.

    Args:
        unit: The unit, with its ``.service`` suffix.

    Returns:
        The reason, or None when it may be restarted.
    """
    for pattern, reason in NEVER_RESTARTED:
        if pattern.match(unit):
            return reason
    return None
