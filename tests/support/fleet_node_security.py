# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_fleet_node_security.py`` that other tests build on."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

#: The tunnel's end: sshd connects to the console from loopback.
TUNNEL = ("127.0.0.1", 50000)


def client_from(app: Any, peer: tuple[str, int] = TUNNEL) -> TestClient:
    """
    Args:
        app: The application.
        peer: The TCP peer the requests come from.

    Returns:
        A client connecting from that peer.
    """
    return TestClient(app, client=peer)


def fleet_headers(token: str, **extra: str) -> dict[str, str]:
    """
    Args:
        token: The fleet token.
        **extra: More headers.

    Returns:
        What a central sends.
    """
    return {"Authorization": f"Bearer {token}", **extra}
