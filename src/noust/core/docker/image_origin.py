# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where an image comes from: a registry, Noust, somebody else's build on this machine, or nobody knows.

A page that lists images has to tell three things apart that all have no reference to ask a registry
about: an image whose registries nobody asked (the operator has not pressed "look for new versions"), an
image Noust built and named after an application's release, and an image somebody built on this machine.
The first is waiting for a look, and the other two will never have one; drawn the same, the second and the
third say they are waiting for something that cannot come.

The origin is a closed list and it is decided by the facts the look for new versions already uses
(rule 3): an image came from a registry when a digest of its ``RepoDigests`` is the one a registry knows a
name of the image by (:meth:`noust.core.docker.registry.LocalImage.digest_for`, which is also what makes
that look skip an image as built here), and a name Noust gives to what it keeps
(:func:`noust.core.docker.names.is_way_back`) is Noust's whatever Docker says of it.
"""

from __future__ import annotations

from noust.core.docker import names
from noust.core.docker.models import Image
from noust.core.docker.registry import LocalImage
from noust.core.docker.versions import parse_reference

__all__ = ["BUILT_NOUST", "BUILT_OTHER", "REGISTRY", "SOURCES", "UNKNOWN", "origin_of"]

#: It has a digest a registry knows it by: it was pulled.
REGISTRY = "registry"

#: Noust named it: an application's release, or the version a stack keeps to go back to.
BUILT_NOUST = "built_noust"

#: It has a name and no registry behind it: somebody built it on this machine, or loaded it.
BUILT_OTHER = "built_other"

#: Docker did not describe it, or it has neither a name nor a digest to say.
UNKNOWN = "unknown"

#: The whole list, in the order it is said.
SOURCES = (REGISTRY, BUILT_NOUST, BUILT_OTHER, UNKNOWN)


def origin_of(image: Image, local: LocalImage | None) -> str:
    """
    Say where an image comes from.

    Args:
        image: An image the gate listed.
        local: What ``docker inspect`` says of it, when Docker was asked and answered.

    Returns:
        One of :data:`SOURCES`. An image that has no registry behind it and no name is not guessed at.
    """
    if image.way_back or any(names.is_way_back(tag) for tag in image.tags):
        return BUILT_NOUST
    if local is None:
        return UNKNOWN
    for tag in image.tags:
        parsed = parse_reference(tag)
        if parsed is not None and local.digest_for(parsed) is not None:
            return REGISTRY
    if not image.tags:
        # A pulled image whose name moved on to a newer one keeps the digest it came with.
        return REGISTRY if local.digests else UNKNOWN
    return BUILT_OTHER
