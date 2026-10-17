# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the store keeps of a container application, and the secrets kept apart from it.

A container application is built from a Dockerfile, built by Railpack, or an
image from a registry (decision D1 of the 3.4 design). How, and how it runs
(its port, its volumes, its restart policy, the account its registry knows it
by), is one row of ``app_container`` (schema v14, :mod:`noust.core.schema_v14`),
keyed by the application and removed with it. A table of its own and not
columns of ``apps``: a redeploy writes back the whole ``apps`` row it read when
it started, and this is what that cannot reach.

Every column has its own setter, and every setter validates through
:mod:`noust.validators.container` before it writes, so a value that could not
be an argument of ``docker`` never reaches the row. :meth:`ContainerStore.save`
validates the whole and the rules between fields (an image needs its reference,
a Dockerfile build has none).

**No secret is in the row.** The password of a registry account and the value
of a build secret are files of the secret store
(:class:`~noust.core.secrets.SecretStore`), 0600 and sealed with the rest on a
central; the row keeps only the name they are kept under, which is derived from
the application so a stale one cannot be read for another.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import ValidationError
from noust.validators import container as rules

if TYPE_CHECKING:
    from noust.core.secrets import SecretStore
    from noust.core.store import NoustStore

#: Where the process listens when the operator does not say, by method: what
#: Dockerfiles of web applications most often expose, what Railpack's own
#: images listen on, and what the image most often is (a web server).
DEFAULT_PORTS: dict[str, int] = {
    rules.METHOD_DOCKERFILE: 8080,
    rules.METHOD_RAILPACK: 3000,
    rules.METHOD_IMAGE: 80,
}

#: The namespace of the secret store this module owns.
SECRET_NAMESPACE = "container"  # noqa: S105 - a namespace, not a password


