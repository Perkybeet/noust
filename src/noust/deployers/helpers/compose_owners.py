# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whose each thing is that a compose file names, judged against what Noust registered.

A compose file can name what it did not make: the project it runs as (``name:``), a volume or a
network that already exists (``external``), a container's name (``container_name``) and the image
another application built (``image: <project>-<service>``). Any of them can be another
application's, or Noust's own, and Compose does not ask: with the same project name it recreates
the other stack's services and, with ``--remove-orphans``, removes the rest; with its volume it
reads the other stack's data.

The store is the authority (:meth:`noust.core.docker.owners.OwnerIndex.of_reference`), as it is
for every other Docker object. What belongs to another application or to Noust is refused with no
exception that can allow it (it is not this application's to take); what belongs to nobody (a
volume an operator made by hand) is something the stack is given from outside, refused for a new
stack and warned about for one that already had it, like everything else the guard weighs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from noust.core.compose_yaml import load_compose
from noust.core.docker import names
from noust.core.docker.models import OwnerKind
from noust.core.docker.owners import OwnerIndex, registry_from_store
from noust.core.server_text import say
from noust.deployers.helpers.compose_host import enabled

if TYPE_CHECKING:
    from noust.core.store import NoustStore
    from noust.deployers.helpers.compose_guard import ComposeFindings, GuardContext


@dataclass(frozen=True)
class Owners:
    """
    Whose Docker objects are, for one application.

    Attributes:
        index: What Noust registered, asked by name.
        domain: The application the file belongs to: what is its own is nobody else's finding.
    """

    index: OwnerIndex
    domain: str

    @classmethod
    def of_store(cls, store: NoustStore, domain: str) -> Owners:
        """
        Args:
            store: The store, the instance's: the owner of an object is a fact, and who may see it
                is decided elsewhere.
            domain: The application.

        Returns:
            The owners as the store has them.
        """
        return cls(OwnerIndex(registry_from_store(store)), domain)

    def mine(self, kind: str, name: str) -> bool:
        """
        Args:
            kind: ``project``, ``volume``, ``network``, ``container`` or ``image``.
            name: The name as the file spells it.

        Returns:
            Whether the name is this application's own: the volume its own project made.
        """
        owner = self.index.of_reference(kind, name)
        return owner.kind is OwnerKind.APP and owner.subject == self.domain

    def other(self, kind: str, name: str) -> str | None:
        """
        Args:
            kind: ``project``, ``volume``, ``network``, ``container`` or ``image``.
            name: The name as the file spells it.

        Returns:
            Whose it is when it is not this application's (the other application's domain, or
            Noust); None when it is this application's own or nobody's.
        """
        owner = self.index.of_reference(kind, name)
        if owner.kind is OwnerKind.NOBODY or (
            owner.kind is OwnerKind.APP and owner.subject == self.domain
        ):
            return None
        return owner.subject if owner.kind is OwnerKind.APP else "Noust"


def own_project_names(document: Mapping[str, Any], env_project: str) -> list[str]:
    """
    List the project names a stack gives itself.

    Args:
        document: The compose file.
        env_project: ``COMPOSE_PROJECT_NAME`` as the project's ``.env`` sets it, empty for none.

    Returns:
        The names as Compose would take them, once each. A name made of variables is not one
        that can be read: ``-p`` takes the application's own and overrides it.
    """
    found: list[str] = []
    declared = document.get("name")
    for value in (declared if isinstance(declared, str) else "", env_project):
        if value.strip() and "$" not in value:
            name = names.project_of_directory(value.strip())
            if name not in found:
                found.append(name)
    return found


