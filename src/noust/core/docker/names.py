# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Noust calls the things it makes in Docker.

The gate to Docker (:mod:`noust.core.docker`) decides whose a container, an image, a volume or a
network is from the names Noust gave them, and it sits in ``core``, below the managers and the
deployers that write those names. They are said here, once (rule 3), and the modules that used to
say them (the images a stack keeps to go back to, the image of a container application, the files of
a database instance) take them from here.

Nothing in this module touches Docker or the disk: it is spelling.
"""

from __future__ import annotations

import re

from noust.core import legacy_names, paths

__all__ = [
    "IMAGE_PREFIX",
    "LEGACY_PREVIOUS_TAG",
    "PREVIOUS_TAG",
    "PREVIOUS_TAGS",
    "RELEASE_ID",
    "TAG_PREFIX",
    "image_repository",
    "image_tag",
    "instance_config_volume",
    "instance_container",
    "instance_data_volume",
    "instance_project",
    "instance_unit",
    "is_way_back",
    "project_of_directory",
    "release_of_tag",
    "release_tag",
    "repository_name",
    "tunnel_container",
]

# -- The images an application keeps to go back to -------------------------------------------------

#: What every tag that keeps a stack's release image starts with after the colon.
TAG_PREFIX = "noust-"

#: The tag the image each service ran before an update is kept under while the update runs
#: (``<project>-<service>:noust_previous``, with an underscore because every tag that starts with
#: ``noust-`` is a release's), and the one an update before 4.0 left: the next update of a stack
#: retags what is under the old name and removes it, and nothing that lists unused images may offer
#: either for removal.
PREVIOUS_TAG = "noust_previous"
LEGACY_PREVIOUS_TAG = legacy_names.PREVIOUS_IMAGE_TAG
PREVIOUS_TAGS = (PREVIOUS_TAG, LEGACY_PREVIOUS_TAG)

#: Every image a container application runs is named after it.
IMAGE_PREFIX = "noust-"

#: A release id, as ``ReleaseManager`` hands them out.
RELEASE_ID = re.compile(r"\d{8}-\d{6}-(?:[0-9a-f]{7}|nogit)(?:-[1-9][0-9]*)?")

#: Characters an image repository name cannot hold.
_NAME_INVALID = re.compile(r"[^a-z0-9._-]+")


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


def image_repository(app_name: str) -> str:
    """
    Name the repository every image of a container application is kept under.

    Args:
        app_name: The application's name.

    Returns:
        ``noust-<app>``.
    """
    return f"{IMAGE_PREFIX}{app_name}"


def image_tag(app_name: str, release_id: str) -> str:
    """
    Name the image of one release of a container application.

    Args:
        app_name: The application's name.
        release_id: The release's id.

    Returns:
        ``noust-<app>:<release>``.
    """
    return f"{image_repository(app_name)}:{release_id}"


def release_of_tag(app_name: str, tag: str) -> str | None:
    """
    Read the release an application's image tag names.

    Args:
        app_name: The application's name.
        tag: An image reference as Docker lists it.

    Returns:
        The release id, or None when the tag is not one of this application's release images.
    """
    repository, _, release = tag.partition(":")
    if repository != image_repository(app_name) or RELEASE_ID.fullmatch(release) is None:
        return None
    return release


def is_way_back(reference: str) -> bool:
    """
    Tell whether an image is one an application keeps to go back to, by its name.

    Such an image has no container after an update, which is exactly what makes it look unused, and
    removing it is removing the application's way back: it is never offered as unused.

    Args:
        reference: ``repository:tag`` as Docker lists an image.

    Returns:
        True for the image a stack keeps under a tag of a release (``<project>-<service>:noust-...``),
        for the one it keeps while an update runs (:data:`PREVIOUS_TAGS`) and for the image of a
        release of a container application (``noust-<app>:<release>``).
    """
    repository, _, tag = reference.rpartition(":")
    if not repository:
        return False
    if tag in PREVIOUS_TAGS or tag.startswith(TAG_PREFIX):
        return True
    return repository.startswith(IMAGE_PREFIX) and RELEASE_ID.fullmatch(tag) is not None


# -- The files and objects of a database instance ---------------------------------------------------


def instance_unit(name: str) -> str:
    """
    Args:
        name: The instance's name.

    Returns:
        The systemd unit that runs it, without the ``.service`` suffix.
    """
    return f"{paths.DB_INSTANCE_UNIT_PREFIX}{name}"


def instance_project(name: str) -> str:
    """
    Args:
        name: The instance's name.

    Returns:
        Its Compose project (passed as ``-p`` everywhere, so the project never depends on the
        directory's name).
    """
    return instance_unit(name)


def instance_container(name: str) -> str:
    """
    Args:
        name: The instance's name.

    Returns:
        Its container's name, fixed so ``docker exec`` and ``docker logs`` always find it.
    """
    return instance_unit(name)


def instance_data_volume(name: str) -> str:
    """
    Args:
        name: The instance's name.

    Returns:
        The Docker volume that holds its data.
    """
    return f"{instance_unit(name)}-data"


def instance_config_volume(name: str) -> str:
    """
    Args:
        name: The instance's name.

    Returns:
        The Docker volume of an engine's second declared volume (MongoDB's ``/data/configdb``),
        named so Docker never leaves an anonymous one behind each time the container is recreated.
    """
    return f"{instance_unit(name)}-config"


# -- The connector of a tunnel ----------------------------------------------------------------------


def tunnel_container(name: str) -> str:
    """
    Args:
        name: The tunnel's name.

    Returns:
        The container that runs its connector where there is no systemd (the central of a NAS), fixed so
        ``docker rm`` and ``docker inspect`` always find it: what the unit of a server is called.
    """
    return f"{paths.TUNNEL_UNIT_PREFIX}{name}"


# -- A Compose project ------------------------------------------------------------------------------

_PROJECT_INVALID = re.compile(r"[^a-z0-9_-]+")
_PROJECT_LEADING_JUNK = re.compile(r"^[^a-z0-9]+")


def project_of_directory(directory_name: str) -> str:
    """
    Name the Compose project a directory gives its stack when nothing names it.

    Compose lower-cases the name of the directory, keeps ``[a-z0-9_-]`` and starts on a letter or a
    digit. The containers, networks and named volumes of the stack carry that name.

    Args:
        directory_name: The last component of the directory the compose file is in.

    Returns:
        The project name; empty when nothing of the name is left.
    """
    name = _PROJECT_INVALID.sub("", directory_name.lower())
    return _PROJECT_LEADING_JUNK.sub("", name)
