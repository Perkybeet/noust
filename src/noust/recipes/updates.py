# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Keeping installed recipes up to date: which are behind, and updating one.

Coolify copies a template when a service is created and never looks at it
again ("Coolify does not merge future template changes into the saved
Service"). Here the catalogue remembers what each application was installed
from (:mod:`noust.recipes.installed`), so it can say that Vaultwarden 1.37.4
is installed and 1.38.0 is in the catalogue, and what changed.

Updating is not a second way to update. :func:`update_recipe` renders the
new version's source and hands it to
:func:`noust.deployers.lifecycle.update_app`, which is the one update: the
pre-update backup (the databases of a Compose stack included), the build, the
health gate, the instant return to the previous release. The only thing this
module adds is what the deployers cannot know:

- the variables the new version declares and the application lacks are
  generated (or demanded, when ``required``) before anything is touched, and the
  ones it has are never regenerated or reset;
- a Compose stack pinned by digest is put back from the files it ran with when
  the new version fails its gate, because the deployer returns to a previous
  image by name and a digest is not a name.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.central import require_server_role
from noust.core.exceptions import DeploymentError, DockerError, NoustError, RolledBackError
from noust.core.fs import FileSystem, get_fs
from noust.core.logger import Logger
from noust.core.store import DeploymentTrigger, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.helpers.hooks import HookFailedError
from noust.recipes import Recipe, RecipeError, RecipeNotFoundError, get_recipe
from noust.recipes.deploy import (
    prepare_source,
    recipe_source_dir,
    require_available,
)
from noust.recipes.installed import (
    InstalledRecipe,
    get_installed,
    list_installed,
    record_update,
)
from noust.recipes.model import ChangelogEntry
from noust.recipes.variables import (
    DeploymentContext,
    refuse_compose_dollar,
    require_inputs,
    resolve_variables,
)
from noust.recipes.versions import is_newer
from noust.validators.domain import validate_domain
from noust.validators.environment import validate_environment

if TYPE_CHECKING:
    from noust.deployers.helpers.health_gate import HealthGate


@dataclass(frozen=True)
class RecipeUpdateStatus:
    """
    Where an installed recipe stands against the catalogue.

    Attributes:
        domain: The application.
        recipe: The recipe it was installed from.
        kind: ``native`` or ``compose``.
        installed_version: The version it runs.
        installed_revision: The recipe's revision of that version.
        latest_version: The catalogue's version, or None when the recipe
            left the catalogue.
        latest_revision: The catalogue's revision of it.
        update_available: The catalogue is ahead and can be deployed.
        reason: Why nothing can be updated, when the recipe is gone or listed
            as unavailable.
        changes: What the versions between say they changed.
    """

    domain: str
    recipe: str
    kind: str
    installed_version: str
    installed_revision: int
    latest_version: str | None
    latest_revision: int | None
    update_available: bool
    reason: str | None = None
    changes: tuple[ChangelogEntry, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the status for the CLI and the API.

        Returns:
            Every field, with the changes as plain mappings.
        """
        return {
            "domain": self.domain,
            "recipe": self.recipe,
            "kind": self.kind,
            "installed_version": self.installed_version,
            "installed_revision": self.installed_revision,
            "latest_version": self.latest_version,
            "latest_revision": self.latest_revision,
            "update_available": self.update_available,
            "reason": self.reason,
            "changes": [
                {"version": change.version, "summary": change.summary} for change in self.changes
            ],
        }


def status_of(installed: InstalledRecipe) -> RecipeUpdateStatus:
    """
    Compare an installed recipe with the catalogue.

    Args:
        installed: What the application was installed from.

    Returns:
        Its status. A recipe the catalogue no longer has, or lists as
        unavailable, is never ``update_available``, and says why.
    """

    def status(**rest: Any) -> RecipeUpdateStatus:
        return RecipeUpdateStatus(
            domain=installed.domain,
            recipe=installed.recipe,
            kind=installed.kind,
            installed_version=installed.version,
            installed_revision=installed.revision,
            **rest,
        )

    try:
        recipe = get_recipe(installed.recipe)
    except RecipeNotFoundError:
        return status(
            latest_version=None,
            latest_revision=None,
            update_available=False,
            reason="The recipe is no longer in the catalogue",
        )
    if not recipe.available:
        return status(
            latest_version=recipe.version or None,
            latest_revision=recipe.revision,
            update_available=False,
            reason=recipe.unavailable_reason or "The recipe is not available in this release",
        )
    ahead = is_newer(
        recipe.version,
        recipe.revision,
        than_version=installed.version,
        than_revision=installed.revision,
    )
    changes = tuple(
        change
        for change in recipe.changelog
        if is_newer(change.version, 1, than_version=installed.version, than_revision=1)
    )
    return status(
        latest_version=recipe.version,
        latest_revision=recipe.revision,
        update_available=ahead,
        changes=changes if ahead else (),
    )


def installed_statuses(*, store: NoustStore | None = None) -> list[RecipeUpdateStatus]:
    """
    Compare every application installed from a recipe with the catalogue.

    Args:
        store: The store. Defaults to the process-wide one.

    Returns:
        One status per application, by domain.
    """
    return [status_of(installed) for installed in list_installed(store=store)]


def outdated(*, store: NoustStore | None = None) -> list[RecipeUpdateStatus]:
    """
    List the installed recipes the catalogue is ahead of.

    Args:
        store: The store. Defaults to the process-wide one.

    Returns:
        The statuses with an update available.
    """
    return [status for status in installed_statuses(store=store) if status.update_available]


@dataclass(frozen=True)
class RecipeUpdate:
    """
    What :func:`update_recipe` did.

    Attributes:
        domain: The application.
        recipe: The recipe.
        from_version: The version it ran before.
        to_version: The version it runs now.
        updated: False when it was already on the catalogue's version.
        deployment_id: The deployment history row of the update.
        warnings: What the update says went wrong without failing it.
        notes: What to tell the operator, rendered.
        added_variables: The variables the new version added.
    """

    domain: str
    recipe: str
    from_version: str
    to_version: str
    updated: bool
    deployment_id: int | None = None
    warnings: str | None = None
    notes: tuple[str, ...] = ()
    added_variables: tuple[str, ...] = ()


def update_recipe(
    domain: str,
    *,
    overrides: Mapping[str, str] | None = None,
    force: bool = False,
    trigger: str = DeploymentTrigger.CLI.value,
    job_id: str | None = None,
    on_phase: Callable[[int, int, str], None] | None = None,
    on_step: Callable[[str], None] | None = None,
    logger: Logger | None = None,
    verbose: bool = False,
    store: NoustStore | None = None,
) -> RecipeUpdate:
    """
    Update an application installed from a recipe to the catalogue's version.

    Args:
        domain: The application's domain.
        overrides: Values for the variables the new version asks for.
        force: Deploy the catalogue's version even when it is the one
            installed (rebuilds it, and re-pulls its pinned images).
        trigger: Who asked, recorded in the deployment history.
        job_id: The background job driving this update, when there is one.
        on_phase: Called as each phase of the update begins.
        on_step: Called as each step of the rebuild begins.
        logger: Logger for the details.
        verbose: Verbosity of the managers and the deployer.
        store: The store. Defaults to the process-wide one.

    Returns:
        What was done; ``updated`` is False when nothing needed doing.

    Raises:
        RecipeError: The application was not installed from a recipe, the
            recipe is gone or unavailable, or the new version needs a
            variable nobody gave or a service the installed one did not have;
            nothing was touched.
        DeploymentError: The new version did not pass its health gate. For a
            Compose stack the message says whether the previous version is
            serving again.
        NoustError: Any other failure of the update.
    """
    from noust.deployers.helpers.app_env import read_app_env, write_app_env
    from noust.deployers.lifecycle import health_gate_for, update_app

    require_server_role("Applications")
    store = store or get_store()
    log = logger or Logger(verbose=verbose)
    domain = validate_domain(domain)
    installed = get_installed(domain, store=store)
    if installed is None:
        raise RecipeError(
            f"{domain} was not installed from a recipe",
            details="Update it as any application: noust update " + domain,
        )
    try:
        recipe = get_recipe(installed.recipe)
    except RecipeNotFoundError as exc:
        raise RecipeError(
            f"The recipe {installed.recipe} is no longer in the catalogue",
            details=f"{domain} keeps running; update it as any application: noust update {domain}",
        ) from exc
    require_available(recipe)
    status = status_of(installed)
    if not status.update_available and not force:
        return RecipeUpdate(
            domain=domain,
            recipe=recipe.name,
            from_version=installed.version,
            to_version=installed.version,
            updated=False,
        )
    if recipe.schema < 2:
        raise RecipeError(
            f"{recipe.title} is written in the 2.3 recipe format, which cannot be updated",
            details=f"Update the application as any other: noust update {domain}",
        )

    app = store.get_app(domain)
    if app is None:
        raise RecipeError(f"{domain} is not deployed", details="Install the recipe first.")
    overrides = validate_environment(dict(overrides or {}))
    existing = read_app_env(app)
    # Nothing is touched before the operator has given everything the new
    # version demands, exactly as an install.
    require_inputs(recipe.title, recipe.variables, overrides, existing)
    if recipe.kind == "compose":
        refuse_compose_dollar(recipe.title, overrides)
    _refuse_new_requirements(recipe, existing)

    context = DeploymentContext(
        domain=domain,
        ssl=app.ssl_enabled,
        port=app.port,
        app_name=domain_to_app_name(domain),
        app_path=app.app_path,
    )
    resolved = resolve_variables(
        recipe.variables, context=context, provisioned={}, overrides=overrides, existing=existing
    )
    added = tuple(
        name for name in resolved.values if name not in existing and name not in overrides
    )
    changed = {name: value for name, value in overrides.items() if existing.get(name) != value}

    app_name = domain_to_app_name(domain)
    snapshot = _snapshot_stack(recipe, app_name, store) if recipe.kind == "compose" else {}
    files_values = {**existing, **resolved.values}
    source, branch = prepare_source(recipe, app_name, store, files_values)
    if added or changed:
        write_app_env(
            app,
            {**existing, **{name: resolved.values[name] for name in added}, **changed},
            logger=log,
        )
        log.substep(f"Variables added: {', '.join(added)}" if added else "Variables updated")
        marks = dict(app.env_secret_marks)
        marks.update({name: True for name in resolved.secrets if name in resolved.values})
        store.set_env_secret_marks(domain, marks)

    try:
        outcome = update_app(
            domain,
            source=source,
            branch=branch,
            trigger=trigger,
            job_id=job_id,
            on_phase=on_phase,
            on_step=on_step,
            logger=log,
            verbose=verbose,
        )
    except NoustError as failure:
        if recipe.kind == "compose" and snapshot:
            _put_stack_back(
                domain, app_name, snapshot, failure, store=store, log=log, gate=health_gate_for
            )
        raise
    record_update(domain, recipe, store=store)
    return RecipeUpdate(
        domain=domain,
        recipe=recipe.name,
        from_version=installed.version,
        to_version=recipe.version,
        updated=True,
        deployment_id=outcome.deployment_id,
        warnings=outcome.warnings,
        added_variables=added,
    )


def _refuse_new_requirements(recipe: Recipe, existing: Mapping[str, str]) -> None:
    """
    Refuse a version that needs a service the installed one never provisioned.

    Services are provisioned when a recipe is installed. A variable the new
    version takes from one (``from: postgres.url``) that the application does
    not have means the installed version did not ask for it.

    Args:
        recipe: The catalogue's recipe.
        existing: The application's variables.

    Raises:
        RecipeError: Naming the variable and the service; nothing was touched.
    """
    for variable in recipe.variables:
        source = variable.source or ""
        if "." in source and not variable.is_external and variable.name not in existing:
            raise RecipeError(
                f"{recipe.title} {recipe.version} needs {source.split('.', 1)[0]}, which the "
                "installed version did not use",
                details="Noust provisions a recipe's services when it installs it. Install "
                "this version on a new domain and move your data, or provision the service "
                f"and give {variable.name} its value in the application's environment.",
            )


def _snapshot_stack(recipe: Recipe, app_name: str, store: NoustStore) -> dict[str, str]:
    """
    Keep the files a Compose stack runs with, before the update replaces them.

    Args:
        recipe: The catalogue's recipe (which names the files by its assets).
        app_name: The application name.
        store: The store, beside which the stack's source is kept.

    Returns:
        Path to text of what the recipe source directory holds now: the
        document and the files of the version that is serving.
    """
    directory = recipe_source_dir(app_name, store)
    files: dict[str, str] = {}
    if not directory.is_dir():
        return files
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            files[path.relative_to(directory).as_posix()] = path.read_text(encoding="utf-8")
    return files


def _put_stack_back(
    domain: str,
    app_name: str,
    snapshot: Mapping[str, str],
    failure: NoustError,
    *,
    store: NoustStore,
    log: Logger,
    gate: Callable[..., HealthGate],
) -> None:
    """
    Run the previous version of a Compose stack again, after the new one failed.

    The deployer goes back by image name, and an image pinned by digest has
    no name to go back to, so a stack that failed its gate is left running
    the new containers on the new file. This undoes what the update wrote, as
    far as the update got:

    - the recipe's own source directory always goes back to what it held, so
      the next ``noust update`` does not start from a version that failed;
    - when the update reached the deployer (it raised a deployment or a Docker
      error), the application's own files go back too, which is what a later
      restart of the unit would read;
    - and when that error is one that follows a recreation of the containers
      (not a hook, which runs before it, and not a rollback the deployer already
      did), the unit is restarted from those files behind the same health gate.

    An application that is busy with another operation, a backup that failed
    and anything refused before the deployer is left alone. What happened is
    added to the error the operator reads.

    Args:
        domain: The application's domain.
        app_name: The application name.
        snapshot: The files the previous version ran with.
        failure: The error being raised, to which the outcome is added.
        store: The store.
        log: Where progress is reported.
        gate: :func:`noust.deployers.lifecycle.health_gate_for`.
    """
    fs = get_fs()
    directory = recipe_source_dir(app_name, store)
    _write_files(fs, directory, snapshot)
    reached = isinstance(failure, DeploymentError | DockerError)
    app = store.get_app(domain)
    if not reached or app is None:
        return
    log.warning("The new version failed; putting the previous one back")
    _write_files(fs, Path(app.app_path), snapshot)
    recreated = not isinstance(failure, RolledBackError | HookFailedError)
    if not recreated:
        outcome = "The files of the previous version were put back in the application."
    else:
        healthy, evidence = gate(app, store, log).restart_and_probe()
        outcome = (
            "The previous version is running again and answering."
            if healthy
            else "The previous version was put back but is not answering either:\n" + evidence
        )
    failure.details = f"{failure.details}\n\n{outcome}".strip()
    log.warning(outcome)


def _write_files(fs: FileSystem, root: Path, files: Mapping[str, str]) -> None:
    """
    Write files under a directory, making what they need.

    Args:
        fs: The filesystem seam.
        root: The directory.
        files: Path inside it to text.
    """
    for path, text in files.items():
        target = root / path
        fs.make_dir(target.parent, parents=True)
        fs.write_text(target, text)
