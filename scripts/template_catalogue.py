#!/usr/bin/env python3
"""
Convert a whole catalogue of another platform's service templates and count what comes out.

The figures in ``docs/coolify.md`` and ``docs/dokploy.md`` ("N templates, M convert to a document
nothing refuses") are the output of this script on a checkout of the catalogue, so anyone can
reproduce them and a new release of the converter can be held to them. It converts every template
with a made-up domain, judges the result with the analysis a pasted Compose document gets, and
prints the totals and the reasons.

Usage:
    scripts/template_catalogue.py coolify PATH/TO/coolify/templates/compose
    scripts/template_catalogue.py dokploy PATH/TO/Dokploy-templates/blueprints [--json]

Nothing is fetched and nothing is written: the catalogue is a directory you checked out
(Coolify: https://github.com/coollabsio/coolify, Apache-2.0; Dokploy:
https://github.com/Dokploy/templates, MIT).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.core.exceptions import ValidationError  # noqa: E402
from noust.deployers.compose_paste import analyze_document  # noqa: E402
from noust.recipes import coolify, dokploy  # noqa: E402

DOMAIN = "app.example.com"


def templates(kind: str, root: Path):
    """
    Walk a catalogue.

    Args:
        kind: ``coolify`` or ``dokploy``.
        root: The directory that holds the templates.

    Yields:
        ``(name, compose text, template.toml text or None)``.
    """
    if kind == "coolify":
        for path in sorted(root.glob("*.y*ml")):
            yield path.stem, path.read_text(encoding="utf-8"), None
        return
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        compose = folder / "docker-compose.yml"
        if not compose.is_file():
            continue
        template = folder / "template.toml"
        yield (
            folder.name,
            compose.read_text(encoding="utf-8"),
            template.read_text(encoding="utf-8") if template.is_file() else None,
        )


def main(argv: list[str] | None = None) -> int:
    """
    Convert a catalogue and print the totals.

    Args:
        argv: Arguments; ``sys.argv`` by default.

    Returns:
        0.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("kind", choices=("coolify", "dokploy"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--json", action="store_true", help="print the totals as JSON")
    args = parser.parse_args(argv)

    total = deployable = unreadable = 0
    refusals: Counter[str] = Counter()
    for name, compose, toml in templates(args.kind, args.directory):
        total += 1
        try:
            if args.kind == "coolify":
                converted = coolify.convert(compose, domain=DOMAIN, port=3021)
                files: dict[str, str] = {}
            else:
                converted = dokploy.convert(compose, toml, domain=DOMAIN, port=3021)
                files = converted.files
            found = analyze_document(converted.document, provided=files)
        except ValidationError as exc:
            unreadable += 1
            refusals[f"not converted: {exc.message}"] += 1
            continue
        if found.deployable:
            deployable += 1
            continue
        # One count per template and reason, so a stack with five services behind the Docker
        # socket is one template that mounts it, not five.
        reasons = {_kind(problem) for problem in found.refused}
        for reason in reasons:
            refusals[reason] += 1
    summary = {
        "templates": total,
        "deployable": deployable,
        "refused": total - deployable - unreadable,
        "not_converted": unreadable,
        "reasons": dict(refusals.most_common()),
    }
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    print(
        f"{total} templates: {deployable} convert to a document nothing refuses, "
        f"{summary['refused']} are refused, {unreadable} could not be converted"
    )
    for reason, count in refusals.most_common():
        print(f"  {count:4d}  {reason}")
    return 0


def _kind(problem: str) -> str:
    """
    Args:
        problem: One refusal of the analysis.

    Returns:
        The kind of refusal, without the service's name.
    """
    for marker, label in (
        ("Docker socket", "mounts the Docker socket"),
        ("privileged", "is privileged"),
        ("mounts /", "mounts a directory that holds Noust's own state"),
        ("env_file", "reads an env_file the template does not provide"),
        ("builds from", "builds from files a pasted document does not have"),
        ("host network", "uses the host network"),
        ("pid", "shares the host's process namespace"),
    ):
        if marker in problem:
            return label
    return problem.split(":", 1)[0][:80]


if __name__ == "__main__":
    sys.exit(main())
