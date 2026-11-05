# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/auth/sign-in``, the picture on the sign-in screen
(:mod:`noust.web.api.sign_in_image`).

Reading is public: the screen is drawn before anyone has a session, and what the two routes carry is
what is shown on it. Changing it is ``security.manage``, with the line and the usage notice an
operator shows before sign-in: what the screen says to someone who has not signed in is part of the
sign-in policy, and an administrator without that duty (the ENS profile splits them) does not
decide it. The picture is not served from anywhere else.
"""

from __future__ import annotations

from noust.web.permissions import PUBLIC, Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/auth/sign-in/appearance"): PUBLIC,
    ("GET", "/api/auth/sign-in/image"): PUBLIC,
    ("PUT", "/api/auth/sign-in/appearance"): Permission.SECURITY_MANAGE,
    ("DELETE", "/api/auth/sign-in/appearance"): Permission.SECURITY_MANAGE,
}
