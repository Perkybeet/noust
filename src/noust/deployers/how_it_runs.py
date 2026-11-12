# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How an application runs: directly on the server, or in a container.

Noust has always deployed both, and nobody could tell. ``--type auto`` decided alone by priority, and
in Node.js, Python, PHP, Next.js and Vite a Dockerfile in the repository was ignored, while in Go, Rust,
.NET, Elixir, Deno, Ruby and Java it was how the repository was run. This module makes that a decision a
person can see and take:

- the **ways** a source can run on this server (:func:`decide`): ``server`` (a service of the system,
  with its own account) and ``container`` (a Dockerfile, Railpack, or a Compose file), each ``yes``,
  ``no`` with the sentence for why not and how to fix it, or ``hidden`` when there is nothing to fix:
  the machine can never run that form (one that only has Docker has no systemd to keep a service) or
  nothing in the source runs that way;
- which one is **proposed**, and why. It is the form of the first type the registry recognises, as it
  always was: an application that deployed one way is still proposed that way. Only what the machine
  cannot do moves it;
- the type a way **deploys as** (``Way.app_type``), so a client that chooses a way sends that type, and
  ``noust create --run-as`` resolves to the same one (:meth:`~noust.deployers.auto.AutoDeployer`).

It is the one implementation (rule 3) that ``POST /api/apps/inspect``, ``noust create`` and the console
read. What the machine can do is not decided here: the table of functions
(:mod:`noust.central.features`) says whether this machine runs a native or a container application and
why not.

A running application's form is :func:`runs_of`, from its type and, for a container, how it is built.

Changing the form of an application that runs is not offered: it is deployed again as the other form,
and the domain is moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from noust.central.features import Availability
from noust.core.docker.names import image_tag
from noust.core.exceptions import NoustError
from noust.core.runner import CommandRunner, get_runner
from noust.core.server_text import ServerText, say
from noust.core.store import COMPOSE_BACKED_TYPES, App, AppType
from noust.core.utils import domain_to_app_name
from noust.deployers.compiled import ignoring_container_files
from noust.deployers.helpers.container_settings import describe_container
from noust.deployers.registry import DeployerRegistry
from noust.managers import railpack

if TYPE_CHECKING:
    from noust.core.config import Config

__all__ = [
    "FORMS",
    "FORM_CONTAINER",
    "FORM_SERVER",
    "STATUS_HIDDEN",
    "STATUS_NO",
    "STATUS_YES",
    "VIA_COMPOSE",
    "VIA_DOCKERFILE",
    "VIA_IMAGE",
    "VIA_RAILPACK",
    "AppRuns",
    "HowItRuns",
    "Machine",
    "TreeReading",
    "Way",
    "decide",
    "describe_runs",
    "form_of_type",
    "machine_here",
    "read_tree",
    "run_as_conflict",
    "runs_of",
]

#: Directly on the server: a service of the system, with an account of its own.
FORM_SERVER = "server"
#: In a container: a Dockerfile, Railpack, an image from a registry or a Compose file.
FORM_CONTAINER = "container"
#: Every form, the one a person is proposed by default first.
FORMS: tuple[str, ...] = (FORM_SERVER, FORM_CONTAINER)

#: This form can be, here.
STATUS_YES = "yes"
#: This form cannot be yet, and the way says why and what would make it possible.
STATUS_NO = "no"
#: There is nothing to fix: this machine can never run this form (one that only has Docker keeps no
#: service), or nothing in the source runs that way. It is not offered, and the proposal says why.
STATUS_HIDDEN = "hidden"

#: A container built from the repository's Dockerfile.
VIA_DOCKERFILE = "dockerfile"
#: A container Railpack builds, which needs no Dockerfile.
VIA_RAILPACK = "railpack"
#: A container run from an image of a registry.
VIA_IMAGE = "image"
#: Containers a Compose file describes.
VIA_COMPOSE = "compose"

#: The application type of a Compose stack, which no Dockerfile or language decides.
_STACK = AppType.DOCKER_COMPOSE.value
#: The application type of a container Noust writes the Compose file of.
_CONTAINER = AppType.CONTAINER.value

#: What a person runs to give a server what a container built without a Dockerfile needs.
_RAILPACK_COMMAND = "noust setup railpack"


