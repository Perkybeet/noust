# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``wasm github``: this server's GitHub App (2.2).
"""

from __future__ import annotations

import click

from wasm.cli.app import WasmGroup


@click.group("github", cls=WasmGroup)
def cli() -> None:
    """This server's GitHub App: private repositories, push and pull request events."""
