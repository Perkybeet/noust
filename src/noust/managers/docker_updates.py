# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What updating an image does, and what it refuses to do, decided by whose the image is.

A new version of an image is not one thing to do. Where Noust made the container it is the application's
update, which has a health gate and a way back (``lifecycle.update_app``); where Noust did not, there is no
way back to promise, and what is offered instead is to bring the stack to Noust first, which changes
nothing of what runs and gives it one. A container on its own is not updated blind, and says why. This
module is the one place that says which it is (rule 4: the owner decides, in one place), and the console,
the command line and the jobs ask it.

Two rules are held here and nowhere else.

- **A major version is a decision.** Taking one is never the effect of a tag moving. It needs the operator
  to say so, and a database engine's also needs a copy of the application's databases that was proven to
  restore (a restore drill that loaded a database, within the last day): the data directory of one major
  version is not read by the next. The rule looks at everything that moves with the update and not at the
  image the person clicked, because an application's update pulls every image it has: a web service's
  update cannot be the way a database changes major.
- **What moves is said before it moves.** The plan lists the images that will change with the update, the
  steps in the order they happen, and what is missing when it cannot go ahead.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from noust.core.docker import image_sources
from noust.core.docker.errors import DockerBlockedError, DockerOwnedError
from noust.core.docker.gate import DockerGate
from noust.core.docker.models import Container, Image, Owner, OwnerKind
from noust.core.docker_image_checks import VersionRecord
from noust.core.restore_drills import DrillRecords
from noust.core.store import NoustStore, get_store
from noust.managers.docker_image_versions import ImageVersions
from noust.managers.stack_databases import image_engine

__all__ = ["COPY_MAX_AGE", "CopyState", "ImageUpdates", "Prepared", "UpdatePlan"]

#: How old the newest proven copy may be for a database's major version to go ahead: the data it holds is
#: what the update would put at risk, so the copy is of today.
COPY_MAX_AGE = timedelta(hours=24)

#: What an application's update does for one that has databases of its own, in order. The copy of its
#: databases is the one the update takes before every Compose update, and a failed copy stops it.
_STEPS = ("pull", "recreate_behind_health", "keep_previous", "roll_back_on_failure")


@dataclass(frozen=True)
class CopyState:
    """
    Whether there is a copy of an application's databases that was proven to restore.

    Attributes:
        state: ``ok`` (a drill that loaded a database passed within :data:`COPY_MAX_AGE`), ``stale`` (there
            is one, older) or ``missing`` (none ever loaded a database).
        last_ok_at: When the newest such drill ended.
        databases: How many databases it loaded.
        via: The commands that make one.
    """

    state: str
    last_ok_at: str | None
    databases: int
    via: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "state": self.state,
            "last_ok_at": self.last_ok_at,
            "databases": self.databases,
            "via": self.via,
        }


