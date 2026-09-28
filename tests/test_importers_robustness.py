# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Other platforms' files that are odd, hostile or merely unusual.

The inspection reads a repository somebody else wrote, so a file nested
past the parser's recursion limit is an unreadable file, not a 500. On
Python 3.10 ``railway.toml`` is read by WASM's own TOML reader, pinned here
against ``tomllib`` on a table of inputs (the comparison runs where
``tomllib`` exists; the expected values are written out for 3.10). Render
and Heroku are pinned on the shapes their own importers used to trip on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_importers import joined, tree
from wasm.core.exceptions import ValidationError
from wasm.deployers.importers import propose, read_platform
from wasm.deployers.importers.toml_fallback import load_toml_fallback

# Nesting --------------------------------------------------------------------------

DEEP = 100_000


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("vercel.json", "[" * DEEP + "]" * DEEP),
        ("railway.json", '{"a":' * (DEEP // 10) + "1" + "}" * (DEEP // 10)),
        ("railway.toml", "a = " + "[" * DEEP + "]" * DEEP + "\n"),
        ("render.yaml", "[" * DEEP + "]" * DEEP),
    ],
    ids=["json-array", "json-object", "toml", "yaml"],
)
def test_a_file_nested_past_the_limit_is_an_unreadable_file(
    tmp_path: Path, name: str, text: str
) -> None:
    tree(tmp_path, {name: text})

    proposal = propose(tmp_path)

    assert proposal is not None
    assert "nests too deeply" in joined(proposal) or "not valid TOML" in joined(proposal)


def test_the_fallback_reader_caps_nesting() -> None:
    with pytest.raises(ValidationError, match="not valid TOML") as caught:
        load_toml_fallback("a = " + "[" * 100 + "]" * 100, name="railway.toml")
    assert "levels deep" in caught.value.details


# TOML on Python 3.10 ------------------------------------------------------------------

PARITY: list[tuple[str, dict[str, Any]]] = [
    (
        '[deploy]\nstartCommand = "sh -c \\"npm start\\""\n',
        {"deploy": {"startCommand": 'sh -c "npm start"'}},
    ),
    (
        'a = "tab\\there\\nline \\\\ \\u00e9 \\U0001F600"\n',
        {"a": "tab\there\nline \\ \u00e9 \U0001f600"},
    ),
    ("a = 'C:\\path\\no \"escapes\"'\n", {"a": 'C:\\path\\no "escapes"'}),
    ('a = """\nline one\nline "two"\n"""\n', {"a": 'line one\nline "two"\n'}),
    ('a = """joined \\\n     here"""\n', {"a": "joined here"}),
    ("a = '''\nraw \\n text'''\n", {"a": "raw \\n text"}),
    ('a = """ends with a quote""""\n', {"a": 'ends with a quote"'}),
    (
        "[build]\nwatchPatterns = [\n  \"src/**\", # the code\n  'docs/**',\n]\n",
        {"build": {"watchPatterns": ["src/**", "docs/**"]}},
    ),
    (
        'deploy.healthcheckPath = "/up"\ndeploy.healthcheckTimeout = 1_000\n',
        {"deploy": {"healthcheckPath": "/up", "healthcheckTimeout": 1000}},
    ),
    ('"quoted key" = 1\n[a."b.c"]\nd = true\n', {"quoted key": 1, "a": {"b.c": {"d": True}}}),
    ("a = { b = 1, c.d = 'x' }\n", {"a": {"b": 1, "c": {"d": "x"}}}),
    ('a = [[1, 2], ["x"]]\nb = []\n', {"a": [[1, 2], ["x"]], "b": []}),
    ("n = [0xff, 0o17, 0b101, -3, +4, 0]\n", {"n": [255, 15, 5, -3, 4, 0]}),
    ("f = [1.5, -0.25, 3e2, 6.02E+23, 1_000.5]\n", {"f": [1.5, -0.25, 300.0, 6.02e23, 1000.5]}),
    ("# comment only\n\n[t] # trailing\nk = 'v' # after\n", {"t": {"k": "v"}}),
    ("a = 1\r\nb = 'x'\r\n", {"a": 1, "b": "x"}),
]


