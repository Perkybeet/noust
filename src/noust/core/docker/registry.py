# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Asking a registry what a tag is now, and what a local image says of itself.

What is asked is the question ``docker pull`` asks, put without taking the layers: the digest of the
manifest behind a tag, and the config of the image behind that, which holds its environment, its labels
and the day it was built. Everything goes through the runner as an argument list built from a reference
that was read (:func:`noust.core.docker.versions.parse_reference`), never from the text it came in, so an
option or a path cannot become part of a command (rule 1, and the gate's property that Docker is only
given what Docker listed).

Two ways, and the second only when it is told to.

- ``docker buildx imagetools inspect`` reads the manifest in the registry. Nothing is downloaded and no
  local tag changes. A Docker without that plugin cannot ask this way, and says so.
- ``docker pull``, then ``inspect``, is the question the existing image update asks. It downloads what
  moved and moves the local tag, which is why it is a thing a person chooses and not what a look does.

The registry's account reaches the command through its environment (``DOCKER_CONFIG`` of a session that
:class:`~noust.managers.registry_logins.RegistryManager` logs in and removes), never in an argument.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from noust.core.docker.versions import ImageRef, parse_reference
from noust.core.docker_image_checks import VersionRecord
from noust.core.runner import CommandRunner

__all__ = [
    "IMAGE_FORMAT",
    "LocalImage",
    "RemoteImage",
    "look_at_registry",
    "read_config",
    "read_local_images",
    "settled",
]

#: Seconds a manifest and a config take to be read.
ASK_TIMEOUT = 60

#: Seconds a pull gets: it is what moves the layers.
PULL_TIMEOUT = 1800

#: Seconds ``inspect`` of images gets.
INSPECT_TIMEOUT = 60

#: What ``docker inspect --type image`` is asked to write: one JSON object a line, naming only what
#: this module reads.
IMAGE_FORMAT = (
    '{"id":{{json .Id}},"digests":{{json .RepoDigests}},"env":{{json .Config.Env}},'
    '"labels":{{json .Config.Labels}},"created":{{json .Created}},"os":{{json .Os}},'
    '"arch":{{json .Architecture}}}'
)

