# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one Jinja environment Noust renders unit files and web server configuration with.

Nine modules built the same environment each (``autoescape=False`` with a ``noqa`` on every
one), which is what CodeQL's ``py/jinja2/autoescape-false`` reported nine times. A unit file is
not markup, so nothing it holds may be HTML-escaped; but the decision is made once, in
:mod:`noust.core.templates`, and a template that is markup is escaped.
"""

from __future__ import annotations

import ast
from pathlib import Path

import jinja2
import pytest

from noust.core import templates

SRC = Path(__file__).resolve().parent.parent / "src" / "noust"


class TestTheEnvironment:
    def test_a_unit_file_keeps_every_character_it_was_given(self) -> None:
        environment = templates.text_environment("templates/systemd")
        template = environment.from_string("ExecStart={{ command }}")

        assert template.render(command="/usr/bin/x --a='b&c' <d>") == (
            "ExecStart=/usr/bin/x --a='b&c' <d>"
        )

    @pytest.mark.parametrize("name", ["unit.service.j2", "site.conf.j2", "pool.conf.j2"])
    def test_what_is_not_markup_is_not_escaped(self, name: str) -> None:
        environment = templates.text_environment("templates/systemd")

        assert not environment.autoescape(name)  # type: ignore[operator]

    @pytest.mark.parametrize("name", ["page.html", "page.htm", "feed.xml", "page.html.j2"])
    def test_a_template_that_is_markup_is_escaped(self, name: str) -> None:
        environment = templates.text_environment("templates/systemd")

        assert environment.autoescape(name)  # type: ignore[operator]

    def test_blocks_are_trimmed_as_the_unit_templates_expect(self) -> None:
        environment = templates.text_environment("templates/systemd")

        assert environment.trim_blocks and environment.lstrip_blocks

    def test_a_missing_value_can_be_made_an_error(self) -> None:
        environment = templates.text_environment("templates/php", undefined=jinja2.StrictUndefined)

        with pytest.raises(jinja2.UndefinedError):
            environment.from_string("{{ missing }}").render()

    def test_the_packaged_templates_are_found(self) -> None:
        for package in (
            "templates/systemd",
            "templates/php",
            "templates/nginx",
            "templates/apache",
        ):
            environment = templates.text_environment(package)
            assert environment.list_templates(), package

    def test_a_package_that_is_not_there_is_the_error_the_callers_already_handle(self) -> None:
        with pytest.raises((ValueError, ImportError)):
            templates.text_environment("templates/nowhere")


class TestNothingBuildsItsOwn:
    """Rule 3: there is one place a text template environment is made."""

    def test_only_core_templates_builds_a_jinja_environment(self) -> None:
        offenders: list[str] = []
        for path in sorted(SRC.rglob("*.py")):
            if path == SRC / "core" / "templates.py" or "web/static" in path.as_posix():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    function = node.func
                    name = (
                        function.id
                        if isinstance(function, ast.Name)
                        else function.attr
                        if isinstance(function, ast.Attribute)
                        else ""
                    )
                    if name == "Environment" and any(
                        keyword.arg in {"loader", "autoescape"} for keyword in node.keywords
                    ):
                        offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")

        assert offenders == [], f"build it with core.templates.text_environment: {offenders}"
