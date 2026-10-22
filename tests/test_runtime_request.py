# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which runtime a project says it needs, read from the files it already has.

What is read is a hint, never an order, and the repository that wrote it is
untrusted: a link is not followed, a file of a megabyte is not read, and what
cannot be understood is reported and skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.deployers.helpers.runtime_request import detect_request


def write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text)


class TestNode:
    @pytest.mark.parametrize(
        ("content", "spec"),
        [
            ("v20.11.0\n", "20.11.0"),
            ("20\n", "20"),
            ("  18.19  \n", "18.19"),
            ("lts/*\n", "lts/*"),
            ("lts/iron", "lts/iron"),
            ("node\n", "node"),
            ("# the version\n20 # for now\n", "20"),
        ],
    )
    def test_nvmrc(self, tmp_path: Path, content: str, spec: str) -> None:
        write(tmp_path, ".nvmrc", content)

        request, notes = detect_request("node", tmp_path)

        assert request is not None
        assert (request.language, request.spec, request.source) == ("node", spec, ".nvmrc")
        assert not request.explicit
        assert notes == []

    def test_node_version_file(self, tmp_path: Path) -> None:
        write(tmp_path, ".node-version", "22.1.0\n")

        request, _ = detect_request("node", tmp_path)

        assert request is not None and request.spec == "22.1.0"
        assert request.source == ".node-version"

    def test_nvmrc_is_read_before_node_version_before_engines(self, tmp_path: Path) -> None:
        write(tmp_path, ".nvmrc", "20\n")
        write(tmp_path, ".node-version", "22\n")
        write(tmp_path, "package.json", json.dumps({"engines": {"node": ">=18"}}))

        request, _ = detect_request("node", tmp_path)

        assert request is not None and request.spec == "20"

    def test_engines_node_is_a_range(self, tmp_path: Path) -> None:
        write(tmp_path, "package.json", json.dumps({"engines": {"node": ">=18 <21"}}))

        request, _ = detect_request("node", tmp_path)

        assert request is not None
        assert request.spec == ">=18 <21"
        assert request.source == "engines.node"

    def test_an_unreadable_engines_range_is_reported_and_skipped(self, tmp_path: Path) -> None:
        write(tmp_path, "package.json", json.dumps({"engines": {"node": "banana"}}))

        request, notes = detect_request("node", tmp_path)

        assert request is None
        assert len(notes) == 1 and "engines.node" in notes[0]

    def test_a_nvmrc_that_is_not_a_version_is_reported_and_the_next_source_is_read(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, ".nvmrc", "iojs-v3\n")
        write(tmp_path, "package.json", json.dumps({"engines": {"node": "20.x"}}))

        request, notes = detect_request("node", tmp_path)

        assert request is not None and request.spec == "20.x"
        assert ".nvmrc says 'iojs-v3'" in notes[0]

    def test_a_workspace_root_may_hold_the_version_for_every_app(self, tmp_path: Path) -> None:
        write(tmp_path, ".nvmrc", "22\n")
        project = tmp_path / "apps" / "web"
        project.mkdir(parents=True)

        request, _ = detect_request("node", project, tmp_path)

        assert request is not None and request.spec == "22"

    def test_the_projects_own_file_wins_over_the_roots(self, tmp_path: Path) -> None:
        write(tmp_path, ".nvmrc", "22\n")
        write(tmp_path / "apps" / "web", ".nvmrc", "20\n")

        request, _ = detect_request("node", tmp_path / "apps" / "web", tmp_path)

        assert request is not None and request.spec == "20"

    def test_nothing_is_nothing(self, tmp_path: Path) -> None:
        assert detect_request("node", tmp_path) == (None, [])

    def test_a_link_is_not_followed(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.write_text("18\n")
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / ".nvmrc").symlink_to(outside)

        assert detect_request("node", tree) == (None, [])

    def test_a_huge_file_is_not_read(self, tmp_path: Path) -> None:
        write(tmp_path, ".nvmrc", "20\n" + "#" * (2 * 1024 * 1024))

        assert detect_request("node", tmp_path) == (None, [])

    def test_a_package_json_that_is_not_json_says_nothing(self, tmp_path: Path) -> None:
        write(tmp_path, "package.json", "{not json")

        assert detect_request("node", tmp_path) == (None, [])


class TestPython:
    @pytest.mark.parametrize(
        ("content", "spec"),
        [
            ("3.12\n", "3.12"),
            ("3.11.4\n", "3.11.4"),
            ("cpython-3.10\n", "3.10"),
            ("3.9\n3.8\n", "3.9"),
        ],
    )
    def test_python_version(self, tmp_path: Path, content: str, spec: str) -> None:
        write(tmp_path, ".python-version", content)

        request, _ = detect_request("python", tmp_path)

        assert request is not None
        assert (request.language, request.spec, request.source) == (
            "python",
            spec,
            ".python-version",
        )

    def test_herokus_runtime_txt(self, tmp_path: Path) -> None:
        write(tmp_path, "runtime.txt", "python-3.11.4\n")

        request, _ = detect_request("python", tmp_path)

        assert request is not None and request.spec == "3.11.4"
        assert request.source == "runtime.txt"

    def test_pypy_is_not_cpython(self, tmp_path: Path) -> None:
        write(tmp_path, ".python-version", "pypy3.10\n")

        request, notes = detect_request("python", tmp_path)

        assert request is None
        assert "not a CPython version" in notes[0]

    def test_a_runtime_txt_of_another_language_is_reported(self, tmp_path: Path) -> None:
        write(tmp_path, "runtime.txt", "ruby-3.2.2\n")

        request, notes = detect_request("python", tmp_path)

        assert request is None and "runtime.txt" in notes[0]

    def test_python_version_is_read_before_runtime_txt(self, tmp_path: Path) -> None:
        write(tmp_path, ".python-version", "3.12\n")
        write(tmp_path, "runtime.txt", "python-3.9.1\n")

        request, _ = detect_request("python", tmp_path)

        assert request is not None and request.spec == "3.12"


class TestPhp:
    def test_composer_require_php(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "composer.json",
            json.dumps({"require": {"php": "^8.1", "laravel/framework": "^10"}}),
        )

        request, _ = detect_request("php", tmp_path)

        assert request is not None
        assert (request.language, request.spec, request.source) == ("php", "^8.1", "composer.json")

    def test_a_composer_constraint_with_an_or_is_understood(self, tmp_path: Path) -> None:
        write(tmp_path, "composer.json", json.dumps({"require": {"php": "^7.4 || ^8.0"}}))

        request, notes = detect_request("php", tmp_path)

        assert request is not None and notes == []

    def test_a_composer_json_without_php_says_nothing(self, tmp_path: Path) -> None:
        write(tmp_path, "composer.json", json.dumps({"require": {"monolog/monolog": "^3"}}))

        assert detect_request("php", tmp_path) == (None, [])

    def test_a_constraint_that_cannot_be_read_is_reported(self, tmp_path: Path) -> None:
        write(tmp_path, "composer.json", json.dumps({"require": {"php": "when it is ready"}}))

        request, notes = detect_request("php", tmp_path)

        assert request is None and "composer.json" in notes[0]
