# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
docs/SECURITY-MODEL.md says what the code does (checklist L-48, L-49, L-50, L-51, L-52).

A public page of guarantees is only worth reading if it cannot drift from what it
describes. It is generated in part from the code (the matrix of roles, the headers,
the routes, the sshd block) and every test it names has to exist. These tests are
what makes that true, and they are why a change to a role, a header or a route
fails the suite until the page is regenerated with
``python scripts/security_model.py --write``.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent
DOC = ROOT / "docs" / "SECURITY-MODEL.md"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "security_model_script", ROOT / "scripts" / "security_model.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["security_model_script"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return load_script()


@pytest.fixture(scope="module")
def page() -> str:
    return DOC.read_text(encoding="utf-8")


class TestThePageIsTheCode:
    def test_the_generated_parts_are_current(self, script: ModuleType, page: str) -> None:
        rendered = script.render(page)

        assert rendered == page, (
            "docs/SECURITY-MODEL.md is out of date with the code: "
            "run `python scripts/security_model.py --write` and commit the result"
        )

    def test_every_block_the_script_writes_has_a_place_on_the_page(
        self, script: ModuleType, page: str
    ) -> None:
        absent = [name for name in script.BLOCKS if f"<!-- generated:{name} -->" not in page]

        assert absent == []

    def test_every_region_on_the_page_is_one_the_script_knows(
        self, script: ModuleType, page: str
    ) -> None:
        named = set(re.findall(r"<!-- generated:([a-z-]+) -->", page))

        assert named <= set(script.BLOCKS), named - set(script.BLOCKS)

    def test_the_check_command_agrees(
        self, script: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert script.check() == 0, capsys.readouterr().out


class TestEveryPromiseCanBeFalsified:
    def test_every_test_it_names_exists(self, script: ModuleType) -> None:
        assert script.missing_evidence() == []

    def test_every_guarantee_has_evidence_and_a_limit_and_a_source_for_its_failure(
        self, script: ModuleType
    ) -> None:
        for guarantee in script.GUARANTEES:
            assert len(guarantee.evidence) >= 3, guarantee.title
            assert guarantee.limit.strip(), guarantee.title
            assert guarantee.failure.strip() and guarantee.mechanism.strip(), guarantee.title

    def test_there_are_eleven_guarantees_numbered_in_order(self, script: ModuleType) -> None:
        assert [item.number for item in script.GUARANTEES] == list(range(1, 12))

    def test_every_guard_of_an_input_class_can_be_called(self, script: ModuleType) -> None:
        assert script.missing_guards() == []

    def test_every_class_of_input_is_attacked_by_a_test_that_exists(
        self, script: ModuleType
    ) -> None:
        for item in script.INPUT_CLASSES:
            assert item.tests, item.name
            for reference in item.tests:
                assert script.evidence_exists(reference), reference

    def test_a_reference_to_something_that_does_not_exist_is_found(
        self, script: ModuleType
    ) -> None:
        assert not script.evidence_exists("tests/test_security_model.py::TestNothing::test_nothing")
        assert not script.evidence_exists("tests/does_not_exist.py::test_x")
        assert script.evidence_exists(
            "tests/test_security_model.py::TestPromisesMadeInProse::test_the_limits_are_stated"
        )


class TestPromisesMadeInProse:
    """What cannot be generated is still held to saying what it must."""

    def test_the_limits_are_stated(self, page: str) -> None:
        assert "## What Noust does not guarantee" in page
        assert "requires root" in page
        assert "not a multi-tenant isolation boundary" in page

    def test_the_absence_of_advisories_is_not_offered_as_evidence_of_safety(
        self, page: str
    ) -> None:
        assert "absence of published advisories is not evidence of safety" in page

    def test_it_says_no_independent_review_has_been_done(self, page: str) -> None:
        assert "No external penetration test has been done" in page

    def test_how_to_verify_a_release_names_the_identity_to_check(self, page: str) -> None:
        assert "## Verifying a release" in page
        assert "release\\.yml@refs/" in page
        assert "gh attestation verify" in page and "cosign verify-blob" in page

    def test_the_dated_claims_are_dated(self, page: str) -> None:
        assert "2026-10-13" in page

    def test_a_failure_that_names_another_product_gives_a_date_or_an_advisory(
        self, script: ModuleType
    ) -> None:
        """What is said of a product is a fact with a date and a source, never a bare claim."""
        for guarantee in script.GUARANTEES:
            for product in ("Coolify", "Dokploy"):
                if product in guarantee.failure:
                    assert re.search(r"20\d\d-\d\d-\d\d|GHSA-", guarantee.failure), guarantee.title

    def test_a_failure_does_not_say_why_another_product_does_what_it_does(
        self, script: ModuleType
    ) -> None:
        """A motive is not a fact anyone can check, so the page does not attribute one."""
        motives = re.compile(
            r"\b(because|protest\w*|complain\w*|trademark|on purpose|deliberately|intends?|"
            r"wants?|refus\w+ to|in order to)\b",
            re.IGNORECASE,
        )
        for guarantee in script.GUARANTEES:
            assert not motives.search(guarantee.failure), guarantee.title

    @pytest.mark.parametrize(
        "anchor",
        ["#the-browser", "#verifying-a-release"],
    )
    def test_the_anchors_other_files_link_to_exist(self, page: str, anchor: str) -> None:
        headings = {
            re.sub(r"[^a-z0-9 -]", "", line.lstrip("# ").strip().lower()).replace(" ", "-")
            for line in page.splitlines()
            if line.startswith("#")
        }

        assert anchor.lstrip("#") in headings
        referrers = [
            ROOT / "SECURITY.md",
            ROOT / "docs" / "security.md",
        ]
        assert any(anchor in path.read_text(encoding="utf-8") for path in referrers)

    def test_every_relative_link_on_the_page_resolves(self, page: str) -> None:
        for target in re.findall(r"\]\((?!https?://|#|mailto:)([^)#]+)(?:#[^)]*)?\)", page):
            assert (DOC.parent / target).exists(), target

    def test_the_guarantee_headings_are_the_eleven(self, page: str) -> None:
        headings = re.findall(r"^### (\d+)\. ", page, flags=re.MULTILINE)

        assert headings == [str(number) for number in range(1, 12)]


class TestTheOtherDocumentsPointHere:
    def test_the_policy_and_the_threat_model_link_to_the_page(self) -> None:
        for name in ("SECURITY.md", "docs/security.md"):
            assert "SECURITY-MODEL.md" in (ROOT / name).read_text(encoding="utf-8"), name

    def test_the_readme_links_to_the_page(self) -> None:
        assert "docs/SECURITY-MODEL.md" in (ROOT / "README.md").read_text(encoding="utf-8")
