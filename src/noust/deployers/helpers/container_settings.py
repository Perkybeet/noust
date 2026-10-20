# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Seeing and changing how a container application is built and run.

The one place ``noust app container``, ``noust create --image`` and
``/api/apps/{domain}/container`` meet (rule 3): they translate what a person
typed into the vocabulary below and this module does the rest, so the CLI and
the API cannot disagree about what is allowed.

The vocabulary is :class:`ContainerSettings` plus three things that are not
stored in its row because they are secrets: ``registry_password``,
``build_secret_values`` and ``clear_registry``. Passwords and build secrets go
to the secret store (:class:`~noust.core.secrets.SecretStore`), 0600, and only
the name they are kept under reaches the row. A change takes effect with the
next deployment, which writes the compose file again: nothing here recreates a
container.

Every change is audited under ``apps.container``, with the names of the
settings that changed and never a value of a secret.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from noust.core import audit
from noust.core.applock import app_lock
from noust.core.container_store import (
    ContainerSettings,
    ContainerStore,
    build_secret_name,
    registry_secret_name,
)
from noust.core.exceptions import NoustError, ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import AppType, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.validators.container import (
    METHOD_DOCKERFILE,
    METHOD_IMAGE,
    METHOD_RAILPACK,
    split_command,
)

if TYPE_CHECKING:
    from noust.core.store import App

#: The settings a change may name. The ids of the build secrets are not among
#: them: they follow from the values given (``build_secret_values``).
SETTINGS_FIELDS = frozenset(
    {
        "method",
        "image",
        "dockerfile",
        "context",
        "target",
        "build_args",
        "container_port",
        "volumes",
        "restart_policy",
        "registry_server",
        "registry_username",
        "entrypoint",
        "command",
    }
)

#: What a change may carry that is a secret, and so is not a column of the row.
SECRET_FIELDS = frozenset({"registry_password", "build_secret_values", "clear_registry"})


