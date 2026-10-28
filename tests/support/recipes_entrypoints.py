# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/recipes/test_entrypoints.py`` that other tests build on."""

from __future__ import annotations

from typing import Any

from tests.recipes.factory import DIGEST, compose_recipe

TWO_SERVICES = f"""
services:
  builder:
    image: ghcr.io/o/builder:1.2.3@{DIGEST}
    restart: unless-stopped
    environment:
      VIEWER_URL: "${{VIEWER_URL}}"
    ports:
      - "127.0.0.1:${{PORT}}:3000"
  viewer:
    image: ghcr.io/o/viewer:1.2.3@{DIGEST}
    restart: unless-stopped
    ports:
      - "127.0.0.1:${{VIEWER_PORT}}:3001"
"""

VARIABLES = {
    "SCHEME": {"from": "scheme"},
    "VIEWER_DOMAIN": {"required": True, "description": "Where visitors open the bots."},
    "VIEWER_PORT": {"default": "3101", "description": "The viewer's port on 127.0.0.1."},
    "VIEWER_URL": {"value": "${SCHEME}://${VIEWER_DOMAIN}"},
}
ENTRYPOINTS = {
    "viewer": {
        "title": "Viewer",
        "description": "Where visitors open and answer the bots you publish.",
        "domain_variable": "VIEWER_DOMAIN",
        "port_variable": "VIEWER_PORT",
    }
}


def two_address_recipe(**overrides: Any) -> dict[str, Any]:
    """A Compose recipe with a builder on the application's address and a viewer on another."""
    data = compose_recipe(
        compose=TWO_SERVICES, variables=dict(VARIABLES), entrypoints=dict(ENTRYPOINTS)
    )
    data.update(overrides)
    return data
