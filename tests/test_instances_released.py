# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a test keeps on ``self`` does not outlive the test.

pytest keeps every collected test, and a test of a class keeps its instance: whatever a test (or the
fixtures it asked for) assigned to ``self`` stayed in memory until the whole run ended. The console's
tests keep a client on ``self``, and a client holds the whole application, so a worker held every
application the tests it ran had built: twenty-one of them after three hundred tests, about 25 MiB
each, and a run of the suite on a CI runner was lost for lack of memory. ``tests/conftest.py`` empties
the instance when its test is over.

The proof runs pytest on a suite of its own, in a process of its own, with the repository's hook: a
test that keeps something alive, and the test after it, which asks whether it still is.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SUITE = textwrap.dedent(
    '''
    import gc
    import unittest
    import weakref


    class Heavy:
        """Something a test keeps: an application, a client."""


    kept: list[weakref.ref] = []


    class TestKeepsAClient:
        def test_a_keeps_one_on_self(self) -> None:
            self.client = Heavy()
            kept.append(weakref.ref(self.client))
            assert kept[0]() is not None

        def test_b_the_next_test_finds_it_gone(self) -> None:
            gc.collect()
            assert kept[0]() is None, "the first test's client is still alive"


    class TestSetupMethodState:
        def setup_method(self) -> None:
            self.fixture_made = Heavy()
            kept.append(weakref.ref(self.fixture_made))

        def test_c_uses_it(self) -> None:
            assert self.fixture_made is not None

        def test_d_the_last_is_gone_too(self) -> None:
            gc.collect()
            assert kept[1]() is None


    class TestAUnittestCase(unittest.TestCase):
        """A unittest's own instance is the framework's: it is left alone."""

        def test_e_runs_and_reports(self) -> None:
            self.assertTrue(True)
    '''
)


@pytest.mark.allow_subprocess
def test_a_class_instance_is_emptied_when_its_test_is_over(tmp_path: Path) -> None:
    suite = tmp_path / "test_suite_under_test.py"
    suite.write_text(SUITE, encoding="utf-8")
    # The hook under test, the way the repository's conftest has it.
    (tmp_path / "conftest.py").write_text(
        "from tests.conftest import pytest_runtest_teardown  # noqa: F401\n", encoding="utf-8"
    )
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(suite),
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(ROOT),
            "-c",
            str(ROOT / "pyproject.toml"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
        env={
            "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}",
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
        },
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "5 passed" in done.stdout, done.stdout
