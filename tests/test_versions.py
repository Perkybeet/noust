# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Versions and the ranges a project declares them in (npm, Composer, PEP 440)."""

from __future__ import annotations

import pytest

from noust.core.exceptions import ValidationError
from noust.core.versions import (
    RuntimeRequest,
    Version,
    parse_range,
    parse_runtime,
    parse_version,
)


def v(text: str) -> Version:
    parsed = parse_version(text)
    assert parsed is not None
    return parsed


class TestParseVersion:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("22", Version(22)),
            ("v20.11.0", Version(20, 11, 0)),
            ("8.3", Version(8, 3)),
            ("3.12.4-rc1", Version(3, 12, 4)),
            ("1.2.3+build.5", Version(1, 2, 3)),
            ("=1.2.3", Version(1, 2, 3)),
        ],
    )
    def test_reads_a_complete_or_partial_version(self, text: str, expected: Version) -> None:
        assert parse_version(text) == expected

    @pytest.mark.parametrize("text", ["lts/iron", "latest", "*", "1.x", "", "abc", "1..2", "01.2"])
    def test_what_is_not_a_version_is_none(self, text: str) -> None:
        assert parse_version(text) is None

    def test_versions_order_numerically_not_alphabetically(self) -> None:
        assert Version(8, 10) > Version(8, 9)
        assert str(Version(22, 1)) == "22.1.0"


