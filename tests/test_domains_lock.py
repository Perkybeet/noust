# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests that a change of an application's names runs under the application's lock.

Adding, removing or re-issuing renders the site again from the store. A
blue/green switch that ran at the same time (turning the mode off, say)
renders it too: interleaved, one could write a site that still includes an
upstream the other just removed. Every change here takes the lock the
switch, an update and a deletion take; the lock is reentrant, so the switch
rendering the site through :func:`~noust.deployers.domains.refresh_site`
still works.
"""

from __future__ import annotations

import pytest

from noust.core.applock import AppBusyError, app_lock, is_held_here
from noust.deployers import domains
from tests.test_applock import Holder
from tests.test_domains import Machine, certs, machine, store, web

__all__ = ["certs", "machine", "store", "web"]  # fixtures, imported for pytest


def test_adding_a_name_is_refused_while_another_operation_runs(machine: Machine) -> None:
    machine.deploy()
    before = machine.config()

    with Holder("example.com", "zero-downtime switch"), pytest.raises(AppBusyError):
        domains.add_domain("example.com", "shop.example.com")

    assert machine.domains() == [("example.com", "primary")]
    assert machine.config() == before


def test_removing_a_name_is_refused_while_another_operation_runs(machine: Machine) -> None:
    machine.deploy()
    domains.add_domain("example.com", "shop.example.com")

    with Holder("example.com", "update"), pytest.raises(AppBusyError):
        domains.remove_domain("example.com", "shop.example.com")

    assert ("shop.example.com", "alias") in machine.domains()


def test_issuing_the_certificate_is_refused_while_another_operation_runs(
    machine: Machine,
) -> None:
    machine.deploy(tls=True)

    with Holder("example.com", "update"), pytest.raises(AppBusyError):
        domains.issue_certificate("example.com")

    assert machine.certbot_orders() == []


def test_the_site_is_rendered_under_the_lock(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine.deploy()
    seen: list[bool] = []
    original = domains.BaseDeployer.refresh_site

    def spy(self: domains.BaseDeployer, *, with_ssl: bool) -> None:
        seen.append(is_held_here("example.com"))
        original(self, with_ssl=with_ssl)

    monkeypatch.setattr(domains.BaseDeployer, "refresh_site", spy)

    domains.add_domain("example.com", "shop.example.com")

    assert seen and all(seen)


def test_a_change_made_by_an_operation_that_holds_the_lock_runs(machine: Machine) -> None:
    """Reentrant: the switch renders the site while it holds the lock."""
    machine.deploy()

    with app_lock("example.com", "zero-downtime switch"):
        domains.add_domain("example.com", "shop.example.com")

    assert ("shop.example.com", "alias") in machine.domains()
