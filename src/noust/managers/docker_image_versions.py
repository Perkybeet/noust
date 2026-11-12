# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Looking at the registries for a new version of every image a container runs.

Until now Noust could say whether the image of an application deployed from a registry had moved
(:mod:`noust.managers.image_updates`). The Docker zone asks it of everything that runs, whoever made
it: for each image a container uses, the digest the registry has behind the tag now is compared with the
digest the running image came with, and when they differ the new image is read for what version it is
and what that is to the one that runs (:mod:`noust.core.docker.versions`).

Four properties decide what this is allowed to do.

- **The operator asks.** A look is the console's "look for new versions" or ``noust docker images check``,
  never something that happens by itself, and what the summary says is what was found then.
- **It is the question ``docker pull`` asks.** Nothing is sent that a pull would not send. The manifest
  is read without downloading anything; a Docker that cannot do that says so, and downloads only when it
  is told to (``pull``).
- **An account is used for its registry only.** The accounts Noust keeps are logged in, in a private
  directory removed afterwards, for the registry they are for.
- **It is read-only about what runs.** Nothing here changes a container, an image's name or a tag unless a
  pull was asked for. What to do about a new version is :mod:`noust.managers.docker_updates`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from noust.core import timeutil
from noust.core.docker import image_sources, registry
from noust.core.docker.gate import DockerGate
from noust.core.docker.models import Image
from noust.core.docker.versions import (
    ImageRef,
    classify,
    parse_reference,
    registry_aliases,
    tag_shape,
    version_of_image,
)
from noust.core.docker_image_checks import ImageVersionStore, VersionRecord
from noust.core.exceptions import DeploymentError, DockerError
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import NoustStore, get_store
from noust.managers.registry_logins import RegistryManager
from noust.managers.stack_databases import image_engine

__all__ = ["ImageVersions", "Skipped", "VersionCheck"]

_DATABASE_WORDS = (
    "its new version could not be read, and a database's is treated as a major one: "
    "a data directory is not read by another major version"
)


@dataclass(frozen=True)
class Skipped:
    """
    An image that has no registry to ask.

    Attributes:
        reference: The image as the container runs it.
        why: ``built_here`` (it came from no registry: Noust or the operator built it) or ``pinned`` (a
            digest decides which image it is, so it never moves).
    """

    reference: str
    why: str

    def to_dict(self) -> dict[str, str]:
        """
        Returns:
            ``reference`` and ``why``.
        """
        return {"reference": self.reference, "why": self.why}


