#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Write ``tests/fixtures/compose_spec_keys.json``: the keys of the Compose specification, by section.

The guard of a compose stack (``noust.deployers.helpers.compose_keys``) refuses a key it has not
decided about, and a test holds its list to the keys of the specification, so a key that Compose
gains is a test that fails and not a hole in the guard. This script reads the JSON Schema that the
Compose project publishes (``compose-spec/compose-spec``, Apache-2.0: only the names of the keys are
kept) and writes them down, with where and when they were read.

``--check`` reads the schema again and fails when it has a key that the file does not (the file is
what a person last reviewed; run it on a schedule, or before a release, to learn that Compose moved).
``--from FILE`` reads a schema already on disk, for a machine with no route out. Nothing in the test
suite runs this: the suite never asks the network.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: Where the specification's schema is published.
SCHEMA_URL = (
    "https://raw.githubusercontent.com/compose-spec/compose-spec/main/schema/compose-spec.json"
)

DEFAULT_OUTPUT = ROOT / "tests" / "fixtures" / "compose_spec_keys.json"

#: What an extension field looks like in the schema: not a key of the specification.
_EXTENSION = "^x-"


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    """Follow ``$ref`` until a node that is not one."""
    hops = 0
    while isinstance(node, dict) and "$ref" in node and hops < 20:
        node = defs[node["$ref"].rsplit("/", 1)[-1]]
        hops += 1
    return node


def _objects(
    node: Any, defs: dict[str, Any], found: list[dict[str, Any]] | None = None
) -> list[Any]:
    """Every object schema reachable from a node through ``allOf``, ``oneOf``, ``anyOf`` and ``items``."""
    found = [] if found is None else found
    node = _resolve(node, defs)
    if not isinstance(node, dict):
        return found
    found.append(node)
    for combinator in ("allOf", "oneOf", "anyOf"):
        for sub in node.get(combinator, []):
            _objects(sub, defs, found)
    if isinstance(node.get("items"), dict):
        _objects(node["items"], defs, found)
    return found


def _properties(node: Any, defs: dict[str, Any]) -> dict[str, Any]:
    """The keys an object schema allows, each with its schema (the first that names it)."""
    keys: dict[str, Any] = {}
    for obj in _objects(node, defs):
        for name, sub in (obj.get("properties") or {}).items():
            keys.setdefault(name, sub)
    return keys


def extract(schema: dict[str, Any]) -> dict[str, list[str]]:
    """
    Read the keys of each section out of the schema.

    Args:
        schema: The Compose specification's JSON Schema.

    Returns:
        Each section's keys, sorted, under the names ``compose_keys.SECTIONS`` uses.
    """
    defs = schema.get("$defs") or schema.get("definitions") or {}
    service = {**_properties(defs["service"], defs), **_properties(defs["job"], defs)}
    sections: dict[str, set[str]] = {
        "top": set(schema["properties"]),
        "service": set(service),
        "pre_start": set(_properties(defs["pre_start_hook"], defs))
        if "pre_start_hook" in defs
        else set(),
        "hook": set(_properties(defs["service_hook"], defs)),
        "network": set(_properties(defs["network"], defs)),
        "volume": set(_properties(defs["volume"], defs)),
        "secret": set(_properties(defs["secret"], defs)),
        "config": set(_properties(defs["config"], defs)),
        "model": set(_properties(defs["model"], defs)),
        "include": set(_properties(defs["include"], defs)),
        "extends": set(_properties(service["extends"], defs)),
        "build": set(_properties(service["build"], defs)),
        "deploy": set(_properties(service["deploy"], defs)),
        "mount": set(_properties(service["volumes"], defs)),
    }
    return {
        name: sorted(key for key in keys if not key.startswith(_EXTENSION))
        for name, keys in sections.items()
    }


def _read(source: str | None) -> dict[str, Any]:
    """Read the schema from a file, or from where Compose publishes it."""
    if source:
        return json.loads(Path(source).read_text(encoding="utf-8"))
    with urllib.request.urlopen(SCHEMA_URL, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def document(schema: dict[str, Any], source: str) -> dict[str, Any]:
    """
    Args:
        schema: The schema.
        source: Where it was read from, for the file's own record.

    Returns:
        What the fixture holds: where and when, and the keys of each section.
    """
    return {
        "source": source,
        "read": date.today().isoformat(),
        "sections": extract(schema),
    }


def main(argv: list[str] | None = None) -> int:
    """
    Args:
        argv: The arguments; ``sys.argv`` by default.

    Returns:
        0 when the file was written or has every key the schema has; 1 when ``--check`` finds one
        it lacks.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Fail when the schema has a key the file lacks."
    )
    parser.add_argument(
        "--from", dest="source", help="A schema already on disk, instead of the network."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write.")
    args = parser.parse_args(argv)
    schema = _read(args.source)
    wanted = document(schema, args.source or SCHEMA_URL)
    if args.check:
        known = json.loads(args.output.read_text(encoding="utf-8"))["sections"]
        new = {
            section: sorted(set(keys) - set(known.get(section, ())))
            for section, keys in wanted["sections"].items()
        }
        new = {section: keys for section, keys in new.items() if keys}
        if new:
            print("Compose has keys the guard has not decided about:")
            for section, keys in sorted(new.items()):
                print(f"  {section}: {', '.join(keys)}")
            print(
                "Classify them in noust.deployers.helpers.compose_keys, then rerun without --check."
            )
            return 1
        return 0
    args.output.write_text(json.dumps(wanted, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
