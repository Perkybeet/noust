# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for Noust."""

try:
    import pytest
except ImportError:  # the console's sandbox imports tests.support without pytest installed
    pass
else:
    # The helpers the tests share were once part of test modules, which pytest rewrites so that a
    # failed ``assert`` in them shows both sides; they keep that now that they are modules of their own.
    pytest.register_assert_rewrite("tests.support")
