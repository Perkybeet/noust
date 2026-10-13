# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The images a Compose stack kept for each release it served (checklist B-16).

A stack on a container application has one image per release and nothing else to
remember. A stack of services has one image per service, under the names its
compose file looks them up by, so going back to a release recreates the
containers from the images that release ran only if those names are known. The
release itself is a row of ``releases`` (when it was made, whether it serves, how
it ended); the rows of this module say which image each service ran in it.

This is the only module that reads or writes the ``compose_release_images`` table
(``schema_v14.RUNTIME_SQL``), by application id as ``releases`` is.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class RetainedImage:
    """
    The image one service ran in a release.

    Attributes:
        service: The Compose service.
        reference: The name the compose file looks the image up by, which the
            image is named again before the containers are recreated.
        image_id: The image's immutable id.
        tag: The name the release keeps it under so it is not pruned.
    """

    service: str
    reference: str
    image_id: str
    tag: str


def put_images(
    app_id: int,
    release_id: str,
    images: Iterable[RetainedImage],
    *,
    store: NoustStore | None = None,
) -> None:
    """
    Remember the images of a release, replacing any it had.

    Args:
        app_id: The application.
        release_id: The release.
        images: One per service.
        store: The store; the process-wide one when None.
    """
    rows = [(app_id, release_id, i.service, i.reference, i.image_id, i.tag) for i in images]
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM compose_release_images WHERE app_id = ? AND release_id = ?",
            (app_id, release_id),
        )
        cursor.executemany(
            "INSERT INTO compose_release_images "
            "(app_id, release_id, service, reference, image_id, tag) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def images_by_release(
    app_id: int, *, store: NoustStore | None = None
) -> dict[str, tuple[RetainedImage, ...]]:
    """
    Read the images of every release of an application.

    Args:
        app_id: The application.
        store: The store; the process-wide one when None.

    Returns:
        Release id to its images, sorted by service.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT release_id, service, reference, image_id, tag FROM compose_release_images "
            "WHERE app_id = ? ORDER BY release_id, service",
            (app_id,),
        )
        rows = cursor.fetchall()
    found: dict[str, list[RetainedImage]] = {}
    for release_id, service, reference, image_id, tag in rows:
        found.setdefault(release_id, []).append(RetainedImage(service, reference, image_id, tag))
    return {release: tuple(images) for release, images in found.items()}


def delete_images(
    app_id: int, release_ids: Iterable[str], *, store: NoustStore | None = None
) -> int:
    """
    Forget the images of some releases.

    Args:
        app_id: The application.
        release_ids: The releases.
        store: The store; the process-wide one when None.

    Returns:
        How many rows were deleted.
    """
    ids = list(release_ids)
    if not ids:
        return 0
    placeholders = ", ".join("?" for _ in ids)
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            f"DELETE FROM compose_release_images WHERE app_id = ? AND release_id IN ({placeholders})",  # noqa: S608 - placeholders only
            [app_id, *ids],
        )
        return cursor.rowcount
