# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The documentation travels inside the package, so the assistant finds it however Noust was installed
(item 171).

``search_docs`` reads the pages from disk. Until 4.3 only a checkout and the RPM had them where it
looked, so the same Noust offered the tool on Fedora and not on Debian, in the central's image or
after ``pip install``. The pages are now a committed copy inside the Python package, written by
``scripts/generate_packaged_docs.py`` from the pages ``docs/site.yaml`` publishes: the wheel, the
``.deb``, the ``.rpm`` and the image inherit them without a recipe of their own.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

import noust
from noust.core.assistant.docsearch import DocIndex, is_history
from noust.core.paths import docs_directories
from scripts import build_docs, generate_packaged_docs

ROOT = Path(__file__).resolve().parent.parent
PACKAGED = ROOT / "src" / "noust" / "docs"

MANIFEST = """\
title: Test docs
repository: https://github.com/example/project
branch: main
base: /docs/
summary: A project, for a test.
home:
  start:
    slug: start/overview
tabs:
  - title: Start
    slug: start
    groups:
      - title: First
        pages:
          - source: README.md
            slug: start/overview
            type: explanation
            summary: The overview.
          - source: docs/guide.md
            slug: start/guide
            type: tutorial
            summary: How to.
          - source: docs/CHANGELOG-1.0.md
            slug: start/changes
            type: reference
            summary: What changed.
          - source: docs/api.md
            slug: start/api
            type: reference
            summary: The API.
      - title: Second
        pages:
          - source: docs/extra.md
            slug: start/extra
            type: reference
            summary: Extra.
exclude:
  - source: docs/private.md
    why: not for readers
"""


