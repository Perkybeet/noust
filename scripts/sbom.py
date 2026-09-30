#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Write a CycloneDX 1.5 SBOM for a Noust wheel, with nothing but the standard library.

What ships in a Noust release is the Python package, whose runtime
dependencies pip resolves on the target, and the console's build under
``src/noust/web/static``, which bundles the npm packages ``panel/`` depends
on at run time (never the build tools). The SBOM lists both:

- the wheel itself, with its SHA-256;
- every Python distribution the wheel pulls in, as resolved in the
  environment the release was built and tested in (``pip install`` of the
  wheel with the extras given), walked from ``[project].dependencies``
  through each distribution's own requirements;
- every npm package of ``panel/package-lock.json`` that is not a development
  dependency, with the lockfile's SHA-512 integrity.

It is attached to each GitHub release (``.github/workflows/supply-chain.yml``)
for ENS op.pl.3 and op.exp.1.r4, and for anyone who scans what they install.

Usage:
    scripts/sbom.py dist/noust-3.1.0-py3-none-any.whl --extras web -o noust.cdx.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata as metadata
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_EXTRA_MARKER = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")


def canonical(name: str) -> str:
    """
    Args:
        name: A distribution name.

    Returns:
        Its PEP 503 normalised form.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _toml_list(text: str, key: str, section: str) -> list[str]:
    """
    Read a list of strings from a TOML section without a TOML parser (3.10).

    Args:
        text: The file.
        key: The key, such as ``dependencies``.
        section: The section header, such as ``[project]``.

    Returns:
        The strings.
    """
    start = text.find(section)
    if start < 0:
        return []
    body = text[start + len(section) :]
    next_section = re.search(r"^\[", body, flags=re.MULTILINE)
    if next_section:
        body = body[: next_section.start()]
    match = re.search(rf"^{re.escape(key)}\s*=\s*\[(.*?)^\]", body, flags=re.MULTILINE | re.DOTALL)
    if not match:
        return []
    return re.findall(r'^\s*"([^"]+)"', match.group(1), flags=re.MULTILINE)


def project_requirements(pyproject: Path, extras: list[str]) -> list[str]:
    """
    Args:
        pyproject: ``pyproject.toml``.
        extras: Optional dependency groups to include.

    Returns:
        The requirement strings of the project and those extras.
    """
    text = pyproject.read_text(encoding="utf-8")
    requirements = _toml_list(text, "dependencies", "[project]")
    for extra in extras:
        requirements += _toml_list(text, extra, "[project.optional-dependencies]")
    return requirements


def _requirement_name(requirement: str) -> str | None:
    match = _REQUIREMENT_NAME.match(requirement)
    return canonical(match.group(1)) if match else None


def python_components(requirements: list[str]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """
    Walk the installed distributions a set of requirements pulls in.

    Args:
        requirements: Top-level requirement strings.

    Returns:
        The components, and each component's direct dependencies by purl.
    """
    components: dict[str, dict[str, Any]] = {}
    edges: dict[str, list[str]] = {}
    pending = [name for name in (_requirement_name(r) for r in requirements) if name]
    while pending:
        name = pending.pop()
        if name in components or name == "noust":
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        version = dist.version
        purl = f"pkg:pypi/{name}@{version}"
        license_text = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": purl,
            "name": name,
            "version": version,
            "purl": purl,
        }
        if license_text and len(license_text) < 100:
            component["licenses"] = [{"license": {"name": license_text.strip()}}]
        components[name] = component
        children = []
        for requirement in dist.requires or []:
            if _EXTRA_MARKER.search(requirement):
                continue
            child = _requirement_name(requirement)
            if child:
                children.append(child)
                pending.append(child)
        edges[purl] = children
    resolved = {name: component["purl"] for name, component in components.items()}
    dependencies = {
        purl: [resolved[child] for child in children if child in resolved]
        for purl, children in edges.items()
    }
    return sorted(components.values(), key=lambda c: c["name"]), dependencies


def npm_components(lockfile: Path) -> list[dict[str, Any]]:
    """
    Args:
        lockfile: ``panel/package-lock.json`` (lockfile version 2 or 3).

    Returns:
        Every package that is not a development dependency: what the console
        build bundles.
    """
    data = json.loads(lockfile.read_text(encoding="utf-8"))
    components = []
    for path, entry in sorted((data.get("packages") or {}).items()):
        if not path or entry.get("dev") or entry.get("link"):
            continue
        name = path.split("node_modules/")[-1]
        version = entry.get("version")
        if not version:
            continue
        purl = f"pkg:npm/{name.replace('@', '%40', 1) if name.startswith('@') else name}@{version}"
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": purl,
            "name": name,
            "version": version,
            "purl": purl,
        }
        integrity = str(entry.get("integrity") or "")
        if integrity.startswith("sha512-"):
            digest = base64.b64decode(integrity[len("sha512-") :]).hex()
            component["hashes"] = [{"alg": "SHA-512", "content": digest}]
        if entry.get("license"):
            component["licenses"] = [{"license": {"name": str(entry["license"])}}]
        components.append(component)
    return components


def build_sbom(
    wheel: Path | None,
    version: str,
    requirements: list[str],
    lockfile: Path | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Assemble the SBOM.

    Args:
        wheel: The wheel, hashed when given.
        version: Noust's version.
        requirements: The requirements to walk.
        lockfile: The console's lockfile, when it is bundled.
        now: The timestamp.

    Returns:
        A CycloneDX 1.5 document.
    """
    root_purl = f"pkg:pypi/noust@{version}"
    root: dict[str, Any] = {
        "type": "application",
        "bom-ref": root_purl,
        "name": "noust",
        "version": version,
        "purl": root_purl,
        "licenses": [{"license": {"id": "AGPL-3.0-or-later"}}],
    }
    if wheel is not None:
        root["hashes"] = [
            {"alg": "SHA-256", "content": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        ]
    python, python_edges = python_components(requirements)
    npm = npm_components(lockfile) if lockfile is not None and lockfile.is_file() else []
    direct = [
        component["purl"]
        for component in python
        if component["name"] in {_requirement_name(r) for r in requirements}
    ]
    dependencies = [{"ref": root_purl, "dependsOn": direct + [c["purl"] for c in npm]}]
    dependencies += [{"ref": ref, "dependsOn": refs} for ref, refs in sorted(python_edges.items())]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            "tools": {"components": [{"type": "application", "name": "noust scripts/sbom.py"}]},
            "component": root,
        },
        "components": python + npm,
        "dependencies": dependencies,
    }


def _version(pyproject: Path) -> str:
    match = re.search(
        r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE
    )
    return match.group(1) if match else "0"


def main(argv: list[str] | None = None) -> int:
    """
    Args:
        argv: The command line, without the program name.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("wheel", nargs="?", type=Path, help="The wheel to describe and hash.")
    parser.add_argument("--extras", default="", help="Optional dependency groups, comma separated.")
    parser.add_argument("--pyproject", type=Path, default=ROOT / "pyproject.toml")
    parser.add_argument("--lockfile", type=Path, default=ROOT / "panel" / "package-lock.json")
    parser.add_argument("-o", "--output", type=Path, help="Write here instead of standard output.")
    args = parser.parse_args(argv)
    extras = [extra.strip() for extra in args.extras.split(",") if extra.strip()]
    sbom = build_sbom(
        args.wheel,
        _version(args.pyproject),
        project_requirements(args.pyproject, extras),
        args.lockfile,
    )
    text = json.dumps(sbom, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(
            f"Wrote {args.output}: {len(sbom['components'])} components",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
