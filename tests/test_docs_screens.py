# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The documentation's pictures of the console, and whether they still show it (checklist N-19).

Coolify's own tutorials were found picturing an interface that had changed since: 29 of the 46
captures checked. Here every picture is recorded with the release it was taken at and a hash, so a
picture edited by hand, a picture nobody recorded and a picture that shows an earlier release are
each a failure with the command that fixes it. The last is only a failure when publishing: between
releases the pictures are one minor release behind by design.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import docs_screens

ROOT = Path(__file__).resolve().parent.parent

PNG = b"\x89PNG\r\n\x1a\n"


def repository(
    tmp_path: Path, *, version: str = "3.4.0", pictures: dict[str, bytes] | None = None
) -> Path:
    """A repository of the shape the check reads: a version, a README that links two pictures."""
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\n', encoding="utf-8"
    )
    folder = tmp_path / "docs" / "assets" / "console"
    folder.mkdir(parents=True)
    for name, data in (
        pictures if pictures is not None else {"overview.png": PNG + b"a", "apps.png": PNG + b"b"}
    ).items():
        (folder / name).write_bytes(data)
    (tmp_path / "README.md").write_text(
        "![Overview](https://raw.githubusercontent.com/o/r/main/docs/assets/console/overview.png)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "console.md").write_text(
        "![Apps](assets/console/apps.png)\n", encoding="utf-8"
    )
    return tmp_path


def test_a_recorded_set_taken_at_this_release_passes_even_when_publishing(tmp_path: Path) -> None:
    root = repository(tmp_path)
    docs_screens.write(root)

    assert docs_screens.check(root, publishing=True) == []


def test_the_manifest_names_the_release_and_the_hash_of_each_picture(tmp_path: Path) -> None:
    root = repository(tmp_path, version="3.4.2")

    docs_screens.write(root)

    manifest = json.loads(
        (root / "docs" / "assets" / "console" / "SCREENS.json").read_text(encoding="utf-8")
    )
    assert manifest["release"] == "3.4"
    assert manifest["images"]["apps.png"] == hashlib.sha256(PNG + b"b").hexdigest()
    assert sorted(manifest["images"]) == ["apps.png", "overview.png"]


def test_pictures_of_an_earlier_release_are_a_note_between_releases_and_a_failure_when_publishing(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path, version="3.4.0")
    docs_screens.write(root, release="3.1")

    assert docs_screens.check(root, publishing=False) == []
    problems = docs_screens.check(root, publishing=True)

    assert len(problems) == 1
    assert "3.1" in problems[0] and "3.4" in problems[0]
    assert "npm run docs:screens" in problems[0]


def test_a_patch_release_does_not_make_the_pictures_stale(tmp_path: Path) -> None:
    root = repository(tmp_path, version="3.4.0")
    docs_screens.write(root)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "3.4.7"\n', encoding="utf-8"
    )

    assert docs_screens.check(root, publishing=True) == []


def test_a_picture_changed_since_it_was_recorded_is_named(tmp_path: Path) -> None:
    root = repository(tmp_path)
    docs_screens.write(root)
    (root / "docs" / "assets" / "console" / "apps.png").write_bytes(PNG + b"retouched")

    problems = docs_screens.check(root, publishing=False)

    assert any("apps.png" in problem and "changed" in problem for problem in problems)


def test_a_picture_nobody_recorded_is_named(tmp_path: Path) -> None:
    root = repository(tmp_path)
    docs_screens.write(root)
    (root / "docs" / "assets" / "console" / "new.png").write_bytes(PNG + b"c")

    problems = docs_screens.check(root, publishing=False)

    assert any("new.png" in problem and "not recorded" in problem for problem in problems)


def test_a_recorded_picture_that_is_gone_is_named(tmp_path: Path) -> None:
    root = repository(tmp_path)
    docs_screens.write(root)
    (root / "docs" / "assets" / "console" / "apps.png").unlink()

    problems = docs_screens.check(root, publishing=False)

    assert any("apps.png" in problem and "missing" in problem for problem in problems)


def test_a_picture_the_text_links_and_the_folder_lacks_is_named_with_the_page_that_links_it(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    docs_screens.write(root)
    (root / "docs" / "guide.md").write_text("![Gone](assets/console/gone.png)\n", encoding="utf-8")

    problems = docs_screens.check(root, publishing=False)

    assert any("gone.png" in problem and "docs/guide.md" in problem for problem in problems)


def test_without_a_manifest_the_command_that_makes_one_is_named(tmp_path: Path) -> None:
    root = repository(tmp_path)

    problems = docs_screens.check(root, publishing=False)

    assert len(problems) == 1
    assert "SCREENS.json" in problems[0] and "docs_screens.py write" in problems[0]


def test_the_repository_records_every_picture_it_ships() -> None:
    """Between releases the pictures may be a release behind; they may never be unrecorded."""
    assert docs_screens.check(ROOT, publishing=False) == []


def test_the_release_workflow_and_the_screens_command_use_the_check() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    package = json.loads((ROOT / "panel" / "package.json").read_text(encoding="utf-8"))

    assert "docs_screens.py check --release" in workflow
    assert "docs_screens.py write" in package["scripts"]["docs:screens"]


@pytest.mark.parametrize("text", ["3.4.0", "3.10.2"])
def test_the_release_is_the_minor_line_of_the_project_version(tmp_path: Path, text: str) -> None:
    root = repository(tmp_path, version=text)

    assert docs_screens.project_line(root) == ".".join(text.split(".")[:2])
