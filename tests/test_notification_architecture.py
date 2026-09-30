# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The shape of the notification system, held by tests.

- **One implementation of each thing** (rule 3): nothing builds the legacy
  ``NotificationEvent`` but the notifier that converts it, and every code the
  composers can produce is in the canonical catalog, so no event can be added
  without a snapshot and no catalog sentence can be left dead.
- **Renderers and composers are pure**: they import nothing that talks to a
  network or starts a process. The only socket use is the hostname.
- **Agreement**: the switches in ``DEFAULT_CONFIG`` are the ones the model
  says, with the right ones off; the wordmark ships with the package.
"""

from __future__ import annotations

import ast
from pathlib import Path

from noust.core.config import DEFAULT_CONFIG
from noust.core.messages import MESSAGES
from noust.core.notifications.model import ALL_KINDS, EVENT_KINDS, OFF_BY_DEFAULT
from noust.core.notifications.render.email import wordmark
from tests.notifications_support import catalog

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "noust" / "core" / "notifications"

#: What a pure module must not import: anything that reaches a network or
#: starts a process.
IMPURE = {
    "subprocess",
    "urllib.request",
    "urllib.error",
    "http.client",
    "http.server",
    "smtplib",
    "ssl",
    "requests",
    "httpx",
    "asyncio",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def test_nobody_but_the_notifier_builds_the_legacy_event() -> None:
    builders = []
    for path in (ROOT / "src" / "noust").rglob("*.py"):
        if path.name == "notifier.py" and path.parent.name == "core":
            continue
        if "NotificationEvent(" in path.read_text(encoding="utf-8"):
            builders.append(path.relative_to(ROOT).as_posix())

    assert builders == []


def test_composers_and_renderers_touch_no_network_and_start_no_process() -> None:
    offenders = {}
    for path in PACKAGE.rglob("*.py"):
        if path.name == "fleet.py":
            # The one module that delivers: it hands to the notifier's worker.
            continue
        bad = _imports(path) & IMPURE
        if bad:
            offenders[path.relative_to(ROOT).as_posix()] = sorted(bad)

    assert offenders == {}


def test_only_the_context_asks_for_the_hostname() -> None:
    users = [path.name for path in PACKAGE.rglob("*.py") if "socket" in _imports(path)]

    assert users == ["context.py"]


def test_every_title_in_the_catalog_of_words_is_an_event_the_catalog_of_examples_has() -> None:
    titles = {key.split(".", 1)[1] for key in MESSAGES if key.startswith("title.")}

    assert titles == set(catalog("en"))


def test_every_kind_has_an_example() -> None:
    kinds = {notification.kind for notification in catalog("en").values()}

    assert kinds == set(ALL_KINDS)


def test_the_defaults_switch_on_exactly_what_the_model_says() -> None:
    events = DEFAULT_CONFIG["notifications"]["events"]

    assert set(events) == set(EVENT_KINDS)
    assert {kind for kind, on in events.items() if not on} == set(OFF_BY_DEFAULT)


def test_the_wordmark_ships_with_the_package() -> None:
    image = wordmark()

    assert image is not None
    assert image.data.startswith(b"\x89PNG")
    # Read as text: tomllib arrived in 3.11 and the suite runs on 3.10.
    assert '"core/notifications/assets/*.png"' in (ROOT / "pyproject.toml").read_text("utf-8")
    assert "core/notifications/assets" in (ROOT / "MANIFEST.in").read_text("utf-8")
    assert (PACKAGE / "assets" / "noust-wordmark.png").is_file()
