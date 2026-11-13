# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What adding a domain to an application and removing one would do, on the machine of the domains.
"""

# The fixtures are imported rather than replicated, so there stays one definition of the machine.
# ruff: noqa: F811

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import is_read_only
from noust.deployers import domains
from noust.deployers.plans.domains import plan_add_domain, plan_remove_domain
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import NGINX_BACKEND
from tests.support.domains import (  # noqa: F401  (fixtures, found by name)
    Machine,
    certs,
    machine,
    store,
    web,
)


def commands_of(plan) -> list[tuple[str, ...]]:
    """What a plan says would run, leaving out the looks."""
    return [command.argv for command in plan.commands if not command.probe]


@pytest.fixture
def domain_machine(machine: Machine, tmp_path: Path) -> Machine:
    """The domains machine, with the web server built where it is used, as a real deploy builds it."""
    backend = replace(
        NGINX_BACKEND,
        sites_available=tmp_path / "nginx/sites-available",
        sites_enabled=tmp_path / "nginx/sites-enabled",
    )

    def wire(deployer: Any) -> Any:
        deployer._webserver_manager = lambda: NginxManager(backend=backend)
        deployer.cert_manager = machine.certs
        return deployer

    machine.wire = wire  # type: ignore[method-assign]
    machine.deploy("example.com", layout="releases")
    return machine


def test_adding_a_domain_runs_what_its_plan_says_and_leaves_the_site_as_it_was(
    domain_machine: Machine,
) -> None:
    site = domain_machine.config("example.com")
    tree = sorted(str(p) for p in domain_machine.tmp_path.rglob("*"))
    runner = domain_machine.runner

    plan = plan_add_domain("example.com", "www.example.com", dns=False)

    assert domain_machine.config("example.com") == site
    assert sorted(str(p) for p in domain_machine.tmp_path.rglob("*")) == tree
    assert domain_machine.domains("example.com") == [("example.com", "primary")]
    # The site that lists the new name is rendered from a row the operation has not written yet.
    assert "plans.unknown.readback" in [u.code for u in plan.unknown]
    assert ("systemctl", "reload", "nginx") in commands_of(plan)
    assert "plans.unknown.dns" in [u.code for u in plan.unknown]

    runner.calls.clear()
    domains.add_domain("example.com", "www.example.com", issue_cert=False, dns=False)

    assert commands_of(plan) == [c for c in runner.calls if not is_read_only(c)]
    assert "www.example.com" in domain_machine.config("example.com")


def test_removing_a_domain_says_what_it_rewrites(domain_machine: Machine) -> None:
    domains.add_domain("example.com", "www.example.com", issue_cert=False, dns=False)
    site = domain_machine.config("example.com")
    domain_machine.runner.calls.clear()

    plan = plan_remove_domain("example.com", "www.example.com")

    assert domain_machine.config("example.com") == site
    assert ("www.example.com", "alias") in domain_machine.domains("example.com")
    assert plan.effect.code == "plans.domain.remove"
    assert ("systemctl", "reload", "nginx") in commands_of(plan)
