# Copyright (c) 2024-2026 Yago López Prado
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

The tables are what the registries served on 2026-10-06 (the versions whose
support has ended, which an operator may still need, on 2026-10-29). A version
added upstream later is not offered until it is added here, which is the safe
direction; one that is withdrawn upstream keeps working from its digest until
it is upgraded.

**Versions without support are offered too** (item 103): MySQL 5.7 and 8.0,
PostgreSQL 12 to 14, MariaDB 10.6 and MongoDB 6.0 are in the table with
``legacy=True``, pinned like the rest. Making one is a decision, not a default:
it carries the date its project stopped fixing security problems and needs the
operator's ``unsupported_ok`` (:func:`unsupported_notice` says when).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING, Any

from noust.core.db_instance_store import CUSTOM_VARIANT, check_version
from noust.core.exceptions import ValidationError
from noust.core.server_text import say
from noust.managers.database.eol import SupportNotice, support_notice
from noust.managers.database.flavours import FLAVOURS
from noust.validators.container import split_image_reference

if TYPE_CHECKING:
    from noust.core.db_instance_store import InstanceRecord
    from noust.core.server_text import ServerText

#: When the digests below were read from the registries.
PINNED_ON = "2026-10-06"

#: The same, for the versions whose support has ended.
LEGACY_PINNED_ON = "2026-10-29"