@dataclass(frozen=True)
class ContainerView:
    """
    What a client sees of a container application's settings.

    Attributes:
        domain: The application.
        settings: The settings, as :meth:`ContainerSettings.public` writes them.
        release: The release that serves, or None when none does.
    """

    domain: str
    settings: dict[str, Any]
    release: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            A JSON-ready mapping.
        """
        return {"domain": self.domain, **self.settings, "release": self.release}


@dataclass(frozen=True)
class ContainerChange:
    """
    What a change did.

    Attributes:
        settings: The settings now.
        changed: The names of the settings that changed, in a stable order.
        applies_on: What makes them take effect: ``next deployment``.
    """

    settings: ContainerSettings
    changed: tuple[str, ...]
    applies_on: str = "next deployment"


def _container_app(domain: str, store: NoustStore) -> App:
    """
    Read the row of an application that must be a container application.

    Args:
        domain: A validated domain.
        store: The store.

    Returns:
        The row.

    Raises:
        NoustError: The application is unknown.
        ValidationError: It is not a container application.
    """
    app = store.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    if app.app_type != AppType.CONTAINER.value:
        raise ValidationError(
            f"{domain} is a {app.app_type} application, not a container application",
            details="Only an application deployed with --type container has these settings.",
        )
    return app


def describe_container(domain: str, *, store: NoustStore | None = None) -> ContainerView:
    """
    Read a container application's settings and the release that serves.

    Args:
        domain: A validated domain.
        store: The store; the process-wide one when None.

    Returns:
        The view; the registry's password is never in it.

    Raises:
        NoustError: The application is unknown, or has no container settings.
        ValidationError: It is not a container application.
    """
    target = store or get_store()
    app = _container_app(domain, target)
    settings = ContainerStore(target).get(domain)
    if settings is None:
        raise NoustError(
            f"{domain} has no container settings",
            details="It was not deployed by a Noust that knows containers: redeploy it.",
        )
    active = next(
        (
            row.id
            for row in (target.list_releases(app.id) if app.id is not None else [])
            if row.status == "active"
        ),
        None,
    )
    return ContainerView(domain=domain, settings=settings.public(), release=active)


def settings_for_new_app(
    domain: str, request: Mapping[str, Any], *, secrets: SecretStore | None = None
) -> ContainerSettings:
    """
    Turn what a client gave at creation into settings, keeping its secrets safe.

    Validates everything first and writes the secrets (the registry password,
    the build secrets) only once nothing is refused, under names derived from
    the application: the deployer forgets them again when the application never
    comes to exist.

    Args:
        domain: The new application's domain.
        request: The fields of :data:`SETTINGS_FIELDS`, and optionally the
            secrets ``registry_password`` and ``build_secret_values``
            (secret id to value).
        secrets: The secret store; the process-wide one when None.

    Returns:
        The validated settings, whose registry secret and build secret ids
        name what was written.

    Raises:
        ValidationError: A field is unknown or not valid, or the registry
            account is half given.
    """
    settings, writes = _apply(
        ContainerSettings(), request, domain_to_app_name(domain), creating=True
    )
    _write_secrets(writes, secrets)
    return settings


def _apply(
    current: ContainerSettings,
    changes: Mapping[str, Any],
    app_name: str,
    *,
    creating: bool = False,
) -> tuple[ContainerSettings, dict[str, str | None]]:
    """
    Apply a change to settings, without writing anything.

    Args:
        current: The settings before.
        changes: What the client gave.
        app_name: The application's name, which names its secrets.
        creating: The settings are a new application's, whose method is
            whatever it is asked to be.

    Returns:
        The settings after, validated, and the secrets to write (name to
        value; None to remove).

    Raises:
        ValidationError: A field is unknown or the result is not valid.
    """
    unknown = sorted(set(changes) - SETTINGS_FIELDS - SECRET_FIELDS)
    if unknown:
        raise ValidationError(
            f"{unknown[0]!r} is not a container setting",
            details="The settings are: " + ", ".join(sorted(SETTINGS_FIELDS | SECRET_FIELDS)),
            field=unknown[0],
        )
    values: dict[str, Any] = {name: changes[name] for name in SETTINGS_FIELDS if name in changes}
    # What cannot be absent falls back to its default when it is cleared.
    for name, default in (("context", "."), ("restart_policy", "unless-stopped")):
        if name in values and values[name] is None:
            values[name] = default
    if values.get("method") is None:
        values.pop("method", None)
    if "build_args" in values:
        values["build_args"] = dict(values["build_args"] or {})
    if "volumes" in values:
        values["volumes"] = tuple(values["volumes"] or ())
    for name in ("entrypoint", "command"):
        # A line is split into words the way a shell would, without running one; a list is
        # taken as it is; None goes back to the image's own.
        if isinstance(values.get(name), str):
            values[name] = split_command(values[name], field=name)
        elif values.get(name) is not None:
            values[name] = tuple(values[name])

    writes: dict[str, str | None] = {}
    ids = list(current.build_secrets)
    for secret_id, value in (changes.get("build_secret_values") or {}).items():
        name = build_secret_name(app_name, secret_id)
        if value is None:
            writes[name] = None
            if secret_id in ids:
                ids.remove(secret_id)
        else:
            writes[name] = str(value)
            if secret_id not in ids:
                ids.append(secret_id)
    values["build_secrets"] = tuple(ids)

    if changes.get("clear_registry"):
        values.update(registry_server=None, registry_username=None, registry_secret=None)
        if current.registry_secret:
            writes[current.registry_secret] = None
    if changes.get("registry_password") is not None:
        secret = registry_secret_name(app_name)
        writes[secret] = str(changes["registry_password"])
        values["registry_secret"] = secret
    updated = replace(current, **values).checked()
    if not creating:
        _check_method_change(current, updated)
    return updated, writes


def _check_method_change(before: ContainerSettings, after: ContainerSettings) -> None:
    """
    Refuse changing between an image and a repository.

    Such an application has no repository (or no image): that is another
    application, not another setting.

    Args:
        before: The settings before.
        after: The settings after.

    Raises:
        ValidationError: The change moves between a build and an image.
    """
    if before.method == after.method:
        return
    if METHOD_IMAGE in (before.method, after.method):
        raise ValidationError(
            "An image from a registry and a repository are different applications",
            details="Create a new application to deploy the other way: its source, its "
            "directory and its releases are not the same.",
            field="method",
        )
    if {before.method, after.method} != {METHOD_DOCKERFILE, METHOD_RAILPACK}:  # pragma: no cover
        raise ValidationError("That is not a method change", field="method")


def _write_secrets(writes: Mapping[str, str | None], secrets: SecretStore | None) -> None:
    """
    Put secrets in the secret store, or take them out.

    Args:
        writes: Name to value; None removes it.
        secrets: The secret store; the process-wide one when None.
    """
    store = secrets or SecretStore()
    for name, value in writes.items():
        if value is None:
            store.delete(name)
        else:
            store.write(name, value)


def change_container(
    domain: str,
    changes: Mapping[str, Any],
    *,
    actor: str | None,
    store: NoustStore | None = None,
    secrets: SecretStore | None = None,
) -> ContainerChange:
    """
    Change how a container application is built and run.

    The one write of those settings for ``noust app container set`` and
    ``PUT /api/apps/{domain}/container`` alike. Nothing is written unless all
    of it is valid. Takes the application's lock, so a change cannot land in
    the middle of an update that is reading the settings.

    Args:
        domain: A validated domain.
        changes: The settings to change (see :data:`SETTINGS_FIELDS`), and the
            secrets ``registry_password``, ``build_secret_values`` (id to
            value, None removing one) and ``clear_registry``. What is not
            named stays as it is.
        actor: Who changed them, for the record.
        store: The store; the process-wide one when None.
        secrets: The secret store; the process-wide one when None.

    Returns:
        What changed.

    Raises:
        NoustError: The application is unknown or has no container settings.
        ValidationError: A value is not valid (the field says which), or the
            change moves between an image and a repository.
        AppBusyError: Another operation is running on the application.
    """
    target = store or get_store()
    app = _container_app(domain, target)
    containers = ContainerStore(target)
    with app_lock(domain, "container settings"):
        current = containers.get(domain)
        if current is None:
            raise NoustError(
                f"{domain} has no container settings",
                details="It was not deployed by a Noust that knows containers: redeploy it.",
            )
        updated, writes = _apply(current, changes, domain_to_app_name(domain))
        changed = _changed(current, updated, changes)
        _write_secrets(writes, secrets)
        containers.save(domain, updated, actor=actor)
        if updated.method == METHOD_IMAGE and updated.image and updated.image != app.source:
            # The application list shows what an image application runs.
            app.source = updated.image
            target.update_app(app)
    audit.record(
        "apps.container",
        target=f"app:{domain}",
        details={"changed": list(changed)},
    )
    return ContainerChange(settings=updated, changed=changed)


def _changed(
    before: ContainerSettings, after: ContainerSettings, changes: Mapping[str, Any]
) -> tuple[str, ...]:
    """
    Name what a change changed, for the audit trail: names, never values.

    Args:
        before: The settings before.
        after: The settings after.
        changes: What the client gave.

    Returns:
        The names of the settings that differ, then the secrets that were given.
    """
    names = [
        name
        for name in (*sorted(SETTINGS_FIELDS), "build_secrets")
        if getattr(before, name) != getattr(after, name)
    ]
    if changes.get("registry_password") is not None:
        names.append("registry_password")
    if changes.get("clear_registry"):
        names.append("registry_account_removed")
    return tuple(names)
