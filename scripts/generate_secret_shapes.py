#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Write ``panel/src/api/secretShapes.gen.ts`` from the shapes and the rules of ``noust.core.secret_detection``.

The report of a problem the console makes adds a block of what the browser knows (the page, the last
errors), and that text is masked in the browser, because the report is also made when the server
cannot be reached, which is exactly when the error screen appears. The console does not carry a
second list of what a secret looks like: it reads this file, which is ``TEXT_SHAPES`` (the tuple
``mask_secret_shapes`` walks) turned into JavaScript regular expressions, together with the cases of
``tests/support/secret_shape_cases.py``, so the same table is run on both sides.

A regular expression is translated, not rewritten: Python and JavaScript read most of it alike, and
what they read differently is refused with a message instead of being turned into something that
looks right (``\\A``, ``\\Z`` and ``$``, a named group, an inline flag other than a leading ``(?i)``,
an atomic group or a possessive quantifier, ``\\d`` and ``\\w``, which are wider in Python). ``\\b``
and ``\\s`` are accepted: they differ only for characters outside ASCII (and a few control
characters), and there JavaScript hides more or the same, never less. ``--check`` fails when the
committed file is not what the shapes generate (a test and CI run it); without it the file is written.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from noust.core.secret_detection import TEXT_SHAPES, variable_rules  # noqa: E402
from tests.support.secret_shape_cases import CASES, MASK, VARIABLE_CASES  # noqa: E402

DEFAULT_OUTPUT = ROOT / "panel" / "src" / "api" / "secretShapes.gen.ts"

#: The flags a pattern may carry: every ``str`` pattern has UNICODE, and the case-insensitive one is
#: the ``i`` of JavaScript. Any other (MULTILINE, DOTALL, ASCII, VERBOSE...) changes what a character
#: class or an anchor means and has no equal there.
_SHARED_FLAGS = re.UNICODE | re.IGNORECASE

#: An escape that Python reads and JavaScript reads as another thing (or as none).
_ESCAPES = {
    "A": "`\\A` (the start of the text) does not exist in JavaScript",
    "Z": "`\\Z` (the very end) does not exist in JavaScript; write `(?![\\s\\S])`",
    "z": "`\\z` (the very end) does not exist in JavaScript; write `(?![\\s\\S])`",
    "G": "`\\G` does not exist in JavaScript",
    "d": "`\\d` is any Unicode digit in Python and only 0-9 in JavaScript; write `[0-9]`",
    "D": "`\\D` is any Unicode non-digit in Python and a different set in JavaScript; write `[^0-9]`",
    "w": "`\\w` is any Unicode letter in Python and only ASCII in JavaScript; write `[A-Za-z0-9_]`",
    "W": "`\\W` is any Unicode non-letter in Python and a different set in JavaScript",
    "N": "`\\N{name}` does not exist in JavaScript",
    "a": "`\\a` is the bell in Python and a plain `a` in JavaScript",
}

#: What may follow ``(?``: a group that does not capture, and the four looks around.
_LOOK = (":", "=", "!", "<=", "<!")


class UnsupportedShape(ValueError):
    """A pattern uses something Python and JavaScript read differently."""


def _refuse(source: str, reason: str) -> UnsupportedShape:
    return UnsupportedShape(
        f"The pattern `{source}` cannot be sent to the console: {reason}. "
        "Write it in the dialect both languages read the same way (see TextShape)."
    )


def _group_problem(body: str, at: int) -> str | None:
    """
    Args:
        body: The pattern.
        at: The index of a ``(`` followed by ``?``.

    Returns:
        Why the group is not read the same way by both languages, or None when it is.
    """
    rest = body[at + 2 :]
    if rest.startswith(_LOOK):
        return None
    if rest.startswith("P<"):
        return "`(?P<` is Python's named group; JavaScript writes `(?<` and Python does not, so use a numbered group"
    if rest.startswith(("P=", "P>")):
        return "`(?P=` and `(?P>` are Python's named groups; use a numbered group"
    if rest.startswith("#"):
        return "`(?#` is a comment in Python only"
    if rest.startswith("("):
        return "`(?(` is a conditional, which JavaScript does not have"
    if rest.startswith(">"):
        return "`(?>` is an atomic group, which JavaScript does not have"
    if re.match(r"[aiLmsux-]", rest):
        return "an inline flag other than a leading `(?i)` has no equal in JavaScript"
    return "this group is not one both languages read the same way"


