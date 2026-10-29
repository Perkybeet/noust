# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central does so that the servers it manages deploy from the repositories of its GitHub App
(item 90, D6).

The App lives on the central and its private key never leaves it. A server that deploys from a
repository of the App needs three things the central gives it:

- **Access to clone with** (:mod:`~noust.fleet.sources.keeper`): an installation token scoped to the
  repositories that server serves and to reading their contents, leased to it and renewed before it
  ends, which the server keeps sealed (:mod:`noust.integrations.github.leases`).
- **The pushes of its repositories** (:mod:`~noust.fleet.sources.forward`): GitHub delivers to the
  central, which finds the servers that deploy the repository and the branch and hands each the
  event; the server queues the update exactly as it does for its own webhook.
- **The status of what they deploy** (:mod:`~noust.fleet.sources.follow`): the central follows the
  deployment or the preview the server started and writes the status of the commit and the comment
  of the pull request with the App's own credentials, which the server has no use for.

A server stays a Noust that works alone: nothing here is needed for it to deploy from a deploy key
or a credential of its own, and what the central leased is gone within the hour when the central
is.
"""

from __future__ import annotations