@dataclass(frozen=True)
class ContainerSettings:
    """
    How a container application is built and run.

    Attributes:
        method: ``dockerfile``, ``railpack`` or ``image``.
        image: The registry reference, for ``image``; None otherwise.
        dockerfile: The Dockerfile, relative to the repository; None finds
            ``Dockerfile`` or ``Containerfile`` at the root of the context.
        context: The build context, relative to the repository.
        target: The stage of a multi-stage Dockerfile, or None for the last.
        build_args: Non-secret build arguments. For Railpack, its variables.
        build_secrets: Ids of the secrets a build mounts; their values are in
            the secret store (:func:`build_secret_name`).
        container_port: Where the process listens in the container, or None
            for :data:`DEFAULT_PORTS`.
        volumes: Container paths kept in named volumes across deployments.
        restart_policy: Docker's, ``unless-stopped`` by default so the
            application comes back after a reboot of the host.
        registry_server: The registry the account is for; None for the image's.
        registry_username: The account.
        registry_secret: The name of the secret holding its password.
        digest: The digest the last deployment of a registry image resolved.
        entrypoint: The words that replace the image's ENTRYPOINT; None keeps the image's own,
            an empty tuple clears it.
        command: The words that replace the image's CMD (the arguments of the entrypoint); None
            keeps the image's own, an empty tuple clears it.
    """

    method: str = rules.METHOD_DOCKERFILE
    image: str | None = None
    dockerfile: str | None = None
    context: str = "."
    target: str | None = None
    build_args: dict[str, str] = field(default_factory=dict)
    build_secrets: tuple[str, ...] = ()
    container_port: int | None = None
    volumes: tuple[str, ...] = ()
    restart_policy: str = "unless-stopped"
    registry_server: str | None = None
    registry_username: str | None = None
    registry_secret: str | None = None
    digest: str | None = None
    entrypoint: tuple[str, ...] | None = None
    command: tuple[str, ...] | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ContainerSettings:
        """
        Build settings from the mapping a request or a job carries.

        Args:
            data: Field names to values, as :meth:`public` writes them or an
                API request gives them; lists are taken as tuples.

        Returns:
            The settings, not yet validated: :meth:`checked` does that.

        Raises:
            ValidationError: A key is not a setting.
        """
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValidationError(
                f"{unknown[0]!r} is not a container setting",
                details="The settings are: " + ", ".join(sorted(known)) + ".",
                field=unknown[0],
            )
        values = dict(data)
        for name in ("build_secrets", "volumes"):
            if name in values:
                values[name] = tuple(values[name] or ())
        for name in ("entrypoint", "command"):
            if values.get(name) is not None:
                values[name] = tuple(values[name])
        if "build_args" in values:
            values["build_args"] = dict(values["build_args"] or {})
        return cls(**values)

    @property
    def effective_port(self) -> int:
        """The port the process listens on in the container."""
        return self.container_port or DEFAULT_PORTS[self.method]

    @property
    def builds(self) -> bool:
        """Whether the image is built here (a Dockerfile or Railpack) and not pulled."""
        return self.method != rules.METHOD_IMAGE

    @property
    def has_account(self) -> bool:
        """Whether a registry account is recorded."""
        return bool(self.registry_username and self.registry_secret)

    def account_server(self) -> str | None:
        """
        Name the registry the account is for.

        Returns:
            The recorded server; for an image the registry it comes from when
            none is recorded; None without an account.
        """
        if not self.has_account:
            return None
        if self.registry_server:
            return self.registry_server
        return rules.registry_of(self.image) if self.image else None

    def checked(self) -> ContainerSettings:
        """
        Validate every field and the rules between them.

        Returns:
            These settings, unchanged, so a call can be chained.

        Raises:
            ValidationError: A value is not valid (``field`` names it), an
                image has no reference, a build has one, an image carries
                build settings, or the registry account is half given or is
                for another registry than the image's.
        """
        rules.check_method(self.method)
        rules.check_restart_policy(self.restart_policy)
        rules.check_repo_path(self.context, field="context")
        if self.dockerfile is not None:
            rules.check_repo_path(self.dockerfile, field="dockerfile")
        if self.target is not None:
            rules.check_target(self.target)
        rules.check_build_args(self.build_args)
        rules.check_secret_ids(self.build_secrets)
        rules.check_volumes(self.volumes)
        if self.container_port is not None:
            rules.check_container_port(self.container_port)
        if self.digest is not None:
            rules.check_digest(self.digest)
        for name in ("entrypoint", "command"):
            given = getattr(self, name)
            if given is not None:
                rules.check_command(given, field=name)
        self._check_method_fields()
        self._check_account()
        return self

    def _check_method_fields(self) -> None:
        """
        Check what each method takes.

        Raises:
            ValidationError: An image without a reference, a build with one,
                or settings the method has no use for.
        """
        if self.method == rules.METHOD_IMAGE:
            if not self.image:
                raise ValidationError(
                    "An application deployed from a registry needs its image",
                    details="Give it as name[:tag][@sha256:digest], such as ghcr.io/owner/app:1.4.",
                    field="image",
                )
            rules.check_image_reference(self.image)
            for name, given in (
                ("dockerfile", self.dockerfile),
                ("target", self.target),
                ("build_args", self.build_args),
                ("build_secrets", self.build_secrets),
                ("context", None if self.context == "." else self.context),
            ):
                if given:
                    raise ValidationError(
                        f"An image from a registry is not built, so it has no {name}",
                        details="Remove it, or choose a Dockerfile or Railpack build.",
                        field=name,
                    )
            return
        if self.image:
            raise ValidationError(
                "Only an application deployed from a registry has an image reference",
                details="A build produces its own image, named after the application.",
                field="image",
            )
        if self.method == rules.METHOD_RAILPACK:
            for name, given in (("dockerfile", self.dockerfile), ("target", self.target)):
                if given:
                    raise ValidationError(
                        f"Railpack builds without a Dockerfile, so it has no {name}",
                        details="Remove it, or build from a Dockerfile.",
                        field=name,
                    )
            # Railpack takes its variables and its secrets from the build's
            # environment, so each id is also the name of one.
            for secret_id in self.build_secrets:
                rules.check_railpack_secret_id(secret_id)
            clashing = sorted(set(self.build_secrets) & set(self.build_args))
            if clashing:
                raise ValidationError(
                    f"{clashing[0]} is both a build argument and a build secret",
                    details="Railpack reads both as environment variables, so a name is one or "
                    "the other.",
                    field="build_secrets",
                )

    def _check_account(self) -> None:
        """
        Check the registry account.

        Raises:
            ValidationError: Half of it, or a server that is not the image's.
        """
        given = (self.registry_server, self.registry_username, self.registry_secret)
        if not any(given):
            return
        if not self.registry_username:
            raise ValidationError(
                "A registry account needs its username",
                details="Give the account the registry knows you by.",
                field="registry_username",
            )
        if not self.registry_secret:
            raise ValidationError(
                "A registry account needs its password",
                details="Give it so it is kept in the secret store, not in the settings.",
                field="registry_secret",
            )
        rules.check_registry_username(self.registry_username)
        if self.registry_server is not None:
            rules.check_registry_server(self.registry_server)
        elif self.method != rules.METHOD_IMAGE:
            raise ValidationError(
                "A build needs to be told which registry its account is for",
                details="Give the registry, such as ghcr.io: a build may pull private base "
                "images from any of them.",
                field="registry_server",
            )
        if (
            self.method == rules.METHOD_IMAGE
            and self.registry_server is not None
            and self.image
            and self.registry_server != rules.registry_of(self.image)
        ):
            raise ValidationError(
                f"The account is for {self.registry_server}, and the image comes from "
                f"{rules.registry_of(self.image)}",
                details="An account is only sent to the registry it belongs to.",
                field="registry_server",
            )

    def public(self) -> dict[str, Any]:
        """
        Describe the settings for a client: everything, and no password.

        Returns:
            A JSON-ready mapping; the registry account is its server, its
            username and whether a password is kept.
        """
        return {
            "method": self.method,
            "image": self.image,
            "dockerfile": self.dockerfile,
            "context": self.context,
            "target": self.target,
            "build_args": dict(self.build_args),
            "build_secrets": list(self.build_secrets),
            "container_port": self.effective_port,
            "volumes": list(self.volumes),
            "restart_policy": self.restart_policy,
            "registry_server": self.account_server(),
            "registry_username": self.registry_username,
            "registry_password_set": self.has_account,
            "digest": self.digest,
            "entrypoint": list(self.entrypoint) if self.entrypoint is not None else None,
            "command": list(self.command) if self.command is not None else None,
        }