def _problem(body: str) -> str | None:
    """
    Args:
        body: A pattern, without its leading ``(?i)``.

    Returns:
        The first thing in it that JavaScript would read differently, or None.
    """
    in_class = False
    quantified = False
    at = 0
    while at < len(body):
        char = body[at]
        if char == "\\":
            reason = _ESCAPES.get(body[at + 1 : at + 2])
            if reason is not None:
                return reason
            at += 2
            quantified = False
            continue
        if in_class:
            in_class = char != "]"
            at += 1
            continue
        if char == "[":
            in_class = True
            at += 1
            if body[at : at + 1] == "^":
                at += 1
            if body[at : at + 1] == "]":
                return "a `]` first in a class is a letter in Python and the end of an empty class in JavaScript"
            quantified = False
            continue
        if char == "$":
            return "`$` also matches before a final newline in Python, and only at the very end in JavaScript; write `(?![\\s\\S])`"
        if char == "(" and body[at + 1 : at + 2] == "?":
            reason = _group_problem(body, at)
            if reason is not None:
                return reason
            at += 2
            quantified = False
            continue
        if char == "+" and quantified:
            return "a possessive quantifier (`*+`, `++`, `?+`, `}+`) does not exist in JavaScript"
        quantified = char in "*+?}"
        at += 1
    return None


def javascript_source(source: str, flags: int) -> tuple[str, str]:
    """
    Turn the source of a Python pattern into the one JavaScript reads the same way.

    Args:
        source: The pattern as written (``Pattern.pattern``).
        flags: Its compiled flags (``Pattern.flags``).

    Returns:
        The source for ``new RegExp`` and its flags: always ``g`` (every match is replaced), and
        ``i`` when the pattern is case-insensitive, which a leading ``(?i)`` says in Python.

    Raises:
        UnsupportedShape: When the pattern uses something JavaScript reads differently, with what.
    """
    unshared = flags & ~_SHARED_FLAGS
    if unshared:
        raise _refuse(
            source,
            f"it is compiled with the flags {re.RegexFlag(unshared)!r}, which JavaScript lacks",
        )
    body = source
    javascript_flags = "g"
    if body.startswith("(?i)"):
        body = body.removeprefix("(?i)")
        javascript_flags += "i"
    elif flags & re.IGNORECASE:
        javascript_flags += "i"
    reason = _problem(body)
    if reason is not None:
        raise _refuse(source, reason)
    return body, javascript_flags


def javascript_form(pattern: re.Pattern[str]) -> tuple[str, str]:
    """
    Args:
        pattern: A compiled shape.

    Returns:
        Its source and flags for JavaScript (see :func:`javascript_source`).

    Raises:
        UnsupportedShape: When JavaScript would read it differently.
    """
    return javascript_source(pattern.pattern, pattern.flags)


