# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where a registry delivers, for an application that runs a registry image (checklist A-14).

An application's webhook secret (:mod:`noust.integrations.webhook`) signs the deliveries of a code
host. The same secret serves a container registry: GitHub Container Registry signs its
``registry_package`` events with it, and Docker Hub, which signs nothing, is given an address that
carries a token made from it (:func:`noust.core.registry_events.registry_hook_token`). This is the
one answer to "what do I give the registry", drawn on by ``noust app webhook registry`` and the
console, so they cannot disagree. The token is a credential: it is shown only when asked, and the
API asks for sudo mode.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from noust.core.container_store import ContainerStore
from noust.core.exceptions import ValidationError
from noust.core.registry_events import GITHUB_EVENT, registry_hook_token
from noust.core.store import App, get_store
from noust.validators.container import split_reference

__all__ = ["RegistryHook", "describe"]

#: What the address shows in place of the token until it is asked for.
MASKED_TOKEN = "<token>"  # noqa: S105 - a placeholder, not a password


@dataclass(frozen=True)
class RegistryHook:
    """
    What to give a registry so a push of the image deploys it.

    Attributes:
        domain: The application.
        supported: Whether it runs an image from a registry; nothing else follows a tag.
        reason: Why not, when it does not.
        image: The image it runs.
        pinned: Whether the image is pinned by digest, which a push never changes.
        enabled: Whether the application has a webhook secret to sign or make tokens with.
        ghcr_url: Where GitHub Container Registry's ``registry_package`` event is sent (the
            secret signs it).
        ghcr_events: The events to enable on the GitHub repository's webhook.
        dockerhub_url: Where Docker Hub's webhook is pointed; the token is masked unless it was
            asked for.
        public: Whether the addresses are ones a registry can reach.
    """

    domain: str
    supported: bool
    reason: str | None = None
    image: str | None = None
    pinned: bool = False
    enabled: bool = False
    ghcr_url: str | None = None
    ghcr_events: tuple[str, ...] = (GITHUB_EVENT,)
    dockerhub_url: str | None = None
    public: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        data = asdict(self)
        data["ghcr_events"] = list(self.ghcr_events)
        return data


def describe(app: App, *, fallback_base: str | None = None, reveal: bool = False) -> RegistryHook:
    """
    Say what to give a registry for an application.

    Args:
        app: The application.
        fallback_base: The base of ``/hooks`` at an address the caller has, for when the hooks
            were not exposed (``noust web expose-hooks``).
        reveal: Put the real token in the Docker Hub address.

    Returns:
        The answer; the addresses are None when there is nowhere to give them or no secret.
    """
    from noust.integrations.hooks_site import public_hooks_url

    settings = ContainerStore().get(app.domain) if app.app_type == "container" else None
    if settings is None or settings.method != "image" or not settings.image:
        return RegistryHook(
            app.domain,
            supported=False,
            reason="Only an application that runs an image from a registry follows its tag",
        )
    try:
        pinned = split_reference(settings.image)[3] is not None
    except ValidationError:
        pinned = False
    secret = get_store().get_webhook_secret(app.domain)
    public = public_hooks_url()
    base = public or (fallback_base.rstrip("/") if fallback_base else None)
    if not secret or base is None:
        return RegistryHook(
            app.domain,
            supported=True,
            image=settings.image,
            pinned=pinned,
            enabled=bool(secret),
            public=public is not None,
        )
    token = registry_hook_token(secret, app.domain) if reveal else MASKED_TOKEN
    return RegistryHook(
        app.domain,
        supported=True,
        image=settings.image,
        pinned=pinned,
        enabled=True,
        ghcr_url=f"{base}/registry/{app.domain}",
        dockerhub_url=f"{base}/registry/{app.domain}/{token}",
        public=public is not None,
    )