def registry_secret_name(app_name: str) -> str:
    """
    Name the secret that holds an application's registry password.

    Args:
        app_name: The application's name (``domain_to_app_name``).

    Returns:
        ``container/<app>/registry``.

    Raises:
        ValidationError: The name is not one the secret store accepts.
    """
    return _secret_name(app_name, "registry")


def build_secret_name(app_name: str, secret_id: str) -> str:
    """
    Name the secret that holds one build secret of an application.

    Args:
        app_name: The application's name.
        secret_id: The id the Dockerfile asks for.

    Returns:
        ``container/<app>/build/<id>``.

    Raises:
        ValidationError: The id is not a valid build secret id, or the name
            is not one the secret store accepts.
    """
    rules.check_secret_ids([secret_id])
    return _secret_name(app_name, "build", secret_id)


def _secret_name(app_name: str, *parts: str) -> str:
    """
    Join and check the segments of a secret name.

    Args:
        app_name: The application's name.
        parts: What follows it.

    Returns:
        The name.

    Raises:
        ValidationError: A segment is not a simple name.
    """
    from noust.core.exceptions import ConfigError
    from noust.core.secrets import _checked_name

    name = "/".join([SECRET_NAMESPACE, app_name, *parts])
    try:
        _checked_name(name)
    except ConfigError as exc:
        raise ValidationError(
            f"{name!r} is not a name the secret store keeps", details=exc.details
        ) from exc
    return name


