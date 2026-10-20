# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db tag`` and ``noust service tag``: free labels on databases and on systemd services.

    noust db tag add shop --engine postgresql prod customer-a
    noust db tag list --tag prod
    noust service tag add worker prod
    noust app tag restart prod

The labels are :class:`~noust.core.resource_tags.ResourceTags`, which the console's API calls
too; the same two groups serve both kinds because only how a resource is found differs. A
database is found through the database service, so one that is not there is refused, and a
service through the service manager. ``noust app tag restart`` is the one command that acts on
a tag, over applications and services alike.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.app_tags import validate_tag
from noust.core.audit import record as audit_record
from noust.core.exceptions import NoustError
from noust.core.resource_tags import KIND_DATABASE, KIND_SERVICE, ResourceTags, database_key

#: How a command turns what the operator typed into the key a resource is tagged by.
Resolver = Callable[[str, str | None], str]


def database_resolver(name: str, engine: str | None) -> str:
    """
    Find a database and name it the way its tags are keyed.

    Args:
        name: The database.
        engine: The engine it is on.

    Returns:
        ``engine/name``, as the listing names the engine.

    Raises:
        NoustError: No engine was given, or the database is not there.
    """
    from noust.managers.database.service import DatabaseService

    if not engine:
        raise NoustError("Say which engine the database is on", details="Use --engine NAME.")
    view = DatabaseService().get(engine, name)
    return database_key(view.engine, view.name)


def service_resolver(name: str, engine: str | None) -> str:
    """
    Find a service and name it the way its tags are keyed.

    Args:
        name: The unit.
        engine: Not used.

    Returns:
        The unit's name as the listing shows it.

    Raises:
        NoustError: The name is not a safe unit name, or there is no such unit.
    """
    from noust.managers.service_manager import ServiceManager
    from noust.validators.names import validate_service_name

    wanted = validate_service_name(name).removesuffix(".service")
    info = ServiceManager(verbose=False).inspect_unit(wanted)
    if not info.exists:
        raise NoustError(f"No service named {wanted}", details="See: noust service list")
    return str(info.unit)


def _engine_option(func: Callable[..., None]) -> Callable[..., None]:
    """
    Add ``--engine`` to a database command.

    Args:
        func: The command's callback.

    Returns:
        It, with the option.
    """
    return click.option(
        "--engine", "-e", default=None, help="The engine the database is on (postgresql, ...)."
    )(func)


def build_group(
    kind: str, noun: str, resolve: Resolver, *, event: str, with_engine: bool
) -> click.Group:
    """
    Build the ``tag`` group of one kind of resource.

    Args:
        kind: ``database`` or ``service``.
        noun: How the resource is called in messages.
        resolve: Finds the resource and returns its key.
        event: The audit event a change records.
        with_engine: Whether commands take ``--engine`` (databases).

    Returns:
        The group.
    """

    @click.group("tag", cls=NoustGroup, help=f"Free labels on {noun}s, to find them by.")
    def group() -> None:
        pass

    def decorate(func: Callable[..., None]) -> Callable[..., None]:
        return _engine_option(func) if with_engine else func

    def changed(ctx: Context, key: str, tags: list[str]) -> None:
        audit_record(event, target=f"{kind}:{key}", details={"tags": tags})
        ctx.logger.success(f"{key}: {', '.join(tags) if tags else 'no tags'}")

    @group.command("list", read_only=True, help=f"List the tags of a {noun}, or every tagged one.")
    @click.argument("name", required=False)
    @click.option("--tag", "wanted", default=None, help=f"Only the {noun}s with this tag.")
    @decorate
    @json_option("Print the tags as JSON.")
    @pass_context
    def list_command(
        ctx: Context, name: str | None, wanted: str | None, engine: str | None = None
    ) -> None:
        if name:
            key = resolve(name, engine)
            rows = {key: ResourceTags().of(kind, key)}
        else:
            rows = ResourceTags().by_resource(kind)
        if wanted is not None:
            needle = validate_tag(wanted)
            rows = {k: t for k, t in rows.items() if needle in t}
        if ctx.json_output:
            click.echo(json.dumps({f"{kind}s": rows}))
            return
        if not rows:
            ctx.logger.info(
                f"No {noun} carries a tag" if wanted is None else f"Nothing has the tag {wanted}"
            )
            return
        ctx.logger.table(
            [noun.capitalize(), "Tags"], [[k, ", ".join(t) or "-"] for k, t in sorted(rows.items())]
        )

    @group.command("add", help=f"Give a {noun} more tags.")
    @click.argument("name")
    @click.argument("tags", nargs=-1, required=True)
    @decorate
    @pass_context
    def add_command(
        ctx: Context, name: str, tags: tuple[str, ...], engine: str | None = None
    ) -> None:
        key = resolve(name, engine)
        changed(ctx, key, ResourceTags().add(kind, key, tags))

    @group.command("remove", help=f"Take tags off a {noun}.")
    @click.argument("name")
    @click.argument("tags", nargs=-1, required=True)
    @decorate
    @pass_context
    def remove_command(
        ctx: Context, name: str, tags: tuple[str, ...], engine: str | None = None
    ) -> None:
        key = resolve(name, engine)
        changed(ctx, key, ResourceTags().remove(kind, key, tags))

    @group.command("set", help=f"Make a {noun} carry exactly these tags (none clears them).")
    @click.argument("name")
    @click.argument("tags", nargs=-1)
    @decorate
    @pass_context
    def set_command(
        ctx: Context, name: str, tags: tuple[str, ...], engine: str | None = None
    ) -> None:
        key = resolve(name, engine)
        changed(ctx, key, ResourceTags().set(kind, key, tags))

    return group


def database_tag_group() -> click.Group:
    """
    Returns:
        ``noust db tag``.
    """
    return build_group(
        KIND_DATABASE, "database", database_resolver, event="databases.tags.set", with_engine=True
    )


def service_tag_group() -> click.Group:
    """
    Returns:
        ``noust service tag``.
    """
    return build_group(
        KIND_SERVICE, "service", service_resolver, event="services.tags.set", with_engine=False
    )
