# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The secret managers an application's variables can refer to, over HTTP.

A client of :class:`~noust.core.variables.providers.ProviderRegistry`, like
``noust secrets``. A provider's token is write-only here: it is accepted on a
write and never returned, by this API or any other. Adding, changing or
removing a provider decides which secrets a deploy can reach, so it is the
server's (never a team's) and asks for sudo mode.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.audit import record as audit_record
from noust.core.teams import TeamStore
from noust.core.variables.providers import KINDS, ProviderConfig, ProviderRegistry
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import actor_label
from noust.web.permissions.principal import actor_of

router = APIRouter(route_class=NoustErrorRoute)


class ProviderOut(BaseModel):
    """
    A secret manager Noust can fetch from.

    Attributes:
        name: What a reference calls it: ``{{secret:NAME/PATH}}``.
        kind: ``vault``, ``doppler`` or ``infisical``.
        address: Where it is; null for the kind's public service.
        settings: Its non-secret settings.
        team: The team allowed to name it; null for the whole server.
        has_token: Whether a token is stored. The token itself is never shown.
        reference: How an application refers to one of its secrets.
        created_at: When it was added.
        created_by: Who added it.
    """

    name: str
    kind: str
    address: str | None = None
    settings: dict[str, str] = Field(default_factory=dict)
    team: str | None = None
    has_token: bool
    reference: str
    created_at: str = ""
    created_by: str | None = None


class ProviderListResponse(BaseModel):
    """The secret managers this server knows."""

    providers: list[ProviderOut]
    total: int


class KindOut(BaseModel):
    """
    One kind of secret manager.

    Attributes:
        kind: Its name.
        default_address: Its public service, when it has one.
        required: The settings it needs, with what each is.
        optional: The settings it accepts.
        path: What follows the provider's name in a reference.
    """

    kind: str
    default_address: str | None = None
    required: dict[str, str]
    optional: dict[str, str]
    path: str


class KindListResponse(BaseModel):
    """The kinds of secret manager Noust can fetch from."""

    kinds: list[KindOut]


class ProviderRequest(BaseModel):
    """
    A secret manager to add, or to change.

    Attributes:
        kind: ``vault``, ``doppler`` or ``infisical``.
        address: Where it is; required unless the kind has a public service.
        settings: Its non-secret settings.
        token: Its token. Left out on a change, the stored one stays.
        team: Restrict it to a team's applications (the team's slug).
    """

    kind: str
    address: str | None = None
    settings: dict[str, str] = Field(default_factory=dict)
    token: str | None = Field(default=None, repr=False)
    team: str | None = None


class ProviderTestResponse(BaseModel):
    """What a check of a provider proved."""

    name: str
    message: str


_PATHS = {
    "vault": "secret/path#field (the field may be left out when the secret has one)",
    "doppler": "the secret's name in the project and config set on the provider",
    "infisical": "the secret's name in the project, environment and folder set on the provider",
}


def _out(config: ProviderConfig, registry: ProviderRegistry) -> ProviderOut:
    """
    Args:
        config: The provider.
        registry: Where its token is looked for.

    Returns:
        The API shape, without the token.
    """
    team = TeamStore().team_by_id(config.team_id) if config.team_id is not None else None
    return ProviderOut(
        name=config.name,
        kind=config.kind,
        address=config.address,
        settings=dict(config.settings),
        team=team.slug if team is not None else None,
        has_token=bool(registry.secrets.read(f"secret-providers/{config.name}")),
        reference=f"{{{{secret:{config.name}/PATH}}}}",
        created_at=config.created_at,
        created_by=config.created_by,
    )


@router.get("/kinds", response_model=KindListResponse)
def list_kinds(session: Annotated[dict, Depends(get_current_session)]) -> KindListResponse:
    """
    List the kinds of secret manager Noust can fetch from.

    Args:
        session: The authenticated session.

    Returns:
        The kinds, with the settings each takes.
    """
    return KindListResponse(
        kinds=[
            KindOut(
                kind=name,
                default_address=cls.default_address,
                required=dict(cls.required),
                optional=dict(cls.optional),
                path=_PATHS.get(name, ""),
            )
            for name, cls in sorted(KINDS.items())
        ]
    )


@router.get("", response_model=ProviderListResponse)
def list_providers(
    session: Annotated[dict, Depends(get_current_session)],
) -> ProviderListResponse:
    """
    List the secret managers this server knows.

    Args:
        session: The authenticated session.

    Returns:
        Them, without their tokens.
    """
    registry = ProviderRegistry()
    providers = [_out(config, registry) for config in registry.list()]
    return ProviderListResponse(providers=providers, total=len(providers))


@router.put("/{name}", response_model=ProviderOut)
def put_provider(
    name: str,
    body: ProviderRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ProviderOut:
    """
    Add a secret manager, or change one.

    Args:
        name: What references will call it.
        body: Its kind, address, settings and token.
        session: The authenticated, elevated session.

    Returns:
        The provider as stored, without its token.
    """
    registry = ProviderRegistry()
    team_id = None
    if body.team:
        team_id = TeamStore().require_team(body.team).id
    config = registry.save(
        name,
        body.kind,
        token=body.token,
        address=body.address,
        settings=body.settings,
        team_id=team_id,
        actor=actor_label(session),
    )
    audit_record(
        "secrets.provider.save",
        actor=actor_of(session),
        target=f"secret-provider:{name}",
        details={"kind": config.kind, "token_replaced": body.token is not None},
    )
    return _out(config, registry)


@router.delete("/{name}", response_model=ProviderListResponse)
def delete_provider(
    name: str, session: Annotated[dict, Depends(require_elevated)]
) -> ProviderListResponse:
    """
    Remove a secret manager and its token.

    Variables that still refer to it fail at their next deploy, saying so.

    Args:
        name: The provider.
        session: The authenticated, elevated session.

    Returns:
        The providers that remain.

    Raises:
        HTTPException: 404 when there is no such provider.
    """
    registry = ProviderRegistry()
    if not registry.delete(name):
        raise HTTPException(status_code=404, detail=f"No secret manager called {name}")
    audit_record(
        "secrets.provider.remove", actor=actor_of(session), target=f"secret-provider:{name}"
    )
    providers = [_out(config, registry) for config in registry.list()]
    return ProviderListResponse(providers=providers, total=len(providers))


@router.post("/{name}/test", response_model=ProviderTestResponse)
def test_provider(
    name: str, session: Annotated[dict, Depends(require_elevated)]
) -> ProviderTestResponse:
    """
    Check that a secret manager answers and accepts its token.

    Reads none of the operator's secrets.

    Args:
        name: The provider.
        session: The authenticated, elevated session.

    Returns:
        What was proven.

    Raises:
        SecretProviderError: It does not answer, or refuses the token.
    """
    message = ProviderRegistry().test(name)
    return ProviderTestResponse(name=name, message=message)