def tree(tmp_path: Path, **pages: str) -> Path:
    """
    Make a small repository: a manifest, its pages and a page nobody listed.

    Args:
        tmp_path: Where to make it.
        **pages: The pages of ``docs/`` by file stem (``guide`` is ``docs/guide.md``) and their text.

    Returns:
        The root of the repository, with an empty ``src/noust/docs``.
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "site.yaml").write_text(MANIFEST, encoding="utf-8")
    for stem, text in pages.items():
        (docs / f"{stem.replace('_', '-')}.md").write_text(text, encoding="utf-8")
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    (tmp_path / "src" / "noust" / "docs").mkdir(parents=True)
    return tmp_path


def packaged_names(root: Path) -> list[str]:
    """
    Args:
        root: A repository.

    Returns:
        The names of the pages in its packaged copy.
    """
    return sorted(path.name for path in (root / "src" / "noust" / "docs").iterdir())


class TestWhatTravels:
    """The pages that are published, and only those, are in the package."""

    def test_the_committed_copy_is_what_the_generator_writes(self) -> None:
        assert generate_packaged_docs.main(["--check"]) == 0, (
            "the packaged documentation is stale: run python scripts/generate_packaged_docs.py"
        )

    def test_the_pages_are_the_ones_the_site_publishes_from_docs(self) -> None:
        listed = build_docs.listed_sources(ROOT / "docs" / "site.yaml")
        expected = {
            Path(source).name
            for source in listed
            if Path(source).parent == Path("docs") and not is_history(Path(source).name)
        }

        assert {path.name for path in PACKAGED.iterdir()} == expected

    def test_each_page_is_the_page_of_docs_byte_for_byte(self) -> None:
        """Not a rendering: git keeps one copy of identical files, and a diff shows only the edit."""
        for path in sorted(PACKAGED.iterdir()):
            assert path.read_bytes() == (ROOT / "docs" / path.name).read_bytes(), path.name

    def test_history_does_not_travel(self) -> None:
        """A changelog says what changed; the index skips it by name, so it would only weigh."""
        names = [path.name for path in PACKAGED.iterdir()]

        assert names, "no page is packaged"
        assert not [name for name in names if re.search("CHANGELOG|UPGRADING", name)]

    def test_what_the_site_leaves_out_does_not_travel(self) -> None:
        excluded = {Path(item["source"]).name for item in _excluded()}

        assert excluded
        assert not excluded & {path.name for path in PACKAGED.iterdir()}

    def test_nothing_but_pages_is_in_the_copy(self) -> None:
        """No image (``docs/assets`` stays out), no manifest, no index of links."""
        assert {path.suffix for path in PACKAGED.iterdir()} == {".md"}
        assert all(path.is_file() for path in PACKAGED.iterdir())


def _excluded() -> list[dict[str, str]]:
    """
    Returns:
        The entries of ``exclude`` in the manifest of the site.
    """
    data = yaml.safe_load((ROOT / "docs" / "site.yaml").read_text(encoding="utf-8"))
    return list(data["exclude"])


class TestTheGenerator:
    """It reads the manifest of the site, writes what it says and sees any difference."""

    def test_it_packages_the_published_pages_of_docs_and_nothing_else(self, tmp_path: Path) -> None:
        root = tree(
            tmp_path, guide="# Guide\n", extra="# Extra\n", api="# API\n", private="# Private\n"
        )
        (root / "docs" / "CHANGELOG-1.0.md").write_text("# Changes\n", encoding="utf-8")

        generate_packaged_docs.write(root)

        # README is a published page, but not of docs/; the changelog is history; private is out.
        assert packaged_names(root) == ["api.md", "extra.md", "guide.md"]

    def test_a_page_is_copied_exactly(self, tmp_path: Path) -> None:
        text = "# Guide\r\n\r\nA line with a tab\t and no final newline"
        root = tree(tmp_path, guide=text, extra="x", api="y")
        (root / "docs" / "guide.md").write_bytes(text.encode())

        generate_packaged_docs.write(root)

        assert (root / "src" / "noust" / "docs" / "guide.md").read_bytes() == text.encode()

    def test_a_copy_that_is_current_passes_the_check(self, tmp_path: Path) -> None:
        root = tree(tmp_path, guide="# Guide\n", extra="# Extra\n", api="# API\n")
        generate_packaged_docs.write(root)

        assert generate_packaged_docs.problems(root) == []

    def test_an_edited_page_makes_the_copy_stale(self, tmp_path: Path) -> None:
        root = tree(tmp_path, guide="# Guide\n", extra="# Extra\n", api="# API\n")
        generate_packaged_docs.write(root)
        (root / "docs" / "guide.md").write_text("# Guide\n\nMore.\n", encoding="utf-8")

        assert generate_packaged_docs.problems(root) == ["guide.md differs from docs/guide.md"]

    def test_a_new_page_makes_the_copy_stale(self, tmp_path: Path) -> None:
        """A page published after the copy was written (here, the copy of one is lost)."""
        root = tree(tmp_path, guide="# Guide\n", extra="# Extra\n", api="# API\n")
        generate_packaged_docs.write(root)
        (root / "src" / "noust" / "docs" / "extra.md").unlink()

        assert generate_packaged_docs.problems(root) == ["extra.md is not in the package"]

    def test_a_page_that_stopped_being_published_makes_the_copy_stale(self, tmp_path: Path) -> None:
        root = tree(tmp_path, guide="# Guide\n", extra="# Extra\n", api="# API\n")
        generate_packaged_docs.write(root)
        stale = root / "src" / "noust" / "docs" / "private.md"
        stale.write_text("# Private\n", encoding="utf-8")

        assert generate_packaged_docs.problems(root) == [
            "private.md is in the package and is not a published page"
        ]

    def test_writing_removes_what_is_no_longer_published(self, tmp_path: Path) -> None:
        root = tree(tmp_path, guide="# Guide\n", extra="# Extra\n", api="# API\n")
        (root / "src" / "noust" / "docs" / "private.md").write_text("old", encoding="utf-8")

        generate_packaged_docs.write(root)

        assert packaged_names(root) == ["api.md", "extra.md", "guide.md"]

    def test_a_listed_page_that_is_missing_is_an_error_not_a_smaller_package(
        self, tmp_path: Path
    ) -> None:
        """A manifest that names a file nobody has is a defect of the documentation, said now."""
        root = tree(tmp_path, guide="# Guide\n", api="# API\n")  # extra.md is listed, absent

        with pytest.raises(generate_packaged_docs.PackagedDocsError, match=r"extra\.md"):
            generate_packaged_docs.write(root)

    def test_check_reports_and_write_repairs(self, tmp_path: Path) -> None:
        root = tree(tmp_path, guide="# Guide\n", extra="# Extra\n", api="# API\n")

        assert generate_packaged_docs.main(["--root", str(root), "--check"]) == 1
        assert generate_packaged_docs.main(["--root", str(root)]) == 0
        assert generate_packaged_docs.main(["--root", str(root), "--check"]) == 0


class TestWhereTheAssistantLooks:
    """The package's copy is found on any install, after the checkout and before the system's paths."""

    def test_the_package_comes_after_the_checkout_and_before_the_system_paths(self) -> None:
        folders = docs_directories()
        installed = Path(noust.__file__).resolve().parent / "docs"

        assert installed in folders
        assert folders.index(installed) == 1, "the checkout's docs/ stays first"
        assert all(
            folder.is_absolute() and str(folder).startswith("/usr/share/doc")
            for folder in folders[2:]
        ), "the paths of the packages that install documentation stay, for whoever has them"

    def test_the_assistant_finds_the_documentation_in_the_package_alone(self) -> None:
        index = DocIndex([PACKAGED])

        assert index.available
        for question in ("zero downtime", "restore a backup", "health check rollback"):
            found = index.search(question)
            assert found, question
            assert all(passage.text for passage in found)

    def test_the_index_over_the_package_is_the_index_over_docs(self) -> None:
        """The copy is the published pages, so a question gets the page the checkout gives."""
        packaged = DocIndex([PACKAGED])
        checkout = DocIndex([ROOT / "docs"])

        for question in ("zero downtime", "restore a backup"):
            assert [p.page for p in packaged.search(question)][:1] == [
                p.page for p in checkout.search(question)
            ][:1]

    def test_history_is_told_by_name_as_the_tool_does(self) -> None:
        """The index skips these pages by name, and the generator asks it which: one definition."""
        assert is_history("CHANGELOG-4.2.md")
        assert is_history("UPGRADING-4.0.md")
        assert is_history("API-CHANGELOG.md")
        assert not is_history("compose.md")
        assert not is_history("assistant.md")


class TestWhatShips:
    """The copy is declared to every build that makes a package from the tree."""

    def test_the_wheel_declares_it_as_package_data(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        assert '"docs/*.md"' in pyproject

    def test_the_source_distribution_carries_it(self) -> None:
        """The sdist is made from MANIFEST.in, and the packages are built from it."""
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

        assert "recursive-include src/noust/docs *.md" in manifest

    def test_git_shows_it_as_generated(self) -> None:
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

        assert "src/noust/docs/** linguist-generated=true" in attributes

    def test_the_tarball_of_the_packages_does_not_leave_it_out(self) -> None:
        """``git archive`` is what OBS builds from: nothing may export-ignore the copy or ``src``."""
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        ignored = [
            line.split()[0]
            for line in attributes.splitlines()
            if line.endswith(" export-ignore") and not line.startswith("#")
        ]

        assert not [pattern for pattern in ignored if "src" in pattern or "docs" in pattern]

    def test_no_recipe_needs_its_own_list_of_pages(self) -> None:
        """Debian's ``debian.docs`` once named ten pages of the 3 series: the package carries all."""
        docs = [
            line.strip()
            for line in (ROOT / "obs" / "debian.docs").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        assert docs == ["README.md"]
        assert all((ROOT / name).is_file() for name in docs)

    def test_the_spec_installs_the_package_directory_whole(self) -> None:
        """So the RPM needed no line for the copy: the directory of the package is in ``%files``."""
        spec = (ROOT / "rpm" / "noust.spec").read_text(encoding="utf-8")

        assert "%{python3_sitelib}/noust/\n" in spec
