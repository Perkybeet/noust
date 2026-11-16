# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Fetch Docker's command line, Compose's plugin and buildx's plugin for the central's image, each fixed by the sum
of its file.

The central deploys containers on its machine's Docker when it is asked to (docs/central-deploy-here.md),
and talks to it with the ``docker`` program, the ``docker compose`` plugin and the ``docker buildx`` plugin, so
the image carries the three. buildx is not an extra: ``docker build`` is BuildKit's and refuses to build without it
("BuildKit is enabled but the buildx component is missing", which is what a Dockerfile deployed from the central of
a NAS ended in), and the question of which digest a tag has now, asked of a registry without downloading the image
(``docker buildx imagetools inspect``), is buildx's too. They are downloaded here, in a build stage of their own that
the final image copies three files out of, and nothing of the build (no package manager, no download tool) reaches
the final image.

**Fixed by sum.** A file is written only after its SHA-256 is the one below: a release that is replaced
upstream, or a download that is altered on the way, fails the build instead of shipping. The sums of Compose and
of buildx are the ones their own releases publish (``checksums.txt`` of the release); Docker publishes none for
its static packages, so those were taken from the files at download.docker.com on 2026-11-13, and a rebuild that
finds another file stops.

**Why these versions.** Docker 29.9.3 (API 1.56), Compose 5.7.1 and buildx 0.39.0, the newest of each when they
were chosen. The program is built to ask the engine which API it speaks and to use the lower of the two, so one
client has to serve the 20.10 of Debian 12 (API 1.41), the Docker of a Synology (24.0.2, API 1.43) and the 29.x of
a UGREEN. An engine from 29.0 to 29.2 refuses a client older than API 1.44, which rules out an old client, and the
newer client is the one that negotiates down: ``tests/integration/docker_run.py`` runs this very pair, in the
image, against an engine of each of those ages (scenarios 8 and 10 to 18: a stack brought up and down, a
Dockerfile built, a registry asked for a tag, a stack that already ran adopted with none of its containers
restarted, a stack edited and put back when its new document did not start). The pair before it, Docker 28.5.2
and Compose 2.40.3 (and buildx 0.29.1, which the image did not carry and ``docker build`` and the check for a new
version of an image need), was built with a Go standard library and dependencies that have published advisories
(5 critical and 154 high between the three programs; Trivy, 2026-11-13); these three have none that is critical and
4 that are high, in libraries they link for what Noust does not ask of them (unpacking archives, copying files into a
container, parsing HTML): the central only talks to the engine's local socket and serves nothing to a
network. A Compose reads a service to another configuration hash than another version of it does, so a stack that a
Compose of another version started looks changed to it in every container; adopting such a stack is refused
saying which Compose made it (``compose_adopt.rehearse_up``) and nothing is recreated, which the harness holds
(scenario 11) as it holds that a stack the same Compose started is adopted without a restart. Neither is a claim
about an engine nobody ran: what each age of engine says to this pair is what the integration harness checks,
not this file.