_ID = re.compile(r"(?:sha256:)?[0-9a-f]{12,64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class LocalImage:
    """
    What a local image says of itself.

    Attributes:
        id: Its id.
        digests: Its ``RepoDigests``: ``repository@sha256:...`` for each registry it came from.
        env: Its environment as ``NAME=value`` lines.
        labels: Its labels.
        created: When it was built, as the image says.
        platform: ``os/architecture``.
    """

    id: str
    digests: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    labels: Mapping[str, str] = field(default_factory=dict)
    created: str = ""
    platform: str = ""

    def digest_for(self, reference: ImageRef) -> str | None:
        """
        Args:
            reference: A reference to the repository the image should have come from.

        Returns:
            The digest the registry knows it by in that repository; None when it came from another or
            from none (a build on this machine has no digest).
        """
        for entry in self.digests:
            name, _, digest = entry.partition("@")
            found = parse_reference(name)
            if found is not None and found.repository_path == reference.repository_path:
                return digest or None
        return None


@dataclass(frozen=True)
class RemoteImage:
    """
    What the registry has behind a tag, or why it could not be asked.

    Attributes:
        ok: The digest was read.
        digest: The manifest's digest now.
        created: When the image was built; None when the config could not be read.
        env: Its environment.
        labels: Its labels.
        method: ``imagetools`` or ``pull``; empty when nothing was asked (a pinned reference).
        why: When it was not read: ``unsupported`` (this Docker cannot ask without downloading),
            ``denied`` (the registry refused the account or has none), ``not_found`` or
            ``unreachable``.
        detail: One sentence for a person: what Docker said, or what would make it work.
    """

    ok: bool
    digest: str | None = None
    created: str | None = None
    env: tuple[str, ...] = ()
    labels: Mapping[str, str] = field(default_factory=dict)
    method: str = ""
    why: str = ""
    detail: str = ""


def _parse_images(text: str) -> dict[str, LocalImage]:
    """
    Args:
        text: What ``inspect`` wrote with :data:`IMAGE_FORMAT`, an object a line.

    Returns:
        The images by the id they were asked by.
    """
    found: dict[str, LocalImage] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not row.get("id"):
            continue
        labels = row.get("labels")
        found[str(row["id"])] = LocalImage(
            id=str(row["id"]),
            digests=tuple(str(item) for item in row.get("digests") or ()),
            env=tuple(str(item) for item in row.get("env") or ()),
            labels={str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {},
            created=str(row.get("created") or ""),
            platform=f"{row.get('os') or ''}/{row.get('arch') or ''}".strip("/"),
        )
    return found


def read_local_images(runner: CommandRunner, ids: Sequence[str]) -> dict[str, LocalImage]:
    """
    Look at local images in one process.

    Args:
        runner: Runs it.
        ids: Image ids as Docker listed them. Anything that is not an id is dropped: what a person typed
            never reaches a command.

    Returns:
        The images by id; empty when there is nobody to ask about or Docker said nothing.
    """
    wanted = [ident for ident in dict.fromkeys(ids) if _ID.fullmatch(ident)]
    if not wanted:
        return {}
    result = runner.run(
        ["docker", "inspect", "--type", "image", "--format", IMAGE_FORMAT, *wanted],
        timeout=INSPECT_TIMEOUT,
    )
    return _parse_images(result.stdout)


def settled(
    runner: CommandRunner, records: Sequence[VersionRecord], image_ids: Mapping[str, str]
) -> list[VersionRecord]:
    """
    Take into account what runs now when reading what a look found.

    What a look found is a fact of its moment: a record that says a version is new stays so until the
    next look, though the application may have been updated since. A ``newer`` record whose running image
    came with the digest the registry had is turned ``current``; everything else comes back as it was.
    Nothing is asked unless there is a ``newer`` record with an image to look at.

    Args:
        runner: Runs ``inspect``.
        records: What the last look found.
        image_ids: The id of the image that runs each reference now.

    Returns:
        The records, in order.
    """
    wanted = [
        image_ids[item.reference]
        for item in records
        if item.state == "newer" and item.reference in image_ids
    ]
    if not wanted:
        return list(records)
    local = read_local_images(runner, wanted)
    found: list[VersionRecord] = []
    for item in records:
        image = local.get(image_ids.get(item.reference, ""))
        parsed = parse_reference(item.reference)
        digest = image.digest_for(parsed) if image is not None and parsed is not None else None
        if item.state == "newer" and digest is not None and digest == item.remote_digest:
            item = replace(item, state="current", change="", local_digest=digest)
        found.append(item)
    return found


def _config(body: Mapping[str, Any]) -> tuple[str, tuple[str, ...], dict[str, str]] | None:
    """
    Args:
        body: One image config as buildx prints it.

    Returns:
        Its ``created``, ``Env`` and ``Labels``; None when it is not an image config.
    """
    inner = body.get("config")
    if not isinstance(inner, dict):
        return None
    labels = inner.get("Labels")
    return (
        str(body.get("created") or ""),
        tuple(str(item) for item in inner.get("Env") or ()),
        {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {},
    )


def read_config(text: str, platform: str) -> tuple[str, tuple[str, ...], dict[str, str]] | None:
    """
    Read the image config ``imagetools inspect --format '{{json .Image}}'`` prints.

    Args:
        text: What it printed: one config, or (for a tag of several platforms) a map of platform to
            config.
        platform: ``os/architecture`` of the image that runs, to pick its own among several.

    Returns:
        ``(created, env, labels)``; None when it is not a config.
    """
    try:
        body = json.loads(text)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    if "config" in body:
        return _config(body)
    chosen = body.get(platform) if platform in body else next(iter(body.values()), None)
    return _config(chosen) if isinstance(chosen, dict) else None


def _failure(output: str, *, method: str) -> RemoteImage:
    """
    Args:
        output: What the failed command said.
        method: How it was asked.

    Returns:
        Why it failed, in the registry's own words.
    """
    lowered = output.lower()
    first = next((line.strip() for line in output.splitlines() if line.strip()), "no output")
    if "not a docker command" in lowered or "unknown command" in lowered:
        return RemoteImage(
            False,
            method=method,
            why="unsupported",
            detail="This Docker cannot ask a registry without downloading the image "
            "(it has no buildx). Look with --pull to download what moved and compare.",
        )
    if "unauthorized" in lowered or "denied" in lowered or "authentication required" in lowered:
        return RemoteImage(
            False,
            method=method,
            why="denied",
            detail=f"The registry refused: {first}. Add the account for it: noust registry add.",
        )
    if "not found" in lowered or "manifest unknown" in lowered or "no such manifest" in lowered:
        return RemoteImage(
            False, method=method, why="not_found", detail=f"The registry has no such tag: {first}"
        )
    return RemoteImage(False, method=method, why="unreachable", detail=first)


def _manifest_digest(output: str) -> str | None:
    """
    Args:
        output: What ``imagetools inspect`` printed: ``Name:``, ``MediaType:`` and ``Digest:`` lines, then
            the manifests the tag lists.

    Returns:
        The digest of the tag's own manifest (the first ``Digest:`` line); None when there is none.
    """
    for line in output.splitlines():
        label, _, value = line.partition(":")
        if label.strip() == "Digest" and _DIGEST.fullmatch(value.strip()):
            return value.strip()
    return None


def look_at_registry(
    runner: CommandRunner,
    reference: ImageRef,
    *,
    env: Mapping[str, str] | None = None,
    platform: str = "linux/amd64",
    pull: bool = False,
) -> RemoteImage:
    """
    Ask a registry what a tag is now.

    Args:
        runner: Runs the commands.
        reference: The image, read.
        env: The environment of the commands: ``DOCKER_CONFIG`` of the account's session, or None.
        platform: ``os/architecture`` of what runs, to read its own config among several.
        pull: Download the image and compare, which is what a Docker without buildx needs; a look that
            is not told to does not move a local tag.

    Returns:
        What is behind the tag, or why that could not be read. A reference that pins a digest is
        answered without asking: it never moves.
    """
    if reference.pinned:
        return RemoteImage(True, digest=reference.digest, detail="Pinned by digest: it never moves")
    target = reference.canonical
    if pull:
        return _by_pull(runner, target, reference, env)
    asked = runner.run(
        ["docker", "buildx", "imagetools", "inspect", target], env=env, timeout=ASK_TIMEOUT
    )
    if not asked.success:
        return _failure(asked.stderr or asked.stdout, method="imagetools")
    digest = _manifest_digest(asked.stdout)
    if digest is None:
        return RemoteImage(
            False,
            method="imagetools",
            why="unreachable",
            detail="The registry answered with something that has no digest",
        )
    described = runner.run(
        ["docker", "buildx", "imagetools", "inspect", "--format", "{{json .Image}}", target],
        env=env,
        timeout=ASK_TIMEOUT,
    )
    config = read_config(described.stdout, platform) if described.success else None
    created, image_env, labels = config if config is not None else (None, (), {})
    return RemoteImage(
        True,
        digest=digest,
        created=created or None,
        env=image_env,
        labels=labels,
        method="imagetools",
    )


def _by_pull(
    runner: CommandRunner, target: str, reference: ImageRef, env: Mapping[str, str] | None
) -> RemoteImage:
    """
    Download an image and read what Docker says of it.

    Args:
        runner: Runs the commands.
        target: The reference, spelled out in full.
        reference: The same, read.
        env: The environment of the commands.

    Returns:
        What came, or why it did not.
    """
    pulled = runner.run(["docker", "pull", target], env=env, timeout=PULL_TIMEOUT)
    if not pulled.success:
        return _failure(pulled.stderr or pulled.stdout, method="pull")
    result = runner.run(
        ["docker", "inspect", "--type", "image", "--format", IMAGE_FORMAT, target],
        timeout=INSPECT_TIMEOUT,
    )
    image = next(iter(_parse_images(result.stdout).values()), None)
    digest = image.digest_for(reference) if image is not None else None
    if image is None or digest is None:
        return RemoteImage(
            False,
            method="pull",
            why="unreachable",
            detail="The image was pulled but Docker did not say which digest it came with",
        )
    return RemoteImage(
        True,
        digest=digest,
        created=image.created or None,
        env=image.env,
        labels=image.labels,
        method="pull",
    )
