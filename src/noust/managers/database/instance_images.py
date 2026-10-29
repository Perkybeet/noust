# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where a tag becomes a digest: pulling it and asking Docker which image that is.

An instance is pinned by digest, because a tag is a promise a registry can break and a digest is
a file. Two moments need the digest of a tag, and this is the one place that reads it (rule 3):

- **an upgrade** moves an instance to the newest image of the tag it was made with;
- **an image of the operator's own given by registry and tag** is pinned when the instance is
  made, so that an operator who types ``ghcr.io/acme/pg:17.2`` gets what a digest gives.

The digest is the one of the image *index*, which is what ``image: repo:tag@sha256:...`` pins on
a multi-architecture image, and what ``RepoDigests`` lists after a pull.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from noust.core.exceptions import DatabaseEngineError

#: What Docker may take to pull an image: a database image is several hundred megabytes.
PULL_TIMEOUT = 1800

#: How long asking Docker about an image it holds may take.
INSPECT_TIMEOUT = 30

#: Registries whose images Docker names without a host, and the namespace of Docker Hub's own.
_HUB_HOSTS = ("docker.io/", "index.docker.io/", "registry-1.docker.io/")
_HUB_NAMESPACE = "library/"


def _canonical(repository: str) -> str:
    """
    Args:
        repository: A repository as typed or as Docker lists it.

    Returns:
        The same name without the parts Docker leaves out of one: Docker Hub's host and its
        ``library/`` namespace.
    """
    for host in _HUB_HOSTS:
        repository = repository.removeprefix(host)
    return repository.removeprefix(_HUB_NAMESPACE)


def digest_of(listed: object, repository: str) -> str | None:
    """
    Pick, among the digests Docker lists for an image, the one of a repository.

    Args:
        listed: ``RepoDigests``: strings ``repository@sha256:...``.
        repository: The repository that was pulled.

    Returns:
        ``sha256:...``; None when the list holds none for it. Docker lists a repository under
        the name it has for it, so Docker Hub's ``docker.io/`` and ``library/`` are not part of
        the comparison, and a list of exactly one entry (what a pull by tag leaves) is that
        repository's whatever it is called.
    """
    entries = [
        entry.partition("@")
        for entry in (listed if isinstance(listed, list) else [])
        if isinstance(entry, str) and entry.partition("@")[2].startswith("sha256:")
    ]
    wanted = _canonical(repository)
    for name, _, digest in entries:
        if _canonical(name) == wanted:
            return digest
    return entries[0][2] if len(entries) == 1 else None


def pull_and_read_digest(
    docker: Callable[..., Any], repository: str, tag: str, *, say: Callable[[str], Any]
) -> str:
    """
    Pull a tag and read the digest the registry served for it.

    Args:
        docker: Runs ``docker`` with the arguments and a ``timeout`` keyword, and returns a
            result with ``success``, ``stdout`` and ``stderr`` (the manager's own door to it,
            which goes through the command runner).
        repository: The image's repository, registry included.
        tag: The tag.
        say: Where progress is reported.

    Returns:
        ``sha256:...``: the digest of the image index the tag points at now.

    Raises:
        DatabaseEngineError: Docker could not pull it, or says nothing of which image it is.
    """
    reference = f"{repository}:{tag}"
    say(f"Reading the digest of {reference}")
    pulled = docker("pull", reference, timeout=PULL_TIMEOUT)
    if not pulled.success:
        raise DatabaseEngineError(
            f"Docker could not pull {reference}",
            details=(
                "Check that this server reaches the registry and that the Docker daemon runs. "
                "A private registry needs 'docker login' on this server."
            ),
            output=(pulled.stderr or pulled.stdout).strip() or None,
        )
    shown = docker(
        "inspect",
        "--type",
        "image",
        "--format",
        "{{json .RepoDigests}}",
        reference,
        timeout=INSPECT_TIMEOUT,
    )
    try:
        listed = json.loads(shown.stdout or "[]") if shown.success else []
    except json.JSONDecodeError:
        listed = []
    found = digest_of(listed, repository)
    if found is None:
        raise DatabaseEngineError(
            f"Docker did not say which digest {reference} is",
            details="Run 'docker image inspect' on it to see what it holds.",
            output=(shown.stderr or shown.stdout).strip() or None,
        )
    return found
