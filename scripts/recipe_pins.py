#!/usr/bin/env python3
"""
Read the pins of the shipped recipes from where they are published, and compare.

A recipe is only as trustworthy as its pins, so a pin is never typed from
memory: it is read from the registry (an image's digest), from the release
(a git tag) or from the file itself (an archive's SHA-256), and this tool is
the one way to do it.

Usage:
    scripts/recipe_pins.py resolve ghcr.io/o/app:1.2.3
        Print ``ghcr.io/o/app:1.2.3@sha256:...``, ready to paste into a recipe.
    scripts/recipe_pins.py check [--evidence DIR] [NAME ...]
        Resolve every pin of the shipped recipes (or the named ones) and fail
        if one differs from what the recipe says. With --evidence, write what
        was read, per recipe, as JSON.
    scripts/recipe_pins.py archive https://host/app.tar.gz
        Download the archive and print its SHA-256.

Only anonymous, read-only requests are made. Registries are asked with HEAD,
which Docker Hub does not count against its anonymous pull limit. A GitHub
token in GITHUB_TOKEN raises the API limit; none is needed for a few recipes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.recipes import get_recipe, list_recipes  # noqa: E402

#: The architectures a recipe may name: Docker's spelling.
ARCHITECTURES = {"amd64", "arm64", "arm", "386", "riscv64"}

_MANIFESTS = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


def split_reference(reference: str) -> tuple[str, str, str]:
    """
    Split an image reference.

    Args:
        reference: ``[registry/]repository[:tag][@digest]``.

    Returns:
        The registry (``docker.io`` when none), the repository and the tag
        (``latest`` when none).
    """
    name = reference.split("@", 1)[0]
    tag = "latest"
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = name.rsplit(":", 1)
    parts = name.split("/")
    if "." in parts[0] or ":" in parts[0] or parts[0] == "localhost":
        return parts[0], "/".join(parts[1:]), tag
    repository = name if "/" in name else f"library/{name}"
    return "docker.io", repository, tag


def _token(registry: str, repository: str) -> str | None:
    """
    Get an anonymous pull token from the registry that asks for one.

    Args:
        registry: The registry.
        repository: The repository.

    Returns:
        The token, or None for a registry that needs none.
    """
    urls = {
        "docker.io": "https://auth.docker.io/token?service=registry.docker.io"
        f"&scope=repository:{repository}:pull",
        "ghcr.io": f"https://ghcr.io/token?scope=repository:{repository}:pull",
        "quay.io": "",
    }
    url = urls.get(registry)
    if not url:
        return None
    with urllib.request.urlopen(url, timeout=30) as response:
        return str(json.load(response)["token"])


def resolve_digest(reference: str) -> str:
    """
    Read the digest a registry serves for an image's tag.

    Args:
        reference: ``[registry/]repository:tag``; a digest in it is ignored.

    Returns:
        ``sha256:...``: for a multi-architecture image, the digest of the
        index, which is what ``image: repo:tag@digest`` pins.

    Raises:
        RuntimeError: The registry did not answer with a digest.
    """
    registry, repository, tag = split_reference(reference)
    host = "registry-1.docker.io" if registry == "docker.io" else registry
    bearer = _token(registry, repository)
    headers = {"Accept": _MANIFESTS, **({"Authorization": f"Bearer {bearer}"} if bearer else {})}
    request = urllib.request.Request(
        f"https://{host}/v2/{repository}/manifests/{tag}", headers=headers, method="HEAD"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            digest = response.headers.get("Docker-Content-Digest")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{reference}: the registry answered HTTP {exc.code}") from exc
    if not digest or not digest.startswith("sha256:"):
        raise RuntimeError(f"{reference}: the registry sent no digest")
    return digest


def resolve_platforms(reference: str, digest: str) -> set[str]:
    """
    Read the architectures an image is published for.

    Asks for the manifest by its digest, which never changes, so what is
    read is what the recipe pins.

    Args:
        reference: ``[registry/]repository[:tag]``, for the registry and repository.
        digest: The ``sha256:`` digest the recipe pins.

    Returns:
        The Linux architectures, with Docker's names (``amd64``, ``arm64``,
        ``arm``, ``386``, ``riscv64``). For an image published for one
        platform only, that platform's.

    Raises:
        RuntimeError: The registry did not answer.
    """
    registry, repository, _ = split_reference(reference)
    host = "registry-1.docker.io" if registry == "docker.io" else registry
    bearer = _token(registry, repository)
    headers = {"Accept": _MANIFESTS, **({"Authorization": f"Bearer {bearer}"} if bearer else {})}

    def fetch(path: str) -> Any:
        request = urllib.request.Request(f"https://{host}/v2/{repository}/{path}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{reference}: the registry answered HTTP {exc.code}") from exc

    manifest = fetch(f"manifests/{digest}")
    if "manifests" in manifest:
        return {
            str(entry["platform"]["architecture"])
            for entry in manifest["manifests"]
            if entry.get("platform", {}).get("os") == "linux"
        }
    config = fetch(f"blobs/{manifest['config']['digest']}")
    return {str(config["architecture"])} if config.get("os") == "linux" else set()


def archive_digest(url: str, algorithm: str = "sha256") -> str:
    """
    Download an archive and hash it.

    Args:
        url: An https URL.
        algorithm: ``sha1``, ``sha256`` or ``sha512``.

    Returns:
        The hash in hexadecimal.

    Raises:
        ValueError: The URL is not https.
    """
    if not url.startswith("https://"):
        raise ValueError("only https archives are read")
    digest = hashlib.new(algorithm)
    with urllib.request.urlopen(url, timeout=300) as response:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def archive_sha256(url: str) -> str:
    """
    Download an archive and hash it with SHA-256.

    Args:
        url: An https URL.

    Returns:
        The SHA-256 in hexadecimal.
    """
    return archive_digest(url, "sha256")


def published_checksum(url: str) -> str:
    """
    Read the checksum a project publishes beside an archive.

    Args:
        url: The ``.sha1``, ``.sha256`` or ``.sha512`` file's https URL.

    Returns:
        The first word of the file: the hash.

    Raises:
        ValueError: The URL is not https or the file is empty.
    """
    if not url.startswith("https://"):
        raise ValueError("only https checksums are read")
    with urllib.request.urlopen(url, timeout=60) as response:
        words = response.read(4096).decode("ascii", "replace").split()
    if not words:
        raise ValueError(f"{url} is empty")
    return words[0].lower()


def github_tag_exists(repository_url: str, ref: str) -> str | None:
    """
    Check that a tag exists in a github.com repository.

    Args:
        repository_url: ``https://github.com/owner/repo(.git)``.
        ref: The tag.

    Returns:
        The commit the tag points at, or None when the tag does not exist or
        the repository is not on github.com.
    """
    path = repository_url.removeprefix("https://github.com/").removesuffix(".git")
    if path == repository_url:
        return None
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    request = urllib.request.Request(
        f"https://api.github.com/repos/{path}/commits/{ref}", headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return str(json.load(response)["sha"])
    except urllib.error.HTTPError:
        return None


def check_recipe(name: str) -> dict[str, Any]:
    """
    Read every pin of a recipe and compare it with what the recipe says.

    Args:
        name: The recipe.

    Returns:
        What was read: ``problems`` is empty when every pin holds.
    """
    recipe = get_recipe(name)
    report: dict[str, Any] = {
        "recipe": name,
        "version": recipe.version,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "digest_verified": recipe.digest_verified if recipe.kind == "compose" else None,
        "images": [],
        "source": None,
        "problems": [],
    }
    for image in recipe.images:
        entry: dict[str, Any] = {
            "service": image.service,
            "reference": f"{image.repository}:{image.tag}",
            "recorded": image.digest,
        }
        try:
            entry["registry"] = resolve_digest(f"{image.repository}:{image.tag}")
        except (RuntimeError, urllib.error.URLError) as exc:
            entry["registry"] = None
            report["problems"].append(str(exc))
        else:
            if image.digest and image.digest != entry["registry"]:
                report["problems"].append(
                    f"{image.repository}:{image.tag}: the recipe says {image.digest}, the "
                    f"registry serves {entry['registry']}"
                )
        if image.digest:
            try:
                entry["platforms"] = sorted(
                    resolve_platforms(f"{image.repository}:{image.tag}", image.digest)
                )
            except RuntimeError as exc:
                entry["platforms"] = None
                report["problems"].append(str(exc))
        report["images"].append(entry)
    platforms = [set(entry["platforms"]) for entry in report["images"] if entry.get("platforms")]
    if platforms:
        common = sorted(set.intersection(*platforms) & ARCHITECTURES)
        report["architectures"] = common
        if sorted(recipe.architectures) != common:
            report["problems"].append(
                f"the recipe says architectures {sorted(recipe.architectures)}, "
                f"its images are all published for {common}"
            )
    source = recipe.source
    if source is not None and source.kind == "git" and source.ref:
        commit = github_tag_exists(source.url, source.ref)
        report["source"] = {"git": source.url, "ref": source.ref, "commit": commit}
        if commit is None:
            report["problems"].append(f"{source.url}: tag {source.ref} not found on GitHub")
    elif source is not None and source.kind == "archive" and source.sha256:
        found = archive_sha256(source.url)
        report["source"] = {"archive": source.url, "recorded": source.sha256, "found": found}
        if found.lower() != source.sha256.lower():
            report["problems"].append(f"{source.url}: its SHA-256 is {found}")
    elif source is not None and source.kind == "archive" and source.checksum_url:
        algorithm = source.checksum_url.rsplit(".", 1)[-1]
        published = published_checksum(source.checksum_url)
        found = archive_digest(source.url, algorithm)
        report["source"] = {
            "archive": source.url,
            "checksum_url": source.checksum_url,
            "published": published,
            "found": found,
        }
        if found != published:
            report["problems"].append(
                f"{source.url}: its {algorithm.upper()} is {found}, the published one {published}"
            )
    return report


def main(argv: list[str] | None = None) -> int:
    """
    Run the tool.

    Args:
        argv: The arguments; ``sys.argv`` when None.

    Returns:
        The exit code: 0 when every pin holds, 1 when one does not.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve", help="print an image reference with its digest")
    resolve.add_argument("reference")
    archive = commands.add_parser("archive", help="print the SHA-256 of an archive")
    archive.add_argument("url")
    check = commands.add_parser("check", help="compare every pin with where it is published")
    check.add_argument("names", nargs="*")
    check.add_argument("--evidence", type=Path, help="write the findings, per recipe, here")
    args = parser.parse_args(argv)

    if args.command == "resolve":
        shown = args.reference.split("@", 1)[0]
        print(f"{shown}@{resolve_digest(args.reference)}")
        return 0
    if args.command == "archive":
        print(archive_sha256(args.url))
        return 0

    names = args.names or [recipe.name for recipe in list_recipes() if recipe.available]
    failed = 0
    for name in names:
        report = check_recipe(name)
        status = "ok" if not report["problems"] else "DIFFERS"
        print(f"{name}: {status}")
        for problem in report["problems"]:
            print(f"  {problem}")
        failed += bool(report["problems"])
        if args.evidence is not None:
            args.evidence.mkdir(parents=True, exist_ok=True)
            (args.evidence / f"{name}.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
