# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Servers created at a cloud provider and brought into the fleet.

A central can ask Hetzner Cloud, DigitalOcean or Vultr for a machine, wait for it,
and enroll it exactly as :class:`noust.fleet.enroll.NodeEnroller` enrolls any
server, so the node that results is the one the manual way gives: a
``noust-tunnel`` account with no shell, a restricted key, a pinned host key.

What makes it safe to do unattended:

- **The host key is chosen before the machine exists.** The central generates an
  ed25519 host key, hands it to cloud-init and pins its fingerprint, so the first
  connection is already to a key it knows: nothing is trusted on first use.
- **The login that installs Noust is a key of one use.** The central generates it,
  gives its public half to the provider, keeps the private half in memory for the
  length of the job and destroys it; the line it left in the server's
  ``authorized_keys`` and the provider-side copy are removed on the way out.
- **No fleet token travels in user-data.** A provider's metadata service hands
  user-data to every local user, so nothing in it opens the node's API: the
  central installs Noust and authorizes itself over the verified connection.

Modules:
    models: what a provider offers (locations, plans, images) and what a server is.
    http: the one JSON client over HTTPS the providers share.
    providers: the Hetzner, DigitalOcean and Vultr adapters and their registry.
    accounts: the API tokens of a central, kept as secrets.
    init: the cloud-init library and the user-data a new server boots with.
    provision: the job that creates a machine and enrolls it.
"""
