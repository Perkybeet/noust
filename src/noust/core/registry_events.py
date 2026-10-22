# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a container registry says when an image was pushed, and which application it is for.

A CI that only pushes an image cannot tell Noust to deploy it, so the registries do (checklist
A-14): GitHub Container Registry sends a ``registry_package`` event (a GitHub webhook, signed
with the application's secret like any other), and Docker Hub sends its own payload with no
signature at all, so the address it is given carries a token only the secret's owner can make.
This module is the pure part: reading either payload, comparing the image they name with the
image an application runs (two spellings of one image are one image), and making the token. The
route that receives them is :mod:`noust.web.api.registry_hooks`; the decision whether the
digest is new is :mod:`noust.managers.image_updates`.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import ValidationError
from noust.validators.container import DOCKER_HUB, split_reference

#: The provider names the delivery log and the hook's answers use.
GHCR = "ghcr"
DOCKER_HUB_PROVIDER = "dockerhub"

#: The GitHub events a delivery may be, for a GitHub Container Registry push.
GITHUB_EVENT = "registry_package"

#: What a registry package's action must be for it to be an image that was pushed.
PUBLISHED_ACTIONS = frozenset({"published", "updated"})

#: How long the token in a Docker Hub address is, in hexadecimal digits.
TOKEN_LENGTH = 40


@dataclass(frozen=True)
class RegistryEvent:
    """
    An image a registry says was pushed.

    Attributes:
        provider: ``ghcr`` or ``dockerhub``.
        registry: The registry host (``docker.io`` for Docker Hub).
        repository: The path within it.
        tag: The tag that was pushed; None when the push has none.
        digest: The digest of what was pushed, when the registry says (GitHub does).
    """

    provider: str
    registry: str
    repository: str
    tag: str | None
    digest: str | None = None

    @property
    def reference(self) -> str:
        """The image as it would be pulled, for the delivery log."""
        host = "" if self.registry == DOCKER_HUB else f"{self.registry}/"
        return f"{host}{self.repository}" + (f":{self.tag}" if self.tag else "")


def registry_hook_token(secret: str, domain: str) -> str:
    """
    Make the token of the address Docker Hub is given.

    Docker Hub signs nothing and cannot send a header, so the address itself is the credential.
    It is made from the application's webhook secret by a one-way function, so the address
    (which ends up in logs on the way, and in Docker Hub's settings page) says nothing of the
    secret that signs the forge's deliveries, and rotating the secret retires it.

    Args:
        secret: The application's webhook secret.
        domain: The application's domain.

    Returns:
        :data:`TOKEN_LENGTH` hexadecimal digits.
    """
    digest = hmac.new(secret.encode(), f"registry-hook:{domain}".encode(), hashlib.sha256)
    return digest.hexdigest()[:TOKEN_LENGTH]


def token_matches(secret: str, domain: str, presented: str) -> bool:
    """
    Compare a token with the one the secret makes, in constant time.

    Args:
        secret: The application's webhook secret.
        domain: The application's domain.
        presented: What the address carried.

    Returns:
        Whether it is the token.
    """
    return hmac.compare_digest(registry_hook_token(secret, domain), presented.strip().lower())


def _clean(value: Any) -> str:
    """
    Args:
        value: Something a payload held.

    Returns:
        It as trimmed lower-case text; empty when it is not text.
    """
    return value.strip().lower() if isinstance(value, str) else ""


def parse_ghcr(payload: dict[str, Any]) -> RegistryEvent | None:
    """
    Read a ``registry_package`` event of GitHub's container registry.

    Args:
        payload: The delivery's JSON.

    Returns:
        The image that was pushed; None when the event is not a container image published (a
        package of another kind, a deletion, an untagged version, a payload with no image in it).
    """
    if _clean(payload.get("action")) not in PUBLISHED_ACTIONS:
        return None
    package = payload.get("registry_package")
    if not isinstance(package, dict) or _clean(package.get("package_type")) != "container":
        return None
    version = package.get("package_version")
    if not isinstance(version, dict):
        return None
    url = _clean(version.get("package_url"))
    metadata = version.get("container_metadata")
    tag_info = metadata.get("tag") if isinstance(metadata, dict) else None
    tag = _clean(tag_info.get("name")) if isinstance(tag_info, dict) else ""
    digest = (_clean(tag_info.get("digest")) if isinstance(tag_info, dict) else "") or _clean(
        version.get("version")
    )
    try:
        # ``package_url`` is ``ghcr.io/owner/name:tag``.
        registry, repository, from_url, _ = split_reference(url.split("@", 1)[0])
    except ValidationError:
        return None
    # The tag the metadata names wins (the url's is the tag too, or ``latest`` by omission).
    chosen = tag or (from_url if ":" in url.rsplit("/", 1)[-1] else "")
    if not chosen:
        return None
    return RegistryEvent(
        provider=GHCR,
        registry=registry,
        repository=repository,
        tag=chosen,
        digest=digest if digest.startswith("sha256:") else None,
    )


def parse_dockerhub(payload: dict[str, Any]) -> RegistryEvent | None:
    """
    Read the payload Docker Hub sends when an image is pushed.

    Args:
        payload: The delivery's JSON (``push_data`` and ``repository``).

    Returns:
        The image that was pushed; None when the payload has no repository or no tag.
    """
    repository = payload.get("repository")
    push = payload.get("push_data")
    if not isinstance(repository, dict) or not isinstance(push, dict):
        return None
    name = _clean(repository.get("repo_name"))
    tag = _clean(push.get("tag"))
    if not name or not tag:
        return None
    try:
        registry, path, _, _ = split_reference(name)
    except ValidationError:
        return None
    return RegistryEvent(provider=DOCKER_HUB_PROVIDER, registry=registry, repository=path, tag=tag)


def why_not_for(image: str, event: RegistryEvent) -> str | None:
    """
    Say why a pushed image is not the one an application runs.

    Args:
        image: The application's image reference.
        event: What the registry said was pushed.

    Returns:
        The reason, or None when the application runs that image at that tag.
    """
    try:
        registry, repository, tag, digest = split_reference(image)
    except ValidationError:
        return "the application's image is not a reference"
    if digest is not None:
        return "the application is pinned by digest, which never changes"
    if (registry, repository) != (event.registry, event.repository):
        return f"it runs {registry}/{repository}, not {event.reference}"
    if tag != event.tag:
        return f"it runs the tag {tag}, and {event.tag} was pushed"
    return None