@dataclass(frozen=True)
class UpdatePlan:
    """
    What updating an image would do.

    Attributes:
        image: The image as the listing names it (a tag, or its id).
        reference: The reference a registry is asked about for it; None when it has none.
        mode: ``with_net`` (through the application, behind its health gate, with the way back kept),
            ``bring_first`` (bring its stack to Noust, then update), ``loose`` (a container on its own:
            not updated), ``via_owner`` (a database engine's or Noust's: through its own door),
            ``rollback`` (a version an application keeps to go back to), ``unknown`` (there is nothing to
            update to: nobody looked, or the look could not tell) or ``none`` (it is current).
        state: What the last look found: ``current``, ``newer``, ``unknown`` or ``not_looked``.
        change: What the new version is to the one that runs.
        floating: The tag is one that moves.
        current_version: The version that runs, as far as it is known.
        latest_version: The version behind the tag now.
        latest_created: When it was built.
        checked_at: When it was looked at.
        detail: What the registry said, when the look could not tell.
        owner: Whose the image is.
        app: The application that is updated (``with_net``).
        stack: The Compose project to bring to Noust (``bring_first``).
        images: Every image that moves with the update, by reference.
        steps: What it does, in order (``with_net``).
        major: Something that moves is a new major version.
        needs: What is missing before it can go ahead: ``verified_copy`` or ``accept_major``.
        backup: The copy of the application's databases, when a database's major version moves.
        apply: Whether it can go ahead once the person has said so.
        via: Where it is done instead, as a command, when it is not here.
    """

    image: str
    reference: str | None
    mode: str
    state: str
    owner: Owner
    change: str = ""
    floating: bool = False
    current_version: str | None = None
    latest_version: str | None = None
    latest_created: str | None = None
    checked_at: str | None = None
    detail: str = ""
    app: str | None = None
    stack: str | None = None
    images: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    major: bool = False
    needs: str | None = None
    backup: CopyState | None = None
    apply: bool = False
    via: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "image": self.image,
            "reference": self.reference,
            "mode": self.mode,
            "state": self.state,
            "change": self.change,
            "floating": self.floating,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "latest_created": self.latest_created,
            "checked_at": self.checked_at,
            "detail": self.detail,
            "owner": self.owner.to_dict(),
            "app": self.app,
            "stack": self.stack,
            "images": list(self.images),
            "steps": list(self.steps),
            "major": self.major,
            "needs": self.needs,
            "backup": self.backup.to_dict() if self.backup else None,
            "apply": self.apply,
            "via": self.via,
        }


@dataclass(frozen=True)
class _Machine:
    """
    What one look at the machine found, so that planning many images asks it once.

    Attributes:
        containers: Every container the principal sees, with its owner.
        records: What the last look at the registries found of the images they run.
    """

    containers: list[Container]
    records: dict[str, VersionRecord]


@dataclass(frozen=True)
class Prepared:
    """
    An update that was checked and can be run.

    Attributes:
        domain: The application to update.
        plan: What it will do.
    """

    domain: str
    plan: UpdatePlan


