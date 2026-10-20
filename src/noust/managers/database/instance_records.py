# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the store's instances meet the containers Docker runs.

An instance Noust runs is a row of the store and a container Docker describes,
and neither says it all: the row knows the image it was pinned to and the memory
it was given, Docker knows whether the container exists and what it listens on.
This module joins them into the one kind of object every database manager
already drives, a :class:`~noust.managers.database.instances.DatabaseInstance`,
so an instance Noust made is listed, browsed, dumped, restored, imported into
and made public by exactly the code that does it for a container Docker
Compose runs. Nothing is duplicated, and nothing is special-cased: the instance
is addressed by a key (``postgresql@pg16``), and the managers only ever see the
key and the bound instance.

Three rules decide what discovery makes of the join:

- **The row is the authority.** A container is Noust's instance when a row of
  that name exists and the container is the one named after it; a container
  that merely carries the label is a stranger, which keeps its own key.
- **A row with no container is a stopped instance**, not a missing one: stopping
  the unit removes the container (``docker compose down``), and the instance
  must still be listed, addressed and started. Its port and its credentials are
  the row's, which is why an instance that is down answers the questions that
  need no container (where it listens, which engine, how much memory).
- **An instance that is not finished is not offered.** While a row says
  ``creating`` or ``removing`` its container exists and cannot be signed in to
  yet, or no more; no application is given a database on it until it is ready.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from noust.core.db_instance_store import READY, UPGRADING, InstanceRecord
from noust.core.exceptions import ValidationError
from noust.managers.database import instance_layout as layout
from noust.managers.database.instance_catalog import spec_for
from noust.managers.database.instance_compose import (
    CLICKHOUSE_ADMIN,
    MONGO_ADMIN,
    PASSWORD_VARIABLES,
)
from noust.managers.database.instances import (
    DatabaseInstance,
    PublishedPort,
    catalog_engine,
    image_engine,
    instance_key,
)

__all__ = ["USABLE_STATES", "assign_managed", "key_of", "stub_instance"]

#: The states in which an instance is offered to the rest of Noust. An upgrade
#: leaves it in use: the applications are not told to wait, the upgrade's own
#: health gate puts the old image back if the new one does not come up.
USABLE_STATES = frozenset({READY, UPGRADING})

#: The loopback address an instance is published on.
LOOPBACK = "127.0.0.1"


def key_of(record: InstanceRecord) -> str:
    """
    Name the key an instance is addressed by.

    Args:
        record: The instance's row.

    Returns:
        ``<engine>@<name>``, such as ``postgresql@pg16``: what goes wherever an
        engine name goes.

    Raises:
        ValidationError: The flavour is not one Noust runs.
    """
    return instance_key(spec_for(record.flavour).engine, container=record.name)


def stub_instance(record: InstanceRecord, *, state: str = "exited") -> DatabaseInstance:
    """
    Describe an instance from its row alone, for when Docker has no container for it.

    The environment's variable *names* and the non-secret settings are what a
    discovered container would have reported, so that the bound manager signs
    in the way it does for any container: the superuser's password is read
    inside the container, from the variable the row's flavour makes it with.

    Args:
        record: The instance's row.
        state: Docker's state word to report: ``exited`` for an instance that
            is down, ``running`` to bind a manager to one that was just made.

    Returns:
        The instance.

    Raises:
        ValidationError: The flavour is not one Noust runs.
    """
    spec = spec_for(record.flavour)
    match = image_engine(record.reference)
    if match is None and record.custom_image:
        # An image the operator chose has any name; the row says what it was made as.
        match = catalog_engine(record.flavour, record.reference)
    if match is None:
        # The catalog's images are all recognised (a test holds it to that); a
        # row edited by hand to name another is not a database Noust can drive.
        raise ValidationError(
            f"{record.reference} is not an image Noust recognises as {spec.display_name}",
            details="The instance's row names an image that is not one the catalog offers.",
            field="image",
        )
    settings = {
        "mongodb": {"MONGO_INITDB_ROOT_USERNAME": MONGO_ADMIN},
        "clickhouse": {"CLICKHOUSE_USER": CLICKHOUSE_ADMIN},
    }.get(record.flavour, {})
    return DatabaseInstance(
        key=key_of(record),
        engine=spec.engine,
        flavour=match.flavour,
        container=layout.container_name(record.name),
        container_id="",
        image=record.reference,
        project=None,
        service=None,
        state=state,
        env_names=frozenset({PASSWORD_VARIABLES[record.flavour]}),
        settings=settings,
        published=(PublishedPort(spec.container_port, LOOPBACK, record.host_port),),
        exposed=(spec.container_port,),
        official=match.official,
        unit=layout.unit_name(record.name),
        instance_label=record.name,
        managed=record.name,
        version=record.version,
        memory_limit_mb=record.memory_limit_mb,
        tls_mode=record.tls_mode,
    )


def _claimed(found: DatabaseInstance, record: InstanceRecord) -> DatabaseInstance:
    """
    Make a container Docker described the instance a row says it is.

    Args:
        found: The container, as discovery described it.
        record: The instance's row.

    Returns:
        The container under the instance's key, with what the row knows. It is
        no Compose project's: the key, not a project, addresses it, and nothing
        of a project's application belongs to it.
    """
    return replace(
        found,
        key=key_of(record),
        project=None,
        service=None,
        app=None,
        unit=layout.unit_name(record.name),
        managed=record.name,
        version=record.version,
        memory_limit_mb=record.memory_limit_mb,
        tls_mode=record.tls_mode,
    )


def assign_managed(
    instances: Iterable[DatabaseInstance], records: Iterable[InstanceRecord]
) -> list[DatabaseInstance]:
    """
    Say which of the containers are the instances Noust runs, and add those that are down.

    Args:
        instances: What discovery found on Docker.
        records: The store's rows.

    Returns:
        Every container that is not an instance's, untouched and sorted with the
        rest by key; then each usable instance, from its container when there is
        one and from its row when there is none. A container of an instance that
        is being made or removed is left out, and so is a stranger that holds an
        instance's key.
    """
    rows = {record.name: record for record in records}
    claimed: dict[str, DatabaseInstance] = {}
    strangers: list[DatabaseInstance] = []
    for found in instances:
        record = rows.get(found.instance_label or "")
        if record is not None and found.container == layout.container_name(record.name):
            if record.state in USABLE_STATES:
                claimed[record.name] = _claimed(found, record)
            continue
        strangers.append(found)
    for record in rows.values():
        if record.state in USABLE_STATES and record.name not in claimed:
            claimed[record.name] = stub_instance(record)
    taken = {instance.key for instance in claimed.values()}
    kept = [found for found in strangers if found.key not in taken]
    return sorted([*kept, *claimed.values()], key=lambda item: item.key)
