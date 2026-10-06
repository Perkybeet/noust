# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which database engines, in which versions, Noust runs as containers of its own.

The host's PostgreSQL, MySQL, Redis and MongoDB are what the server's packages
give. An instance is another one beside them, from the engine's **official
image**, and this module is the one table of what Noust offers (rule 3): the
repository of each engine, the versions it offers and, for each, the digest the
tag pointed at when it was written.

**An image is pinned by tag and by digest.** The tag says what an operator
chose (PostgreSQL ``17``); the digest says which bytes that is, so an instance
made today and one made in a year from the same row are the same software until
an upgrade is asked for (:meth:`ManagedInstances.upgrade`). A digest is the one
of the image *index*, which is what ``image: repo:tag@sha256:...`` pins on a
multi-architecture image. None is ever typed from memory: ``scripts/database_pins.py
check`` reads each from its registry and fails when the table differs, and the
tests refuse a digest that is not shaped like one.

The tables are what the registries served on 2026-10-06. A version added
upstream later is not offered until it is added here, which is the safe
direction; one that is withdrawn upstream keeps working from its digest until
it is upgraded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import ValidationError
from noust.managers.database.eol import support_notice
from noust.managers.database.flavours import FLAVOURS

#: When the digests below were read from the registries.
PINNED_ON = "2026-10-06"


@dataclass(frozen=True)
class ImagePin:
    """
    One version Noust offers, as the registry served it.

    Attributes:
        version: The tag, a major such as ``17`` or ``8.4``.
        digest: The digest of the tag's image index.
        default: The version offered when none is asked for.
    """

    version: str
    digest: str
    default: bool = False


@dataclass(frozen=True)
class EngineSpec:
    """
    What Noust knows of running one flavour from its image.

    Attributes:
        flavour: Its name, a key of
            :data:`~noust.managers.database.flavours.FLAVOURS`.
        engine: The manager that speaks it (``mysql`` for MariaDB).
        repository: The image's repository on Docker Hub.
        container_port: The port the engine listens on in the image.
        min_memory_mb: The least memory it starts and answers in.
        default_memory_mb: What an interface suggests when asking for the limit.
        pins: The versions it is offered in, oldest first.
        data_path: Where the image keeps its data, by version: the volume goes
            here. PostgreSQL 18 moved it.
        extra_volumes: Container paths the image declares as volumes besides the
            data, which get a named volume too so Docker leaves no anonymous one
            behind each time the container is recreated.
    """

    flavour: str
    engine: str
    repository: str
    container_port: int
    min_memory_mb: int
    default_memory_mb: int
    pins: tuple[ImagePin, ...]
    data_path: Callable[[str], str]
    extra_volumes: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        """How the flavour is named to a person."""
        return FLAVOURS[self.flavour].display_name

    def default_pin(self) -> ImagePin:
        """
        Returns:
            The version offered when none is asked for.
        """
        return next((pin for pin in self.pins if pin.default), self.pins[-1])


def _postgres_data(version: str) -> str:
    """
    Args:
        version: A PostgreSQL major.

    Returns:
        Where the image keeps its data: from 18 the image's volume is the parent
        of the version's directory, so that one major can be upgraded in place.
    """
    major = int(version.split(".")[0])
    return "/var/lib/postgresql" if major >= 18 else "/var/lib/postgresql/data"


def _fixed(path: str) -> Callable[[str], str]:
    """
    Args:
        path: A container path.

    Returns:
        A data-path function that answers it for every version.
    """
    return lambda _version: path