def forget_secrets(app_name: str, secrets: SecretStore | None = None) -> None:
    """
    Remove an application's registry password and build secrets.

    Called when the application is deleted, and when a failed first deployment
    is undone: nothing of the account outlives the application that used it.

    Args:
        app_name: The application's name.
        secrets: The secret store; the process-wide one when None.
    """
    from noust.core.secrets import SecretStore

    (secrets or SecretStore()).delete_namespace(f"{SECRET_NAMESPACE}/{app_name}")


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 in UTC.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _decode_json(raw: Any, default: Any) -> Any:
    """
    Read a JSON column.

    Args:
        raw: The column's value.
        default: What a missing or damaged value reads as: a damaged column
            must not stop the application from loading.

    Returns:
        The decoded value, or the default.
    """
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default
    return value if isinstance(value, type(default)) else default


def _decode_words(raw: Any) -> tuple[str, ...] | None:
    """
    Read a JSON column that holds a list of words, or nothing.

    Args:
        raw: The column's value.

    Returns:
        The words; None for a column that holds none (the image's own) or a damaged one.
    """
    if raw is None:
        return None
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return tuple(str(word) for word in decoded) if isinstance(decoded, list) else None


def _from_row(row: sqlite3.Row) -> ContainerSettings:
    """
    Build the settings a row holds.

    Args:
        row: A row of ``app_container``.

    Returns:
        The settings.
    """
    build_args = _decode_json(row["build_args"], {})
    return ContainerSettings(
        method=row["method"],
        image=row["image"],
        dockerfile=row["dockerfile"],
        context=row["context"] or ".",
        target=row["target"],
        build_args={str(k): str(v) for k, v in build_args.items()},
        build_secrets=tuple(str(i) for i in _decode_json(row["build_secrets"], [])),
        container_port=row["container_port"],
        volumes=tuple(str(v) for v in _decode_json(row["volumes"], [])),
        restart_policy=row["restart_policy"] or "unless-stopped",
        registry_server=row["registry_server"],
        registry_username=row["registry_username"],
        registry_secret=row["registry_secret"],
        digest=row["digest"],
        entrypoint=_decode_words(row["entrypoint"]),
        command=_decode_words(row["command"]),
    )


