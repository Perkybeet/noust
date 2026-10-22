# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PHP applications already update without a cut, and why.

A PHP application has no unit of its own: its pool lives in the distribution's
PHP-FPM, and nginx hands PHP ``$realpath_root`` of ``current``. Activating a
release is one rename of ``current`` and a graceful reload of FPM (the
requests in flight finish on the old workers), never a stop or a restart. So
the zero-downtime mode, which exists to start a process beside the one that
serves, has nothing to add. These tests hold that to the code
(``docs/releases.md`` says it).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import delivery
from noust.core.store import NoustStore
from noust.deployers import lifecycle
from noust.deployers.php_fpm import PhpFpmDeployer
from tests.test_php_fpm import (
    DOMAIN,
    WORDPRESS,
    WORDPRESS_OPTIONS,
    deploy,
    machine,
    store,
    wire,
)
from tests.test_release_pipeline import write_tree

#: pytest resolves these by name; naming them here is what keeps the linter from
#: reading the tests' parameters as redefinitions of unused imports.
FIXTURES = (machine, store)


def systemd_calls(machine: SimpleNamespace) -> list[tuple[str, ...]]:
    """
    Args:
        machine: The fakes.

    Returns:
        Every command that asked systemd for something, in order.
    """
    return [call for call in machine.runner.calls if call[0] in ("systemctl", "service")]


def test_a_php_update_replaces_current_in_one_rename_and_reloads_fpm_gracefully(
    tmp_path: Path,
    machine: SimpleNamespace,
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The link is never absent, FPM is reloaded (not stopped, not restarted), no unit exists."""
    machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))
    deploy(machine, **WORDPRESS_OPTIONS)
    first = os.readlink(machine.root / "current")
    machine.git.publish(write_tree(tmp_path / "v2", {**WORDPRESS, "index.php": "<?php // v2\n"}))
    current = machine.root / "current"
    seen: list[tuple[bool, bool]] = []
    real_replace = os.replace

    def replace(source: Any, destination: Any) -> None:
        if Path(destination) == current:
            before = os.path.lexists(current)
            real_replace(source, destination)
            seen.append((before, os.path.lexists(current)))
            return
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace)
    machine.runner.calls.clear()
    monkeypatch.setattr(
        lifecycle,
        "get_deployer",
        lambda app_type, verbose=False: wire(
            PhpFpmDeployer(verbose=False, runner=machine.runner), machine
        ),
    )

    outcome = lifecycle.update_app(DOMAIN)

    assert os.readlink(current) != first
    assert (current / "index.php").read_text() == "<?php // v2\n"
    assert seen == [(True, True)], "one rename of current, present before and after"
    assert outcome.is_static is True and outcome.restarted == ()
    verbs = {call[1] for call in systemd_calls(machine)}
    assert verbs == {"reload-or-restart"}, f"only a graceful reload, not {verbs}"
    assert machine.services.units == {}, "no unit to start twice"


def test_a_php_application_never_gets_the_zero_downtime_step(
    tmp_path: Path,
    machine: SimpleNamespace,
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No process of its own to run twice: the default for new applications does not apply."""
    monkeypatch.setattr(
        delivery, "current", lambda **_k: delivery.DeliverySettings(zero_downtime=True)
    )
    machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))
    deployer = wire(PhpFpmDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(
        DOMAIN, "https://github.com/example/blog.git", port=3100, ssl=False,
        app_path=machine.root, layout="releases", **WORDPRESS_OPTIONS,
    )  # fmt: skip
    deployer.resolve_layout(None)
    deployer._is_new_deployment = True

    titles = [step.title for step in deployer.build_pipeline()]

    assert "Enabling updates without downtime" not in titles
    assert "Activating release" in titles
