# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the console server's modelled journal says about the applications (4.1).

The console's Logs page searches the applications' units (``journalctl -u shop-example-com*``);
the sandbox it is developed and photographed on has to answer that search, or the page that
answers "why is this application down" has nothing to show. What is defended:

- **An application that runs says it started and answers**; one that failed says why, in the
  words a runtime and systemd use, newest first.
- **The search's narrowing is honoured**: its window and its priority.
- **Another application's units are not answered** for a pattern that is not theirs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from scripts.console_server.machine import Unit
from scripts.console_server.server_host import ServerHost


def host(tmp_path: Path) -> ServerHost:
    return ServerHost(
        tmp_path,
        "web-1",
        {
            "shop-example-com": Unit(active="active", pid=2201),
            "clientes-example-com": Unit(active="failed"),
            "nginx": Unit(active="active", managed=False),
        },
    )


def search(server: ServerHost, pattern: str, *extra: str) -> list[dict[str, str]]:
    now = int(time.time())
    argv = (
        "--no-pager",
        "--output=json",
        "--reverse",
        f"--since=@{now - 3600}",
        f"--until=@{now + 60}",
        *extra,
        "-u",
        pattern,
    )
    return [json.loads(line) for line in server._applications_journal(argv).splitlines()]


def test_an_application_that_runs_says_it_started_and_answers(tmp_path: Path) -> None:
    lines = search(host(tmp_path), "shop-example-com*")

    assert [line["MESSAGE"] for line in lines] == [
        "GET /api/health 200 3ms",
        "Server listening on port 3000",
    ]
    assert {line["_SYSTEMD_UNIT"] for line in lines} == {"shop-example-com.service"}


def test_an_application_that_failed_says_why_newest_first(tmp_path: Path) -> None:
    lines = search(host(tmp_path), "clientes-example-com*")

    messages = [line["MESSAGE"] for line in lines]
    assert "Error: connect ECONNREFUSED 127.0.0.1:5432" in messages
    assert "clientes-example-com.service: Failed with result 'exit-code'." in messages
    # Newest first, as the search asks with --reverse.
    stamps = [int(line["__REALTIME_TIMESTAMP"]) for line in lines]
    assert stamps == sorted(stamps, reverse=True)


def test_the_priority_keeps_only_what_is_that_bad(tmp_path: Path) -> None:
    lines = search(host(tmp_path), "clientes-example-com*", "--priority=3")

    assert lines
    assert {line["PRIORITY"] for line in lines} == {"3"}


def test_the_window_keeps_only_what_happened_in_it(tmp_path: Path) -> None:
    server = host(tmp_path)
    now = int(time.time())
    argv = (
        "--output=json",
        f"--since=@{now - 60}",
        f"--until=@{now + 60}",
        "-u",
        "clientes-example-com*",
    )

    # The failure was twelve minutes ago: a window of the last minute holds none of it.
    assert server._applications_journal(argv) == ""


def test_a_pattern_that_is_not_an_applications_gets_nothing(tmp_path: Path) -> None:
    assert search(host(tmp_path), "nginx*") == []
    assert search(host(tmp_path), "other-example-com*") == []
