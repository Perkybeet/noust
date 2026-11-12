# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an image reference and its tag say of a version, and what a tag that moved is to the one that runs.

Asking a registry is another module (:mod:`noust.core.docker.registry`) and deciding what to do with the
answer is another (``managers/docker_image_versions``). This one is the part that needs no process: read a
reference the way Docker reads it, read the numbers a tag carries, read the version a running image says
of itself, and say what a newer one is to it: a patch, a minor, a major, or that it cannot be said.

Three rules decide it, and each is the reason a screen can be trusted.

- **A tag that carries a major cannot jump to another by moving.** ``postgres:16`` is 16.x tomorrow,
  whatever the registry's newest is; ``postgres:latest`` can be 17. So a moved ``16`` is at most a minor
  and a moved ``latest`` is whatever the versions say.
- **The image's own words come before the tag's.** The label ``org.opencontainers.image.version`` and the
  ``<NAME>_VERSION`` variable of the official images say what runs; a tag that is ``latest`` says nothing.
- **When it cannot be said, it is said.** Two versions that are not both known, behind a tag that pins no
  major, are ``unknown``, and the caller treats a database that way as the worst it could be.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "ImageRef",
    "TagShape",
    "classify",
    "parse_reference",
    "parse_version",
    "registry_aliases",
    "tag_shape",
    "version_of_image",
    "with_default_tag",
]

#: The registry a reference without one is asked of.
DEFAULT_REGISTRY = "docker.io"

#: Every name Docker Hub answers to.
_DOCKER_HUB = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})

_COMPONENT = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*")
_TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
#: What Docker shows in place of a name once a tag has moved to another image: a short or a full id.
_AN_ID = re.compile(r"(?:sha256:)?(?:[0-9a-f]{12}|[0-9a-f]{64})")
# A host opens with a letter or a digit, never a dash: what becomes an argument must not be an option.
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::\d{1,5})?|\[[0-9a-f:]+\](?::\d{1,5})?")

#: A version at the front of a text: ``7.4.2``, ``v1.23``, ``16.4-1.pgdg`` (a Debian epoch ``1:`` first).
_VERSION = re.compile(r"(?:\d+:)?v?(\d+(?:\.\d+){0,3})")
#: A tag that opens with a number: ``7-alpine``, ``17.2``, ``v4.8.4``.
_TAG_NUMBERS = re.compile(r"v?(\d+(?:\.\d+){0,3})(?P<suffix>(?:[-_.+].*)?)")

#: The names the official images give the variable that says what version they are, where it is not the
#: repository's own name in capitals.
_VERSION_PREFIXES = {
    "postgres": ("PG", "POSTGRES", "POSTGRESQL"),
    "mongo": ("MONGO",),
    "mariadb": ("MARIADB",),
    "mysql": ("MYSQL",),
}

_VERSION_LABEL = "org.opencontainers.image.version"


@dataclass(frozen=True)
class ImageRef:
    """
    An image reference, read.

    Attributes:
        registry: The registry (``docker.io`` when the reference names none).
        repository: The repository, with ``library/`` for a Docker Hub image of no namespace.
        tag: The tag; ``latest`` when the reference names none and no digest; empty for a digest alone.
        digest: ``sha256:...`` when the reference pins one.
    """

    registry: str
    repository: str
    tag: str
    digest: str

    @property
    def pinned(self) -> bool:
        """Whether a digest decides which image it is: such a reference never moves."""
        return bool(self.digest)

    @property
    def name(self) -> str:
        """The repository as people write it: without ``docker.io`` and without ``library/``."""
        if self.registry == DEFAULT_REGISTRY:
            return self.repository.removeprefix("library/")
        return f"{self.registry}/{self.repository}"

    @property
    def canonical(self) -> str:
        """
        The reference spelled out in full, which is what a command is given: it is built from the
        parts that were read, never from the text they came in.
        """
        text = f"{self.registry}/{self.repository}"
        if self.tag:
            text += f":{self.tag}"
        return f"{text}@{self.digest}" if self.digest else text

    @property
    def repository_path(self) -> str:
        """``registry/repository``: what a digest of the repository is written after."""
        return f"{self.registry}/{self.repository}"


def parse_reference(text: str) -> ImageRef | None:
    """
    Read an image reference the way Docker does.

    Args:
        text: What a container says it runs.

    Returns:
        The reference; None for anything that is not one, among them the id Docker shows in place of a
        name once the tag moved to another image.
    """
    text = text.strip()
    if not text or _AN_ID.fullmatch(text) or any(char.isspace() for char in text):
        return None
    rest, _, digest = text.partition("@")
    if digest and not _DIGEST.fullmatch(digest):
        return None
    last = rest.rsplit("/", 1)[-1]
    tag = ""
    if ":" in last:
        rest, _, tag = rest.rpartition(":")
        if not _TAG.fullmatch(tag):
            return None
    parts = rest.split("/")
    registry = DEFAULT_REGISTRY
    if len(parts) > 1 and (("." in parts[0]) or (":" in parts[0]) or parts[0] == "localhost"):
        registry = parts[0]
        parts = parts[1:]
        if not _HOST.fullmatch(registry):
            return None
    if not parts or not all(_COMPONENT.fullmatch(part) for part in parts):
        return None
    if registry in _DOCKER_HUB:
        registry = DEFAULT_REGISTRY
        if len(parts) == 1:
            parts = ["library", parts[0]]
    if not tag and not digest:
        tag = "latest"
    return ImageRef(registry, "/".join(parts), tag, digest)


