# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What every part of the server reads: the repository's root, the environment variables the
end-to-end suites set and the limits."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

#: The file the end-to-end suites, CI and the documentation start, which runs the package beside it.
LAUNCHER = REPO / "scripts" / "console_server.py"

#: Most commands a :class:`ConsoleRunner` remembers. It runs for as long as a
#: developer leaves it up, and an unbounded call log is a slow memory leak.
CALL_HISTORY = 256

#: Names a domain this server should seed as its own single application, so a
#: fleet E2E test (panel/e2e/fleet.spec.ts) can tell, from the Apps page alone,
#: that it is reading this server's node and not the central's cache of it. An
#: environment variable, not a flag: it is set once by the Playwright fixture that
#: starts a node, never typed by a developer, and keeping it off the CLI's surface
#: keeps it out of everything that only ever runs one console_server.py.
FLEET_NODE_APP_ENV = "NOUST_E2E_FLEET_APP"

#: Set to "1" by the catalogue's E2E spec (panel/e2e/catalog.spec.ts) to seed what its pages
#: need beyond the default machine: container applications, applications installed from a
#: recipe at an older version and one whose recipe left the catalogue. An environment variable
#: and not a seed of every run: the default machine's applications are counted by other specs.
CATALOG_ENV = "NOUST_E2E_CATALOG"

#: What :func:`seed_catalog_apps` creates, for the specs that open each.
CATALOG_DOCKERFILE_APP = "crate.example.net"
CATALOG_IMAGE_APP = "registry.example.net"
CATALOG_VAULT_APP = "vault.example.net"
CATALOG_READER_APP = "reader.example.net"
CATALOG_RETIRED_APP = "retired.example.net"

#: Set to "1" by the clients that deploy for real against this server (the Terraform provider's
#: acceptance tests, the Python SDK's live test) to model a code host and the applications' listeners:
#: see :func:`model_code_host`. An environment variable and not a seed of every run, since it changes what
#: a ``git clone`` and a ``systemctl start`` do and the console's specs count on the default machine.
CODE_HOST_ENV = "NOUST_E2E_CODE_HOST"

#: The code host :func:`model_code_host` answers for, and the branches each repository on it has.
CODE_HOST_NAME = "code.example.net"
CODE_HOST_BRANCHES = ("main", "staging")

#: Set to a version ("3.0.0") by the fleet's E2E suite to make this node pretend to be an
#: older Noust: it reports that version and does not offer OLDER_NODE_MISSING (see
#: pretend_older_node), so a central's views say "unsupported" for it.
OLDER_NODE_ENV = "NOUST_E2E_OLDER_NODE"

#: What a 3.0 node does not offer of what a 3.1 central's fleet views ask.
OLDER_NODE_MISSING = (
    "/api/overview",
    "/api/server/summary",
    "/api/system/update",
    "/api/auth/fleet/self",
)

#: Seconds uvicorn waits for open connections at shutdown. The event stream
#: never ends by itself, so without a bound a Ctrl+C would wait for the tab.
GRACEFUL_SHUTDOWN_SECONDS = 2
