# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The shapes of a secret are one list, and the console reads that list, not a copy of it.

``mask_secret_shapes`` walks ``TEXT_SHAPES``; ``scripts/generate_secret_shapes.py`` writes the same
tuple into ``panel/src/api/secretShapes.gen.ts``, with the cases of
``tests/support/secret_shape_cases.py``, so the report of a problem the console makes can hide a
secret in the text of the browser even when the server is down. These tests hold the three
together: the table through Python's masking, the generated file against the tuple, and the
generator against every construction JavaScript would read differently from Python.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

from noust.core import secret_detection
from noust.core.secret_detection import TEXT_SHAPES, TextShape, mask_secret_shapes
from tests.support.secret_shape_cases import CASES, MASK, ShapeCase

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ROOT / "panel" / "src" / "api" / "secretShapes.gen.ts"


def _generator() -> ModuleType:
    script = ROOT / "scripts" / "generate_secret_shapes.py"
    spec = importlib.util.spec_from_file_location("generate_secret_shapes", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- the table, through Python ---------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_python_hides_what_the_table_says_and_leaves_the_rest(case: ShapeCase) -> None:
    assert mask_secret_shapes(case.text) == case.masked


def test_the_table_says_the_placeholder_python_writes() -> None:
    assert MASK == secret_detection.REDACTED


def test_the_table_has_both_a_secret_and_a_harmless_line_of_each_kind() -> None:
    hidden = [case for case in CASES if case.masked != case.text]
    kept = [case for case in CASES if case.masked == case.text]

    assert len(hidden) >= 25
    assert len(kept) >= 10
    assert len({case.name for case in CASES}) == len(CASES)


# -- one list in Python ----------------------------------------------------------------


def test_masking_walks_the_one_tuple_of_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    only_digits = TextShape("digits", re.compile(r"[0-9]{4,}"))
    monkeypatch.setattr(secret_detection, "TEXT_SHAPES", (only_digits,))

    assert mask_secret_shapes("pin 12345 and ghp_" + "a" * 36) == f"pin {MASK} and ghp_" + "a" * 36


def test_a_kept_head_stays_and_what_follows_it_goes() -> None:
    shape = TextShape("assigned", re.compile(r"(key=)(\S+)"), keep_head=True)

    assert shape.mask("key=abc def", "***") == "key=*** def"


def test_every_vendor_shape_is_in_the_tuple() -> None:
    names = {shape.name for shape in TEXT_SHAPES}

    for kind, pattern in secret_detection._VALUE_PATTERNS:
        assert kind in names
        assert any(shape.pattern is pattern for shape in TEXT_SHAPES)


def test_the_shapes_have_a_name_each_and_a_head_where_one_is_kept() -> None:
    assert len({shape.name for shape in TEXT_SHAPES}) == len(TEXT_SHAPES)
    for shape in TEXT_SHAPES:
        if shape.keep_head:
            assert shape.pattern.groups >= 1, shape.name


def test_the_shapes_are_applied_in_the_order_they_are_listed() -> None:
    # An Authorization header keeps its scheme word, which only holds while the assignment is
    # read before the bare Bearer rule; the console reads the same list in the same order.
    names = [shape.name for shape in TEXT_SHAPES]

    assert names.index("assigned secret") < names.index("bearer")
    assert names.index("private key block") < names.index("private key")


# -- the generated file ----------------------------------------------------------------


def test_the_file_the_console_reads_is_what_the_shapes_generate() -> None:
    generator = _generator()

    assert GENERATED.read_text(encoding="utf-8") == generator.typescript_shapes(), (
        "panel/src/api/secretShapes.gen.ts is stale: run python scripts/generate_secret_shapes.py"
    )


def test_the_generated_file_carries_every_shape_and_every_case() -> None:
    generator = _generator()
    text = generator.typescript_shapes()

    for shape in TEXT_SHAPES:
        assert json.dumps(shape.name) in text, shape.name
    for case in CASES:
        assert json.dumps(case.name) in text, case.name


def test_the_generator_script_checks_and_writes_the_same_file(tmp_path: Path) -> None:
    generator = _generator()

    assert generator.main(["--check"]) == 0
    stale = tmp_path / "secretShapes.gen.ts"
    stale.write_text("// old\n", encoding="utf-8")
    assert generator.main(["--check", "--output", str(stale)]) == 1
    assert generator.main(["--output", str(stale)]) == 0
    assert stale.read_text(encoding="utf-8") == generator.typescript_shapes()


# -- what JavaScript would read differently --------------------------------------------


def test_a_leading_case_insensitive_flag_becomes_the_flag_of_javascript() -> None:
    generator = _generator()

    source, flags = generator.javascript_form(re.compile(r"(?i)\b(Bearer\s+)[a-z]{4,}"))

    assert source == r"\b(Bearer\s+)[a-z]{4,}"
    assert flags == "gi"
    assert generator.javascript_form(re.compile(r"abc"))[1] == "g"
    assert generator.javascript_form(re.compile(r"abc", re.IGNORECASE)) == ("abc", "gi")


@pytest.mark.parametrize(
    ("source", "why"),
    [
        (r"\Aabc", r"\A"),
        (r"abc\Z", r"\Z"),
        (r"abc$", "$"),
        (r"(?P<name>abc)", "(?P<"),
        (r"(?P<name>a)(?P=name)", "(?P"),
        (r"a(?i:b)c", "inline flag"),
        (r"a(?s)b", "inline flag"),
        (r"(?i)a(?i)b", "inline flag"),
        (r"(?#comment)a", "comment"),
        (r"(?(1)a|b)(c)", "conditional"),
        (r"(?>abc)", "atomic"),
        (r"a++b", "possessive"),
        (r"\d{4}", r"\d"),
        (r"\w+", r"\w"),
        (r"\Wx", r"\W"),
    ],
)
def test_a_construction_javascript_reads_differently_is_refused_with_its_reason(
    source: str, why: str
) -> None:
    generator = _generator()

    # The source as a string: an atomic group or a possessive quantifier does not compile before
    # Python 3.11, and the generator has to refuse it whichever Python builds the file.
    with pytest.raises(generator.UnsupportedShape) as caught:
        generator.javascript_source(source, 0)

    assert why in str(caught.value)
    assert source in str(caught.value)


@pytest.mark.parametrize(
    "flag",
    [re.MULTILINE, re.DOTALL, re.ASCII, re.VERBOSE],
    ids=["multiline", "dotall", "ascii", "verbose"],
)
def test_a_flag_javascript_does_not_share_is_refused(flag: re.RegexFlag) -> None:
    generator = _generator()

    with pytest.raises(generator.UnsupportedShape):
        generator.javascript_form(re.compile(r"abc", flag))


@pytest.mark.parametrize(
    "source",
    [
        r"[$]x",
        r"[\s\S]{0,16}?(?![\s\S])",
        r"(?<![A-Za-z])(a)(?=b)(?!c)(?<=d)",
        r"\bx\B\s\S",
        r"\\Z and \$",
    ],
)
def test_what_both_languages_read_alike_is_accepted(source: str) -> None:
    generator = _generator()

    assert generator.javascript_form(re.compile(source))[0] == source


def test_every_shape_of_the_tuple_is_in_the_dialect_both_languages_read() -> None:
    generator = _generator()

    for shape in TEXT_SHAPES:
        source, flags = generator.javascript_form(shape.pattern)
        assert "g" in flags
        assert source, shape.name
