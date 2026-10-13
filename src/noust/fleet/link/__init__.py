# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Servers that dial in to their central, instead of being dialled.

A server behind NAT or a carrier's CGNAT has no SSH port a central can reach.
It runs ``noust fleet link``, which keeps an ``ssh -R`` open to the central
(:mod:`noust.fleet.link.node`); the central, prepared with ``noust node link
setup``, reaches the server's console through the port that leaves on its
loopback (:mod:`noust.fleet.link.central`, :meth:`TunnelManager.endpoint
<noust.fleet.tunnels.TunnelManager.endpoint>`). The model does not change: the
central never gets a shell on a server, a server gets none on its central, and
the central talks to a server with the same fleet token, from loopback.
"""