class TestNpmRanges:
    @pytest.mark.parametrize(
        ("text", "allowed", "refused"),
        [
            (">=18", ["18.0.0", "20.11.0", "22.1.0"], ["17.9.0"]),
            (">=18 <21", ["18.0.0", "20.19.0"], ["21.0.0", "17.0.0"]),
            ("^20.11", ["20.11.0", "20.19.1"], ["20.10.9", "21.0.0"]),
            ("^0.2.3", ["0.2.3", "0.2.9"], ["0.3.0", "0.2.2"]),
            ("^0.0.3", ["0.0.3"], ["0.0.4"]),
            ("~1.2.3", ["1.2.3", "1.2.9"], ["1.3.0"]),
            ("~1.2", ["1.2.0", "1.2.7"], ["1.3.0"]),
            ("20.x", ["20.0.0", "20.9.9"], ["21.0.0", "19.9.9"]),
            ("20", ["20.4.0"], ["21.0.0"]),
            ("18 || 20", ["18.2.0", "20.1.0"], ["19.0.0", "21.0.0"]),
            ("1.2.3 - 2.3.4", ["1.2.3", "2.3.4", "2.0.0"], ["1.2.2", "2.3.5"]),
            ("1.2.3 - 2.3", ["2.3.9"], ["2.4.0"]),
            ("*", ["0.0.1", "99.0.0"], []),
            ("", ["1.0.0"], []),
            (">= 18.0.0", ["18.0.0"], ["17.0.0"]),
            (">18", ["19.0.0"], ["18.9.9"]),
            ("<=20", ["20.9.9"], ["21.0.0"]),
            ("<20", ["19.9.9"], ["20.0.0"]),
            ("=18.2.0", ["18.2.0"], ["18.2.1"]),
        ],
    )
    def test_engines_ranges(self, text: str, allowed: list[str], refused: list[str]) -> None:
        wanted = parse_range(text, "npm")

        assert all(wanted.allows(v(item)) for item in allowed), text
        assert not any(wanted.allows(v(item)) for item in refused), text

    def test_the_best_of_some_versions_is_the_newest_allowed(self) -> None:
        wanted = parse_range(">=18 <21", "npm")

        assert wanted.best([v("16.0.0"), v("18.19.0"), v("20.11.0"), v("22.0.0")]) == v("20.11.0")
        assert wanted.best([v("22.0.0")]) is None

    def test_a_form_that_is_not_npm_is_refused_with_the_way_out(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            parse_range("banana", "npm")

        assert "build: {runtime: node@22}" in excinfo.value.details


class TestComposerConstraints:
    @pytest.mark.parametrize(
        ("text", "allowed", "refused"),
        [
            ("^8.1", ["8.1.0", "8.3.5"], ["8.0.9", "9.0.0"]),
            (">=8.1,<8.4", ["8.1.0", "8.3.9"], ["8.4.0", "8.0.0"]),
            ("^7.4|^8.0", ["7.4.33", "8.2.0"], ["7.3.0", "9.0.0"]),
            ("8.3.*", ["8.3.0", "8.3.21"], ["8.4.0", "8.2.9"]),
            ("~8.2.0", ["8.2.0", "8.2.9"], ["8.3.0"]),
            ("^8.1@dev", ["8.2.0"], ["7.9.0"]),
        ],
    )
    def test_require_php(self, text: str, allowed: list[str], refused: list[str]) -> None:
        wanted = parse_range(text, "composer")

        assert all(wanted.allows(v(item)) for item in allowed), text
        assert not any(wanted.allows(v(item)) for item in refused), text


class TestPep440Specifiers:
    @pytest.mark.parametrize(
        ("text", "allowed", "refused"),
        [
            (">=3.10,<4", ["3.10.0", "3.12.4"], ["3.9.9", "4.0.0"]),
            ("~=3.11", ["3.11.0", "3.13.1"], ["3.10.9", "4.0.0"]),
            ("~=3.11.2", ["3.11.2", "3.11.9"], ["3.12.0", "3.11.1"]),
            ("==3.11.*", ["3.11.0", "3.11.8"], ["3.12.0"]),
            ("==3.12", ["3.12.0", "3.12.9"], ["3.13.0"]),
            (">3.9", ["3.10.0"], ["3.9.5"]),
            ("<=3.12", ["3.12.9"], ["3.13.0"]),
            (">=3.9, <3.13", ["3.9.0", "3.12.9"], ["3.13.0"]),
        ],
    )
    def test_requires_python(self, text: str, allowed: list[str], refused: list[str]) -> None:
        wanted = parse_range(text, "pep440")

        assert all(wanted.allows(v(item)) for item in allowed), text
        assert not any(wanted.allows(v(item)) for item in refused), text

    def test_an_exclusion_is_refused_rather_than_misread(self) -> None:
        with pytest.raises(ValidationError):
            parse_range(">=3.8,!=3.9.0", "pep440")


class TestRuntimeRequests:
    @pytest.mark.parametrize(
        ("text", "language", "spec"),
        [
            ("node@22", "node", "22"),
            ("nodejs@22.11.0", "node", "22.11.0"),
            ("Python@3.12", "python", "3.12"),
            ("php@8.3", "php", "8.3"),
            ("node@LTS", "node", "lts"),
            ("node@latest", "node", "latest"),
        ],
    )
    def test_a_pin(self, text: str, language: str, spec: str) -> None:
        request = parse_runtime(text)

        assert (request.language, request.spec, request.explicit) == (language, spec, True)

    @pytest.mark.parametrize(
        "text", ["node", "ruby@3.2", "node@>=18", "node@22.11.0.1", "node@", 22, None, "@22"]
    )
    def test_what_is_not_a_pin_names_the_field(self, text: object) -> None:
        with pytest.raises(ValidationError) as excinfo:
            parse_runtime(text, field="build.runtime")

        assert excinfo.value.field == "build.runtime"

    def test_a_bare_version_pins_a_major_not_one_release(self) -> None:
        request = parse_runtime("node@22")

        assert request.allows(v("22.9.0"))
        assert not request.allows(v("23.0.0"))
        assert request.exact is None
        assert parse_runtime("node@22.11.0").exact == v("22.11.0")

    def test_the_aliases_are_satisfied_by_any_version(self) -> None:
        assert RuntimeRequest("node", "lts/*", explicit=False).alias == "lts"
        assert RuntimeRequest("node", "node", explicit=False).alias == "latest"
        assert RuntimeRequest("node", "lts/iron", explicit=False).allows(v("16.0.0"))

    def test_a_version_file_may_hold_a_range_in_its_languages_dialect(self) -> None:
        request = RuntimeRequest("php", "^8.1", explicit=False, source="composer.json")

        assert request.allows(v("8.2.7"))
        assert not request.allows(v("7.4.0"))
        assert request.text == "php@^8.1"
