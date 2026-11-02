# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :func:`noust.core.fd_wait.wait_readable`, which a terminal waits on.

``select.select`` raises for a descriptor numbered 1024 or more, and a console
that has been running a while opens its descriptors past that line. What matters
here is that the answer is the same for every number.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from noust.core.fd_wait import wait_readable
from tests.support.busy_process import descriptors_taken_up_to


@pytest.fixture
def pipe():
    """
    A pipe whose two ends are closed when the test is done.

    Yields:
        The read end and the write end.
    """
    ends = os.pipe()
    yield ends
    for end in ends:
        try:
            os.close(end)
        except OSError:
            pass


class TestWaitReadable:
    def test_nothing_to_read_waits_the_time_asked_and_says_no(self, pipe):
        started = time.monotonic()

        assert wait_readable(pipe[0], 0.2) is False
        assert time.monotonic() - started >= 0.15

    def test_what_is_written_is_there_at_once(self, pipe):
        os.write(pipe[1], b"x")

        started = time.monotonic()

        assert wait_readable(pipe[0], 5.0) is True
        assert time.monotonic() - started < 2

    def test_a_write_that_arrives_while_waiting_ends_the_wait(self, pipe):
        timer = threading.Timer(0.1, os.write, (pipe[1], b"x"))
        timer.start()
        try:
            assert wait_readable(pipe[0], 5.0) is True
        finally:
            timer.join()

    def test_the_other_side_closing_is_something_to_read(self):
        # A read then says it is over; a wait that missed this would hang a
        # session whose program had already gone.
        read_end, write_end = os.pipe()
        os.close(write_end)
        try:
            assert wait_readable(read_end, 5.0) is True
        finally:
            os.close(read_end)

    def test_a_zero_wait_looks_and_does_not_block(self, pipe):
        started = time.monotonic()

        assert wait_readable(pipe[0], 0) is False
        assert time.monotonic() - started < 1

    def test_a_descriptor_past_the_select_ceiling_is_waited_on_like_any_other(self):
        with descriptors_taken_up_to(1030):
            read_end, write_end = os.pipe()
            try:
                assert min(read_end, write_end) >= 1024
                assert wait_readable(read_end, 0.05) is False
                os.write(write_end, b"x")
                assert wait_readable(read_end, 5.0) is True
            finally:
                os.close(read_end)
                os.close(write_end)