#: Every flavour an instance can run, from the engine's official image.
SPECS: dict[str, EngineSpec] = {
    spec.flavour: spec
    for spec in (
        EngineSpec(
            flavour="postgresql",
            engine="postgresql",
            repository="postgres",
            container_port=5432,
            min_memory_mb=256,
            default_memory_mb=512,
            pins=(
                ImagePin(
                    "15", "sha256:7e2070cf6ad06fb3cbbd141b1bafbb7fd5bb63e2b6001e6daf34448eb555e4b3"
                ),
                ImagePin(
                    "16", "sha256:65b16a8b326e0cfbdf33fa7e783f2a0cb352a61448616ccccfd616ef42aa0f65"
                ),
                ImagePin(
                    "17", "sha256:ae69c452f483507a6b99fb654cf93aad7fe156ffd2c56247707eef4e36d3c12b"
                ),
                ImagePin(
                    "18",
                    "sha256:fc973eb97c9fd04bfa1840e0f510719a584ccb3be8debfe6a4144637a9dfe8cf",
                    default=True,
                ),
            ),
            data_path=_postgres_data,
        ),
        EngineSpec(
            flavour="mysql",
            engine="mysql",
            repository="mysql",
            container_port=3306,
            min_memory_mb=512,
            default_memory_mb=1024,
            pins=(
                ImagePin(
                    "8.4",
                    "sha256:6ea90827b1100f8f2ae306a539f86d2c264a26ed435a2a9f75551dd5c3aeb242",
                    default=True,
                ),
                ImagePin(
                    "9.7", "sha256:e2bde46db6563855d7177adb5f0b57b9dc663f5a20927a90f4259d3312068497"
                ),
            ),
            data_path=_fixed("/var/lib/mysql"),
        ),
        EngineSpec(
            flavour="mariadb",
            engine="mysql",
            repository="mariadb",
            container_port=3306,
            min_memory_mb=256,
            default_memory_mb=512,
            pins=(
                ImagePin(
                    "10.11",
                    "sha256:7db29378d4fdab73f8123bbc2b48905c90d1a4b00cf848b028f1e81e623257f2",
                ),
                ImagePin(
                    "11.4",
                    "sha256:1292844148b311e4ed4300022a996d39083f415a963e970cf47cad1b3b18e3a6",
                ),
                ImagePin(
                    "11.8",
                    "sha256:6422478cb8e159f080fb1d8ccf65101e26fe51385787fde7d16c3b165a331f15",
                    default=True,
                ),
            ),
            data_path=_fixed("/var/lib/mysql"),
        ),
        EngineSpec(
            flavour="redis",
            engine="redis",
            repository="redis",
            container_port=6379,
            min_memory_mb=64,
            default_memory_mb=256,
            pins=(
                ImagePin(
                    "7.2", "sha256:46f15cf64fdef63e483dcda99e74e3d51c017b1175547b6bb874152ee2391aad"
                ),
                ImagePin(
                    "7.4", "sha256:4fa24486b8bcca8eec45ee0eb166edc674795e53a2b53d1a9ef263eecebaac85"
                ),
                ImagePin(
                    "8.0",
                    "sha256:ae471bdc20de180beed36e347d170ec0bdf8faa327959ea1f24692f22b05b955",
                    default=True,
                ),
                ImagePin(
                    "8.4", "sha256:b595f965375098bb2aa039d5534f4532d21852ce00e27fde591f9b6c96ef0a28"
                ),
            ),
            data_path=_fixed("/data"),
        ),
        EngineSpec(
            flavour="valkey",
            engine="redis",
            repository="valkey/valkey",
            container_port=6379,
            min_memory_mb=64,
            default_memory_mb=256,
            pins=(
                ImagePin(
                    "7.2", "sha256:ef0f9fb533b1f06fb7aba6478d758ca1c077d49e2bcb2b4365a4ea7f50b6d42e"
                ),
                ImagePin(
                    "8",
                    "sha256:640c5e62cea04b6d6f2084232651d0cc70362d31f4f805e7be94dbed6855e8f2",
                    default=True,
                ),
                ImagePin(
                    "9", "sha256:418652cfb58ef879d4978c33553735d7147016032d5aefaa14c828e611eb9dfd"
                ),
            ),
            data_path=_fixed("/data"),
        ),
        EngineSpec(
            flavour="mongodb",
            engine="mongodb",
            repository="mongo",
            container_port=27017,
            min_memory_mb=512,
            default_memory_mb=1024,
            pins=(
                ImagePin(
                    "7.0", "sha256:1f995ad6fdb93244a1addab1b58f934a0bc2f5643c38e02f5e9d7f0c7d227a7b"
                ),
                ImagePin(
                    "8.0",
                    "sha256:d0d926f94df099bff534b7ee5b5986458131a22489dfff8664509af0c1e2ca9c",
                    default=True,
                ),
            ),
            data_path=_fixed("/data/db"),
            extra_volumes=("/data/configdb",),
        ),
    )
}

