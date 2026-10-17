# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
No GET route does something (checklist L-20).

Coolify had a ``GET`` that changed a password, and a GET is the one request a
page can make for a victim with an image tag or a link: it carries no CSRF
token and, with cookies, no check at all. Noust's CSRF defence covers POST,
PUT, PATCH and DELETE, and the guarantee that makes that enough is that nothing
else mutates. It was checked by hand once; this is the check that does not need
anyone to remember.

An operation is judged by what it is called and by what it demands: a path with
an action verb in it, or a route that asks for sudo mode or for a second
person's approval, is not a read. The few GETs that really are reads and read
as if they were not are listed below, each with the reason.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.web.auth import SecurityConfig
from noust.web.server import create_app

#: Words that name a thing being done. Matched as whole words of the path, so a
#: noun that contains one (``runs``, ``backups``) is not a hit.
ACTION_VERBS = frozenset(
    """
    start stop restart delete remove revoke deploy rollback enable disable reboot run apply
    create reset unlock lock kill cancel approve deny restore migrate install uninstall upgrade
    update push pull sync trigger send test regenerate rotate seal unseal freeze unfreeze
    """.split()
)

#: Reads that carry one of those words. Path template to why it is a read.
READS_THAT_SOUND_LIKE_ACTIONS = {
    "/api/apps/{domain}/previews/pull-requests": "the open pull requests a preview can be made from",
    "/api/apps/{domain}/rollback-points": "lists the releases one can roll back to",
    "/api/apps/{domain}/restore-drills": "the rehearsals of a restore so far, listed; making one is the POST",
    "/api/apps/{domain}/migrate/plan": "the plan of a migration, computed and not run",
    "/api/server/updates/runs/{update_id}": "the status of an update that already ran",
    "/api/system/update": "which way this installation updates itself, and whether it can",
    "/api/system/update/auto": "the settings of Noust's automatic update, its timer and its last run; changing them is the PUT",
    "/api/databases/instances/{name}/remove-plan": "what removing an instance would delete",
    "/api/apps/{domain}/deploy-key": "the public half of an application's deploy key",
    "/api/deploy-keys": "the deploy keys, public halves",
    "/api/deploy-freezes": "the freezes of deploys, listed; making and lifting one are the POST and DELETE",
    "/api/apps/{domain}/update-schedule": "when the application updates itself and how the last run ended; the PUT writes the timer",
    "/api/deploy-keys/hosts": "the hosts a deploy key is pinned to",
    "/api/push": "whether the server takes git pushes and which applications deploy that way",
    "/api/apps/{domain}/push": "how an application's push deployment stands: branch, remote, hook",
    "/api/apps/{domain}/push/keys": "the public halves of the keys that may push",
    # A login redirect is a GET by OAuth's design; it only mints a one-time state
    # that /callback consumes, and the callback is held to the lockout like a sign-in.
    "/api/auth/oidc/start": "the redirect to the identity provider that begins a sign-in",
    "/api/auth/saml/start": "the redirect to the SAML identity provider that begins a sign-in",
    "/api/server/restore/plan": "what restoring the server would do: it reads the store, the "
    "machine and the destinations and changes nothing",
}

#: Reads that ask for sudo mode. Template to why a GET and not a POST.
SENSITIVE_READS = {
    "/api/databases/backups/{name}/download": "a download a browser makes with a link; "
    "it only reads the file, and asks for sudo mode because the file is a database",
    "/api/apps/{domain}/files/content": "a file of the application's tree, which may hold "
    "what the application keeps from its operators; reading it is a terminal's power",
    "/api/apps/{domain}/files/download": "the same file, downloaded with a link",
    "/api/apps/{domain}/mounts/content": "the text of a mounted file, which is a secret "
    "as often as it is a configuration",
}


@pytest.fixture(scope="module")
def schema() -> Iterator[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="noust-get-routes-") as tmp:
        app = create_app(SecurityConfig(state_dir=Path(tmp) / "state", rate_limit_requests=5000))
        yield dict(app.openapi())


def gets(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {path: item["get"] for path, item in schema["paths"].items() if "get" in item}


def words(path: str) -> set[str]:
    return {word for word in re.split(r"[/\-_{}.]+", path.lower()) if word}


def test_there_are_get_routes_to_judge(schema: dict[str, Any]) -> None:
    assert len(gets(schema)) > 150


def test_no_get_route_is_named_for_an_action(schema: dict[str, Any]) -> None:
    offenders = {
        path: sorted(words(path) & ACTION_VERBS)
        for path in gets(schema)
        if words(path) & ACTION_VERBS and path not in READS_THAT_SOUND_LIKE_ACTIONS
    }

    assert offenders == {}, (
        "A GET must not do anything. If these only read, add each to "
        f"READS_THAT_SOUND_LIKE_ACTIONS with why: {offenders}"
    )


def test_no_get_route_asks_for_sudo_mode_or_approval_unless_it_is_a_sensitive_read(
    schema: dict[str, Any],
) -> None:
    asking = {
        path
        for path, operation in gets(schema).items()
        if operation.get("x-noust-requires-elevation") or operation.get("x-noust-requires-approval")
    }

    assert asking == set(SENSITIVE_READS), (
        "A route that needs sudo mode or a second person changes something, or reads "
        f"what only an elevated person may; the GETs that do: {sorted(asking)}"
    )


def test_every_exception_still_names_a_route_that_exists(schema: dict[str, Any]) -> None:
    routes = set(gets(schema))

    stale = (set(READS_THAT_SOUND_LIKE_ACTIONS) | set(SENSITIVE_READS)) - routes

    assert stale == set(), f"remove from the lists: {sorted(stale)}"


def test_a_get_never_declares_a_request_body(schema: dict[str, Any]) -> None:
    assert [path for path, operation in gets(schema).items() if "requestBody" in operation] == []
