# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The operations that have a plan (item 183).

Each module here answers one operation's "what will it do": it runs the operation's own code
through :func:`noust.core.plans.engine.rehearse` and says in a
:class:`~noust.core.plans.shape.PlanOut` what that run would have run and changed, what it found
out by looking, and what it does not know. The operations and the routes of their plans are listed
in :mod:`noust.core.plans.registry`; the API (:mod:`noust.web.api.plans`) and the command line
(``--plan``) are two clients of these functions and add nothing to them.
"""
