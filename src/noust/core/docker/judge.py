# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Judging an action on a Docker object: (owner, action, object).

This is the chokepoint of rule 4 for Docker. A permission check in the caller is a check with as many
holes as there are callers, and the notices of 2026 against the tools that manage Docker are the
holes: a member who may open a shell in their own containers may open one in anyone's because nothing
tied the id to its owner first. The gate resolves the object to its owner
(:mod:`noust.core.docker.owners`) and asks this module, and no action reaches Docker without passing
it.

The rules, in the order they are applied.

1. **What a person may not see does not exist.** A principal that sees only some teams is answered
   as if an object that is not its teams' were missing: the same error, the same words, never a 403
   that says it is there. What nobody made (and a database engine's, and Noust's own) is the
   instance's: only who administers the server sees it.
2. **What belongs to an application is changed through the application.** Stopping the container of
   a stack from under its health gate, removing the image it keeps to go back to, deleting the volume
   its data is in: each is a way to break an application that Noust has the proper door for, and the
   refusal says which (a command that exists).
3. **What belongs to a database engine or to Noust itself** is refused the same way, with its own door.
4. **What nobody made is done by who administers the server**, with two things Docker itself makes
   left alone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from noust.core.docker.errors import DockerNotFoundError, DockerOwnedError
from noust.core.docker.models import Action, ObjectKind, Owner, OwnerKind

__all__ = ["Can", "can", "judge", "not_found", "refusal", "visible"]


def visible(owner: Owner, *, restricted: bool, sees_app: Callable[[str], bool]) -> bool:
    """
    Tell whether a principal may know an object exists.

    Args:
        owner: The object's owner.
        restricted: The principal sees only some teams. Whoever sees every team (the instance,
            an administrator) sees everything on the machine.
        sees_app: Whether the principal sees the application with this domain.

    Returns:
        True for everything when the principal is not restricted; for a restricted one, only the
        objects of an application it sees.
    """
    if not restricted:
        return True
    return owner.kind is OwnerKind.APP and sees_app(owner.subject)


def not_found(kind: ObjectKind, ref: str) -> DockerNotFoundError:
    """
    Make the answer for an object that is missing or that a principal may not see.

    One answer for both, so that what cannot be seen cannot be told apart from what is not there.

    Args:
        kind: What was asked for.
        ref: How it was named.

    Returns:
        The error.
    """
    return DockerNotFoundError(
        f"No {kind.value} {ref!r} on this server",
        details=f"List the {kind.value}s to see their names.",
    )


def _through_the_application(domain: str, action: Action, kind: ObjectKind) -> str:
    """
    Args:
        domain: The application.
        action: What was asked.
        kind: Of what.

    Returns:
        The command, written as a sentence, that does it through the application.
    """
    if kind is ObjectKind.CONTAINER:
        if action is Action.REMOVE:
            return (
                f"A container of an application is replaced by its deploys: noust update {domain}. "
                f"To remove it for good, remove the application: noust delete {domain}."
            )
        return f"Do it to the whole application: noust {action.value} {domain}."
    if kind is ObjectKind.IMAGE:
        return (
            "The images it keeps are the versions it can go back to. List them with: "
            f"noust releases list {domain}."
        )
    if kind is ObjectKind.VOLUME:
        return (
            "Its volumes hold its data. Remove them with the application, which says what it "
            f"takes: noust delete {domain} --remove-volumes."
        )
    return f"Its networks go with the application: noust delete {domain}."


def _through_the_engine(name: str, action: Action, kind: ObjectKind) -> str:
    """
    Args:
        name: The database instance.
        action: What was asked.
        kind: Of what.

    Returns:
        The command, written as a sentence, that does it through the instance.
    """
    if kind is ObjectKind.CONTAINER and action is Action.START:
        return f"Start the instance: noust db instance start {name}."
    if kind is ObjectKind.CONTAINER and action in (Action.STOP, Action.RESTART):
        return (
            f"Stop the instance with: noust db instance stop {name}. "
            f"Start it again with: noust db instance start {name}."
        )
    return (
        "The instance's container, volumes, network and image go with it, and the command says "
        f"what that takes: noust db instance remove {name}."
    )


