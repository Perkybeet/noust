# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a recipe needs from this server, checked before anything is created.

A Coolify template carries its runtime inside its image, so its user never sees
one; a native product has to say. A recipe's needs are three kinds, each
checked by the code that already knows how:

- **tools**: what its deployer runs (Docker with the Compose plugin for a
  stack, Node and a package manager for a Node application), asked of
  :func:`noust.core.dependencies.check_deployment_ready`;
- **a runtime**: the language version the recipe pins in ``build.runtime``
  (``node@22``, ``python@3.12``, ``php@8.3``), asked of
  :class:`~noust.managers.runtimes.RuntimeManager`, which can also install it;
- **services**: the database engines it ``requires``, installed and running.

:func:`check_requirements` only looks. :func:`refuse_unmet` is the gate the
install passes before a database or a directory exists, so a server that cannot
run the recipe says so with every missing piece and the command for each,
instead of failing in the middle. With ``install`` it installs what Noust knows
how to install (a runtime, an engine) first; Docker and an operating system
package are never installed on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from noust.core.dependencies import check_deployment_ready
from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.versions import RuntimeRequest, parse_runtime
from noust.recipes.model import ENGINE_OF, SQL_REQUIREMENTS, Recipe, RecipeError

#: The requirement kinds.
TOOL = "tool"
RUNTIME = "runtime"
SERVICE = "service"


@dataclass(frozen=True)
class Requirement:
    """
    One thing a recipe needs, and whether this server has it.

    Attributes:
        kind: ``tool``, ``runtime`` or ``service``.
        name: What it is, as the operator reads it (``Docker``, ``node@22``,
            ``postgresql``).
        met: Whether the server has it now.
        detail: What was found, or what is missing.
        fix: The command that provides it, when there is one.
        installable: Whether the install can provide it itself when asked
            (a runtime, a database engine).
    """

    kind: str
    name: str
    met: bool
    detail: str
    fix: str | None = None
    installable: bool = False

    def to_dict(self) -> dict[str, object]:
        """
        Describe the requirement for the API and the command line.

        Returns:
            Every attribute.
        """
        return {
            "kind": self.kind,
            "name": self.name,
            "met": self.met,
            "detail": self.detail,
            "fix": self.fix,
            "installable": self.installable,
        }


def check_requirements(recipe: Recipe) -> list[Requirement]:
    """
    Look at what a recipe needs and report each piece. Changes nothing.

    Args:
        recipe: The recipe.

    Returns:
        One entry per tool, runtime and service, met or not. A recipe that is
        not available has none.
    """
    if not recipe.available:
        return []
    found = _tools(recipe)
    found.extend(_runtime(recipe))
    found.extend(_services(recipe))
    return found


def _tools(recipe: Recipe) -> list[Requirement]:
    """
    Check the tools the recipe's deployer runs.

    Args:
        recipe: The recipe.

    Returns:
        A requirement per missing tool, or one met entry naming the check.
    """
    ready, missing, _warnings = check_deployment_ready(recipe.app_type)
    if ready:
        label = "Docker with the Compose plugin" if recipe.kind == "compose" else "build tools"
        return [Requirement(TOOL, label, True, "found")]
    return [
        Requirement(TOOL, item, False, "not found on this server", fix=_tool_fix(item))
        for item in missing
    ]


def _tool_fix(item: str) -> str | None:
    """
    Say how to get a tool the readiness check found missing.

    Args:
        item: What the check called it.

    Returns:
        A command or a page, when the tool is one Noust points people at.
    """
    lowered = item.lower()
    if "docker" in lowered:
        return "https://docs.docker.com/engine/install/ (then: noust setup doctor)"
    return None


def _runtime(recipe: Recipe) -> list[Requirement]:
    """
    Check the language runtime a recipe pins.

    Args:
        recipe: The recipe.

    Returns:
        The requirement, met when a matching runtime is installed, and
        installable when the runtime manager can fetch it.
    """
    if recipe.build is None or not recipe.build.runtime:
        return []
    request = parse_runtime(recipe.build.runtime, field="build.runtime")
    return [_runtime_requirement(request)]


