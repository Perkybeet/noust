#!/usr/bin/env python3
"""
Read the pins of the database images Noust offers as instances, and compare.

An instance runs from an official image pinned by tag and by digest
(``noust.managers.database.instance_catalog``), and a digest is never typed
from memory: it is read from the registry that serves it. This tool is the one
way to do it, with the same anonymous, read-only requests the recipe pins use
(``scripts/recipe_pins.py``, whose registry functions it reuses).

Usage:
    scripts/database_pins.py resolve postgres:17
        Print ``postgres:17@sha256:...``, ready to paste into the catalog.
    scripts/database_pins.py check [--evidence FILE] [FLAVOUR ...]
        Resolve every pin of the catalog (or the named flavours) and fail if
        one differs from what the registry serves for its tag. With --evidence,
        write what was read as JSON.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.managers.database.instance_catalog import SPECS  # noqa: E402


def _recipe_pins() -> Any:
    """
    Load the module whose registry functions are shared.

    Returns:
        ``scripts/recipe_pins.py`` as a module.
    """
    path = Path(__file__).with_name("recipe_pins.py")
    spec = importlib.util.spec_from_file_location("recipe_pins", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_pins"] = module
    spec.loader.exec_module(module)
    return module


def check(flavours: list[str]) -> dict[str, Any]:
    """
    Read every pin of the catalog and compare it with what the registry serves.

    Args:
        flavours: The flavours to read; every one when empty.

    Returns:
        What was read; ``problems`` is empty when every pin holds.
    """
    pins = _recipe_pins()
    report: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "images": [],
        "problems": [],
    }
    for name, spec in SPECS.items():
        if flavours and name not in flavours:
            continue
        # The engine's own image, and each variant's: another repository, another tag.
        images = [(name, spec.repository, spec.pins, spec.platforms)] + [
            (f"{name}/{variant.name}", variant.repository, variant.pins, variant.platforms)
            for variant in spec.variants
        ]
        for label, repository, pinned, platforms in images:
            for pin in pinned:
                _check_one(
                    report,
                    pins,
                    label,
                    f"{repository}:{pin.tag or pin.version}",
                    pin.digest,
                    platforms,
                )
    return report


def _check_one(
    report: dict[str, Any],
    pins: Any,
    label: str,
    reference: str,
    digest: str,
    platforms: tuple[str, ...],
) -> None:
    """
    Compare one pin with what its registry serves.

    Args:
        report: The report, which this adds to.
        pins: The module whose registry functions are shared.
        label: The flavour (``postgresql``) or flavour and variant (``postgresql/pgvector``).
        reference: ``repository:tag``.
        digest: The digest the catalog pins.
        platforms: The processors the catalog says the image is published for.
    """
    entry: dict[str, Any] = {
        "flavour": label,
        "reference": reference,
        "pinned": digest,
        "served": None,
        "platforms": [],
    }
    try:
        entry["served"] = pins.resolve_digest(reference)
        entry["platforms"] = sorted(pins.resolve_platforms(reference, digest))
    except RuntimeError as exc:
        report["problems"].append(str(exc))
    if entry["served"] is not None and entry["served"] != digest:
        report["problems"].append(
            f"{reference}: the catalog pins {digest} and the registry serves "
            f"{entry['served']} now (the tag moved: read the new image's release notes, "
            "then update the pin)"
        )
    if not set(platforms) <= set(entry["platforms"]):
        report["problems"].append(
            f"{reference}: the catalog says it is published for {', '.join(platforms)} and the "
            f"registry lists {', '.join(entry['platforms']) or 'none'}"
        )
    elif {"amd64", "arm64"} <= set(entry["platforms"]) and set(platforms) != {"amd64", "arm64"}:
        report["problems"].append(
            f"{reference}: now published for both amd64 and arm64: the catalog can drop its "
            "platform restriction"
        )
    report["images"].append(entry)


def main(argv: list[str]) -> int:
    """
    Run the tool.

    Args:
        argv: The arguments after the program name.

    Returns:
        The exit status: 0 when every pin holds.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve", help="print an image's tag and digest")
    resolve.add_argument("reference")
    checking = commands.add_parser("check", help="compare the catalog with the registries")
    checking.add_argument("flavours", nargs="*")
    checking.add_argument("--evidence", type=Path, help="write what was read, as JSON")
    arguments = parser.parse_args(argv)

    if arguments.command == "resolve":
        digest = _recipe_pins().resolve_digest(arguments.reference)
        repository = arguments.reference.split("@", 1)[0]
        print(f"{repository}@{digest}")
        return 0

    report = check(arguments.flavours)
    for entry in report["images"]:
        state = "ok" if entry["served"] == entry["pinned"] else "DIFFERS"
        print(
            f"{entry['reference']:<28} {str(entry['served'])[:19]:<20} "
            f"{','.join(entry['platforms']):<28} {state}"
        )
    for problem in report["problems"]:
        print(f"problem: {problem}", file=sys.stderr)
    if arguments.evidence is not None:
        arguments.evidence.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
