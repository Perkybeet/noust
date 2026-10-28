# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How an application's directory is laid out on disk, below the managers and the deployers.

Releases, the layout an application is on and the hand-over of files to its account are read by
the managers that back up, move and inspect an application as much as by the deployers that build
one. They depend on nothing above ``core``, so they live under it and neither side imports the
other to reach them.
"""

from __future__ import annotations
