# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the sign-in screen is told before anybody has signed in, while the server has no account yet.

A console in a container is opened for the first time with a token that no installer showed: it is
in the container's log. The sign-in screen has to say so instead of "the installer showed it", and
to open on the token because there is no account to type. It can only know from the server, and an
anonymous caller is told nothing about a server (ENS op.acc.6.7): so what is published here is the
least that does it, only while the server has no account (the one moment the master token is how
anybody gets in), and nothing once there is one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from noust.core.docker.owners import own_container_reference

__all__ = ["FirstStart", "session_first_start"]


class FirstStart(BaseModel):
    """
    The facts of a server's first start the sign-in screen needs.

    Attributes:
        token_shown_by: Where the access token was shown: ``installer`` (the installer's own box,
            on a server) or ``container_log`` (the log of the container, which has no installer).
    """

    token_shown_by: Literal["installer", "container_log"]


def session_first_start(accounts_exist: bool) -> FirstStart | None:
    """
    Say how the first sign-in is reached, to someone who is not signed in.

    Args:
        accounts_exist: Whether the server has any account yet.

    Returns:
        The facts while the server has no account; None once it has one, and nothing else about
        the server either way.
    """
    if accounts_exist:
        return None
    in_container = own_container_reference() is not None
    return FirstStart(token_shown_by="container_log" if in_container else "installer")