It uses the standard library only, because the stage it runs in has Python and nothing else.
"""

from __future__ import annotations

import hashlib
import io
import platform
import stat
import sys
import tarfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

DOCKER_VERSION = "29.9.3"
COMPOSE_VERSION = "5.7.1"
BUILDX_VERSION = "0.39.0"

#: What each architecture is called by Docker's static packages and by Compose's releases, the name buildx's
#: releases give it, and the SHA-256 of the file each ships:
#: ``machine -> (docker's name, docker's sum, compose's sum, buildx's sum)``.
PINS: dict[str, tuple[str, str, str, str]] = {
    "x86_64": (
        "x86_64",
        "3fb5d1f13014c18cc54945bc177c58a9056a482dada031e17d12cf2e6922c973",
        "af426722a8af5de812fcee30cc1c39f0639f5f723cbadefb25e5d4db054064cf",
        "5fb31a097304fbf6dd4038649a0decf32c2b9510e4047acd7d68b9d700c46194",
    ),
    "aarch64": (
        "aarch64",
        "1b27fd739378ef938401e81ce5afb73b7ad7432d4f598814a9ff5f826bb464d2",
        "f2f49f903240895fa764c0dca3a14548b754ad9426f6b609ccaf55ec5a3aa2a5",
        "722cdcdfdd6f3fb943fa1ba75102d057feb18ba72aab93b1b0a8cf2b2b48a25f",
    ),
}

#: What buildx's releases call each processor.
BUILDX_ARCH = {"x86_64": "amd64", "aarch64": "arm64"}

#: Where each file is downloaded from.
DOCKER_URL = "https://download.docker.com/linux/static/stable/{arch}/docker-{version}.tgz"
COMPOSE_URL = (
    "https://github.com/docker/compose/releases/download/v{version}/docker-compose-linux-{arch}"
)
BUILDX_URL = (
    "https://github.com/docker/buildx/releases/download/v{version}/buildx-v{version}.linux-{arch}"
)

#: The seconds a download may take to answer.
TIMEOUT = 300

Opener = Callable[[str], bytes]


class PinError(Exception):
    """A file is not the one that was pinned."""


def download(url: str) -> bytes:
    """
    Fetch a file.

    Args:
        url: An https address.

    Returns:
        Its bytes.
    """
    if not url.startswith("https://"):
        raise PinError(f"{url} is not an https address")
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        data: bytes = response.read()
    return data


def verified(data: bytes, wanted: str, name: str) -> bytes:
    """
    Check a file against the sum it was pinned to.

    Args:
        data: The file.
        wanted: Its SHA-256, in hex.
        name: What to call it in the error.

    Returns:
        The file, unchanged.

    Raises:
        PinError: When its sum is another, naming both.
    """
    found = hashlib.sha256(data).hexdigest()
    if found != wanted:
        raise PinError(f"{name} is not the file that was pinned: expected {wanted}, got {found}")
    return data


def install(
    destination: Path, machine: str | None = None, fetch: Opener | None = None
) -> list[Path]:
    """
    Put the two programs where the final image copies them from.

    Args:
        destination: An existing directory; it gets ``docker``, ``docker-compose`` and ``docker-buildx``.
        machine: The processor, as ``platform.machine()`` says it; this machine's by default.
        fetch: Gets a file from an address; :func:`download` by default, looked up when called.

    Returns:
        The three files written, executable.

    Raises:
        PinError: A processor that has no pin, or a file whose sum is not its pin: nothing of it is
            written.
    """
    get = fetch or download
    arch = machine or platform.machine()
    if arch not in PINS:
        raise PinError(f"there is no pinned Docker client for {arch}: {', '.join(PINS)}")
    name, docker_sum, compose_sum, buildx_sum = PINS[arch]
    package = verified(
        get(DOCKER_URL.format(arch=name, version=DOCKER_VERSION)),
        docker_sum,
        f"docker-{DOCKER_VERSION}.tgz",
    )
    compose = verified(
        get(COMPOSE_URL.format(arch=name, version=COMPOSE_VERSION)),
        compose_sum,
        f"docker-compose-linux-{name}",
    )
    buildx = verified(
        get(BUILDX_URL.format(arch=BUILDX_ARCH[arch], version=BUILDX_VERSION)),
        buildx_sum,
        f"buildx-v{BUILDX_VERSION}.linux-{BUILDX_ARCH[arch]}",
    )
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as archive:
        member = archive.extractfile("docker/docker")
        if member is None:
            raise PinError(f"docker-{DOCKER_VERSION}.tgz has no docker/docker")
        program = member.read()
    written = []
    for file_name, content in (
        ("docker", program),
        ("docker-compose", compose),
        ("docker-buildx", buildx),
    ):
        path = destination / file_name
        path.write_bytes(content)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    """
    Args:
        argv: The destination directory.

    Returns:
        0 when the three programs were written, 1 when a file was not the one pinned.
    """
    if len(argv) != 1:
        print("usage: install_docker_client.py DESTINATION", file=sys.stderr)  # noqa: T201
        return 2
    destination = Path(argv[0])
    destination.mkdir(parents=True, exist_ok=True)
    try:
        written = install(destination)
    except PinError as exc:
        print(f"install_docker_client: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    for path in written:
        print(f"{path}: {hashlib.sha256(path.read_bytes()).hexdigest()}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
