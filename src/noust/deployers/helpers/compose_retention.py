# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Keeping the images of the releases a Compose stack served (checklist B-16).

A stack that built its images from a repository has no way back but building the
old commit again, which is minutes and needs everything the build needed. Keeping
the image each service ran, under a name of the release, makes going back the same
instant operation it is on the release layout: the images are named again as the
compose file looks them up, the containers are recreated without building, and the
health gate judges them.

Docker deletes an image that has no name (``docker image prune``), so a release's
image is kept by a tag of its own, ``<project>-<service>:noust-<release id>``. This
module is the only place that names, lists, restores and removes those tags; it
runs docker through the :class:`~noust.core.runner.CommandRunner` it is given.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from noust.core import legacy_names
from noust.core.compose_release_store import RetainedImage
from noust.core.runner import CommandRunner

#: What every tag that keeps a release's image starts with after the colon.
TAG_PREFIX = "noust-"

#: The tag the image each service ran before an update is kept under while the update runs
#: (``<project>-<service>:noust_previous``, with an underscore because every tag that starts with
#: ``noust-`` is a release's), and the one an update before 4.0 left: the next
#: update of a stack retags what is under the old name and removes it, and nothing that lists
#: unused images may offer either for removal.
PREVIOUS_TAG = "noust_previous"
LEGACY_PREVIOUS_TAG = legacy_names.PREVIOUS_IMAGE_TAG
PREVIOUS_TAGS = (PREVIOUS_TAG, LEGACY_PREVIOUS_TAG)

#: Deadline of one docker image command: they only touch names.
IMAGE_TIMEOUT = 60

#: Characters an image repository name cannot hold.
_NAME_INVALID = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class ImageRelease:
    """
    One release of an application that is kept as images.

    Attributes:
        id: The release id.
        commit: Short commit it was built from; None for a registry image.
        created_at: When it was created, ISO 8601 in UTC.
        activated_at: When it last became the one that serves.
        status: ``active``, ``superseded``, ``rolled_back``, ``failed`` or ``built``.
        active: Whether it is the one serving.
        present: Whether its images are on this host, so it can be activated.
    """

    id: str
    commit: str | None
    created_at: str
    activated_at: str | None
    status: str
    active: bool
    present: bool


def repository_name(project: str, service: str) -> str:
    """
    Name the repository a service's kept images live under.

    Args:
        project: The Compose project.
        service: The service.

    Returns:
        ``<project>-<service>`` restricted to what an image name may hold.
    """
    return _NAME_INVALID.sub("-", f"{project}-{service}".lower()).strip("._-") or "noust"


def release_tag(project: str, service: str, release_id: str) -> str:
    """
    Name the tag that keeps a service's image of a release.

    Args:
        project: The Compose project.
        service: The service.
        release_id: The release.

    Returns:
        ``<project>-<service>:noust-<release id>``.
    """
    return f"{repository_name(project, service)}:{TAG_PREFIX}{release_id}"


def kept_tags(runner: CommandRunner) -> set[str] | None:
    """
    List the tags on this host that keep a release's image.

    Args:
        runner: Runs docker.

    Returns:
        ``repository:tag`` of each, or None when Docker could not say (which is
        not the same as there being none).
    """
    listed = runner.run(
        [
            "docker",
            "images",
            "--filter",
            f"reference=*:{TAG_PREFIX}*",
            "--format",
            "{{.Repository}}:{{.Tag}}",
        ],
        timeout=IMAGE_TIMEOUT,
    )
    if not listed.success:
        return None
    return {line.strip() for line in listed.stdout.splitlines() if line.strip()}


def retain(
    runner: CommandRunner,
    project: str,
    serving: Iterable[tuple[str, str, str]],
    release_id: str,
) -> tuple[list[RetainedImage], list[str]]:
    """
    Name the images a stack serves after a release, so they are not pruned.

    Args:
        runner: Runs docker.
        project: The Compose project.
        serving: ``(service, reference, image id)`` of each service that runs.
        release_id: The release being kept.

    Returns:
        The images that were named, and a line for each that could not be: a
        release with a missing image cannot be gone back to, and says so.
    """
    kept: list[RetainedImage] = []
    problems: list[str] = []
    for service, reference, image_id in serving:
        tag = release_tag(project, service, release_id)
        tagged = runner.run(["docker", "image", "tag", image_id, tag], timeout=IMAGE_TIMEOUT)
        if tagged.success:
            kept.append(RetainedImage(service, reference, image_id, tag))
        else:
            problems.append(f"{service}: {(tagged.stderr or tagged.stdout).strip()}")
    return kept, problems


def restore(runner: CommandRunner, images: Iterable[RetainedImage]) -> list[str]:
    """
    Give the images of a release the names the compose file looks them up by.

    Every one is attempted even when an earlier one failed.

    Args:
        runner: Runs docker.
        images: The release's images.

    Returns:
        A line for each that could not be named; empty when all were.
    """
    problems: list[str] = []
    for image in images:
        named = runner.run(
            ["docker", "image", "tag", image.tag, image.reference], timeout=IMAGE_TIMEOUT
        )
        if not named.success:
            problems.append(
                f"{image.service}: {(named.stderr or named.stdout).strip() or 'docker image tag failed'}"
            )
    return problems


def remove(runner: CommandRunner, tags: Iterable[str]) -> list[str]:
    """
    Remove the tags that keep images.

    Only the name goes: an image another name or a container still uses stays,
    which Docker says and is reported rather than forced.

    Args:
        runner: Runs docker.
        tags: ``repository:tag`` of each.

    Returns:
        A line for each that could not be removed; empty when all were.
    """
    failures: list[str] = []
    for tag in tags:
        removed = runner.run(["docker", "image", "rm", tag], timeout=IMAGE_TIMEOUT)
        if not removed.success:
            failures.append(f"{tag}: {(removed.stderr or removed.stdout).strip()}")
    return failures
