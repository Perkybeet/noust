# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A Compose recipe that mounts the Docker socket, as the root-equivalent recipes do."""

from __future__ import annotations

from typing import Any

from tests.recipes.factory import DIGEST, compose_recipe

SOCKET = "/var/run/docker.sock:/var/run/docker.sock"


def manager(*mounts: str, **extra: str) -> dict[str, Any]:
    """A Compose recipe whose web service mounts what is given, as Portainer's does."""
    lines = "".join(f"      - {mount}\n" for mount in mounts)
    extras = "".join(f"      {key}: {value}\n" for key, value in extra.items())
    document = (
        "services:\n"
        "  web:\n"
        f"    image: ghcr.io/o/app:1.2.3@{DIGEST}\n"
        "    restart: unless-stopped\n"
        f"{extras}"
        "    volumes:\n"
        f"{lines}"
        "      - data:/data\n"
        "    ports:\n"
        '      - "127.0.0.1:${PORT}:9000"\n'
        "volumes:\n"
        "  data:\n"
    )
    return compose_recipe(compose=document, variables={})