def _runtime_requirement(request: RuntimeRequest) -> Requirement:
    """
    Ask the runtime manager whether a version is there.

    Args:
        request: The pinned runtime.

    Returns:
        The requirement.
    """
    from noust.managers.runtimes import RuntimeManager

    manager = RuntimeManager()
    try:
        installed = manager.find(request)
    except NoustError as exc:
        return Requirement(RUNTIME, request.text, False, exc.message, installable=False)
    if installed is not None:
        origin = "managed by Noust" if installed.managed else "the server's own"
        return Requirement(
            RUNTIME, request.text, True, f"{request.language} {installed.version}, {origin}"
        )
    return Requirement(
        RUNTIME,
        request.text,
        False,
        f"{request.language} {request.spec} is not installed",
        fix=f"noust runtime install {request.text}",
        installable=True,
    )


def _services(recipe: Recipe) -> list[Requirement]:
    """
    Check that each database engine the recipe requires is installed and running.

    Args:
        recipe: The recipe.

    Returns:
        A requirement per engine. An object store (``s3``) is the operator's
        own endpoint and has nothing to check here.
    """
    from noust.managers.database.service import DatabaseService

    found: list[Requirement] = []
    service = DatabaseService()
    for requirement in recipe.requires:
        engine = ENGINE_OF.get(requirement)
        if engine is None:
            continue
        try:
            manager = service.manager(engine)
            installed = manager.is_installed()
            running = installed and manager.is_running()
        except NoustError as exc:
            found.append(Requirement(SERVICE, engine, False, exc.message))
            continue
        if running:
            found.append(Requirement(SERVICE, engine, True, f"{manager.DISPLAY_NAME} is running"))
            continue
        kind = "SQL database" if requirement in SQL_REQUIREMENTS else "service"
        detail = (
            f"{manager.DISPLAY_NAME} is installed but not running"
            if installed
            else f"No {kind} engine for {engine} is installed"
        )
        found.append(
            Requirement(
                SERVICE,
                engine,
                False,
                detail,
                fix=(f"noust db start {engine}" if installed else f"noust db install {engine}"),
                installable=not installed,
            )
        )
    return found


def unmet(requirements: list[Requirement]) -> list[Requirement]:
    """
    Pick out what a server lacks.

    Args:
        requirements: What :func:`check_requirements` found.

    Returns:
        The ones not met.
    """
    return [item for item in requirements if not item.met]


def refuse_unmet(
    recipe: Recipe,
    *,
    install: bool,
    logger: Logger,
    kinds: tuple[str, ...] = (RUNTIME, SERVICE),
) -> None:
    """
    Stop an install before anything exists when the server cannot run the recipe.

    With ``install``, the runtimes and database engines that are missing are
    installed first (the same installs ``noust runtime install`` and ``noust
    db install`` do), and what cannot be installed that way still stops it.

    Args:
        recipe: The recipe.
        install: Install what Noust knows how to install.
        logger: Where progress is reported.
        kinds: Which kinds of requirement stop the install. The tools a
            deployer runs are checked by the deployment's own readiness check,
            so they are not asked twice by default.

    Raises:
        RecipeError: Something is missing: every missing piece is named with
            the command that provides it.
    """
    missing = [item for item in unmet(check_requirements(recipe)) if item.kind in kinds]
    if not missing:
        return
    if install:
        for item in [entry for entry in missing if entry.installable]:
            logger.substep(f"Installing {item.name}")
            _install(item)
        missing = [item for item in unmet(check_requirements(recipe)) if item.kind in kinds]
        if not missing:
            return
    lines = "; ".join(f"{item.name}: {item.detail}" for item in missing)
    fixes = "\n".join(f"  {item.fix}" for item in missing if item.fix)
    installable = any(item.installable for item in missing)
    raise RecipeError(
        f"This server cannot run {recipe.title} yet: {lines}",
        details=(fixes + "\n" if fixes else "")
        + (
            "Or install what Noust can install on its own with --install-requirements "
            "(install_requirements in the API)."
            if installable and not install
            else ""
        ),
    )


def _install(item: Requirement) -> None:
    """
    Install one requirement the install may provide itself.

    Args:
        item: A requirement with ``installable`` set.

    Raises:
        NoustError: The runtime manager or the database service refused.
    """
    if item.kind == RUNTIME:
        from noust.managers.runtimes import RuntimeManager

        RuntimeManager().ensure(parse_runtime(item.name, field="build.runtime"))
    elif item.kind == SERVICE:
        from noust.managers.database.service import DatabaseService

        DatabaseService().install_engine(item.name)
