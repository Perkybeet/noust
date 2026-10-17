# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
DNS for Noust: accounts at DNS providers, the records Noust manages in them, and the
DNS-01 challenge that proves control of a name without an open port.

- :mod:`noust.managers.dns.provider`: what a provider is (zones and records).
- :mod:`noust.managers.dns.cloudflare`: the one client of Cloudflare's API.
- :mod:`noust.managers.dns.rest`: the one JSON-over-HTTPS client of the other providers;
  :mod:`~noust.managers.dns.digitalocean`, :mod:`~noust.managers.dns.hetzner`,
  :mod:`~noust.managers.dns.gandi` and :mod:`~noust.managers.dns.route53` are their APIs, the last
  three over :mod:`~noust.managers.dns.rrset` (records kept in sets).
- :mod:`noust.managers.dns.catalog`: the providers Noust answers challenges through.
- :mod:`noust.managers.dns.accounts`: the accounts and their credentials.
- :mod:`noust.managers.dns.records`: the ownership rules of the records.
- :mod:`noust.managers.dns.challenge`: the hooks certbot runs for DNS-01.
"""