class ImageUpdates:
    """
    Decide what updating an image does.

    Args:
        gate: The gate, which resolves an image to its owner as whoever asks.
        versions: What the last look at the registries found.
        store: The store; the process-wide one when None.
        now: The time; the machine's when None.
    """

    def __init__(
        self,
        gate: DockerGate,
        *,
        versions: ImageVersions,
        store: NoustStore | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._gate = gate
        self._versions = versions
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    # -- the plan --------------------------------------------------------------------------

    def plan_for(self, ref: str) -> UpdatePlan:
        """
        Say what updating an image would do.

        Args:
            ref: One of its names, or its id.

        Returns:
            The plan.

        Raises:
            DockerNotFoundError: There is no such image, or the principal may not see it.
            DockerUnavailableError: Docker cannot be asked.
        """
        return self.plan(self._gate.image(ref))

    def plans(self, images: Sequence[Image]) -> dict[str, UpdatePlan]:
        """
        Say what updating each of several images would do, looking at the machine once.

        Args:
            images: Images the gate listed, with their owners.

        Returns:
            The plan of each, by image id.
        """
        machine = _Machine(list(self._gate.containers().items), self._versions.records())
        return {image.id: self.plan(image, machine) for image in images}

    def plan(self, image: Image, machine: _Machine | None = None) -> UpdatePlan:
        """
        Say what updating an image would do.

        Args:
            image: An image the gate listed, with its owner.
            machine: What the machine looks like now, when a caller plans several images.

        Returns:
            The plan.
        """
        label = image.tags[0] if image.tags else image.id
        owner = image.owner
        if image.way_back:
            return UpdatePlan(label, None, "rollback", "current", owner)
        machine = machine or _Machine(list(self._gate.containers().items), self._versions.records())
        containers = machine.containers
        users = [c for c in containers if c.name in image.used_by]
        references = image_sources.registry_references(users, self.store)
        reference = next((references[c.name] for c in users if c.name in references), None)
        record = machine.records.get(reference) if reference else None
        if owner.kind is OwnerKind.NOUST:
            return self._with_record(
                UpdatePlan(
                    label,
                    reference,
                    "via_owner",
                    "newer",
                    owner,
                    via="Noust and what it starts for itself are updated with Noust: "
                    "noust self-update",
                ),
                record,
            )
        if owner.kind is OwnerKind.DATABASE:
            return self._with_record(
                UpdatePlan(
                    label,
                    reference,
                    "via_owner",
                    "newer",
                    owner,
                    via=f"noust db instance upgrade {owner.subject}",
                ),
                record,
            )
        if reference is None or record is None:
            return UpdatePlan(label, reference, "unknown", "not_looked", owner)
        base = self._with_record(
            UpdatePlan(label, reference, "unknown", record.state, owner), record
        )
        if record.state == "unknown":
            return base
        if record.state == "current":
            return _replaced(base, mode="none")
        if owner.kind is OwnerKind.APP:
            return self._through_the_application(base, owner.subject, machine)
        project = next((c.project for c in users if c.project), "")
        if project:
            return _replaced(
                base,
                mode="bring_first",
                stack=project,
                via=f"noust docker stack bring {project}",
            )
        return _replaced(base, mode="loose")

    def _with_record(self, plan: UpdatePlan, record: VersionRecord | None) -> UpdatePlan:
        """
        Args:
            plan: A plan so far.
            record: What the last look found of its image, when anybody looked.

        Returns:
            The plan with what the look found.
        """
        if record is None:
            return _replaced(plan, state="not_looked")
        return _replaced(
            plan,
            state=record.state,
            change=record.change,
            floating=record.floating,
            current_version=record.current_version,
            latest_version=record.latest_version,
            latest_created=record.latest_created,
            checked_at=record.checked_at,
            detail=record.detail,
        )

    def _through_the_application(
        self, base: UpdatePlan, domain: str, machine: _Machine
    ) -> UpdatePlan:
        """
        Plan the update of an image of an application, with everything that moves with it.

        Args:
            base: The plan so far.
            domain: The application.
            machine: What the machine looks like now.

        Returns:
            The plan.
        """
        records = machine.records
        mine = [
            c
            for c in machine.containers
            if c.owner.kind is OwnerKind.APP and c.owner.subject == domain
        ]
        references = image_sources.registry_references(mine, self.store)
        moving = sorted(
            {ref for ref in references.values() if ref in records and records[ref].state == "newer"}
            | ({base.reference} if base.reference else set())
        )
        majors = [ref for ref in moving if records[ref].change == "major"]
        database_majors = [ref for ref in majors if image_engine(ref) is not None]
        has_database = any(image_engine(c.image) is not None for c in mine)
        steps = [
            _STEPS[0],
            *(["copy_databases"] if has_database else []),
            *_STEPS[1:],
        ]
        copy = self._copy(domain) if database_majors else None
        needs: str | None = None
        if database_majors and copy is not None and copy.state != "ok":
            needs = "verified_copy"
        elif majors:
            needs = "accept_major"
        return _replaced(
            base,
            mode="with_net",
            app=domain,
            images=moving,
            steps=steps,
            major=bool(majors),
            needs=needs,
            backup=copy,
            apply=needs != "verified_copy",
        )

    def _copy(self, domain: str) -> CopyState:
        """
        Find out whether there is a copy of an application's databases that was proven to restore.

        Args:
            domain: The application.

        Returns:
            The state of the newest such copy.
        """
        via = f"noust backup create {domain}, then noust backup drill {domain}"
        good = next(
            (
                report
                for report in DrillRecords(self.store).list(domain, limit=20)
                if report.ok and report.databases_loaded > 0
            ),
            None,
        )
        if good is None:
            return CopyState("missing", None, 0, via)
        try:
            ended = datetime.fromisoformat(good.finished_at)
        except ValueError:
            return CopyState("missing", None, 0, via)
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
        fresh = self._now() - ended <= COPY_MAX_AGE
        return CopyState("ok" if fresh else "stale", good.finished_at, good.databases_loaded, via)

    # -- doing it --------------------------------------------------------------------------

    def prepare(self, ref: str, *, accept_major: bool = False) -> Prepared:
        """
        Check that an image may be updated now, and say which application is.

        Args:
            ref: One of its names, or its id.
            accept_major: The person said they take a new major version.

        Returns:
            The application to update, with the plan.

        Raises:
            DockerNotFoundError: There is no such image, or the principal may not see it.
            DockerOwnedError: A database engine or Noust owns it; the error says which door.
            DockerBlockedError: It cannot be done yet; ``reason`` says what is missing.
            DockerUnavailableError: Docker cannot be asked.
        """
        plan = self.plan_for(ref)
        refusal = _refusal(plan, accept_major)
        if refusal is not None:
            raise refusal
        if plan.app is None:  # with_net always names its application
            raise DockerBlockedError(
                f"{plan.image} has no application to update",
                "Look at the image again.",
                reason="unknown",
            )
        return Prepared(plan.app, plan)


def _replaced(plan: UpdatePlan, **changes: Any) -> UpdatePlan:
    """
    Args:
        plan: A plan.
        **changes: Fields to change.

    Returns:
        The plan with them changed.
    """
    from dataclasses import replace

    return replace(plan, **changes)


def _refusal(plan: UpdatePlan, accept_major: bool) -> DockerOwnedError | DockerBlockedError | None:
    """
    Say why a plan cannot be run.

    Args:
        plan: The plan.
        accept_major: The person said they take a new major version.

    Returns:
        The refusal, not raised; None when the plan can be run.
    """
    name = plan.image
    if plan.mode == "with_net":
        if plan.needs == "verified_copy" and plan.backup is not None:
            return DockerBlockedError(
                f"The update of {name} takes a new major version of a database, and there is no "
                "copy of its data that was proven to restore",
                "A database's data directory is not read by another major version. Make a copy and "
                f"prove it restores, then update: {plan.backup.via}.",
                reason="verified_copy",
                via=plan.backup.via,
            )
        if plan.major and not accept_major:
            return DockerBlockedError(
                f"The update of {name} takes a new major version",
                "A major version is a decision: it can change what the application writes. "
                "Update with the major version accepted if that is what you want.",
                reason="accept_major",
            )
        return None
    if plan.mode == "bring_first":
        return DockerBlockedError(
            f"{name} runs in {plan.stack}, a stack Noust did not create",
            "Noust does not know how it was created, so it cannot promise a way back. Bring the "
            f"stack to Noust first, which changes nothing of what runs: {plan.via}.",
            reason="bring_first",
            via=plan.via,
        )
    if plan.mode == "loose":
        return DockerBlockedError(
            f"{name} is a container Noust did not create; Noust does not know how it was created, "
            "so it is not updated blind",
            "Run it again from the command or the file that made it. A container on its own is not "
            "brought to Noust in this version.",
            reason="loose",
        )
    if plan.mode == "via_owner":
        return DockerOwnedError(
            f"The image {name} belongs to {_owner_words(plan.owner)}",
            f"It is updated there: {plan.via}.",
            owner_kind=plan.owner.kind.value,
            subject=plan.owner.subject,
        )
    if plan.mode == "rollback":
        return DockerBlockedError(
            f"{name} is a version an application keeps to go back to",
            "It is not something to update to; it is what a rollback goes back to.",
            reason="way_back",
        )
    if plan.mode == "none":
        return DockerBlockedError(
            f"{name} is already the version its tag points at",
            "Look again for new versions if you think the registry has one.",
            reason="nothing_new",
        )
    if plan.state == "not_looked":
        return DockerBlockedError(
            f"Nobody looked for a new version of {name}",
            "Look for new versions first: noust docker images check.",
            reason="not_looked",
            via="noust docker images check",
        )
    return DockerBlockedError(
        f"The registry could not say whether {name} has a new version",
        plan.detail or "Look again; the registry did not answer.",
        reason="unknown",
    )


def _owner_words(owner: Owner) -> str:
    """
    Args:
        owner: Whose an image is.

    Returns:
        How a refusal names the owner.
    """
    if owner.kind is OwnerKind.DATABASE:
        return f"the database instance {owner.subject}"
    return "Noust"
