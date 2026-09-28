# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``wasm preview``: pull request previews of an application (2.2).
"""

from __future__ import annotations

import click

from wasm.cli.app import WasmGroup


@click.group("preview", cls=WasmGroup)
def cli() -> None:
    """Pull request previews: a short-lived copy of an application per pull request."""