def typescript_shapes() -> str:
    """
    Returns:
        The TypeScript module the console reads (``panel/src/api/secretShapes.gen.ts``), byte for
        byte what the script writes: each shape of ``TEXT_SHAPES`` in the order it is applied, and
        the table of cases both sides are held to.

    Raises:
        UnsupportedShape: When a shape cannot be said in JavaScript.
    """
    shapes = []
    for shape in TEXT_SHAPES:
        source, flags = javascript_form(shape.pattern)
        shapes.append(
            f"  {{ name: {json.dumps(shape.name)}, source: {json.dumps(source)}, "
            f"flags: {json.dumps(flags)}, keepHead: {json.dumps(shape.keep_head)}, "
            f"ofValue: {json.dumps(shape.of_value)} }},"
        )
    cases = [
        f"  {{ name: {json.dumps(case.name)}, text: {json.dumps(case.text)}, "
        f"masked: {json.dumps(case.masked)} }},"
        for case in CASES
    ]
    rules = variable_rules()
    entropy = rules["entropy"]
    variable_cases = [
        f"  {{ name: {json.dumps(case.name)}, value: {json.dumps(case.value)}, "
        f"secret: {json.dumps(case.secret)} }},"
        for case in VARIABLE_CASES
    ]
    return (
        "// Generated by scripts/generate_secret_shapes.py from TEXT_SHAPES in\n"
        "// src/noust/core/secret_detection.py and the cases of tests/support/secret_shape_cases.py.\n"
        "// Do not edit: change the shape there and run the script.\n"
        "\n"
        "export interface SecretShape {\n"
        "  /** What the shape is. */\n"
        "  readonly name: string;\n"
        "  /** The pattern, in the dialect Python and JavaScript read alike. */\n"
        "  readonly source: string;\n"
        "  /** The flags for `new RegExp`: `g`, and `i` when the shape ignores case. */\n"
        "  readonly flags: string;\n"
        "  /** Whether group 1 stays as it was and only what follows it is replaced. */\n"
        "  readonly keepHead: boolean;\n"
        "  /** Whether it is a credential on its own, so a value typed in a field is judged by it. */\n"
        "  readonly ofValue: boolean;\n"
        "}\n"
        "\n"
        "/** One line of text and what masking it leaves: what both sides are held to. */\n"
        "export interface SecretShapeCase {\n"
        "  readonly name: string;\n"
        "  readonly text: string;\n"
        "  /** The same text when nothing in it is a secret. */\n"
        "  readonly masked: string;\n"
        "}\n"
        "\n"
        "/** What is written where a secret was. */\n"
        f"export const SECRET_MASK = {json.dumps(MASK)};\n"
        "\n"
        "/** Every shape of a secret, in the order it is applied. */\n"
        "export const SECRET_SHAPES: readonly SecretShape[] = [\n" + "\n".join(shapes) + "\n];\n"
        "\n"
        "/** What the shapes hide and what they leave alone. */\n"
        "export const SECRET_SHAPE_CASES: readonly SecretShapeCase[] = [\n"
        + "\n".join(cases)
        + "\n];\n"
        "\n"
        "/** What `classify` judges a variable by besides the shapes: its name, what makes a name public, how random a value is. */\n"
        "export interface SecretVariableRules {\n"
        "  /** Matched in the upper-case name. */\n"
        "  readonly nameSubstrings: readonly string[];\n"
        "  /** Whole words of a name split on separators and camel case. */\n"
        "  readonly nameWords: readonly string[];\n"
        "  /** Names the words excuse. */\n"
        "  readonly nonSecretNames: readonly string[];\n"
        "  /** A name that starts so is public by its framework's contract. */\n"
        "  readonly publicPrefixes: readonly string[];\n"
        "  /** A name that holds one of these is never public, whatever it starts with. */\n"
        "  readonly neverPublicWords: readonly string[];\n"
        "  readonly entropy: { readonly minLength: number; readonly minBits: number; readonly minClasses: number };\n"
        "}\n"
        "\n"
        "export const SECRET_VARIABLE_RULES: SecretVariableRules = {\n"
        f"  nameSubstrings: {json.dumps(rules['name_substrings'])},\n"
        f"  nameWords: {json.dumps(rules['name_words'])},\n"
        f"  nonSecretNames: {json.dumps(rules['non_secret_names'])},\n"
        f"  publicPrefixes: {json.dumps(rules['public_prefixes'])},\n"
        f"  neverPublicWords: {json.dumps(rules['never_public_words'])},\n"
        f"  entropy: {{ minLength: {entropy['min_length']}, minBits: {entropy['min_bits']}, minClasses: {entropy['min_classes']} }},\n"
        "};\n"
        "\n"
        "/** One variable and whether it is a secret: what the server and the console are held to. */\n"
        "export interface SecretVariableCase {\n"
        "  readonly name: string;\n"
        "  readonly value: string;\n"
        "  readonly secret: boolean;\n"
        "}\n"
        "\n"
        "export const SECRET_VARIABLE_CASES: readonly SecretVariableCase[] = [\n"
        + "\n".join(variable_cases)
        + "\n];\n"
    )


def main(argv: list[str] | None = None) -> int:
    """
    Args:
        argv: The arguments; ``sys.argv`` by default.

    Returns:
        0 when the file was written or is current, 1 when ``--check`` finds it stale.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when the file is stale.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write.")
    args = parser.parse_args(argv)
    try:
        wanted = typescript_shapes()
    except UnsupportedShape as error:
        print(error)
        return 1
    present = args.output.read_text(encoding="utf-8") if args.output.exists() else None
    if args.check:
        if present != wanted:
            print(f"{args.output} is stale: run python scripts/generate_secret_shapes.py")
            return 1
        return 0
    args.output.write_text(wanted, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
