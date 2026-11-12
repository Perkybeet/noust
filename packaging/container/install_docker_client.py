# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Fetch Docker's command line and Compose's plugin for the central's image, each fixed by the sum of its file.

The central deploys containers on its machine's Docker when it is asked to (docs/central-deploy-here.md),
and talks to it with the ``docker`` program and the ``docker compose`` plugin, so the image carries both.
They are downloaded here, in a build stage of their own that the final image copies two files out of, and
nothing of the build (no package manager, no download tool) reaches the final image.

**Fixed by sum.** A file is written only after its SHA-256 is the one below: a release that is replaced
upstream, or a download that is altered on the way, fails the build instead of shipping. The sums of Compose
are the ones its own release publishes (``checksums.txt`` of the release); Docker publishes none for its
static packages, so those were taken from the files at download.docker.com on 2026-11-12, and a rebuild
that finds another file stops.

**Why these two versions.** Docker 28.5.2 (API 1.51) and Compose 2.40.3. The program is built to ask the
engine which API it speaks and to use the lower of the two, so one client has to serve the Docker of a
Synology (24.0.2, API 1.43) and the 29.x of a UGREEN. An engine from 29.0 to 29.2 refuses a client older than
API 1.44, which rules out an old client (24.x speaks 1.43), and the newer client is the one that negotiates
down. Compose 2.40.3 is the 2.x the packages of Ubuntu carry beside Docker 29.1.3, rather than the 5.x series,
whose reconciliation changed in 5.2 and 5.5 and recreates containers on a first ``up``. Neither is a claim
about a real engine: what each age of engine says to this pair is what the integration harness has to check,
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

DOCKER_VERSION = "28.5.2"
COMPOSE_VERSION = "2.40.3"

#: What each architecture is called by Docker's static packages and by Compose's releases, and the SHA-256
#: of the file each ships: ``machine -> (docker's name, docker's sum, compose's sum)``.
PINS: dict[str, tuple[str, str, str]] = {
    "x86_64": (
        "x86_64",
        "ea90cfd12e1eeb12aa1c971741adb8bd4ed88e2a574eaac13f5029a1dbc6300d",
        "dba9d98e1ba5bfe11d88c99b9bd32fc4a0624a30fafe68eea34d61a3e42fd372",
    ),
    "aarch64": (
        "aarch64",
        "9e4f82996ab790724094475ebed33a736434bfe5d45231b676fef22ffb80044d",
        "d26373b19e89160546d15407516cc59f453030d9bc5b43ba7faf16f7b4980137",
    ),
}

#: Where each file is downloaded from.
DOCKER_URL = "https://download.docker.com/linux/static/stable/{arch}/docker-{version}.tgz"
COMPOSE_URL = (
    "https://github.com/docker/compose/releases/download/v{version}/docker-compose-linux-{arch}"
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
        destination: An existing directory; it gets ``docker`` and ``docker-compose``.
        machine: The processor, as ``platform.machine()`` says it; this machine's by default.
        fetch: Gets a file from an address; :func:`download` by default, looked up when called.

    Returns:
        The two files written, executable.

    Raises:
        PinError: A processor that has no pin, or a file whose sum is not its pin: nothing of it is
            written.
    """
    get = fetch or download
    arch = machine or platform.machine()
    if arch not in PINS:
        raise PinError(f"there is no pinned Docker client for {arch}: {', '.join(PINS)}")
    name, docker_sum, compose_sum = PINS[arch]
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
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as archive:
        member = archive.extractfile("docker/docker")
        if member is None:
            raise PinError(f"docker-{DOCKER_VERSION}.tgz has no docker/docker")
        program = member.read()
    written = []
    for file_name, content in (("docker", program), ("docker-compose", compose)):
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
        0 when both programs were written, 1 when a file was not the one pinned.
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