@dataclass(frozen=True)
class TreeReading:
    """
    What a source is, and what it would be if its Dockerfile were not how it runs.

    Attributes:
        detected: Every type that recognises the source, most specific first: what ``inspect`` has
            always said. A Dockerfile makes Go, Rust, .NET, Elixir, Deno, Ruby and Java decline.
        native: The types that run as a service of the system when the Dockerfile is not taken as how
            the repository runs, most specific first. A Compose file and the container are not here:
            they are the other form.
        dockerfile: The source has a Dockerfile or a Containerfile.
        port: The port its Dockerfile says the process listens on (``EXPOSE``), when it says one.
    """

    detected: tuple[str, ...]
    native: tuple[str, ...]
    dockerfile: bool
    port: int | None = None


@dataclass(frozen=True)
class Machine:
    """
    What this machine can run, as :func:`decide` needs it.

    Attributes:
        server: Whether it runs a native application, and why not when it does not.
        container: Whether it runs a container application, and why not.
        docker_only: Docker is what it has and nothing keeps a service (no systemd): the form that
            needs one is never offered, whatever is installed later.
        railpack: Railpack is installed, so a repository without a Dockerfile can be built.
    """

    server: Availability
    container: Availability
    docker_only: bool = False
    railpack: bool = False


@dataclass(frozen=True)
class Way:
    """
    One form an application can run in, here.

    Attributes:
        form: ``server`` or ``container``.
        status: ``yes``, ``no`` or ``hidden``.
        app_type: The type it deploys as when chosen (``nodejs``, ``container``, ``docker-compose``);
            None when it cannot be.
        via: For a container, how it is built: ``dockerfile``, ``railpack`` or ``compose``.
        reason: When it cannot be, the sentence for why, with its fix as the sentence's hint.
        port: For a container built from its Dockerfile, the port the Dockerfile says the process
            listens on, when it says one. What a service listens on is its type's, which the
            inspection already says.
    """

    form: str
    status: str
    app_type: str | None = None
    via: str | None = None
    reason: ServerText | None = None
    port: int | None = None


@dataclass(frozen=True)
class HowItRuns:
    """
    The decision: the two ways, which is proposed and why.

    Attributes:
        choice: Both ways can be, so there is something to choose.
        proposed: The form Noust proposes: the one the registry's first type has, unless this machine
            cannot run it and can run the other.
        why: The sentence for the proposal.
        server: The directly-on-the-server way.
        container: The container way.
    """

    choice: bool
    proposed: str
    why: ServerText
    server: Way
    container: Way

    def way(self, form: str) -> Way:
        """
        Args:
            form: ``server`` or ``container``.

        Returns:
            That way.

        Raises:
            KeyError: When there is no such form.
        """
        if form == FORM_SERVER:
            return self.server
        if form == FORM_CONTAINER:
            return self.container
        raise KeyError(f"{form!r} is not a way to run an application: one of {', '.join(FORMS)}")


def _display_name(app_type: str) -> str:
    """
    Args:
        app_type: A registered application type.

    Returns:
        What a person calls it (``Node.js``), or the type itself when the registry does not know it.
    """
    deployer = DeployerRegistry.get(app_type)
    return deployer.DISPLAY_NAME if deployer is not None else app_type


def _dockerfile_port(path: Path) -> int | None:
    """
    Args:
        path: The directory holding the source.

    Returns:
        The port its Dockerfile exposes, as the container type reads it for its own inspection.
    """
    from noust.deployers.container import ContainerDeployer

    return ContainerDeployer.inspected_port(path)


def read_tree(path: Path) -> TreeReading:
    """
    Read what a source is, and what it could be if a Dockerfile did not decide how it runs.

    Args:
        path: The directory holding the source.

    Returns:
        The reading. The second look only happens for a repository that has a Dockerfile, which is the
        only one whose answer can differ, and runs inside a scope that ends with it.
    """
    detected = tuple(deployer.APP_TYPE for deployer in DeployerRegistry.detect_all(path))
    dockerfile = _CONTAINER in detected
    if dockerfile:
        with ignoring_container_files():
            again = tuple(deployer.APP_TYPE for deployer in DeployerRegistry.detect_all(path))
    else:
        again = detected
    return TreeReading(
        detected=detected,
        native=tuple(app_type for app_type in again if app_type not in COMPOSE_BACKED_TYPES),
        dockerfile=dockerfile,
        port=_dockerfile_port(path) if dockerfile else None,
    )


