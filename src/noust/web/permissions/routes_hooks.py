# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the forge webhooks and their secrets
(:mod:`noust.web.api.hooks`, :mod:`noust.web.api.github_hooks`).

A delivery carries no session: the per-application HMAC secret, or the GitHub
App's, is checked inside the route, so the route itself is public.

Creating, rotating and discarding a secret manage the application; seeing the
state and the deliveries only reads it; showing the secret again is revealing a
credential, like the export or a database connection string.
"""

from __future__ import annotations

from noust.web.permissions import PUBLIC, Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/hooks/deploy/{domain}"): PUBLIC,
    ("POST", "/hooks/github"): PUBLIC,
    ("POST", "/api/apps/{domain}/webhook-secret"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/webhook-secret"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/webhook"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}/webhook/received"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}/webhook/deliveries"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/webhook/reveal"): Permission.SECRETS_REVEAL,
}