def with_default_tag(reference: str) -> str:
    """
    Args:
        reference: An image reference as a container was made from it.

    Returns:
        The reference with the tag Docker gives one that has none; one with a digest or that is an id
        is returned as it is.
    """
    last = reference.rsplit("/", 1)[-1]
    if "@" in last or ":" in last or reference.startswith("sha256:"):
        return reference
    return f"{reference}:latest"


def registry_aliases(registry: str) -> frozenset[str]:
    """
    Args:
        registry: A registry host as a reference names it.

    Returns:
        Every host name an account for that registry may carry (Docker Hub answers to three).
    """
    return _DOCKER_HUB if registry in _DOCKER_HUB else frozenset({registry})


@dataclass(frozen=True)
class TagShape:
    """
    What a tag carries.

    Attributes:
        numbers: The version at its front (``7-alpine`` is ``(7,)``), empty for a tag with none.
        suffix: What follows the number (``-alpine``).
        floating: The tag has no number: ``latest``, ``stable``, ``alpine``. What it points at is
            whatever the publisher decides.
    """

    numbers: tuple[int, ...]
    suffix: str
    floating: bool


def tag_shape(tag: str) -> TagShape:
    """
    Args:
        tag: An image tag; empty for a reference with a digest alone.

    Returns:
        The numbers it carries, what follows them, and whether it is one that moves with no number.
    """
    match = _TAG_NUMBERS.fullmatch(tag)
    if match is None:
        return TagShape((), "", True)
    return TagShape(
        tuple(int(part) for part in match.group(1).split(".")), match.group("suffix"), False
    )


def parse_version(text: str) -> tuple[int, ...] | None:
    """
    Args:
        text: A version as an image says it: ``7.4.2``, ``v1.23.16``, ``1:11.5.2+maria~ubu2404``.

    Returns:
        Its numbers; None when the text does not open with one.
    """
    match = _VERSION.match(text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _prefixes(repository: str) -> tuple[str, ...]:
    """
    Args:
        repository: ``library/postgres``, ``acme/shop``.

    Returns:
        The capitalised names its version variable may start with.
    """
    base = repository.rsplit("/", 1)[-1]
    squeezed = re.sub(r"[^a-z0-9]", "", base)
    return (
        *_VERSION_PREFIXES.get(base, ()),
        squeezed.upper(),
        re.sub(r"[^a-z0-9]", "_", base).upper(),
    )


def version_of_image(
    repository: str, *, tag: str, labels: Mapping[str, str], env: Sequence[str]
) -> tuple[int, ...] | None:
    """
    Read what version an image says it is.

    Args:
        repository: Its repository (``library/redis``).
        tag: The tag it runs under.
        labels: Its labels.
        env: Its environment as ``NAME=value`` lines.

    Returns:
        The version: its own label, else the variable the official images set (``REDIS_VERSION``, named
        after the repository, never another program's), else the numbers of the tag; None when none of
        them says.
    """
    said = parse_version(labels.get(_VERSION_LABEL, ""))
    if said is not None:
        return said
    prefixes = _prefixes(repository)
    for line in env:
        name, _, value = line.partition("=")
        if not name.endswith("_VERSION"):
            continue
        if name.removesuffix("_VERSION") in prefixes:
            found = parse_version(value)
            if found is not None:
                return found
    return tag_shape(tag).numbers or None


def classify(
    current: tuple[int, ...] | None, latest: tuple[int, ...] | None, shape: TagShape
) -> str:
    """
    Say what the version a tag points at now is to the one that runs.

    Args:
        current: The version that runs, when it is known.
        latest: The version the tag points at now, when it is known.
        shape: What the tag carries.

    Returns:
        ``major``, ``minor`` or ``patch`` by where the two first differ; when they are not both known, a
        tag that pins the major is a ``minor`` (it names ``7`` or ``16`` and no more) or a ``patch`` (it
        names ``16.4``), and one that pins nothing is ``unknown``.
    """
    if current is not None and latest is not None:
        width = max(len(current), len(latest))
        for index in range(width):
            before = current[index] if index < len(current) else None
            after = latest[index] if index < len(latest) else None
            if before != after:
                return "major" if index == 0 else "minor" if index == 1 else "patch"
        return "patch"
    if shape.numbers:
        return "minor" if len(shape.numbers) == 1 else "patch"
    return "unknown"
