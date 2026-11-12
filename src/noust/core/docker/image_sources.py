# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which reference a registry is asked about for what a container runs.

A container says the image it was made from, and that is the reference to ask the registry about.
There is one exception, which is why this is a module and not a line: a container application that runs
an image from a registry does not run that reference. Noust pulls it and names the image after the
application and its release (``noust-<app>:<release>``, so that every release can be gone back to), and
the registry's reference is kept in the application's settings. The gate's summary and the check that asks
the registries both need the same answer (rule 3), so it is here.

What has no registry to ask has no entry: what Noust started for itself, what a container application
builds from a Dockerfile, and a container Docker shows by an id because its tag moved to another image.
"""

from __future__ import annotations

from collections.abc import Sequence

from noust.core.container_store import ContainerStore
from noust.core.docker import names
from noust.core.docker.models import Container, OwnerKind
from noust.core.docker.versions import parse_reference, with_default_tag
from noust.core.store import NoustStore
from noust.validators.container import METHOD_IMAGE

__all__ = ["registry_references"]


def registry_references(containers: Sequence[Container], store: NoustStore) -> dict[str, str]:
    """
    Find the reference to ask a registry about for each container.

    Args:
        containers: The containers, each with its owner.
        store: The store, for the settings of the container applications.

    Returns:
        The reference by container name, with the tag Docker gives one that has none. A container with no
        registry to ask is not in it.
    """
    settings = ContainerStore(store)
    found: dict[str, str] = {}
    for container in containers:
        if container.owner.kind is OwnerKind.NOUST:
            continue
        reference = container.image
        if container.owner.kind is OwnerKind.APP and _is_release_image(
            container.image, container.owner.subject
        ):
            saved = settings.get(container.owner.subject)
            reference = (
                saved.image if saved and saved.method == METHOD_IMAGE and saved.image else ""
            )
        if reference and parse_reference(reference) is not None:
            found[container.name] = with_default_tag(reference)
    return found


def _is_release_image(image: str, domain: str) -> bool:
    """
    Args:
        image: What a container runs.
        domain: The application that owns it.

    Returns:
        Whether it is the image Noust names after a container application's release.
    """
    repository, _, tag = image.partition(":")
    return repository == names.image_repository(domain.replace(".", "-")) and bool(
        names.RELEASE_ID.fullmatch(tag)
    )