def declared_project(compose_path: Path) -> str | None:
    """
    Read the project a stack names itself: ``name:``, or ``COMPOSE_PROJECT_NAME`` in its ``.env``.

    Every command Noust runs passes ``-p``, so a stack that names its own project keeps the name it
    always had (renaming it would give it new, empty volumes) and no ``name:`` can claim another
    application's project without the guard seeing it.

    Args:
        compose_path: The compose file in use.

    Returns:
        The name as Compose would take it; None when the file names none, cannot be read, or
        names one made of variables (which only the model Compose resolves can read).
    """
    from noust.deployers.docker_compose import _env_project_name

    try:
        document = load_compose(compose_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    found = own_project_names(
        document if isinstance(document, Mapping) else {}, _env_project_name(compose_path) or ""
    )
    return found[0] if found else None


def judge_project(
    findings: ComposeFindings, document: Mapping[str, Any], context: GuardContext
) -> None:
    """
    Judge the project a stack runs as: it is the application's, and nobody else's.

    Args:
        findings: Where the findings go.
        document: The compose file.
        context: What the guard knows of the application.
    """
    if not context.project:
        return
    own = own_project_names(document, context.env_project)
    for name in own:
        if name != context.project:
            findings.add(
                ("compose file", "project", name),
                say("compose_guard.project_names", name=name, project=context.project),
                refused=True,
            )
    if context.owners is None:
        return
    for name in dict.fromkeys([context.project, *own]):
        owner = context.owners.other("project", name)
        if owner is not None:
            findings.add(
                ("compose file", "project_taken", name),
                say("compose_guard.project_taken", name=name, owner=owner),
                refused=True,
                hard=True,
            )


def _external_name(key: str, entry: Mapping[str, Any]) -> str | None:
    """
    Args:
        key: The name a volume or a network has in the file.
        entry: Its definition.

    Returns:
        The name it has in Docker when the stack does not make it (``external``, in either of its
        spellings); None when the stack makes it.
    """
    external = entry.get("external")
    if not (enabled(external) or isinstance(external, Mapping)):
        return None
    legacy = external.get("name") if isinstance(external, Mapping) else None
    return str(entry.get("name") or legacy or key)


def judge_externals(
    findings: ComposeFindings, document: Mapping[str, Any], context: GuardContext
) -> None:
    """
    Judge the volumes and the networks the file does not make.

    Args:
        findings: Where the findings go.
        document: The compose file.
        context: What the guard knows of the application.
    """
    for section, kind in (("volumes", "volume"), ("networks", "network")):
        entries = document.get(section)
        if not isinstance(entries, Mapping):
            continue
        for key, entry in entries.items():
            if not isinstance(entry, Mapping):
                continue
            name = _external_name(str(key), entry)
            if name is None:
                continue
            who = f"{kind} {key}"
            owner = context.owners.other(kind, name) if context.owners is not None else None
            if owner is not None:
                taken = (
                    say("compose_guard.volume_taken", who=who, name=name, owner=owner)
                    if kind == "volume"
                    else say("compose_guard.network_taken", who=who, name=name, owner=owner)
                )
                findings.add((who, f"{kind}_taken", name), taken, refused=True, hard=True)
            elif not (
                context.project
                and (name == context.project or name.startswith(f"{context.project}_"))
            ) and (context.owners is None or not context.owners.mine(kind, name)):
                outside = (
                    say("compose_guard.external_volume", who=who, name=name)
                    if kind == "volume"
                    else say("compose_guard.external_network", who=who, name=name)
                )
                findings.add((who, f"external_{kind}", name), outside, refused=True)


def judge_service(
    findings: ComposeFindings, who: str, service: Mapping[str, Any], context: GuardContext
) -> None:
    """
    Judge the names a service claims: its container's and its image's.

    Args:
        findings: Where the findings go.
        who: ``service web`` or ``job migrate``.
        service: Its entry.
        context: What the guard knows of the application.
    """
    if context.owners is None:
        return
    container = service.get("container_name")
    if isinstance(container, str) and container and "$" not in container:
        owner = context.owners.other("container", container)
        if owner is not None:
            findings.add(
                (who, "container_taken", container),
                say("compose_guard.container_taken", who=who, name=container, owner=owner),
                refused=True,
                hard=True,
            )
    image = service.get("image")
    if isinstance(image, str) and image and "$" not in image:
        owner = context.owners.other("image", image)
        if owner is not None:
            findings.add(
                (who, "image_taken", image),
                say("compose_guard.image_taken", who=who, name=image, owner=owner),
                refused=True,
                hard=True,
            )