def refusal(
    owner: Owner, action: Action, kind: ObjectKind, name: str, *, builtin: bool = False
) -> DockerOwnedError | None:
    """
    Say whether the owner of an object lets an action through, and if not, which door to use.

    The rules of the module docstring that are about whose the object is (the second to the fourth);
    who may see it at all is :func:`visible`, which :func:`judge` and :func:`can` apply first.

    Args:
        owner: Whose the object is.
        action: What is asked.
        kind: Of which kind of object.
        name: How the object is called, for the words of the refusal.
        builtin: The object is one Docker makes itself (``bridge``, ``host`` and ``none``).

    Returns:
        The refusal, not raised; None when the action is the instance's to do.
    """
    if owner.kind is OwnerKind.APP:
        return DockerOwnedError(
            f"The {kind.value} {name} belongs to {owner.subject}",
            _through_the_application(owner.subject, action, kind),
            owner_kind=owner.kind.value,
            subject=owner.subject,
        )
    if owner.kind is OwnerKind.DATABASE:
        return DockerOwnedError(
            f"The {kind.value} {name} belongs to the database instance {owner.subject}",
            _through_the_engine(owner.subject, action, kind),
            owner_kind=owner.kind.value,
            subject=owner.subject,
        )
    if owner.kind is OwnerKind.NOUST:
        if owner.subject == "central":
            return DockerOwnedError(
                f"{name} is the container Noust runs in: changing it stops Noust",
                "Change it where Noust was started (its compose file or the NAS's own Docker "
                "screen), not from inside Noust.",
                owner_kind=owner.kind.value,
                subject=owner.subject,
            )
        return DockerOwnedError(
            f"{name} is something Noust started for a copy, and it ends with the copy",
            "Leave it: it is removed when the copy finishes. If a copy was interrupted, "
            "the next one cleans up.",
            owner_kind=owner.kind.value,
            subject=owner.subject,
        )
    if builtin:
        return DockerOwnedError(
            f"The network {name} is Docker's own and cannot be removed",
            "Docker makes it itself and every container without a network of its own is on it.",
            owner_kind=OwnerKind.NOUST.value,
            subject="docker",
        )
    return None


def judge(
    owner: Owner,
    action: Action,
    kind: ObjectKind,
    name: str,
    *,
    restricted: bool,
    sees_app: Callable[[str], bool],
    builtin: bool = False,
) -> None:
    """
    Allow an action on an object, or refuse it with the door to go through.

    Args:
        owner: Whose the object is.
        action: What is asked.
        kind: Of which kind of object.
        name: How the object is called, for the words of the refusal.
        restricted: The principal sees only some teams.
        sees_app: Whether the principal sees the application with a domain.
        builtin: The object is one Docker makes itself (``bridge``, ``host`` and ``none``).

    Raises:
        DockerNotFoundError: The principal may not see it: answered as if it did not exist.
        DockerOwnedError: It belongs to an application, a database engine or Noust, which changes
            it; nothing was changed, and ``details`` is the command that does.
    """
    if not visible(owner, restricted=restricted, sees_app=sees_app):
        raise not_found(kind, name)
    refused = refusal(owner, action, kind, name, builtin=builtin)
    if refused is not None:
        raise refused


@dataclass(frozen=True)
class Can:
    """
    What a row of the console may offer for an object: the verdict of :func:`judge`, before asking it.

    Attributes:
        start: A container may be started.
        stop: A container may be stopped.
        restart: A container may be restarted.
        remove: The object may be removed.
        via: When the owner is somebody else's, the sentence that says where it is changed (a
            command that exists); empty when the instance does it, or when the principal may not
            even know the object exists.
    """

    start: bool = False
    stop: bool = False
    restart: bool = False
    remove: bool = False
    via: str = ""

    @classmethod
    def none(cls) -> Can:
        """
        Returns:
            Nothing allowed and nothing to say: what a principal without the permission to change
            Docker is offered.
        """
        return cls()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            ``start``, ``stop``, ``restart``, ``remove`` and ``via``.
        """
        return {
            "start": self.start,
            "stop": self.stop,
            "restart": self.restart,
            "remove": self.remove,
            "via": self.via,
        }


def can(
    owner: Owner,
    kind: ObjectKind,
    name: str,
    *,
    restricted: bool,
    sees_app: Callable[[str], bool],
    builtin: bool = False,
) -> Can:
    """
    Ask :func:`judge` for every action an object has, without raising.

    The console offers a button for what is in the answer and nothing else, so a row never invites a
    click that ends in a refusal. It is the same judgement an action goes through, asked once for
    each action, so what is offered and what is let through cannot differ.

    Args:
        owner: Whose the object is.
        kind: Of which kind of object.
        name: How the object is called.
        restricted: The principal sees only some teams.
        sees_app: Whether the principal sees the application with a domain.
        builtin: The object is one Docker makes itself.

    Returns:
        What may be done to it. Only a container is started, stopped or restarted.
    """
    if not visible(owner, restricted=restricted, sees_app=sees_app):
        return Can.none()
    actions = (
        (Action.START, Action.STOP, Action.RESTART, Action.REMOVE)
        if kind is ObjectKind.CONTAINER
        else (Action.REMOVE,)
    )
    verdicts = {action: refusal(owner, action, kind, name, builtin=builtin) for action in actions}
    # One sentence for the object: the door of the verb that fits whether it runs or not (a restart
    # of a container, the removal of anything else).
    said = verdicts.get(Action.RESTART) or verdicts[Action.REMOVE]
    door = said.details if said is not None else ""
    return Can(
        start=Action.START in verdicts and verdicts[Action.START] is None,
        stop=Action.STOP in verdicts and verdicts[Action.STOP] is None,
        restart=Action.RESTART in verdicts and verdicts[Action.RESTART] is None,
        remove=verdicts[Action.REMOVE] is None,
        via=door,
    )
