# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The files a guard over the whole tree reads are the ones git does not ignore."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tree_support import ignore_patterns, is_ignored, tree_files

RULES = [
    "*.swp",
    "node_modules/",
    "build/",
    "!panel/src/features/build/",
    "/dist",
    "docs/private/",
]


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("src/app.py", False),
        ("src/app.py.swp", True),
        ("panel/node_modules/react/index.js", True),
        ("build/out.txt", True),
        ("panel/src/features/build/page.tsx", False),
        ("tools/build/out.txt", True),
        ("dist/wheel.whl", True),
        ("sub/dist/wheel.whl", False),
        ("docs/private/plan.md", True),
        ("docs/public/plan.md", False),
    ],
)
def test_a_path_is_ignored_as_git_ignores_it(path: str, ignored: bool) -> None:
    assert is_ignored(path, RULES) is ignored


def test_a_directory_only_rule_does_not_ignore_a_file_of_that_name() -> None:
    assert not is_ignored("node_modules", ["node_modules/"])
    assert is_ignored("node_modules", ["node_modules/"], is_directory=True)


def test_the_files_are_those_the_checkouts_rules_leave(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("# generated\nout/\n*.log\n")
    git_dir = tmp_path / ".git"
    (git_dir / "info").mkdir(parents=True)
    (git_dir / "info" / "exclude").write_text("notes/\n")
    for relative in ("a.py", "out/b.py", "c.log", "notes/d.md", "docs/e.md", ".git/HEAD"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")

    found = [path.relative_to(tmp_path).as_posix() for path in tree_files(tmp_path)]

    assert found == [".gitignore", "a.py", "docs/e.md"]


def test_a_linked_checkout_reads_the_exclude_file_of_the_main_one(tmp_path: Path) -> None:
    main = tmp_path / "main" / ".git"
    (main / "info").mkdir(parents=True)
    (main / "info" / "exclude").write_text("private/\n")
    linked = main / "linked" / "one"
    linked.mkdir(parents=True)
    (linked / "commondir").write_text("../..\n")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text(f"gitdir: {linked}\n")

    assert ignore_patterns(checkout) == ["private/"]


def test_what_is_skipped_by_name_is_not_read_even_when_tracked(tmp_path: Path) -> None:
    for relative in ("src/a.py", "src/static/b.js"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")

    found = [
        path.relative_to(tmp_path).as_posix()
        for path in tree_files(tmp_path, under=["src"], skip=["src/static"])
    ]

    assert found == ["src/a.py"]