class ContainerStore:
    """The settings of container applications, in the store's own connection."""

    def __init__(self, store: NoustStore | None = None) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
        """
        if store is None:
            from noust.core.store import get_store

            store = get_store()
        self.store = store

    def get(self, domain: str) -> ContainerSettings | None:
        """
        Read an application's container settings.

        Args:
            domain: The application's domain.

        Returns:
            The settings, or None when the application has none (it is not a
            container application, or does not exist).
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT c.* FROM app_container c JOIN apps a ON a.id = c.app_id WHERE a.domain = ?",
                (domain,),
            )
            row = cursor.fetchone()
            return _from_row(row) if row is not None else None

    def save(self, domain: str, settings: ContainerSettings, *, actor: str | None) -> bool:
        """
        Store an application's container settings whole, replacing any before.

        Args:
            domain: The application's domain.
            settings: The settings; validated here.
            actor: Who changed them, for the record.

        Returns:
            True when the application exists and the settings were stored.

        Raises:
            ValidationError: See :meth:`ContainerSettings.checked`; nothing is
                written.
        """
        settings.checked()
        with self.store._transaction() as cursor:
            cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
            found = cursor.fetchone()
            if found is None:
                return False
            cursor.execute(
                "INSERT INTO app_container (app_id, method, image, dockerfile, context, target, "
                "build_args, build_secrets, container_port, volumes, restart_policy, "
                "registry_server, registry_username, registry_secret, digest, entrypoint, "
                "command, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(app_id) DO UPDATE SET method = excluded.method, "
                "image = excluded.image, dockerfile = excluded.dockerfile, "
                "context = excluded.context, target = excluded.target, "
                "build_args = excluded.build_args, build_secrets = excluded.build_secrets, "
                "container_port = excluded.container_port, volumes = excluded.volumes, "
                "restart_policy = excluded.restart_policy, "
                "registry_server = excluded.registry_server, "
                "registry_username = excluded.registry_username, "
                "registry_secret = excluded.registry_secret, digest = excluded.digest, "
                "entrypoint = excluded.entrypoint, command = excluded.command, "
                "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                (
                    found[0],
                    settings.method,
                    settings.image,
                    settings.dockerfile,
                    settings.context,
                    settings.target,
                    json.dumps(settings.build_args, sort_keys=True),
                    json.dumps(list(settings.build_secrets)),
                    settings.container_port,
                    json.dumps(list(settings.volumes)),
                    settings.restart_policy,
                    settings.registry_server,
                    settings.registry_username,
                    settings.registry_secret,
                    settings.digest,
                    None if settings.entrypoint is None else json.dumps(list(settings.entrypoint)),
                    None if settings.command is None else json.dumps(list(settings.command)),
                    actor,
                    _now(),
                ),
            )
            return True

    def _set(self, domain: str, column: str, value: Any, *, actor: str | None = None) -> bool:
        """
        Write one column of an application's settings.

        Args:
            domain: The application's domain.
            column: One of the table's columns; code, never input.
            value: The already validated value.
            actor: Who changed it, when that is known.

        Returns:
            True when the application has settings and the column was written.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                # The column is one of this module's own setters', never input.
                f"UPDATE app_container SET {column} = ?, updated_by = COALESCE(?, updated_by), "  # noqa: S608
                "updated_at = ? WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
                (value, actor, _now(), domain),
            )
            return cursor.rowcount > 0

    def _changed(self, domain: str, **fields: Any) -> ContainerSettings:
        """
        Build the settings a change would leave, so the rules between fields hold.

        Args:
            domain: The application's domain.
            fields: The fields changed.

        Returns:
            The settings with the change applied; the defaults when the
            application has none yet.
        """
        return replace(self.get(domain) or ContainerSettings(), **fields)

    def set_method(self, domain: str, method: str) -> bool:
        """
        Store how the image comes to exist.

        Args:
            domain: The application's domain.
            method: ``dockerfile``, ``railpack`` or ``image``.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Not a method.
        """
        return self._set(domain, "method", rules.check_method(method))

    def set_image(self, domain: str, image: str | None) -> bool:
        """
        Store the registry reference of an image application.

        Args:
            domain: The application's domain.
            image: ``name[:tag][@sha256:digest]``, or None.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Not an image reference.
        """
        return self._set(
            domain, "image", None if image is None else rules.check_image_reference(image)
        )

    def set_dockerfile(self, domain: str, path: str | None) -> bool:
        """
        Store the Dockerfile of a build, relative to the repository.

        Args:
            domain: The application's domain.
            path: The path, or None to look for ``Dockerfile`` or ``Containerfile``.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Not a path inside the repository.
        """
        return self._set(
            domain,
            "dockerfile",
            None if path is None else rules.check_repo_path(path, field="dockerfile"),
        )

    def set_context(self, domain: str, path: str) -> bool:
        """
        Store the build context, relative to the repository.

        Args:
            domain: The application's domain.
            path: The directory; ``.`` is the repository itself.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Not a path inside the repository.
        """
        return self._set(domain, "context", rules.check_repo_path(path, field="context"))

    def set_target(self, domain: str, target: str | None) -> bool:
        """
        Store the stage of a multi-stage Dockerfile.

        Args:
            domain: The application's domain.
            target: The stage, or None for the last.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Not a stage name.
        """
        return self._set(domain, "target", None if target is None else rules.check_target(target))

    def set_build_args(self, domain: str, args: dict[str, str]) -> bool:
        """
        Store the build arguments.

        Args:
            domain: The application's domain.
            args: Name to value; none of them a secret.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: A name or a value that cannot be a build argument.
        """
        return self._set(
            domain, "build_args", json.dumps(rules.check_build_args(args), sort_keys=True)
        )

    def set_build_secrets(self, domain: str, ids: list[str] | tuple[str, ...]) -> bool:
        """
        Store the ids of the secrets a build mounts.

        Args:
            domain: The application's domain.
            ids: The ids; their values are in the secret store.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Repeated, or not valid ids.
        """
        return self._set(domain, "build_secrets", json.dumps(list(rules.check_secret_ids(ids))))

    def set_container_port(self, domain: str, port: int | None) -> bool:
        """
        Store where the process listens in the container.

        Args:
            domain: The application's domain.
            port: The port, or None for the method's default.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Not a port.
        """
        return self._set(
            domain, "container_port", None if port is None else rules.check_container_port(port)
        )

    def set_volumes(self, domain: str, paths: list[str] | tuple[str, ...]) -> bool:
        """
        Store the container paths that keep their data across deployments.

        Args:
            domain: The application's domain.
            paths: Absolute paths in the container.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: A path that cannot be a volume.
        """
        return self._set(domain, "volumes", json.dumps(list(rules.check_volumes(paths))))

    def set_restart_policy(self, domain: str, policy: str) -> bool:
        """
        Store Docker's restart policy.

        Args:
            domain: The application's domain.
            policy: One of :data:`noust.validators.container.RESTART_POLICIES`.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Not a policy.
        """
        return self._set(domain, "restart_policy", rules.check_restart_policy(policy))

    def set_digest(self, domain: str, digest: str | None) -> bool:
        """
        Store the digest the last deployment of a registry image resolved.

        Args:
            domain: The application's domain.
            digest: The digest, or None.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Not a digest.
        """
        return self._set(domain, "digest", None if digest is None else rules.check_digest(digest))

    def set_command(self, domain: str, words: list[str] | tuple[str, ...] | None) -> bool:
        """
        Store the words that replace the image's CMD.

        Args:
            domain: The application's domain.
            words: The arguments of the entrypoint; an empty list clears the image's own;
                None keeps the image's.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Not a list of words a process can be given.
        """
        return self._set(
            domain,
            "command",
            None
            if words is None
            else json.dumps(list(rules.check_command(words, field="command"))),
        )

    def set_entrypoint(self, domain: str, words: list[str] | tuple[str, ...] | None) -> bool:
        """
        Store the words that replace the image's ENTRYPOINT.

        Args:
            domain: The application's domain.
            words: The program and its leading arguments; an empty list clears the image's
                own; None keeps the image's.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Not a list of words a process can be given.
        """
        return self._set(
            domain,
            "entrypoint",
            None
            if words is None
            else json.dumps(list(rules.check_command(words, field="entrypoint"))),
        )

    def set_registry(
        self, domain: str, server: str | None, username: str | None, secret: str | None
    ) -> bool:
        """
        Store the registry account, as a whole, or remove it.

        Args:
            domain: The application's domain.
            server: The registry; None for the image's own.
            username: The account.
            secret: The name of the secret that holds its password.

        Returns:
            True when the application has settings and they were written.

        Raises:
            ValidationError: Half an account, or one that is not valid.
        """
        changed = self._changed(
            domain, registry_server=server, registry_username=username, registry_secret=secret
        )
        changed._check_account()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE app_container SET registry_server = ?, registry_username = ?, "
                "registry_secret = ?, updated_at = ? "
                "WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
                (server, username, secret, _now(), domain),
            )
            return cursor.rowcount > 0