@dataclass
class VersionCheck:
    """
    What a look found.

    Attributes:
        records: One for each image that was asked about, by reference.
        skipped: The images that were not asked about, and why.
        checked_at: When, ISO 8601 UTC.
    """

    records: list[VersionRecord] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The records and the skipped, JSON-ready.
        """
        return {
            "checked_at": self.checked_at,
            "records": [
                {
                    "reference": item.reference,
                    "state": item.state,
                    "change": item.change,
                    "floating": item.floating,
                    "current_version": item.current_version,
                    "latest_version": item.latest_version,
                    "latest_created": item.latest_created,
                    "method": item.method,
                    "detail": item.detail,
                }
                for item in self.records
            ],
            "skipped": [item.to_dict() for item in self.skipped],
        }


def _text(version: tuple[int, ...] | None) -> str | None:
    """
    Args:
        version: A version's numbers.

    Returns:
        ``7.4.2``; None for none.
    """
    return ".".join(str(part) for part in version) if version else None


class ImageVersions:
    """
    Look at the registries for the images the containers of this machine run.

    Args:
        gate: The gate, which lists the containers and the images with their owners.
        store: The store the findings are kept in; the process-wide one when None.
        runner: Runs docker; the gate's when None.
        registries: The registry accounts Noust keeps; built over the runner when None.
        clock: The time as ISO 8601 UTC; the machine's when None.
    """

    def __init__(
        self,
        gate: DockerGate,
        *,
        store: NoustStore | None = None,
        runner: CommandRunner | None = None,
        registries: RegistryManager | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._gate = gate
        self._store = store
        self._runner = runner
        self._registries = registries
        self._clock = clock or timeutil.utc_now_iso

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def runner(self) -> CommandRunner:
        """The runner docker goes through."""
        return self._runner or get_runner()

    @property
    def registries(self) -> RegistryManager:
        """The registry accounts Noust keeps."""
        if self._registries is None:
            self._registries = RegistryManager(runner=self.runner)
        return self._registries

    def records(self) -> dict[str, VersionRecord]:
        """
        Returns:
            What the last look found of the images a container runs, by reference, as of now (a version it
            called new that already runs is current). Nothing is asked of a registry.

        Raises:
            DockerUnavailableError: Docker cannot be asked.
        """
        return self._gate.version_records()

    @contextmanager
    def _session(self, host: str) -> Iterator[dict[str, str] | None]:
        """
        Log in the accounts Noust keeps for one registry, for the length of a block.

        Args:
            host: The registry's host as a reference names it.

        Yields:
            The environment the commands are run with: ``DOCKER_CONFIG`` of a private directory that
            holds the logins and is removed afterwards, or None when there is no account for it.

        Raises:
            DeploymentError: A password is missing from the secret store.
            DockerError: The registry did not accept the account.
        """
        names = [
            login.name
            for login in self.registries.accounts.list()
            if login.server in registry_aliases(host)
        ]
        with self.registries.session_for("the Docker zone", names) as env:
            yield env or None

    def look(self, reference: ImageRef) -> registry.RemoteImage:
        """
        Ask a registry whether it has an image, with the account Noust keeps for it.

        Nothing is downloaded. A registry whose account cannot be used is answered as one that refused.

        Args:
            reference: The image, read.

        Returns:
            What the registry has behind the reference, or why that could not be read.
        """
        try:
            with self._session(reference.registry) as env:
                return registry.look_at_registry(self.runner, reference, env=env)
        except (DockerError, DeploymentError) as exc:
            return registry.RemoteImage(False, why="denied", detail=exc.message)

    def check(
        self,
        *,
        pull: bool = False,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> VersionCheck:
        """
        Ask the registries about every image a container runs, and keep what they said.

        Args:
            pull: Download what moved to compare, which is what a Docker without buildx needs; without
                it a Docker that cannot read a manifest answers ``unknown`` and says why.
            on_progress: Told ``(done, total, reference)`` as each image is looked at.

        Returns:
            What was found, and what has no registry to ask.

        Raises:
            DockerUnavailableError: Docker cannot be asked about the containers.
        """
        containers = list(self._gate.containers().items)
        images = list(self._gate.images().items)
        references = image_sources.registry_references(containers, self.store)
        run = VersionCheck(checked_at=self._clock())
        image_of: dict[str, Image] = {name: image for image in images for name in image.used_by}

        # One question per image, however many containers run it: the image of the first that does.
        subjects: dict[str, tuple[ImageRef, Image | None]] = {}
        for container in containers:
            reference = references.get(container.name)
            parsed = parse_reference(reference) if reference else None
            if reference is None or parsed is None or reference in subjects:
                continue
            subjects[reference] = (parsed, image_of.get(container.name))
        for reference in [ref for ref, (parsed, _) in subjects.items() if parsed.pinned]:
            run.skipped.append(Skipped(reference, "pinned"))
            del subjects[reference]

        local = registry.read_local_images(
            self.runner, [image.id for _, image in subjects.values() if image is not None]
        )
        askable: dict[str, tuple[ImageRef, registry.LocalImage]] = {}
        for reference, (parsed, image) in sorted(subjects.items()):
            seen = local.get(image.id) if image is not None else None
            if seen is None or seen.digest_for(parsed) is None:
                run.skipped.append(Skipped(reference, "built_here"))
            else:
                askable[reference] = (parsed, seen)

        done = 0
        for host, group in self._by_registry(askable):
            try:
                with self._session(host) as env:
                    for reference in group:
                        parsed, seen = askable[reference]
                        run.records.append(self._look(reference, parsed, seen, env, pull, run))
                        done += 1
                        if on_progress is not None:
                            on_progress(done, len(askable), reference)
            except (DockerError, DeploymentError) as exc:
                # The account of this registry could not be used: what was not asked is unknown, with
                # the reason, and the registries after it are still asked.
                for reference in group:
                    if all(item.reference != reference for item in run.records):
                        parsed, seen = askable[reference]
                        run.records.append(self._unknown(reference, parsed, seen, exc.message, run))
        versions = ImageVersionStore(self.store)
        for record in run.records:
            versions.save(record)
        versions.keep_only(set(references.values()))
        run.records.sort(key=lambda item: item.reference)
        return run

    @staticmethod
    def _by_registry(
        askable: dict[str, tuple[ImageRef, registry.LocalImage]],
    ) -> list[tuple[str, list[str]]]:
        """
        Args:
            askable: The images to ask about, by reference.

        Returns:
            Their references grouped by registry, so that an account is logged in once for all of them.
        """
        groups: dict[str, list[str]] = {}
        for reference, (parsed, _) in askable.items():
            groups.setdefault(parsed.registry, []).append(reference)
        return sorted(groups.items())

    @staticmethod
    def _unknown(
        reference: str,
        parsed: ImageRef,
        seen: registry.LocalImage,
        detail: str,
        run: VersionCheck,
    ) -> VersionRecord:
        """
        Args:
            reference: The image as the container runs it.
            parsed: The same, read.
            seen: What the running image says of itself.
            detail: Why it could not be told.
            run: The look it belongs to.

        Returns:
            A record that says it could not be told and why.
        """
        current = version_of_image(parsed.repository, tag="", labels=seen.labels, env=seen.env)
        return VersionRecord(
            reference=reference,
            state="unknown",
            local_digest=seen.digest_for(parsed),
            remote_digest=None,
            current_version=_text(current),
            latest_version=None,
            latest_created=None,
            change="",
            floating=tag_shape(parsed.tag).floating,
            method="",
            detail=detail,
            checked_at=run.checked_at,
        )

    def _look(
        self,
        reference: str,
        parsed: ImageRef,
        seen: registry.LocalImage,
        env: dict[str, str] | None,
        pull: bool,
        run: VersionCheck,
    ) -> VersionRecord:
        """
        Ask the registry about one image and say what is behind its tag.

        Args:
            reference: The image as the container runs it.
            parsed: The same, read.
            seen: What the running image says of itself.
            env: The environment of the commands (the registry account's session).
            pull: Download to compare.
            run: The look it belongs to.

        Returns:
            What was found.
        """
        remote = registry.look_at_registry(
            self.runner, parsed, env=env, platform=seen.platform or "linux/amd64", pull=pull
        )
        if not remote.ok:
            return self._unknown(reference, parsed, seen, remote.detail, run)
        shape = tag_shape(parsed.tag)
        # The image's own words only: the tag says which line it follows, not which version it is.
        current = version_of_image(parsed.repository, tag="", labels=seen.labels, env=seen.env)
        local_digest = seen.digest_for(parsed)
        if remote.digest == local_digest:
            return VersionRecord(
                reference=reference,
                state="current",
                local_digest=local_digest,
                remote_digest=remote.digest,
                current_version=_text(current),
                latest_version=_text(current),
                latest_created=remote.created,
                change="",
                floating=shape.floating,
                method=remote.method,
                detail="",
                checked_at=run.checked_at,
            )
        latest = version_of_image(parsed.repository, tag="", labels=remote.labels, env=remote.env)
        change = classify(current, latest, shape)
        detail = ""
        if change == "unknown" and image_engine(reference) is not None:
            change, detail = "major", _DATABASE_WORDS
        return VersionRecord(
            reference=reference,
            state="newer",
            local_digest=local_digest,
            remote_digest=remote.digest,
            current_version=_text(current),
            latest_version=_text(latest),
            latest_created=remote.created,
            change=change,
            floating=shape.floating,
            method=remote.method,
            detail=detail,
            checked_at=run.checked_at,
        )