@dataclass(frozen=True)
class ImagePin:
    """
    One version Noust offers, as the registry served it.

    Attributes:
        version: The tag, a major such as ``17`` or ``8.4``.
        digest: The digest of the tag's image index.
        default: The version offered when none is asked for.
        tag: The tag the digest was read from, when it is not the version.
        legacy: The project no longer supports it, or is about to stop. Noust offers it for the
            application that needs it, beside the current ones, and the operator confirms the
            price (``unsupported_ok``) before one is made. Never the default, and a prefix an
            operator types (``8``) never lands on it while a supported version answers.
        platforms: The processors this image alone is published for, when fewer than the
            engine's (MySQL 5.7 has an amd64 image only); None for the engine's own.
    """

    version: str
    digest: str
    default: bool = False
    tag: str | None = None
    legacy: bool = False
    platforms: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ImageVariant:
    """
    The same engine from another project's image, with something added to it.

    PostgreSQL's extensions (PostGIS, pgvector) are files on disk, so an instance that
    needs one runs an image that has them. A variant is that image: the same engine, the
    same major versions, a repository of its own. Everything else about the instance (its
    ports, its health probe, how it is configured) is the engine's.

    Attributes:
        name: What an operator types (``pgvector``).
        label: How the engine with it is named to a person.
        description: What it adds, in a sentence.
        repository: The image's repository.
        pins: The versions it is offered in (the engine's major, each with the tag the
            registry serves it under), oldest first.
        platforms: The processors the image is published for.
    """

    name: str
    label: str
    description: str
    repository: str
    pins: tuple[ImagePin, ...]
    platforms: tuple[str, ...] = ("amd64", "arm64")

    def default_pin(self) -> ImagePin:
        """
        Returns:
            The version offered when none is asked for.
        """
        return next((pin for pin in self.pins if pin.default), self.pins[-1])


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
        variants: Other images of the same engine an instance may run (PostgreSQL with
            pgvector); see :class:`ImageVariant`.
        label: How the spec is named to a person when it is a variant's; the flavour's
            own name otherwise.
        platforms: The processors the image is published for: both Noust installs on,
            unless a variant's project publishes for fewer.
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
    variants: tuple[ImageVariant, ...] = ()
    label: str | None = None
    platforms: tuple[str, ...] = ("amd64", "arm64")

    @property
    def display_name(self) -> str:
        """How the flavour is named to a person."""
        return self.label or FLAVOURS[self.flavour].display_name

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
                # The versions whose project has stopped fixing security problems, or is about to,
                # for the application that still needs one (item 103). They are the engine's own
                # image only: a variant offers the supported majors.
                ImagePin(
                    "12",
                    "sha256:2f2a8c2a7d10862e7fba2602e304523554f9df8244c632dafe2628ccb398fb5c",
                    legacy=True,
                ),
                ImagePin(
                    "13",
                    "sha256:4689940c683801b4ab839ab3b0a0a3555a5fe425371422310944e89eca7d8068",
                    legacy=True,
                ),
                ImagePin(
                    "14",
                    "sha256:0472f5295d05c211cc42b3daa5e84a128e390c5267b60ced9fe044bcf4edfdb8",
                    legacy=True,
                ),
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
            variants=(
                ImageVariant(
                    name="pgvector",
                    label="PostgreSQL with pgvector",
                    description="Vector similarity search: the vector type and its indexes.",
                    repository="pgvector/pgvector",
                    pins=(
                        ImagePin(
                            "15",
                            "sha256:47af0b65960b14f1c6945aa4ccae37836c632e9a6fe0eea9160936e616e5b2e9",
                            tag="pg15",
                        ),
                        ImagePin(
                            "16",
                            "sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a",
                            tag="pg16",
                        ),
                        ImagePin(
                            "17",
                            "sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d",
                            tag="pg17",
                        ),
                        ImagePin(
                            "18",
                            "sha256:2358fcba361ed2233a5ed81b5fe4ca779ccb304120ce531a3bf51c0ed7e2bc11",
                            default=True,
                            tag="pg18",
                        ),
                    ),
                ),
                ImageVariant(
                    name="postgis",
                    label="PostgreSQL with PostGIS",
                    description="Geographic objects: the geometry and geography types, and GIST indexes.",
                    repository="postgis/postgis",
                    # The project publishes this image for amd64 only (read on 2026-10-13).
                    platforms=("amd64",),
                    pins=(
                        ImagePin(
                            "15",
                            "sha256:2a60827705ad26af212f31ca6ed4884f9fa95fffbe7eed5be516fece1bca25f8",
                            tag="15-3.5",
                        ),
                        ImagePin(
                            "16",
                            "sha256:94146ac37bc61e2322f88016056c5920729cb8c64c8542ed590af8fc2abdac07",
                            tag="16-3.5",
                        ),
                        ImagePin(
                            "17",
                            "sha256:01a6a70e41e6c4467c8f55f6063555ed72db2d6662cd0d571040d42eadaeb6f6",
                            tag="17-3.5",
                        ),
                        ImagePin(
                            "18",
                            "sha256:60f6ad1d21ea86a67d47780b9a0d1e1d200500f62b19293fa834d0dea80b8677",
                            default=True,
                            tag="18-3.6",
                        ),
                    ),
                ),
            ),
        ),
        EngineSpec(
            flavour="mysql",
            engine="mysql",
            repository="mysql",
            container_port=3306,
            min_memory_mb=512,
            default_memory_mb=1024,
            pins=(
                # 5.7 has an image for amd64 only. Its project's last release was in 2023.
                ImagePin(
                    "5.7",
                    "sha256:4bc6bc963e6d8443453676cae56536f4b8156d78bae03c0145cbe47c2aad73bb",
                    legacy=True,
                    platforms=("amd64",),
                ),
                ImagePin(
                    "8.0",
                    "sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b",
                    legacy=True,
                ),
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
                    "10.6",
                    "sha256:f1c56f035ed268a0d7c2c2e06dc879f6319b997cb434cf822a4cfd72a7aa184d",
                    legacy=True,
                ),
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
        # KeyDB speaks Redis's protocol and keeps its files (it is a fork of Redis 6.2), so
        # the Redis manager drives it as it drives Valkey. It is container-only: no
        # distribution packages it, so it has no row in the host's install table, and the
        # label names it. Its project has published nothing since 6.3.4 (October 2023):
        # the image is the one the project itself publishes for both processors, under the
        # tag ``latest``, which is why the pin names that tag and no other.
        EngineSpec(
            flavour="keydb",
            engine="redis",
            repository="eqalpha/keydb",
            container_port=6379,
            min_memory_mb=64,
            default_memory_mb=256,
            pins=(
                ImagePin(
                    "6.3",
                    "sha256:6537505c42355ca1f571276bddf83f5b750f760f07b2a185a676481791e388ac",
                    default=True,
                    tag="latest",
                ),
            ),
            data_path=_fixed("/data"),
            label="KeyDB",
        ),
        # ClickHouse is container-only too: its project publishes packages, but Noust runs it
        # from the image so that its version, its memory and its backups are the instance's,
        # like every other engine's beside the host's. The versions are the long-term ones
        # (March and August releases). Applications reach it by its HTTP interface, so that
        # is the port the instance publishes; the client inside the container uses the
        # native one.
        EngineSpec(
            flavour="clickhouse",
            engine="clickhouse",
            repository="clickhouse/clickhouse-server",
            container_port=8123,
            min_memory_mb=1024,
            default_memory_mb=2048,
            pins=(
                ImagePin(
                    "25.8",
                    "sha256:0152dd511befe6a2c2ef53e930726179669b08116da78500b37c51c96ff5ee77",
                ),
                ImagePin(
                    "26.3",
                    "sha256:5637f7b3024c95ee94b8e4c1255c90c8151618088c5549cc77b886786a86beb0",
                ),
                ImagePin(
                    "26.8",
                    "sha256:6ded2b2c2f2c46f12784bd8000c75f8aa7363372ed64afccaa932456b8cb41e4",
                    default=True,
                ),
            ),
            data_path=_fixed("/var/lib/clickhouse"),
            extra_volumes=("/var/log/clickhouse-server",),
            label="ClickHouse",
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
                    "6.0",
                    "sha256:8b6d8f5bbedb25cb73517b65cf99f13aeb75ad5b157a56c479287a840bbad3ac",
                    legacy=True,
                ),
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


def with_variant(spec: EngineSpec, variant: str | None) -> EngineSpec:
    """
    Turn an engine into one of its variants: the same engine from another image.

    Args:
        spec: The engine.
        variant: A variant's name, or None for the engine's own image.

    Returns:
        ``spec`` itself for None; otherwise a spec with the variant's repository, versions
        and name, and everything else the engine's.

    Raises:
        ValidationError: The engine has no such variant; the error lists the ones it has.
    """
    if variant is None or not variant.strip():
        return spec
    wanted = variant.strip().lower()
    found = next((item for item in spec.variants if item.name == wanted), None)
    if found is None:
        offered_names = ", ".join(item.name for item in spec.variants)
        raise ValidationError(
            f"{spec.display_name} has no variant called {variant!r}",
            details=(
                f"Variants of {spec.display_name}: {offered_names}."
                if offered_names
                else f"{spec.display_name} has no variants: it runs from the engine's own image."
            ),
            field="variant",
        )
    return replace(
        spec,
        repository=found.repository,
        pins=found.pins,
        label=found.label,
        platforms=found.platforms,
    )


@dataclass(frozen=True)
class CustomImage:
    """
    An image the operator chose, with the digest the operator pinned it to.

    Attributes:
        repository: Where it is pulled from, registry included (``ghcr.io/acme/pg``).
        tag: The tag the digest was read from, or None.
        digest: The digest. Mandatory: a tag is a promise and a digest is a file, and the
            official images are pinned by Noust so that the same row is the same software.
            Empty only for an image given by registry and tag, which Noust pins when the
            instance is made (:func:`parse_custom_image`, ``pin_later``).
    """

    repository: str
    tag: str | None
    digest: str


def parse_custom_image(reference: str, *, pin_later: bool = False) -> CustomImage:
    """
    Read the image an operator asked for.

    Args:
        reference: ``repository[:tag]@sha256:digest``.
        pin_later: Accept ``repository:tag`` too, for Noust to read the digest of the tag when
            it makes the instance (:func:`~noust.managers.database.instance_images.pull_and_read_digest`).
            The tag is then mandatory: ``latest`` is not assumed for an image nobody named.

    Returns:
        The image; its digest is empty when it is to be pinned later.

    Raises:
        ValidationError: It is not an image reference, or it has no digest (``field`` is
            ``image``), or, with ``pin_later``, no tag either.
    """
    name, tag, digest = split_image_reference(reference if isinstance(reference, str) else "")
    host, slash, path = name.partition("/")
    is_registry = bool(slash) and ("." in host or ":" in host or host == "localhost")
    if (path if is_registry else name) != (path if is_registry else name).lower():
        raise ValidationError(
            f"{name!r} is not an image repository",
            details="A repository is lower case: only a registry's host name may have capitals.",
            field="image",
        )
    if digest is None and pin_later:
        if tag is None:
            raise ValidationError(
                "Say which tag of the image to run",
                details=(
                    "Write it as repository:tag, such as ghcr.io/acme/pg:17.2. Noust reads the "
                    "tag's digest when it makes the instance and pins it, so that what runs is "
                    "exactly those bytes; it does not assume 'latest'."
                ),
                field="image",
            )
        return CustomImage(repository=name, tag=tag, digest="")
    if digest is None:
        raise ValidationError(
            "An image Noust did not choose has to be pinned by its digest",
            details=(
                "Write it as repository:tag@sha256:<64 hex digits>. 'docker buildx imagetools "
                "inspect <repository:tag>' prints the digest of a tag, and the digest is what "
                "is pulled: a tag can be moved after you looked, a digest cannot."
            ),
            field="image",
        )
    return CustomImage(repository=name, tag=tag, digest=digest)


def custom_spec(spec: EngineSpec, image: CustomImage, version: str) -> EngineSpec:
    """
    Turn an engine into the same engine from an image the operator chose.

    Args:
        spec: The engine.
        image: The image, pinned.
        version: The version the operator says it is: it decides where the data lives, what
            TLS the engine can serve and when its support ends.

    Returns:
        A spec with the image's repository and one version, and everything else the engine's.

    Raises:
        ValidationError: The version is not numbers and dots (``field`` is ``version``).
    """
    wanted = check_version(version)
    return replace(
        spec,
        repository=image.repository,
        pins=(ImagePin(wanted, image.digest, default=True, tag=image.tag),),
        label=f"{spec.display_name} (your own image)",
        variants=(),
    )


def spec_of_record(record: InstanceRecord) -> EngineSpec:
    """
    Find the spec an instance runs: the catalog's, a variant's, or the operator's own image.

    Args:
        record: The instance's row.

    Returns:
        Its spec.

    Raises:
        ValidationError: The row names a flavour or a variant the catalog does not have.
    """
    spec = spec_for(record.flavour)
    if record.variant == CUSTOM_VARIANT:
        return custom_spec(
            spec, CustomImage(record.image, record.image_tag, record.digest), record.version
        )
    return with_variant(spec, record.variant)


def resolve_pin(spec: EngineSpec, version: str | None) -> ImagePin:
    """
    Decide which image a version asked for is.

    The version is a prefix by components, as everywhere a version is typed:
    ``17`` is PostgreSQL 17, ``8`` is MySQL 8.4 (the one supported 8 there is; 8.0
    is asked for by its name), and ``11`` is ambiguous between MariaDB's 11.4 and
    11.8, so it is refused with both.

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
    # A prefix means the supported version it always meant (``8`` is MySQL 8.4, not 8.0): the ones
    # without support answer only when nothing supported does.
    matching = [pin for pin in spec.pins if pin.version.split(".")[: len(wanted)] == wanted]
    supported = [pin for pin in matching if not pin.legacy]
    if supported:
        matching = supported
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


def unsupported_notice(
    spec: EngineSpec, pin: ImagePin, *, today: date | None = None
) -> SupportNotice | None:
    """
    Say whether making an instance of a version needs the operator's confirmation.

    It does when the table of the catalog lists the version as one without support (``legacy``)
    and when its project's date has passed, which is what an image of the operator's own that
    says it is MySQL 5.7 comes to. A version with no published date is not unsupported: Noust
    invents none.

    Args:
        spec: The engine (the operator's image, when it is one: its ``pins`` has the version
            the operator says it is).
        pin: The version.
        today: The date to judge against; today when omitted.

    Returns:
        The support notice of the version when it needs the confirmation, else None.
    """
    notice = support_notice(spec.flavour, pin.version, today=today)
    if notice.end_of_life is None:
        return None
    if pin.legacy or notice.status == "ended":
        return notice
    return None


def unsupported_text(spec: EngineSpec, pin: ImagePin, notice: SupportNotice) -> ServerText:
    """
    Say, as a sentence the console translates, what a version without support costs.

    Args:
        spec: The engine.
        pin: The version.
        notice: Its :func:`unsupported_notice`.

    Returns:
        ``database_servers.unsupported_ended`` when its project's date has passed, else
        ``database_servers.unsupported_ending``, with the engine, the version and the date.
    """
    values = {
        "name": FLAVOURS[spec.flavour].display_name,
        "version": pin.version,
        "end_of_life": notice.end_of_life or "",
    }
    if notice.status == "ended":
        return say("database_servers.unsupported_ended", **values)
    return say("database_servers.unsupported_ending", **values)


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


def offered(engine: str | None = None) -> list[dict[str, Any]]:
    """
    List what Noust can run as an instance, for the API and the CLI.

    Args:
        engine: Only this flavour (or an alias of it): what "another version" is asked with
            once the engine is chosen. All of them when omitted.

    Returns:
        One entry per flavour: its name, engine, display name, image
        repository, minimum and suggested memory, and each version with its
        digest, whether it is the default, where the project publishes one
        when its upstream support ends and whether it is ``support``ed, whether making it
        needs the operator's ``unsupported_ok`` and the processors it is published for.

    Raises:
        ValidationError: Noust runs no such engine as a container.
    """
    chosen = spec_for(engine).flavour if engine else None
    entries: list[dict[str, Any]] = []
    for spec in SPECS.values():
        if chosen is not None and spec.flavour != chosen:
            continue
        versions = []
        for pin in spec.pins:
            notice = support_notice(spec.flavour, pin.version)
            versions.append(
                {
                    "version": pin.version,
                    "digest": pin.digest,
                    "default": pin.default,
                    "end_of_life": notice.end_of_life,
                    "support": notice.status,
                    "unsupported": unsupported_notice(spec, pin) is not None,
                    "platforms": list(pin.platforms or spec.platforms),
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
                "variants": [
                    {
                        "name": variant.name,
                        "display_name": variant.label,
                        "description": variant.description,
                        "image": variant.repository,
                        "platforms": list(variant.platforms),
                        "versions": [
                            {
                                "version": pin.version,
                                "digest": pin.digest,
                                "default": pin.default,
                                "tag": pin.tag or pin.version,
                                "end_of_life": support_notice(
                                    spec.flavour, pin.version
                                ).end_of_life,
                                "support": support_notice(spec.flavour, pin.version).status,
                                "unsupported": unsupported_notice(spec, pin) is not None,
                            }
                            for pin in variant.pins
                        ],
                    }
                    for variant in spec.variants
                ],
            }
        )
    return entries
