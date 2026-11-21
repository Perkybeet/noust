# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Adopting a stack writes down what it runs with, once the application exists.

The plan of an adoption only says what the stack runs with (and it is made for a preview and for a
rehearsal, which write nothing); applying it registers the application, and that is when what the stack was
given of the server becomes what its unit's start is held to.
"""

from __future__ import annotations

from pathlib import Path

from noust.core import compose_acceptance
from noust.core.store import NoustStore
from noust.managers.nginx_manager import NginxManager
from tests.support.compose_adopt import (  # noqa: F401
    DOMAIN,
    Docker,
    docker,
    root,
    store,
    units,
    web,
)

# ruff: noqa: F811


def test_a_stack_adopted_with_what_it_runs_is_recorded_with_it(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    from noust.deployers.compose_adopt import adopt_stack

    adopt_stack(DOMAIN, root, web=web, accept_host_access=True, accepted_by="alice")

    accepted = compose_acceptance.get(DOMAIN, store=store)
    assert accepted is not None and accepted.source == "adopt"
    assert {key[1] for key in accepted.keys} == {"socket"}


def test_a_plan_that_is_only_looked_at_records_nothing(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    plan_adoption(DOMAIN, root, web=web, accept_host_access=True, accepted_by="alice")

    assert compose_acceptance.get(DOMAIN, store=store) is None