#: Names an operator may type for a flavour besides its own.
ALIASES: dict[str, str] = {
    "postgres": "postgresql",
    "pg": "postgresql",
    "maria": "mariadb",
    "mongo": "mongodb",
}


def spec_for(flavour: str) -> EngineSpec:
    """
    Find what Noust knows of running a flavour.

    Args:
        flavour: A flavour or an alias (``pg``, ``mongo``).

    Returns:
        Its spec.

    Raises:
        ValidationError: Noust runs no such engine from an image; the error
            lists the ones it does.
    """
    name = ALIASES.get(flavour.strip().lower(), flavour.strip().lower())
    spec = SPECS.get(name)
    if spec is None:
        raise ValidationError(
            f"Noust does not run {flavour!r} as a container",
            details=f"Choose one of: {', '.join(SPECS)}.",
            field="engine",
        )
    return spec


def resolve_pin(spec: EngineSpec, version: str | None) -> ImagePin:
    """
    Decide which image a version asked for is.

    The version is a prefix by components, as everywhere a version is typed:
    ``17`` is PostgreSQL 17, ``8`` is MySQL 8.4 (the one 8 there is), and ``11``
    is ambiguous between MariaDB's 11.4 and 11.8, so it is refused with both.

    Args:
        spec: The engine.
        version: The version asked for; None for the default.

    Returns:
        The pin.

    Raises:
        ValidationError: The version is not offered, or is ambiguous.
    """
    if version is None or not version.strip():
        return spec.default_pin()
    wanted = version.strip().split(".")
    exact = [pin for pin in spec.pins if pin.version == version.strip()]
    if exact:
        return exact[0]
    matching = [pin for pin in spec.pins if pin.version.split(".")[: len(wanted)] == wanted]
    if len(matching) == 1:
        return matching[0]
    offered = ", ".join(pin.version for pin in spec.pins)
    if matching:
        raise ValidationError(
            f"{spec.display_name} {version} is ambiguous",
            details=f"It could be {', '.join(pin.version for pin in matching)}: name one.",
            field="version",
        )
    raise ValidationError(
        f"Noust does not offer {spec.display_name} {version} as a container",
        details=f"Versions it offers: {offered}.",
        field="version",
    )


def pin_of(spec: EngineSpec, version: str) -> ImagePin | None:
    """
    Find the pin of a version exactly.

    Args:
        spec: The engine.
        version: A tag, as an instance's row records it.

    Returns:
        The pin, or None when the catalog no longer offers that version (an
        instance made from it still runs from its own row's digest).
    """
    return next((pin for pin in spec.pins if pin.version == version), None)


def offered() -> list[dict[str, Any]]:
    """
    List what Noust can run as an instance, for the API and the CLI.

    Returns:
        One entry per flavour: its name, engine, display name, image
        repository, minimum and suggested memory, and each version with its
        digest, whether it is the default and, where the project publishes one,
        when its upstream support ends.
    """
    entries: list[dict[str, Any]] = []
    for spec in SPECS.values():
        versions = []
        for pin in spec.pins:
            notice = support_notice(spec.flavour, pin.version)
            versions.append(
                {
                    "version": pin.version,
                    "digest": pin.digest,
                    "default": pin.default,
                    "end_of_life": notice.end_of_life,
                }
            )
        entries.append(
            {
                "flavour": spec.flavour,
                "engine": spec.engine,
                "display_name": spec.display_name,
                "image": spec.repository,
                "container_port": spec.container_port,
                "min_memory_mb": spec.min_memory_mb,
                "default_memory_mb": spec.default_memory_mb,
                "versions": versions,
            }
        )
    return entries
