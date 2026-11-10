# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a process holds open against what it may, and what is said when none is left (issue 19).

A central's console reached 1024 open files and answered every request, its own pages included,
with "Too many open files": a 500 with no name. :mod:`noust.core.descriptors` is the one place that
measures it (from ``/proc``), recognises it (an ``OSError`` with ``EMFILE`` or ``ENFILE``, at any
depth of its causes) and says it ("descriptores agotados") with what to do.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from noust.core import descriptors
from noust.core.exceptions import NoustError


def process(root: Path, pid: int, *, open_files: int, limits: str) -> None:
    """Describe a process under a fake ``/proc``."""
    base = root / str(pid)
    (base / "fd").mkdir(parents=True)
    for number in range(open_files):
        (base / "fd" / str(number)).write_text("")
    (base / "limits").write_text(
        "Limit                     Soft Limit           Hard Limit           Units     \n"
        "Max cpu time              unlimited            unlimited            seconds   \n"
        f"Max open files            {limits}            files     \n"
        "Max locked memory         8388608              8388608              bytes     \n"
    )


class TestWhatAProcessHolds:
    def test_the_files_open_and_the_limit_are_read_from_proc(self, tmp_path: Path) -> None:
        process(tmp_path, 4242, open_files=193, limits="1024                 524288")

        usage = descriptors.read_usage(4242, proc=tmp_path)

        assert usage == descriptors.Usage(open=193, limit=1024)
        assert usage is not None and round(usage.fraction or 0, 3) == 0.188

    def test_no_limit_is_a_limit_nobody_reaches(self, tmp_path: Path) -> None:
        process(tmp_path, 7, open_files=10, limits="unlimited            unlimited")

        usage = descriptors.read_usage(7, proc=tmp_path)

        assert usage == descriptors.Usage(open=10, limit=None)
        assert usage is not None and usage.fraction is None and not usage.near

    def test_a_process_that_is_gone_or_not_ours_to_read_is_not_an_error(
        self, tmp_path: Path
    ) -> None:
        assert descriptors.read_usage(999, proc=tmp_path) is None

    def test_a_limits_file_that_says_nothing_of_files_is_not_guessed_at(
        self, tmp_path: Path
    ) -> None:
        process(tmp_path, 8, open_files=3, limits="1024                 524288")
        (tmp_path / "8" / "limits").write_text("Max cpu time unlimited unlimited seconds\n")

        assert descriptors.read_usage(8, proc=tmp_path) is None

    @pytest.mark.parametrize(
        ("held", "near", "failing"),
        [
            (0, False, False),
            (819, False, False),
            (820, True, False),
            (972, True, False),
            (973, True, True),
            (1024, True, True),
        ],
    )
    def test_it_warns_at_80_percent_and_is_critical_close_to_the_limit(
        self, held: int, near: bool, failing: bool
    ) -> None:
        usage = descriptors.Usage(open=held, limit=1024)

        assert usage.near is near
        assert usage.critical is failing


class TestRecognisingIt:
    @pytest.mark.parametrize("number", [errno.EMFILE, errno.ENFILE])
    def test_either_way_of_running_out_of_files_is_one(self, number: int) -> None:
        assert descriptors.is_exhaustion(OSError(number, "Too many open files"))

    def test_another_os_error_is_not(self) -> None:
        assert not descriptors.is_exhaustion(OSError(errno.ENOENT, "No such file"))
        assert not descriptors.is_exhaustion(ValueError("Too many open files"))

    def test_it_is_found_under_the_error_that_was_made_of_it(self) -> None:
        try:
            try:
                raise OSError(errno.EMFILE, "Too many open files")
            except OSError as inner:
                raise NoustError("Could not write the unit", details=str(inner)) from inner
        except NoustError as wrapped:
            assert descriptors.is_exhaustion(wrapped)

    def test_it_is_found_when_it_was_only_the_context(self) -> None:
        try:
            try:
                raise OSError(errno.ENFILE, "Too many open files in system")
            except OSError:
                raise RuntimeError("could not read")  # noqa: B904 - the context is the point
        except RuntimeError as outer:
            assert descriptors.is_exhaustion(outer)

    def test_a_cycle_of_causes_ends(self) -> None:
        first, second = RuntimeError("a"), RuntimeError("b")
        first.__cause__, second.__cause__ = second, first

        assert not descriptors.is_exhaustion(first)


class TestSayingIt:
    def test_the_error_is_named_and_says_what_to_do(self) -> None:
        error = descriptors.exhausted_error()

        assert error.coded is not None and error.coded.code == "server.descriptors_exhausted"
        assert "file descriptors" in error.message.lower()
        assert "noust web restart" in error.details and "noust setup doctor" in error.details
        assert error.coded.params == {"command": "noust web restart"}

    def test_the_command_it_gives_is_one_the_tool_has(self) -> None:
        import shlex

        from click.testing import CliRunner

        from noust.cli.app import cli

        error = descriptors.exhausted_error()
        assert error.coded is not None
        command = str(error.coded.params["command"])

        result = CliRunner().invoke(cli, [*shlex.split(command)[1:], "--help"])

        assert result.exit_code == 0, result.output

    def test_the_words_of_the_sentence_are_in_the_catalog_with_its_fix(self) -> None:
        from noust.core.server_text import CATALOG

        assert "server.descriptors_exhausted" in CATALOG
        assert "server.descriptors_exhausted.hint" in CATALOG


class TestWhatAUnitSays:
    @pytest.mark.parametrize(
        ("line", "limit"),
        [
            ("LimitNOFILE=65536", 65536),
            ("LimitNOFILE=1024", 1024),
            ("LimitNOFILE=1024:524288", 1024),
            ("LimitNOFILE=infinity", descriptors.UNLIMITED),
            ("LimitNOFILE=", None),
            ("LimitNOFILE=lots", None),
        ],
    )
    def test_the_limit_a_unit_sets(self, line: str, limit: int | None) -> None:
        assert descriptors.unit_limit(f"[Service]\nExecStart=/bin/true\n{line}\n") == limit

    def test_a_unit_without_one_has_the_systems_default_and_says_nothing(self) -> None:
        assert descriptors.unit_limit("[Service]\nExecStart=/bin/true\n") is None

    @pytest.mark.parametrize(
        ("text", "old"),
        [
            ("[Service]\n", True),
            ("[Service]\nLimitNOFILE=1024\n", True),
            ("[Service]\nLimitNOFILE=65535\n", True),
            ("[Service]\nLimitNOFILE=65536\n", False),
            ("[Service]\nLimitNOFILE=1048576\n", False),
            ("[Service]\nLimitNOFILE=infinity\n", False),
        ],
    )
    def test_a_unit_below_what_noust_gives_its_own_is_old(self, text: str, old: bool) -> None:
        assert descriptors.has_old_limit(text) is old
