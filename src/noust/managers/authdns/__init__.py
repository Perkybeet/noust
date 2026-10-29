# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust DNS: an authoritative name server Noust installs on servers of the fleet, a primary that
holds the zones and secondaries that copy them, with the zones edited through the API.

The engine is Knot DNS (``engine.py``), chosen for four reasons that are verified in
``tests/integration/network_run.py`` against the real program:

- **The zones stay in Noust's store.** The editor writes ``authdns_zones`` and
  ``authdns_records``; a zone file is rendered from them (``zonefile.py``) and the engine reads
  it, so there is no second database to keep in step with the first.
- **DNSSEC is the engine's.** It keeps the keys, signs, re-signs, publishes the CDS and CDNSKEY a
  registrar can read, and prints the DS to give the registrar. A signed zone reaches a secondary
  as an ordinary transfer, so secondaries hold no key.
- **Secondaries learn the zones by themselves.** The primary lists its zones in a catalog zone
  (RFC 9432), the secondaries interpret it, and a zone added or removed on the primary appears or
  goes on every secondary with no call between servers and nothing from the central.
- **It never recurses.** A query for a name it is not authoritative for is refused, and there is
  no management API or key to protect: the control socket is for root.

- :mod:`~noust.managers.authdns.zonefile` and :mod:`~noust.managers.authdns.knotconf`: what is
  written.
- :mod:`~noust.managers.authdns.engine`: installing, configuring and asking the program.
- :mod:`~noust.managers.authdns.server`: the server's part (role, addresses, secondaries).
- :mod:`~noust.managers.authdns.zones`: the zone editor and DNSSEC.
- :mod:`~noust.managers.authdns.delegation`: the steps to delegate a domain here, and the check.
"""
