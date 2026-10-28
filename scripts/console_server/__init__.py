# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Run the real Noust panel against a seeded, sandboxed machine.

This is the backend the console is developed and tested against: the real
FastAPI application from :func:`noust.web.server.create_app`, served by uvicorn
so its lifespan runs and the ``/events`` stream and the WebSockets work, over
a store seeded by :func:`tests.support.panel_factory.seed_console_state` - running,
stopped, failed and static applications, deployments with captured build
logs, sites, certificates, databases, backups, cron jobs and a job history.

Nothing touches the machine it runs on:

- every path Noust reads or writes (the store, the config file, the panel's
  secrets and sessions, backups, systemd units, nginx sites, certificates) is
  redirected into a temporary directory, removed on exit;
- a :class:`SandboxFileSystem` refuses any write that would still land
  outside that directory;
- a :class:`ConsoleRunner` answers ``systemctl``, ``journalctl``, ``nginx``
  and ``certbot`` from an in-memory model of the seeded machine, so starting
  or stopping an application from the console changes what the next status
  query reports, and no process is ever spawned.

It prints exactly one JSON line on stdout once the server accepts
connections, then serves until SIGINT or SIGTERM::

    {"url": "http://127.0.0.1:43127", "token": "...", "totp_secret": null}

Develop the console against it::

    python scripts/console_server.py --port 8080      # terminal 1
    cd panel && npm run dev                           # terminal 2

Vite proxies ``/api``, ``/events``, ``/hooks`` and ``/ws`` to ``VITE_BACKEND``
(default ``http://127.0.0.1:8080``); sign in with the printed token. The
Playwright suite in ``panel/e2e`` starts one of these per worker.

The code is this package, split by what each part answers for (the sandbox, the machine behind the
runner, each page's seed, the fleet, the Server area); ``scripts/console_server.py`` stays the path
the end-to-end suites, CI and the documentation start, and only calls ``entrypoint.main``.
"""
