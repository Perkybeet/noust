# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The size limit of a backup policy is read and written one way everywhere (checklist H-04).

``--keep-gb`` and the fourth part of ``--to NAME[:COUNT[:DAYS[:GB]]]`` are read by
:mod:`noust.cli.commands.retention_options` for a database's policy, an application's
schedule, its data and Noust's own state, and the managers check and store the value with
the helpers of :mod:`noust.managers.retention`.
"""

from __future__ import annotations

import click
import pytest

from noust.cli.commands import backup, backup_volumes, db_backups, self_backup
from noust.cli.commands.retention_options import add_size, gigabytes
from noust.core.exceptions import ValidationError
from noust.managers.retention import (
    MAX_RETENTION_GB,
    check_gigabytes,
    gigabytes_to_bytes,
    with_gigabytes,
)


class TestTheReader:
    @pytest.mark.parametrize("text", ["none", "NONE", "off", "no", "", "  "])
    def test_none_is_no_limit(self, text: str) -> None:
        assert gigabytes(text, "--keep-gb") is None

    def test_a_number_is_gigabytes(self) -> None:
        assert gigabytes("2.5", "--keep-gb") == 2.5
        assert gigabytes("40", "--keep-gb") == 40.0

    def test_anything_else_is_a_usage_error_that_names_the_option(self) -> None:
        with pytest.raises(click.BadParameter, match="--keep-gb takes a number"):
            gigabytes("lots", "--keep-gb")

    def test_a_destination_without_a_size_has_none(self) -> None:
        assert add_size({"name": "nas"}, "") == {"name": "nas", "retention_gb": None}
        assert add_size({"name": "nas"}, "5")["retention_gb"] == 5.0


class TestEveryCommandReadsTheFourthPart:
    @pytest.mark.parametrize(
        "parser",
        [db_backups._destination, backup_volumes._destination, self_backup._destination],
    )
    def test_to_names_count_days_and_size(self, parser) -> None:  # type: ignore[no-untyped-def]
        assert parser("nas:7:30:50") == {
            "name": "nas",
            "retention_count": 7,
            "retention_days": 30,
            "retention_gb": 50.0,
        }
        assert parser("nas")["retention_gb"] is None
        assert parser("nas:7")["retention_count"] == 7

    def test_the_application_schedule_reads_it_too(self) -> None:
        assert backup._parse_destination_spec("nas:7:30:0.5")["retention_gb"] == 0.5

    @pytest.mark.parametrize(
        "parser",
        [db_backups._destination, backup_volumes._destination, self_backup._destination],
    )
    def test_a_size_that_is_not_a_number_is_refused(self, parser) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(click.BadParameter):
            parser("nas:7:30:lots")


class TestTheManagersHelpers:
    @pytest.mark.parametrize("value", [None, 0.001, 1, 2.5, MAX_RETENTION_GB])
    def test_a_good_size_passes_as_a_float_or_none(self, value: float | None) -> None:
        assert check_gigabytes(value, "retention_gb") == (None if value is None else float(value))

    @pytest.mark.parametrize("value", [0, -1, MAX_RETENTION_GB + 1, "5", True, float("nan")])
    def test_a_bad_size_is_refused_naming_the_field(self, value: object) -> None:
        with pytest.raises(ValidationError, match="retention_gb") as caught:
            check_gigabytes(value, "retention_gb")
        assert caught.value.field == "retention_gb"

    def test_a_gigabyte_is_two_to_the_thirtieth_and_never_zero_bytes(self) -> None:
        assert gigabytes_to_bytes(1) == 1024**3
        assert gigabytes_to_bytes(None) is None
        assert gigabytes_to_bytes(1e-12) == 1

    def test_a_stored_size_is_shown_in_gigabytes_in_its_place(self) -> None:
        shown = with_gigabytes({"retention_bytes": int(2.5 * 1024**3), "schedule": "daily"})

        assert shown == {"retention_gb": 2.5, "schedule": "daily"}
        assert with_gigabytes({"retention_bytes": None})["retention_gb"] is None
        assert with_gigabytes({})["retention_gb"] is None
