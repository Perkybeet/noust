# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pull request previews of an application: settings and the previews themselves (2.2).
"""

from __future__ import annotations

from fastapi import APIRouter

from wasm.web.api.deps import WASMErrorRoute

router = APIRouter(route_class=WASMErrorRoute)
