#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The chores of writing a recipe, so that none of them is typed from memory.

A recipe is written by a person from the project's own documentation (and, for the long tail,
from a Coolify service template, which is Apache-2.0 and is credited in the recipe's header).
What this tool does is the part that is a lookup:

    scripts/recipe_draft.py tags IMAGE [--limit N] [--all]
        The newest release tags of an image in its registry (``ghcr.io/o/app``, ``postgres``),
        newest first. A tag is a release when it is numbers (``1.2.3``, ``v1.2``, ``1.2.3-alpine``),
        unless ``--all`` shows every tag the registry lists.
    scripts/recipe_draft.py release OWNER/REPO
        The latest release of a GitHub repository: its tag and date.
    scripts/recipe_draft.py pin FILE [--write | --to OTHER]
        Read every ``image: repo:tag`` of the Compose block of a recipe file, resolve the digest
        the registry serves for that tag, and print the architectures the recipe can claim.
        With ``--write`` the file is rewritten with ``@sha256:...`` on each image; with ``--to``
        the pinned recipe is written to another file and the draft is left as it was.
    scripts/recipe_draft.py config REFERENCE
        What an image says it runs, read from its registry without pulling it: the account it runs
        as, its entrypoint and command, the ports it listens on and the volumes it declares. It is
        what tells a recipe's author the port nginx must reach, whether a file the recipe mounts
        has to be readable by that account, and which directories a named volume inherits.
    scripts/recipe_draft.py logo NAME TITLE
        Write the monogram ``recipes/assets/NAME/logo.svg`` the catalogue ships (two letters on a
        dark tile, drawn for Noust: a project's own mark needs a licence).
    scripts/recipe_draft.py template NAME
        Print Coolify's service template of that name, to read next to the project's own docs.

Only anonymous, read-only registry requests are made, plus ``gh api`` for releases.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import recipe_pins  # noqa: E402

ASSETS = ROOT / "src" / "noust" / "recipes" / "assets"
COOLIFY_TEMPLATES = Path.home() / "research-src" / "coolify-src" / "templates" / "compose"

#: A tag that names a release: numbers, an optional ``v``, an optional variant suffix.
RELEASE_TAG = re.compile(r"v?\d+(?:\.\d+){1,3}(?:-[A-Za-z][A-Za-z0-9.]*)?")

#: Words in a tag that make it a pre-release or a build for a different purpose.
NOT_A_RELEASE = re.compile(r"(rc|alpha|beta|dev|nightly|snapshot|test|preview|canary|pre)", re.I)

#: An ``image:`` line of a Compose block, with its indentation, reference and trailing comment.
IMAGE_LINE = re.compile(r"^(?P<indent>\s+image:\s*)(?P<reference>[^\s#]+)(?P<rest>.*)$")


def version_key(tag: str) -> tuple[Any, ...]:
    """
    Sort key of a release tag: its numbers, then whether it has a variant suffix.

    Args:
        tag: ``1.2.3``, ``v1.2``, ``1.2.3-alpine``.

    Returns:
        A tuple that sorts newer versions higher; a plain tag sorts above its variants.
    """
    body = tag.removeprefix("v")
    numbers, _, suffix = body.partition("-")
    parts = tuple(int(part) for part in numbers.split(".") if part.isdigit())
    return (parts, suffix == "", suffix)


def release_tags(tags: Sequence[str], *, limit: int = 15, everything: bool = False) -> list[str]:
    """
    Pick the release tags out of everything a registry lists, newest first.

    Args:
        tags: Every tag.
        limit: How many to keep.
        everything: Keep tags that are not releases too (sorted by name).

    Returns:
        The newest ``limit`` tags.
    """
    if everything:
        return sorted(tags)[-limit:][::-1]
    releases = [tag for tag in tags if RELEASE_TAG.fullmatch(tag) and not NOT_A_RELEASE.search(tag)]
    return sorted(set(releases), key=version_key, reverse=True)[:limit]


def registry_tags(reference: str) -> list[str]:
    """
    List an image's tags from its registry, anonymously.

    Docker Hub is asked through its web API (newest first, which is the order that matters for
    a repository with thousands of tags); any other registry through the distribution API.

    Args:
        reference: ``[registry/]repository``; a tag in it is ignored.

    Returns:
        The tags.

    Raises:
        RuntimeError: The registry did not answer.
    """
    registry, repository, _ = recipe_pins.split_reference(reference)
    found: list[str] = []
    if registry == "docker.io":
        namespace, _, name = repository.partition("/")
        url: str | None = (
            f"https://hub.docker.com/v2/repositories/{namespace}/{name}/tags"
            "?page_size=100&ordering=last_updated"
        )
        for _page in range(8):
            if url is None:
                break
            with urllib.request.urlopen(url, timeout=30) as response:
                data = json.load(response)
            found += [str(entry["name"]) for entry in data.get("results", ())]
            url = data.get("next")
        return found
    bearer = recipe_pins._token(registry, repository)
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    url = f"https://{registry}/v2/{repository}/tags/list?n=1000"
    for _page in range(30):
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            found += json.load(response).get("tags") or []
            link = response.headers.get("Link", "")
        match = re.search(r"<([^>]+)>;\s*rel=\"next\"", link)
        if not match:
            break
        url = urllib.parse.urljoin(f"https://{registry}", match.group(1))
    return found


def latest_release(repository: str) -> dict[str, str]:
    """
    Read a GitHub repository's latest release with the ``gh`` client.

    Args:
        repository: ``owner/name``.

    Returns:
        ``tag`` and ``published`` (a date), or ``tag`` empty when the project makes none.
    """
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/releases/latest", "--jq", '.tag_name+" "+.published_at'],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        return {"tag": "", "published": ""}
    tag, _, published = result.stdout.strip().partition(" ")
    return {"tag": tag, "published": published[:10]}


def known_digests(directory: Path) -> dict[str, str]:
    """
    Collect the digests the shipped recipes already pin, by ``repo:tag``.

    A tag is mutable on the registry side (a base image is rebuilt under the same tag), so two
    recipes that run ``postgres:17.11-alpine`` would pin two images; reusing the digest the
    catalogue already holds keeps them one image on a server, and one thing to update.

    Args:
        directory: Where the recipes are.

    Returns:
        ``repository:tag`` to ``sha256:...``; a tag two recipes pin differently is left out.
    """
    found: dict[str, str] = {}
    conflicting: set[str] = set()
    for path in sorted(directory.glob("*.yaml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = IMAGE_LINE.match(line)
            if match is None or "@sha256:" not in match["reference"]:
                continue
            reference, _, digest = match["reference"].partition("@")
            if found.setdefault(reference, digest) != digest:
                conflicting.add(reference)
    return {
        reference: digest for reference, digest in found.items() if reference not in conflicting
    }


def pin_lines(
    lines: Sequence[str], *, resolve: Any, platforms: Any
) -> tuple[list[str], dict[str, set[str]]]:
    """
    Add the registry's digest to every image of a recipe file.

    Args:
        lines: The file, split in lines.
        resolve: ``reference -> digest``.
        platforms: ``(reference, digest) -> set of architectures``.

    Returns:
        The lines with ``@sha256:...`` on each image, and each image's architectures.

    Raises:
        RuntimeError: An image has no tag, or the registry does not know it.
    """
    result: list[str] = []
    archs: dict[str, set[str]] = {}
    for line in lines:
        match = IMAGE_LINE.match(line)
        if match is None or "$" in match["reference"]:
            result.append(line)
            continue
        reference = match["reference"].split("@", 1)[0]
        name = reference.rsplit("/", 1)[-1]
        if ":" not in name:
            raise RuntimeError(f"{reference} has no tag")
        digest = resolve(reference)
        archs[reference] = set(platforms(reference, digest))
        result.append(f"{match['indent']}{reference}@{digest}{match['rest']}")
    return result, archs


def common_architectures(archs: dict[str, set[str]]) -> list[str]:
    """
    Intersect the architectures of every image, in the order the recipes write them.

    Args:
        archs: Image to its architectures.

    Returns:
        What every image is published for.
    """
    if not archs:
        return []
    shared = set.intersection(*archs.values())
    order = ["386", "amd64", "arm", "arm64", "riscv64"]
    return [arch for arch in order if arch in shared]


def describe_config(config: dict[str, Any]) -> str:
    """
    Say in a few lines what an image's configuration runs and listens on.

    Args:
        config: The ``config`` object of an image's configuration blob.

    Returns:
        The account, the entrypoint and command, the ports, the volumes and the working directory.
    """
    ports = ", ".join(sorted((config.get("ExposedPorts") or {}), key=_port_order)) or "none"
    volumes = ", ".join(sorted(config.get("Volumes") or {})) or "none"
    return (
        f"user: {config.get('User') or 'root'}\n"
        f"entrypoint: {config.get('Entrypoint')}\n"
        f"command: {config.get('Cmd')}\n"
        f"ports: {ports}\n"
        f"volumes: {volumes}\n"
        f"working directory: {config.get('WorkingDir') or '/'}\n"
        f"health check: {'yes' if config.get('Healthcheck') else 'no'}"
    )


def _port_order(port: str) -> tuple[int, str]:
    """
    Sort ``8080/tcp`` by its number.

    Args:
        port: An exposed port as the configuration spells it.

    Returns:
        The number and the protocol.
    """
    number, _, protocol = port.partition("/")
    return (int(number) if number.isdigit() else 0, protocol)


def image_config(reference: str) -> dict[str, Any]:
    """
    Read an image's configuration from its registry, for linux/amd64, without pulling it.

    Args:
        reference: ``[registry/]repository:tag``, with or without a digest.

    Returns:
        The ``config`` object of the image's configuration blob.

    Raises:
        RuntimeError: The registry did not answer.
    """
    registry, repository, _tag = recipe_pins.split_reference(reference)
    digest = (
        reference.split("@", 1)[1]
        if "@" in reference
        else recipe_pins.resolve_digest(reference.split("@", 1)[0])
    )
    host = "registry-1.docker.io" if registry == "docker.io" else registry
    bearer = recipe_pins._token(registry, repository)
    headers = {"Accept": recipe_pins._MANIFESTS}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    def fetch(path: str) -> Any:
        request = urllib.request.Request(f"https://{host}/v2/{repository}/{path}", headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    try:
        manifest = fetch(f"manifests/{digest}")
        if "manifests" in manifest:
            chosen = next(
                entry
                for entry in manifest["manifests"]
                if entry.get("platform", {}).get("os") == "linux"
                and entry["platform"].get("architecture") == "amd64"
            )
            manifest = fetch(f"manifests/{chosen['digest']}")
        blob = fetch(f"blobs/{manifest['config']['digest']}")
    except (OSError, StopIteration, KeyError) as exc:
        raise RuntimeError(
            f"{reference}: the registry did not give its configuration ({exc})"
        ) from exc
    return dict(blob.get("config") or {})


def monogram(title: str) -> str:
    """
    Make the two letters of a title's logo.

    Args:
        title: The project's name.

    Returns:
        Two characters: the initials of its first two words, or its first two letters.
    """
    words = re.findall(r"[A-Za-z0-9]+", title)
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return (words[0] if words else "??")[:2].upper()


def logo_svg(title: str) -> str:
    """
    Draw a recipe's logo: two letters on a dark tile, nothing the console's policy refuses.

    Args:
        title: The project's name.

    Returns:
        The SVG text.
    """
    safe = re.sub(r"[^A-Za-z0-9 ._+-]", "", title)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" '
        f'aria-label="{safe}">\n'
        f"  <title>{safe}</title>\n"
        '  <rect width="64" height="64" rx="14" fill="#24292f"/>\n'
        '  <text x="32" y="41" text-anchor="middle" font-family="sans-serif" font-size="26" '
        f'font-weight="700" fill="#ffffff">{monogram(title)}</text>\n'
        "</svg>\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the tool.

    Args:
        argv: The arguments; ``sys.argv`` when None.

    Returns:
        0 on success, 1 when something could not be read.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    tags = commands.add_parser("tags")
    tags.add_argument("image")
    tags.add_argument("--limit", type=int, default=15)
    tags.add_argument("--all", action="store_true")
    release = commands.add_parser("release")
    release.add_argument("repository")
    pin = commands.add_parser("pin")
    pin.add_argument("file", type=Path)
    pin.add_argument("--write", action="store_true", help="rewrite the file in place")
    pin.add_argument("--to", type=Path, help="write the pinned recipe to this file instead")
    pin.add_argument(
        "--fresh",
        action="store_true",
        help="ask the registry even for a tag another recipe already pins",
    )
    pin.add_argument(
        "--offline",
        action="store_true",
        help="ask no registry: digests come from the recipes already pinned, and the "
        "architectures line is kept as the --to file (or the draft) has it",
    )
    config = commands.add_parser("config")
    config.add_argument("reference")
    logo = commands.add_parser("logo")
    logo.add_argument("name")
    logo.add_argument("title")
    template = commands.add_parser("template")
    template.add_argument("name")
    args = parser.parse_args(argv)

    try:
        if args.command == "tags":
            for tag in release_tags(
                registry_tags(args.image), limit=args.limit, everything=args.all
            ):
                print(tag)
        elif args.command == "release":
            found = latest_release(args.repository)
            print(f"{found['tag'] or '(no release)'} {found['published']}")
        elif args.command == "pin":
            lines = args.file.read_text(encoding="utf-8").splitlines()
            known = {} if args.fresh else known_digests(ROOT / "src" / "noust" / "recipes")

            def resolve(reference: str) -> str:
                if known.get(reference):
                    return known[reference]
                if args.offline:
                    raise RuntimeError(f"{reference} is pinned by no recipe yet: ask the registry")
                return recipe_pins.resolve_digest(reference)

            pinned, archs = pin_lines(
                lines,
                resolve=resolve,
                platforms=(lambda reference, digest: set())
                if args.offline
                else recipe_pins.resolve_platforms,
            )
            for reference, found in archs.items():
                print(f"{reference}: {', '.join(sorted(found)) or '(not asked)'}")
            if args.offline:
                kept = next(
                    (
                        line
                        for line in (
                            args.to.read_text(encoding="utf-8")
                            if args.to and args.to.exists()
                            else ""
                        ).splitlines()
                        if line.startswith("architectures:")
                    ),
                    next((line for line in lines if line.startswith("architectures:")), ""),
                )
                shared = kept.removeprefix("architectures:").strip()
            else:
                shared = json.dumps(common_architectures(archs))
            print(f"architectures: {shared}")
            if args.write or args.to:
                # The recipe claims exactly what every one of its images is published for.
                pinned = [
                    f"architectures: {shared}" if line.startswith("architectures:") else line
                    for line in pinned
                ]
                (args.to or args.file).write_text("\n".join(pinned) + "\n", encoding="utf-8")
        elif args.command == "config":
            print(describe_config(image_config(args.reference)))
        elif args.command == "logo":
            target = ASSETS / args.name
            target.mkdir(parents=True, exist_ok=True)
            (target / "logo.svg").write_text(logo_svg(args.title), encoding="utf-8")
            print(f"wrote {target / 'logo.svg'}")
        elif args.command == "template":
            print((COOLIFY_TEMPLATES / f"{args.name}.yaml").read_text(encoding="utf-8"))
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
