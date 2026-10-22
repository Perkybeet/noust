# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's terminal: a shell in the browser, and one-off commands.

A terminal is the most root-equivalent thing the console can open, so what is
here is almost all about what surrounds the shell, not the shell:

- :mod:`~noust.terminal.settings` the limits and switches, with the security
  profile applied;
- :mod:`~noust.terminal.targets` what a session runs: an application's own
  account in its sandbox, a container of its stack, or (behind its own switch)
  root on the server;
- :mod:`~noust.terminal.tickets` the one-time ticket an authenticated, elevated
  call mints and the WebSocket redeems;
- :mod:`~noust.terminal.sessions` the open sessions, their limits and their
  audit trail;
- :mod:`~noust.terminal.recording` what was typed and printed, bounded and
  scrubbed, kept for the audit;
- :mod:`~noust.terminal.execution` the one-off command, without a terminal.

The terminal itself is :meth:`noust.core.runner.CommandRunner.pty`: nothing
here starts a process (rule 1). ``docs/terminal.md`` explains the model.
"""

from __future__ import annotations