def machine_here(runner: CommandRunner | None = None, config: Config | None = None) -> Machine:
    """
    Ask this machine what it can run.

    Args:
        runner: Asks the machine; the process-wide one by default.
        config: The configuration; the process-wide one by default.

    Returns:
        What :mod:`noust.central.features` says of a native and of a container application, which is
        the one place a machine's abilities are decided, and whether Railpack is there.
    """
    from noust.central import features, machine, role

    active = runner or get_runner()
    facts = machine.read_machine_facts(active)
    this_role = role(config)
    return Machine(
        server=features.availability("native_deploy", role=this_role, facts=facts),
        container=features.availability("container_deploy", role=this_role, facts=facts),
        docker_only=facts.docker and not facts.systemd,
        railpack=active.exists(railpack.BINARY_NAME),
    )


def _server_way(native: str | None, machine: Machine) -> Way:
    """
    Args:
        native: The type the source would run as directly, or None when none would.
        machine: What this machine can run.

    Returns:
        The directly-on-the-server way.
    """
    if native is None:
        # Not a way that is missing something: nothing in the source runs like that, which the
        # proposal already says.
        return Way(FORM_SERVER, STATUS_HIDDEN, reason=say("how_it_runs.server.nothing_recognised"))
    if machine.server.available:
        return Way(FORM_SERVER, STATUS_YES, app_type=native)
    status = STATUS_HIDDEN if machine.docker_only else STATUS_NO
    return Way(FORM_SERVER, status, reason=machine.server.reason)


def _container_way(reading: TreeReading, stack: bool, machine: Machine) -> Way:
    """
    Args:
        reading: What the source is.
        stack: A Compose file describes it, and that is what runs.
        machine: What this machine can run.

    Returns:
        The container way: Compose when there is a file, else the Dockerfile, else Railpack.
    """
    # What a Dockerfile says is a fact of the repository, true where this machine cannot run it yet too.
    port = reading.port if reading.dockerfile and not stack else None
    if not machine.container.available:
        return Way(FORM_CONTAINER, STATUS_NO, reason=machine.container.reason, port=port)
    if stack:
        return Way(FORM_CONTAINER, STATUS_YES, app_type=_STACK, via=VIA_COMPOSE)
    if reading.dockerfile:
        return Way(FORM_CONTAINER, STATUS_YES, app_type=_CONTAINER, via=VIA_DOCKERFILE, port=port)
    if machine.railpack:
        return Way(FORM_CONTAINER, STATUS_YES, app_type=_CONTAINER, via=VIA_RAILPACK)
    return Way(
        FORM_CONTAINER,
        STATUS_NO,
        reason=say("how_it_runs.container.no_build", command=_RAILPACK_COMMAND),
    )


def _proposed(wanted: str, server: Way, container: Way) -> str:
    """
    Args:
        wanted: The form of the type the registry recognises first.
        server: The directly-on-the-server way.
        container: The container way.

    Returns:
        ``wanted`` unless this machine cannot run it and can run the other.
    """
    ways = {FORM_SERVER: server, FORM_CONTAINER: container}
    other = FORM_CONTAINER if wanted == FORM_SERVER else FORM_SERVER
    if ways[wanted].status != STATUS_YES and ways[other].status == STATUS_YES:
        return other
    return wanted


def _why(
    proposed: str, native: str | None, stack: bool, server: Way, container: Way, machine: Machine
) -> ServerText:
    """
    Args:
        proposed: The form proposed.
        native: The type the source would run as directly, or None.
        stack: A Compose file describes it.
        server: The directly-on-the-server way.
        container: The container way.
        machine: What this machine can run.

    Returns:
        The sentence for the proposal.
    """
    if STATUS_YES not in (server.status, container.status):
        return say("how_it_runs.why.none")
    name = _display_name(native) if native is not None else ""
    if proposed == FORM_SERVER:
        if container.status == STATUS_YES and container.via == VIA_DOCKERFILE:
            return say("how_it_runs.why.server_dockerfile", type=name)
        if container.status == STATUS_YES and container.via == VIA_RAILPACK:
            return say("how_it_runs.why.server_railpack", type=name)
        return say("how_it_runs.why.server_only", type=name)
    if machine.docker_only:
        return say("how_it_runs.why.docker_only")
    if stack:
        return say("how_it_runs.why.compose")
    if native is not None and server.status == STATUS_YES:
        return say("how_it_runs.why.container_dockerfile", type=name)
    if native is not None:
        return say("how_it_runs.why.container_machine")
    if container.via == VIA_RAILPACK:
        return say("how_it_runs.why.container_railpack")
    return say("how_it_runs.why.container_only")


