# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central does to keep the copies of the servers it manages (item 195).

The tunnel a central opens to a server goes one way on purpose: the central dials, the server
cannot reach back (:mod:`noust.fleet.authorize`). So a server cannot send its copies anywhere, and
does not need to: the central comes and takes them. The server keeps no credential of the place its
copies live, and whoever gets into it cannot delete or encrypt what the central already holds,
because nothing it holds reaches that far.

- :mod:`~noust.fleet.collect.names`: what a server says is judged here, and nothing of it reaches a
  path or a store before it has been.
- :mod:`~noust.fleet.collect.settings`: who follows, how fast, how long copies are kept, how much
  room is left alone.
- :mod:`~noust.fleet.collect.records`: the copies the central holds, and how each server is doing.
- :mod:`~noust.fleet.collect.layout`: where on the central's disk a copy is, by server and by
  application.
- :mod:`~noust.fleet.collect.throttle`, :mod:`~noust.fleet.collect.space`: the speed limit and the
  disk guard.
- :mod:`~noust.fleet.collect.fetch`: one file, in pieces that resume, never left half-made under the
  name of a good copy, checked against the digest the server listed.
- :mod:`~noust.fleet.collect.plan`, :mod:`~noust.fleet.collect.retention`: what to take, what to let go.
- :mod:`~noust.fleet.collect.collector`, :mod:`~noust.fleet.collect.worker`: a pass over a server, and
  the thread that makes them while the console runs.
- :mod:`~noust.fleet.collect.restore`: giving a copy back, to its server or to another.

The central never deletes a copy of its own because a server says so, and never to make room.
"""

from __future__ import annotations
