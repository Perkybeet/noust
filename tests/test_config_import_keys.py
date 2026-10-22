# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The two settings of the dump import (3.4, H-18): the upload cap and the folders paths are read from."""

from __future__ import annotations

import pytest

from noust.core.config import _KEY_VALIDATORS, DEFAULT_CONFIG
from noust.core.exceptions import ConfigError


def test_the_defaults_are_ten_gib_and_no_extra_folder() -> None:
    assert DEFAULT_CONFIG["databases"]["import_max_bytes"] == 10 * 1024**3
    assert DEFAULT_CONFIG["databases"]["import_directories"] == []


@pytest.mark.parametrize("value", [1024 * 1024, "2147483648", 10 * 1024**3])
def test_the_cap_takes_a_whole_number_of_bytes(value: object) -> None:
    assert _KEY_VALIDATORS["databases.import_max_bytes"](value) == int(value)  # type: ignore[call-overload]


@pytest.mark.parametrize("value", [0, 1024, -5, "ten", 1024**5])
def test_the_cap_refuses_what_is_not_a_sensible_size(value: object) -> None:
    with pytest.raises(ConfigError):
        _KEY_VALIDATORS["databases.import_max_bytes"](value)


def test_the_folders_come_as_a_list_or_a_comma_separated_string() -> None:
    validate = _KEY_VALIDATORS["databases.import_directories"]

    assert validate(["/srv/dumps", "/data/in/"]) == ["/srv/dumps", "/data/in"]
    assert validate("/srv/dumps, /srv/dumps,,/mnt/x") == ["/srv/dumps", "/mnt/x"]
    assert validate("") == []


@pytest.mark.parametrize(
    "value", ["relative/dir", ["/ok", "also/relative"], "/", ["/srv", "//"], 7]
)
def test_the_folders_must_be_absolute_and_never_the_root(value: object) -> None:
    with pytest.raises(ConfigError):
        _KEY_VALIDATORS["databases.import_directories"](value)
