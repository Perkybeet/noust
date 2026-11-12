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

from noust.core.docker.errors import DockerNotFoundError, DockerOwnedError
from noust.core.docker.models import Action, ObjectKind, Owner, OwnerKind

__all__ = ["judge", "not_found", "visible"]


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
    if owner.kind is OwnerKind.APP:
        raise DockerOwnedError(
            f"The {kind.value} {name} belongs to {owner.subject}",
            _through_the_application(owner.subject, action, kind),
            owner_kind=owner.kind.value,
            subject=owner.subject,
        )
    if owner.kind is OwnerKind.DATABASE:
        raise DockerOwnedError(
            f"The {kind.value} {name} belongs to the database instance {owner.subject}",
            _through_the_engine(owner.subject, action, kind),
            owner_kind=owner.kind.value,
            subject=owner.subject,
        )
    if owner.kind is OwnerKind.NOUST:
        if owner.subject == "central":
            raise DockerOwnedError(
                f"{name} is the container Noust runs in: changing it stops Noust",
                "Change it where Noust was started (its compose file or the NAS's own Docker "
                "screen), not from inside Noust.",
                owner_kind=owner.kind.value,
                subject=owner.subject,
            )
        raise DockerOwnedError(
            f"{name} is something Noust started for a copy, and it ends with the copy",
            "Leave it: it is removed when the copy finishes. If a copy was interrupted, "
            "the next one cleans up.",
            owner_kind=owner.kind.value,
            subject=owner.subject,
        )
    if builtin:
        raise DockerOwnedError(
            f"The network {name} is Docker's own and cannot be removed",
            "Docker makes it itself and every container without a network of its own is on it.",
            owner_kind=OwnerKind.NOUST.value,
            subject="docker",
        )
