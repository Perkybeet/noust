# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fleet's settings: what a central wants every server to have, and what each server applied.

A setting of the fleet is a notification channel, an alert rule, a backup destination, a registry
login or a shared variable that is written once, on the central, and reaches every server it
manages (item 90). The central keeps the state it wants (:mod:`~noust.fleet.settings.desired`),
each node keeps what it applied of it without the values (:mod:`~noust.fleet.settings.managed`),
and the reconciler of :mod:`noust.web.fleet_settings_actions` brings the second to the first
through the node's own typed routes: the central validates nothing a node validates (rule 3).

Modules:
    kinds: the closed list of what can be a setting of the fleet, how it is named and which of
        its fields are secrets.
    signature: how a push says which person approved the change it carries.
    desired: the central's table, ``fleet_desired``, with the secrets in its secret store.
    managed: the node's table, ``fleet_managed``: fingerprints, never values.
    snapshots: a node reads what it holds of a setting, to fingerprint it.
    push: the typed calls that write one setting on a node.
    reconcile: what a node needs, given what the central wants and what it applied.
    status: the central's view of every node's state for every setting.

Nothing is imported here: importing a module under this package must not load the others.
"""
