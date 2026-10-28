# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_site_policy.py`` that other tests build on."""

from __future__ import annotations

import pytest

from noust.core.store import NoustStore
from tests.support.domains import Machine


@pytest.fixture
def served(machine: Machine, store: NoustStore) -> NoustStore:
    machine.deploy(app_type="nodejs")
    return store
