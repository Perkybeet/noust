# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What restarting, changing limits, renewing a certificate, adding a domain and deleting would do.

Each plan is held to the operation it plans in the same way: the operation is applied for real against
the same machine and the commands it ran are the plan's, and the plan leaves nothing behind. The machine
of ``test_plans_update`` serves the unit and the limits; the certificate, the domain and the deletion have
the machines of their own areas.
"""

# The fixtures are imported rather than replicated, so there stays one definition of each machine.
# ruff: noqa: F811

from __future__ import annotations

import os
from pathlib import Path

import pytest

from noust.core.runner import is_read_only, set_runner
from noust.deployers import lifecycle
from noust.deployers.plans.certificate import plan_cert_renew
from noust.deployers.plans.limits import plan_limits
from noust.managers.cert_manager import CertManager
from noust.managers.service_manager import ResourceLimits
from noust.web.api.apps import _service_action
from noust.web.api.plan_service import plan_service
from tests.support.certbot_sim import CertbotSim
from tests.support.plans_machine import (  # noqa: F401  (fixtures, found by name)
    DOMAIN,
    git,
    machine,
    node_tree,
    root,
    served,
    store,
    two_releases,
    unit_dirs,
)

CANARY = "canary-6b2f9d4e1a7c3085"


def commands_of(plan) -> list[tuple[str, ...]]:
    """What a plan says would run, leaving out the looks."""
    return [command.argv for command in plan.commands if not command.probe]


def listing(path: Path) -> list[str]:
    """Every path under a directory, with links told from files."""
    return sorted(
        f"{p.relative_to(path)}{'->' + os.readlink(p) if p.is_symlink() else ''}"
        for p in path.rglob("*")
    )


# -- restarting, stopping, starting -------------------------------------------------------


@pytest.mark.parametrize("action", ["restart", "stop", "start"])
def test_an_action_on_the_service_runs_what_its_plan_says(served, action: str) -> None:
    plan = plan_service(DOMAIN, action)
    assert served.runner.changes == []

    _service_action(DOMAIN, action, "done")

    assert commands_of(plan) == served.runner.changes and served.runner.changes
    assert plan.operation == f"app.{action}" and plan.effect.code == f"plans.service.{action}"
    assert ("plans.unknown.service_health" in [u.code for u in plan.unknown]) == (action != "stop")


# -- limits ---------------------------------------------------------------------------------


def test_new_limits_rewrite_the_unit_and_say_what_changes_in_it(served) -> None:
    unit = next(served.root.parent.parent.rglob("rel-example-com.service"))
    before = unit.read_text()

    plan = plan_limits(DOMAIN, memory_max_mb=512, restart=True)

    assert unit.read_text() == before and served.runner.changes == []
    assert [(f.change, Path(f.path).name) for f in plan.files] == [
        ("modify", "rel-example-com.service")
    ]
    assert ("add", "MemoryMax=512M") in [(line.kind, line.text) for line in plan.files[0].lines]
    assert plan.effect.code == "plans.limits.restart"
    assert [c.text for c in plan.commands if c.check] == ["GET http://127.0.0.1:3100/"]

    lifecycle.set_resource_limits(DOMAIN, ResourceLimits(memory_max_mb=512), restart=True)

    assert commands_of(plan) == served.runner.changes


def test_limits_that_are_out_of_range_are_what_the_operation_would_be_refused_with(served) -> None:
    plan = plan_limits(DOMAIN, memory_max_mb=1)

    assert plan.blocked is not None and plan.blocked.sentence.code.startswith(
        "plans.blocked.refused"
    )
    assert served.runner.changes == []


# -- a certificate --------------------------------------------------------------------------


@pytest.fixture
def certbot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CertbotSim:
    """A certbot that has one lineage, on the machine's own letsencrypt tree."""
    live = tmp_path / "letsencrypt" / "live"
    sim = CertbotSim(live)
    sim.have("shop.example.com", ["shop.example.com"])
    monkeypatch.setattr(CertManager, "LETSENCRYPT_DIR", live.parent)
    monkeypatch.setattr(CertManager, "LIVE_DIR", live)
    set_runner(sim)
    yield sim
    set_runner(None)


def test_renewing_a_certificate_runs_what_its_plan_says_and_says_what_it_cannot_know(
    certbot: CertbotSim,
) -> None:
    plan = plan_cert_renew("shop.example.com", force=True)
    assert certbot.renewals == []

    CertManager(verbose=False).renew_and_report(domain="shop.example.com", force=True)

    applied = [c for c in certbot.calls if not is_read_only(c)]
    assert commands_of(plan) == applied and applied
    assert plan.effect.code == "plans.cert.renew_forced"
    assert "plans.unknown.authority" in [u.code for u in plan.unknown]


def test_a_certificate_there_is_none_of_is_an_error(certbot: CertbotSim) -> None:
    from noust.core.exceptions import NoustError

    with pytest.raises(NoustError, match="Certificate not found"):
        plan_cert_renew("nobody.example.com")
