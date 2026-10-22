# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Is there a new image behind the tag an application runs? (checklist A-14).

An application deployed from a registry image is updated by pulling its tag again, which is
cheap and changes nothing when the registry has the same image, and a rolling release of every
container when it does: the container is recreated and has to pass the health gate. What an
operator who wants it to follow its tag needs is the question asked first, so the update happens
only when the answer is yes. This is that question, with one definition of the answer for the
three ways it is asked: a schedule (``noust deploy schedule``, which runs it before it updates),
a registry's webhook (:mod:`noust.web.api.registry_hooks`) and ``noust app container check``.

The question is answered by pulling the tag and reading the digest of what came: a tag the host
has costs one request, a tag that moved is what the update needs anyway, and the registry's
account (the application's own, or the ones attached to it) is used exactly as an update uses it.
An image pinned by digest never changes, so it is never new.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.core.container_store import ContainerSettings, ContainerStore
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import CommandRunner
from noust.core.secrets import SecretStore
from noust.core.store import AppType, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.helpers.container_image import ImageBuilder
from noust.validators.container import METHOD_IMAGE, split_reference

__all__ = ["ImageCheck", "check_image"]


@dataclass(frozen=True)
class ImageCheck:
    """
    What looking at an image's tag found.

    Attributes:
        domain: The application.
        reference: The image it runs, as recorded.
        deployed: The digest the last deployment resolved; None when none is recorded.
        current: The digest the tag is now; None when the registry has none to give or the tag
            is pinned.
        changed: Whether an update would run something new.
        reason: One sentence saying why, for the schedule's record, the hook's answer and the CLI.
    """

    domain: str
    reference: str
    deployed: str | None
    current: str | None
    changed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "domain": self.domain,
            "image": self.reference,
            "deployed": self.deployed,
            "current": self.current,
            "changed": self.changed,
            "reason": self.reason,
        }


def check_image(
    domain: str,
    *,
    store: NoustStore | None = None,
    runner: CommandRunner | None = None,
    secrets: SecretStore | None = None,
    logger: Logger | None = None,
) -> ImageCheck:
    """
    Look at the tag of an application's image and say whether it moved.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.
        runner: Runs docker; the process-wide one when None.
        secrets: The secret store, for a registry account's password.
        logger: Where the pull is reported.

    Returns:
        What was found.

    Raises:
        NoustError: The application is unknown.
        ValidationError: It is not an application that runs a registry image.
        DockerError: The registry could not be asked (the account is refused, it does not
            answer, the tag does not exist).
    """
    target = store or get_store()
    app = target.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    settings = (
        ContainerStore(target).get(domain) if app.app_type == AppType.CONTAINER.value else None
    )
    if settings is None or settings.method != METHOD_IMAGE or not settings.image:
        raise ValidationError(
            f"{domain} does not run an image from a registry",
            details="Only an application deployed with --image has a tag to follow; the others "
            "are built from a repository, which 'noust update' follows.",
            field="domain",
        )
    return _look(domain, settings, target, runner, secrets, logger)


def _look(
    domain: str,
    settings: ContainerSettings,
    store: NoustStore,
    runner: CommandRunner | None,
    secrets: SecretStore | None,
    logger: Logger | None,
) -> ImageCheck:
    """
    Pull the tag and compare its digest with the deployed one.

    Args:
        domain: The application.
        settings: Its container settings.
        store: The store.
        runner: Runs docker.
        secrets: The secret store.
        logger: Where the pull is reported.

    Returns:
        What was found.
    """
    reference = settings.image or ""
    _, _, _, pinned = split_reference(reference)
    if pinned is not None:
        return ImageCheck(
            domain, reference, settings.digest, pinned, False, "the image is pinned by digest"
        )
    app_name = domain_to_app_name(domain)
    builder = ImageBuilder(
        runner=runner,
        logger=logger or Logger(),
        app_name=app_name,
        domain=domain,
        app_path=Path(store.get_app(domain).app_path or "."),  # type: ignore[union-attr]
        secrets=secrets or SecretStore(root=store.db_path.parent / "secrets"),
        work_dir=store.db_path.parent / "container" / app_name,
    )
    current = builder.latest_digest(settings)
    if current is None:
        return ImageCheck(
            domain, reference, settings.digest, None, True,
            "the registry gave no digest to compare, so it is taken as new",
        )  # fmt: skip
    if settings.digest is None:
        return ImageCheck(
            domain,
            reference,
            None,
            current,
            True,
            f"the deployed digest is not known; {current} is",
        )
    if current == settings.digest:
        return ImageCheck(
            domain, reference, settings.digest, current, False, f"{current} is already running"
        )
    return ImageCheck(
        domain,
        reference,
        settings.digest,
        current,
        True,
        f"{reference} is now {current}, and {settings.digest} runs",
    )
