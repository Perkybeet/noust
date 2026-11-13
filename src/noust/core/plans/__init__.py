# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
"See what it will do": what Noust would run and change, said before it does (item 183).

The pieces, from the bottom:

- :mod:`~noust.core.plans.shape`: :class:`~noust.core.plans.shape.PlanOut`, the one shape every plan
  has, so that the console draws all of them with one piece and the built-in assistant reads them
  with one rule.
- :mod:`~noust.core.plans.recorder`: the rehearsing runner and filesystem that note every command
  and every change, in order, and execute nothing but what only looks.
- :mod:`~noust.core.plans.engine`: :func:`~noust.core.plans.engine.rehearse`, which runs the code
  that applies an operation inside a rehearsal that belongs to the caller alone. A plan is that
  operation's own code and never a list somebody wrote beside it (rule 3).
- :mod:`~noust.core.plans.diffs`: the diff of a file as a person may see it: a secret never shows.

The operations that have a plan are in :mod:`noust.deployers.plans`.
"""
