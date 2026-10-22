# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Recipes made for tests: small documents named ``demo``, valid until a test breaks one."""

from __future__ import annotations

from typing import Any

import pytest

from noust.recipes import Recipe, parse_recipe

DIGEST = "sha256:" + "ab" * 32

COMPOSE = """
services:
  web:
    image: ghcr.io/o/app:1.2.3@{digest}
    restart: unless-stopped
    environment:
      SECRET: "${{SECRET}}"
    volumes:
      - data:/data
    ports:
      - "127.0.0.1:${{PORT}}:8080"
volumes:
  data:
"""


def compose_document(**replace: str) -> str:
    """
    A valid Compose document, with parts of it replaced.

    Args:
        **replace: Text to replace, as ``old=new``.

    Returns:
        The document.
    """
    text = COMPOSE.format(digest=DIGEST)
    for old, new in replace.items():
        text = text.replace(old, new)
    return text


def compose_recipe(**overrides: Any) -> dict[str, Any]:
    """
    A minimal valid Compose recipe named ``demo``.

    Args:
        **overrides: Keys to set or replace.

    Returns:
        The document, as the YAML would load.
    """
    data: dict[str, Any] = {
        "schema": 2,
        "name": "demo",
        "kind": "compose",
        "title": "Demo",
        "slogan": "A demo.",
        "description": "A demo recipe.",
        "category": "utilities",
        "tags": ["demo"],
        "logo": "logo.svg",
        "version": "1.2.3",
        "updated": "2026-10-06",
        "homepage": "https://example.com",
        "needs": ["Docker Engine with the Compose plugin"],
        "architectures": ["amd64", "arm64"],
        "variables": {"SECRET": {"generate": "password32"}},
        "health": {"path": "/", "expect": "200"},
        "compose": compose_document(),
        "notes": ["Open {{ url }}."],
    }
    data.update(overrides)
    return data


def native_recipe(**overrides: Any) -> dict[str, Any]:
    """
    A minimal valid native recipe named ``demo``.

    Args:
        **overrides: Keys to set or replace.

    Returns:
        The document, as the YAML would load.
    """
    data: dict[str, Any] = {
        "schema": 2,
        "name": "demo",
        "kind": "native",
        "title": "Demo",
        "slogan": "A demo.",
        "description": "A demo recipe.",
        "category": "utilities",
        "tags": ["demo"],
        "logo": "logo.svg",
        "version": "1.2.3",
        "updated": "2026-10-06",
        "homepage": "https://example.com",
        "app_type": "nodejs",
        "source": {"git": "https://github.com/o/r.git", "ref": "v1.2.3"},
        "notes": ["Open {{ url }}."],
    }
    data.update(overrides)
    return data


def use_demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Let the recipe named ``demo`` use the assets of a shipped recipe as its own.

    Args:
        monkeypatch: The test's monkeypatch.
    """
    from noust.recipes import assets

    real = assets.asset_resource

    def resource(name: str, asset: str) -> Any:
        return real("vaultwarden" if name == "demo" else name, asset)

    monkeypatch.setattr(assets, "asset_resource", resource)


def demo(**overrides: Any) -> Recipe:
    """
    Parse a minimal Compose recipe; call after :func:`use_demo_assets`.

    Args:
        **overrides: Keys to set or replace.

    Returns:
        The recipe.
    """
    return parse_recipe("demo", compose_recipe(**overrides))
