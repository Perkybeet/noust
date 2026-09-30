# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fleet: one Noust (the central) managing others (the nodes) through their API.

The central never has a shell on a node. Enrollment runs the other way round:
``noust fleet authorize`` on the node installs the central's key for the
unprivileged ``noust-tunnel`` account, restricted in sshd and in the key line
to forwarding one port - the node's console on loopback - creates a ``fleet``
token and prints a join code; the operator pastes that code into the central
(``noust node add``), which pins the node's host key from it, opens an
``ssh -N -L`` tunnel and talks to the node's API with the token. Everything
the central does to a node is an API call the node authenticates, scopes,
holds to its own access ceiling and audits itself.

Modules:
    models: node records, names, SSH targets and public keys, validated.
    joincode: the one-line code a node prints and a central accepts.
    keys: the per-node key pair and pinned ``known_hosts`` on the central.
    tunnels: the SSH tunnels, as long-lived processes the runner owns.
    client: HTTP to a node's API through its tunnel, with the fleet token.
    policy: what a central needs before it may register a node, and a node's
        access ceiling for its centrals.
    nodes: the node registry (add, list, test, remove) - the chokepoint.
    authorize: the node side (``noust fleet authorize``/``deauthorize``).
    status: the fleet summary behind ``noust fleet status``.
    aggregate: one view asked of every server at once (``/api/fleet/*``).
    labels: ``key=value`` labels a central groups its nodes by.
"""