def decide(reading: TreeReading, machine: Machine) -> HowItRuns:
    """
    Say how a source can run on this machine, and which way is proposed.

    What is proposed does not depend on what is installed unless that is what stands in the way: it is
    the form of the first type the registry recognises (a Dockerfile makes that a container for Go and
    a service for Node.js, as it always did), so nothing that deployed one way is proposed the other.
    The other way is offered when it can be, and said not to be, with its reason, when it cannot.

    Args:
        reading: What the source is (:func:`read_tree`, plus the container Railpack makes of what no
            deployer knows, which :func:`~noust.deployers.inspect.inspect_source` adds).
        machine: What this machine can run (:func:`machine_here`).

    Returns:
        The decision.
    """
    native = next(
        (app_type for app_type in reading.native if app_type not in COMPOSE_BACKED_TYPES), None
    )
    stack = _STACK in reading.detected
    server = _server_way(native, machine)
    container = _container_way(reading, stack, machine)
    winner = reading.detected[0] if reading.detected else None
    wanted = FORM_CONTAINER if winner in COMPOSE_BACKED_TYPES else FORM_SERVER
    proposed = _proposed(wanted, server, container)
    return HowItRuns(
        choice=server.status == STATUS_YES and container.status == STATUS_YES,
        proposed=proposed,
        why=_why(proposed, native, stack, server, container, machine),
        server=server,
        container=container,
    )


def form_of_type(app_type: str) -> str:
    """
    Args:
        app_type: An application type.

    Returns:
        The form it runs in: ``container`` for a container and a Compose stack, ``server`` for
        every type that is a service of the system.
    """
    return FORM_CONTAINER if app_type in COMPOSE_BACKED_TYPES else FORM_SERVER


def run_as_conflict(run_as: str, app_type: str) -> str | None:
    """
    Say when a form asked for contradicts a type asked for.

    Args:
        run_as: ``server`` or ``container``.
        app_type: The type the person named; ``auto`` leaves the decision to what the source is.

    Returns:
        The sentence to refuse with, or None when they agree (or the type is ``auto``).
    """
    if app_type == "auto" or form_of_type(app_type) == run_as:
        return None
    if run_as == FORM_SERVER:
        return f"--run-as server contradicts --type {app_type}: a {app_type} application runs in containers."
    return (
        f"--run-as container contradicts --type {app_type}: a {app_type} application runs "
        "directly on the server. Use --type container to run it in a container."
    )


@dataclass(frozen=True)
class AppRuns:
    """
    How an application that exists runs.

    Attributes:
        form: ``server`` (a service of the system) or ``container``.
        via: For a container: ``dockerfile``, ``railpack``, ``image`` or, for a Compose stack,
            ``compose``; None for a service.
        image: For a container application, the image that serves: the registry's reference, or the
            one Noust built for the release that is active; None when none serves yet.
    """

    form: str
    via: str | None = None
    image: str | None = None


def runs_of(app_type: str, method: str | None = None) -> AppRuns | None:
    """
    Say how an application runs, from what it is.

    Args:
        app_type: Its type.
        method: How a container application is built (``dockerfile``, ``railpack`` or ``image``),
            from its settings; None for any other type.

    Returns:
        The form, or None for an application Noust adopted and does not run (a site and a unit an
        operator wrote) or whose type is unknown.
    """
    if app_type in (AppType.ADOPTED.value, AppType.UNKNOWN.value):
        return None
    if app_type == _STACK:
        return AppRuns(FORM_CONTAINER, VIA_COMPOSE)
    if app_type == _CONTAINER:
        return AppRuns(FORM_CONTAINER, method or VIA_DOCKERFILE)
    return AppRuns(FORM_SERVER)


def describe_runs(app: App) -> AppRuns | None:
    """
    Say how an application that exists runs, from what is recorded of it.

    Args:
        app: The application's row.

    Returns:
        The form, with how a container is built and the image that serves; None for an application
        Noust adopted and does not run.
    """
    if app.app_type != _CONTAINER:
        return runs_of(app.app_type)
    try:
        view = describe_container(app.domain)
    except NoustError:
        # Deployed before its settings were recorded: it is a container, and how is not known.
        return runs_of(app.app_type)
    method = str(view.settings.get("method") or VIA_DOCKERFILE)
    image = view.settings.get("image") if method == VIA_IMAGE else None
    if image is None and view.release is not None:
        image = image_tag(domain_to_app_name(app.domain), view.release)
    return AppRuns(FORM_CONTAINER, method, str(image) if image else None)
