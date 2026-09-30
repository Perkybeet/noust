# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Managing the server itself: its packages, its power, its disks, its clock.

Everything here acts on the machine Noust runs on, as root, through the shared
:class:`~noust.core.runner.CommandRunner` and :mod:`noust.core.fs`, so
``--dry-run`` rehearses all of it. The API (``noust.web.api.server``) and the
CLI (``noust server``) are two clients of these managers and hold no logic of
their own.

The submodules are imported by name; this package imports none of them, so the
security checks (``security*.py``) and the rest can be loaded independently.
"""
