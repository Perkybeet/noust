#!/usr/bin/env python3
"""
Compare the API against the baseline it promised to keep.

``panel/openapi.baseline.json`` is the API as a release shipped it, exported
the way ``panel/openapi.json`` is. Within an API version a client written
against the baseline must keep working, so this fails when the API:

- loses an operation, or answers it to another method;
- loses a response code it documents, a response field, or a media type;
- gains a required parameter or request-body field, or a body where it had none;
- loses a parameter or a request-body field.

Adding is always allowed. Extensions (``x-noust-*``), descriptions, titles and
types are not compared: what is promised is what a client sends and what it can
count on getting back, by name.

Usage:
    scripts/api_compat.py                           Fail if the API breaks the baseline.
    scripts/api_compat.py --update                  Take what the API added into the baseline.
    scripts/api_compat.py --update --accept-breaking
                                                    Take the API as it is, breaks included.
                                                    Record each one in docs/API-CHANGELOG.md.

``--accept-breaking`` is the one way to change what was promised, and it is meant to
be seen in review: the baseline changes in the same commit as the code that broke it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: The API as the last release that promised it shipped it.
BASELINE = ROOT / "panel" / "openapi.baseline.json"

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

_PATH_PARAMETER = re.compile(r"\{[^}]*\}")

#: Deeper than any model of this API; a guard, not a limit anyone meets.
MAX_DEPTH = 12

Schema = dict[str, Any]


@dataclass(frozen=True)
class Break:
    """
    One incompatible change.

    Attributes:
        operation: The operation it breaks, ``POST /api/apps``.
        kind: What kind of break, a stable word (``operation-removed``,
            ``method-changed``, ``response-removed``, ``parameter-required``,
            ``parameter-removed``, ``body-required``, ``body-removed``,
            ``body-field-required``, ``body-field-removed``,
            ``response-field-removed``, ``media-type-removed``).
        detail: What exactly, for the person reading the failure.
    """

    operation: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.operation}: {self.kind}: {self.detail}"


@dataclass(frozen=True)
class _Operation:
    """An operation with the parameters its path item shares with it."""

    path: str
    method: str
    document: dict[str, Any]
    body: dict[str, Any] | None
    responses: dict[str, Any]
    parameters: dict[tuple[str, str], dict[str, Any]]

    @property
    def label(self) -> str:
        return f"{self.method.upper()} {self.path}"


@dataclass
class _Shape:
    """What a client can see of a schema: its fields, which are required, its items."""

    properties: dict[str, Schema]
    required: set[str]
    items: list[Schema]


def _deref(document: dict[str, Any], node: Any) -> tuple[Any, str | None]:
    """
    Follow local ``$ref`` links to what they name.

    Args:
        document: The OpenAPI document the reference points into.
        node: A schema, parameter, request body or response, or a reference.

    Returns:
        ``(target, last reference)``; the target is None when a reference
        points at nothing, and the reference is None for an inline node.
    """
    reference: str | None = None
    for _ in range(MAX_DEPTH):
        if not isinstance(node, dict) or "$ref" not in node:
            return node, reference
        reference = str(node["$ref"])
        target: Any = document
        for part in reference.removeprefix("#/").split("/"):
            target = target.get(part) if isinstance(target, dict) else None
        node = target
    return None, reference


def _shape(document: dict[str, Any], schema: Any, trail: frozenset[str] = frozenset()) -> _Shape:
    """
    Flatten a schema into the fields a client sees.

    ``allOf`` adds its parts together; ``anyOf`` and ``oneOf`` offer
    alternatives, so a field is there if any has it and required only if every
    alternative that has fields requires it.

    Args:
        document: The document the schema belongs to.
        schema: The schema, or a reference to one.
        trail: The references being followed, so a model that contains itself ends.

    Returns:
        The shape, empty for what is not an object or an array.
    """
    resolved, reference = _deref(document, schema)
    if not isinstance(resolved, dict) or (reference is not None and reference in trail):
        return _Shape({}, set(), [])
    if reference is not None:
        trail = trail | {reference}
    properties: dict[str, Schema] = dict(resolved.get("properties") or {})
    required = set(resolved.get("required") or [])
    items: list[Schema] = [resolved["items"]] if isinstance(resolved.get("items"), dict) else []
    for part in resolved.get("allOf") or []:
        inner = _shape(document, part, trail)
        for name, value in inner.properties.items():
            properties.setdefault(name, value)
        required |= inner.required
        items += inner.items
    alternatives = [
        _shape(document, part, trail)
        for key in ("anyOf", "oneOf")
        for part in resolved.get(key) or []
    ]
    for alternative in alternatives:
        for name, value in alternative.properties.items():
            properties.setdefault(name, value)
        items += alternative.items
    with_fields = [alternative for alternative in alternatives if alternative.properties]
    if with_fields:
        required |= set.intersection(*(alternative.required for alternative in with_fields))
    return _Shape(properties, required, items)


def _operations(document: dict[str, Any]) -> dict[tuple[str, str], _Operation]:
    """
    Index a document's operations by what a client addresses them by.

    A path template's parameter names are not on the wire (``/apps/{domain}``
    and ``/apps/{name}`` are one address), so the key blanks them.

    Args:
        document: An OpenAPI document.

    Returns:
        ``(template with blanked parameters, method)`` to the operation.
    """
    found: dict[tuple[str, str], _Operation] = {}
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters") or []
        for method in HTTP_METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            parameters: dict[tuple[str, str], dict[str, Any]] = {}
            for declared in [*shared, *(operation.get("parameters") or [])]:
                parameter, _ = _deref(document, declared)
                if isinstance(parameter, dict) and "name" in parameter and "in" in parameter:
                    parameters[(str(parameter["in"]), str(parameter["name"]))] = parameter
            body, _ = _deref(document, operation.get("requestBody"))
            found[(_PATH_PARAMETER.sub("{}", path), method)] = _Operation(
                path=path,
                method=method,
                document=document,
                body=body if isinstance(body, dict) else None,
                responses=operation.get("responses") or {},
                parameters=parameters,
            )
    return found


def _compare_schema(
    before: _Operation,
    after: _Operation,
    old: Any,
    new: Any,
    *,
    request: bool,
    where: str,
    out: list[Break],
    seen: set[tuple[str, str]],
    depth: int = 0,
) -> None:
    """
    Compare what a client sees of one schema in two documents, and recurse.

    Args:
        before: The baseline's operation, for its document and its label.
        after: The current operation.
        old: The baseline's schema.
        new: The current one.
        request: True for a request body (a new required field is a break);
            False for a response (a removed field is).
        where: The dotted path to this schema inside the body.
        out: Where the breaks are appended.
        seen: The pairs of references already compared, so shared and
            recursive models are walked once.
        depth: How deep this is.
    """
    if depth > MAX_DEPTH:
        return
    _, old_reference = _deref(before.document, old)
    _, new_reference = _deref(after.document, new)
    if old_reference is not None and new_reference is not None:
        if (old_reference, new_reference) in seen:
            return
        seen.add((old_reference, new_reference))

    old_shape = _shape(before.document, old)
    new_shape = _shape(after.document, new)
    prefix = f"{where}." if where else ""
    for name, old_field in old_shape.properties.items():
        if name not in new_shape.properties:
            kind = "body-field-removed" if request else "response-field-removed"
            out.append(Break(before.label, kind, f"{prefix}{name} is gone"))
            continue
        _compare_schema(
            before,
            after,
            old_field,
            new_shape.properties[name],
            request=request,
            where=f"{prefix}{name}",
            out=out,
            seen=seen,
            depth=depth + 1,
        )
    if request:
        for name in sorted(new_shape.required - old_shape.required):
            out.append(
                Break(
                    before.label,
                    "body-field-required",
                    f"{prefix}{name} is required now",
                )
            )
    if old_shape.items and new_shape.items:
        _compare_schema(
            before,
            after,
            old_shape.items[0],
            new_shape.items[0],
            request=request,
            where=f"{where}[]",
            out=out,
            seen=seen,
            depth=depth + 1,
        )


def _compare_content(
    before: _Operation,
    after: _Operation,
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    request: bool,
    label: str,
    out: list[Break],
    seen: set[tuple[str, str]],
) -> None:
    """
    Compare the media types of a request body or a response.

    Args:
        before: The baseline's operation.
        after: The current operation.
        old: The baseline's body or response, resolved.
        new: The current one, resolved.
        request: Whether this is a request body.
        label: What it is, for the message (``the request body``, ``200``).
        out: Where the breaks are appended.
        seen: The references already compared for this operation.
    """
    new_content = new.get("content") or {}
    for media, old_media in (old.get("content") or {}).items():
        if media not in new_content:
            out.append(Break(before.label, "media-type-removed", f"{label} no longer has {media}"))
            continue
        _compare_schema(
            before,
            after,
            (old_media or {}).get("schema"),
            (new_content[media] or {}).get("schema"),
            request=request,
            where="",
            out=out,
            seen=seen,
        )


def _compare_operation(before: _Operation, after: _Operation) -> list[Break]:
    """
    Everything one operation of the baseline promised.

    Args:
        before: The baseline's operation.
        after: The same operation in the current document.

    Returns:
        Its breaks.
    """
    out: list[Break] = []
    seen: set[tuple[str, str]] = set()

    for status in sorted(before.responses):
        if status not in after.responses:
            out.append(Break(before.label, "response-removed", f"the {status} response is gone"))
            continue
        old, _ = _deref(before.document, before.responses[status])
        new, _ = _deref(after.document, after.responses[status])
        if isinstance(old, dict) and isinstance(new, dict):
            _compare_content(
                before,
                after,
                old,
                new,
                request=False,
                label=f"the {status} response",
                out=out,
                seen=seen,
            )

    # A path parameter is in the template, which already matched.
    for key in sorted(before.parameters):
        if key[0] != "path" and key not in after.parameters:
            out.append(Break(before.label, "parameter-removed", f"{key[0]} parameter {key[1]}"))
    for key, parameter in sorted(after.parameters.items()):
        if key[0] == "path" or not parameter.get("required"):
            continue
        was = before.parameters.get(key)
        if was is None or not was.get("required"):
            out.append(Break(before.label, "parameter-required", f"{key[0]} parameter {key[1]}"))

    if after.body is not None and after.body.get("required"):
        if before.body is None or not before.body.get("required"):
            out.append(Break(before.label, "body-required", "a request body is required now"))
    if before.body is not None:
        if after.body is None:
            out.append(Break(before.label, "body-removed", "the operation takes no body now"))
        else:
            _compare_content(
                before,
                after,
                before.body,
                after.body,
                request=True,
                label="the request body",
                out=out,
                seen=seen,
            )
    return out


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> list[Break]:
    """
    Find what the current API no longer keeps of what the baseline promised.

    Args:
        baseline: The OpenAPI document of the API as it was promised.
        current: The OpenAPI document of the API as it is.

    Returns:
        Every break, in the baseline's order of paths and methods. Empty when
        the current API only adds.
    """
    promised = _operations(baseline)
    served = _operations(current)
    breaks: list[Break] = []
    for key in sorted(promised):
        before = promised[key]
        after = served.get(key)
        if after is not None:
            breaks.extend(_compare_operation(before, after))
            continue
        offered = {method for (template, method) in served if template == key[0]}
        asked = {method for (template, method) in promised if template == key[0]}
        instead = sorted(offered - asked)
        if instead:
            breaks.append(
                Break(
                    before.label,
                    "method-changed",
                    "the path answers " + ", ".join(m.upper() for m in instead) + " now",
                )
            )
        else:
            breaks.append(Break(before.label, "operation-removed", "no longer served"))
    return breaks


def _count(promised: int) -> str:
    """
    Args:
        promised: A number of operations.

    Returns:
        ``1 operation`` or ``N operations``.
    """
    return f"{promised} operation" + ("" if promised == 1 else "s")


def _current_document() -> dict[str, Any]:
    """
    Build the API's schema the way the export does.

    Returns:
        The OpenAPI document of the application as it is now.
    """
    try:
        from scripts.export_openapi import _build_schema
    except ModuleNotFoundError:  # run as a script: scripts/ is sys.path[0]
        from export_openapi import _build_schema  # type: ignore[no-redef]
    return _build_schema()


def _render(document: dict[str, Any]) -> str:
    """
    Args:
        document: An OpenAPI document.

    Returns:
        The text ``scripts/export_openapi.py`` writes for it.
    """
    return json.dumps(document, sort_keys=True, indent=2) + "\n"


def _guidance(breaks: list[Break]) -> str:
    """
    Args:
        breaks: What broke.

    Returns:
        The failure message: each break, then what to do.
    """
    lines = [str(item) for item in breaks]
    return (
        f"The API breaks what {BASELINE.name} promises ({len(breaks)}):\n"
        + "\n".join(f"  {line}" for line in lines)
        + "\n\nUndo the change, or make it on purpose: run "
        "'python scripts/api_compat.py --update --accept-breaking' and add each one to "
        "docs/API-CHANGELOG.md under 'Breaking changes' (what, why, what a client does now)."
    )


def main(argv: list[str] | None = None) -> int:
    """
    Command line entry point.

    Args:
        argv: Arguments, defaulting to ``sys.argv``.

    Returns:
        0 when the API keeps the baseline (or the baseline was updated), 1
        when it breaks it or the baseline is missing.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("--baseline", type=Path, default=BASELINE, help="the promised API")
    parser.add_argument(
        "--current", type=Path, help="an OpenAPI file to compare instead of building the API"
    )
    parser.add_argument(
        "--update", action="store_true", help="take the API as it is into the baseline"
    )
    parser.add_argument(
        "--accept-breaking",
        action="store_true",
        help="with --update, also take what breaks the baseline",
    )
    args = parser.parse_args(argv)

    current = (
        json.loads(args.current.read_text(encoding="utf-8"))
        if args.current
        else _current_document()
    )

    if not args.baseline.exists():
        if not args.update:
            print(
                f"{args.baseline} does not exist. Run scripts/api_compat.py --update.",
                file=sys.stderr,
            )
            return 1
        breaks: list[Break] = []
    else:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        breaks = compare(baseline, current)

    if breaks and not (args.update and args.accept_breaking):
        print(_guidance(breaks), file=sys.stderr)
        return 1

    if args.update:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(_render(current), encoding="utf-8")
        print(f"Wrote {args.baseline}")
        return 0

    promised = len(_operations(json.loads(args.baseline.read_text(encoding="utf-8"))))
    print(f"The API keeps all {_count(promised)} of {args.baseline.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