@pytest.mark.parametrize(("text", "expected"), PARITY)
def test_the_fallback_reads_toml_as_tomllib_does(text: str, expected: dict[str, Any]) -> None:
    assert load_toml_fallback(text, name="railway.toml") == expected
    if sys.version_info >= (3, 11):
        import tomllib

        assert tomllib.loads(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "a = 'unterminated\n",
        'a = "bad \\q escape"\n',
        "a = 1 2\n",
        "this is not toml\n",
        "a = 1\na = 2\n",
        "a = 007\n",
        "[a\n",
    ],
)
def test_the_fallback_refuses_what_tomllib_refuses(text: str) -> None:
    with pytest.raises(ValidationError, match="not valid TOML"):
        load_toml_fallback(text, name="railway.toml")
    if sys.version_info >= (3, 11):
        import tomllib

        with pytest.raises(tomllib.TOMLDecodeError):
            tomllib.loads(text)


def test_what_the_fallback_does_not_read_is_a_warning_not_a_failure() -> None:
    warnings: list[str] = []
    text = (
        "[deploy]\nstartCommand = 'npm start'\ncreated = 1979-05-27T07:32:00Z\n"
        "[[services]]\nname = 'web'\n[build]\nbuilder = 'NIXPACKS'\n"
    )

    parsed = load_toml_fallback(text, name="railway.toml", warn=warnings.append)

    assert parsed == {"deploy": {"startCommand": "npm start"}, "build": {"builder": "NIXPACKS"}}
    assert any("line 3: deploy.created holds a date" in w for w in warnings)
    assert any("line 4: [[services]] is an array of tables" in w for w in warnings)


def test_railway_on_python_3_10_unescapes_the_start_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 10, 12, "final", 0))
    tree(
        tmp_path,
        {
            "railway.toml": '[deploy]\nstartCommand = "sh -c \\"npm start\\""\n'
            "[[deploy.sidecars]]\nname = 'x'\n"
        },
    )

    proposal = read_platform("railway", tmp_path)

    assert proposal.start_command == 'sh -c "npm start"'
    assert "array of tables" in joined(proposal)


# Render ---------------------------------------------------------------------------------


def test_render_without_a_web_service_names_the_others(tmp_path: Path) -> None:
    tree(
        tmp_path,
        {
            "render.yaml": "services:\n  - type: worker\n    name: queue\n"
            "  - type: cron\n    name: nightly\n"
        },
    )

    proposal = read_platform("render", tmp_path)

    assert "no web or static service" in joined(proposal)
    assert "queue (worker), nightly (cron)" in joined(proposal)


@pytest.mark.parametrize("services", ["7", "web", "{a: 1}"])
def test_render_services_of_the_wrong_shape_are_not_a_crash(tmp_path: Path, services: str) -> None:
    tree(tmp_path, {"render.yaml": f"services: {services}\ndatabases: 3\n"})
    proposal = read_platform("render", tmp_path)
    assert "declares no services" in joined(proposal)


# Heroku ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["node server.js --port $PORT", "gunicorn -b 0.0.0.0:${PORT}", "serve -l ${PORT:-3000}"],
)
def test_heroku_notes_every_way_of_reading_port(tmp_path: Path, command: str) -> None:
    tree(tmp_path, {"Procfile": f"web: {command}\n"})
    assert "reads $PORT" in joined(read_platform("heroku", tmp_path))


def test_heroku_does_not_mistake_a_longer_name_for_port(tmp_path: Path) -> None:
    tree(tmp_path, {"Procfile": "web: node server.js $PORTAL\n"})
    assert "reads $PORT" not in joined(read_platform("heroku", tmp_path))


def test_heroku_warns_about_a_second_web_line(tmp_path: Path) -> None:
    tree(tmp_path, {"Procfile": "web: npm start\nweb: node server.js\n"})

    proposal = read_platform("heroku", tmp_path)

    assert proposal.start_command == "node server.js"
    assert "declares web more than once" in joined(proposal)


def test_heroku_reads_addons_written_as_a_mapping(tmp_path: Path) -> None:
    manifest = {
        "addons": {"heroku-postgresql": {"plan": "heroku-postgresql:essential-0"}, "papertrail": {}}
    }
    tree(tmp_path, {"app.json": json.dumps(manifest)})

    proposal = read_platform("heroku", tmp_path)

    assert proposal.databases == ["postgresql"]
    assert "papertrail add-on has no WASM equivalent" in joined(proposal)


@pytest.mark.parametrize("addons", ["heroku-postgresql", 7])
def test_heroku_addons_of_another_shape_are_a_warning(tmp_path: Path, addons: Any) -> None:
    tree(tmp_path, {"app.json": json.dumps({"addons": addons, "buildpacks": 3})})
    proposal = read_platform("heroku", tmp_path)
    assert "addons in app.json is not a list" in joined(proposal)
