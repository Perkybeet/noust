# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
docs/COMPLIANCE.md says what the code does.

The page is generated from the one mapping table, so it cannot claim a framework asks for
something the code does not. These tests are what makes that true, and they are why a change
to a framework or a control fails the suite until the page is regenerated with
``python scripts/compliance_docs.py --write``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from noust.core.compliance import controls, docs, frameworks

ROOT = Path(__file__).parent.parent
DOC = ROOT / "docs" / "COMPLIANCE.md"


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "compliance_docs_script", ROOT / "scripts" / "compliance_docs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["compliance_docs_script"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def page() -> str:
    return DOC.read_text(encoding="utf-8")


def test_the_page_is_current(script: ModuleType) -> None:
    assert script.check() == 0, "run python scripts/compliance_docs.py --write"


def test_the_page_is_what_the_code_renders(page: str) -> None:
    assert page == docs.render()


def test_every_control_and_every_framework_is_on_the_page(page: str) -> None:
    for control in controls.CONTROLS:
        assert f"`{control.id}`" in page
    for framework in frameworks.FRAMEWORKS:
        assert f"### {framework.name}" in page
        assert f"noust compliance enable {framework.id}" in page


def test_every_clause_a_preset_cites_is_on_the_page(page: str) -> None:
    for framework in frameworks.FRAMEWORKS:
        for required in framework.all_requirements().values():
            for clause in required.clauses:
                assert clause.id in page, (framework.id, clause.id)


def test_the_page_says_what_a_preset_is_not(page: str) -> None:
    lowered = page.lower()

    assert "never all of it" in lowered
    assert "what stays organisational" in lowered
    assert "only your auditor can" in lowered
    assert "is compliant" not in lowered.replace('"compliant"', "")


def test_the_page_has_no_emoji(page: str) -> None:
    assert all(ord(char) < 0x2190 or char in "ÁÓáéíóúñ" for char in page if ord(char) > 127)


def test_the_script_refuses_an_unsound_table(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(frameworks, "check_table", lambda: ["iso-27001/nothing: no clause"])

    with pytest.raises(SystemExit) as caught:
        script.render()

    assert "not sound" in str(caught.value)
