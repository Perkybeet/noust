# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The request and response models every identity source's administration shares.

One definition of what a group mapping looks like on the wire, whichever source (OpenID Connect,
a directory, SCIM, SAML) is being configured: the models of each source's routes import it, so a
new kind of mapping reaches them all at once.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Mapping(BaseModel):
    """
    One line of a source's group mapping.

    Attributes:
        group: The group, as the source spells it.
        role: The instance role it gives (``viewer``, ``operator`` or ``admin``).
        team: The team it puts the person in, or the team a role is held in.
        team_role: Their role there.
        bind: The role held at the scope the team, project and environment name:
            a built-in team role or a custom role.
        project: The project a ``bind`` is held at.
        environment: The environment a ``bind`` is held at.
    """

    group: str = Field(max_length=256)
    role: str | None = None
    team: str | None = None
    team_role: str | None = None
    bind: str | None = None
    project: str | None = None
    environment: str | None = None


class CheckInfo(BaseModel):
    """
    One thing looked at when a source is tested.

    Attributes:
        name: What.
        ok: Whether it is right.
        detail: What was found, or what is wrong.
    """

    name: str
    ok: bool
    detail: str


class TestResponse(BaseModel):
    """
    A source looked at the way a sign-in would, without signing anybody in.

    Attributes:
        ok: Whether every check passed.
        checks: Each thing looked at.
    """

    __test__ = False

    ok: bool
    checks: list[CheckInfo]
