# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Static sites already update without a cut, and why.

A static site has no process, so there is nothing to start twice and no unit
to restart. Its web server serves ``current/...``, and activating a release is
one rename of that link over the old one: every request resolves it to the old
release before the rename and to the new one after, and never to nothing.
These tests hold the claim (documented in ``docs/releases.md``) to the code, so
the day an update of a static site restarts, stops or reloads something, one of
them says so before an operator's site goes dark for it.
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
from noust.deployers.static import StaticDeployer
from tests.test_release_pipeline import (
    DOMAIN,
    GIT_URL,
    PORT,
    active_id,
    deploy_new,
    git,
    machine,
    root,
    store,
    wire,
    write_tree,
)

#: pytest resolves these by name; naming them here is what keeps the linter from
#: reading the tests' parameters as redefinitions of unused imports.
FIXTURES = (git, machine, root, store)


def site(directory: Path, body: str) -> Path:
    """
    A static project.

    Args:
        directory: Where to create it.
        body: What its page says.

    Returns:
        The directory.
    """
    return write_tree(directory, {"public/index.html": f"<h1>{body}</h1>"})


def update(machine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> Any:
    """
    Update the site through the shared sequence, over the fakes.

    Args:
        machine: The fakes.
        monkeypatch: Patching helper.

    Returns:
        What the update returned.
    """
    monkeypatch.setattr(
        lifecycle,
        "get_deployer",
        lambda app_type, verbose=False: wire(
            StaticDeployer(verbose=False, runner=machine.runner), machine
        ),
    )
    return lifecycle.update_app(DOMAIN)


def test_activating_a_static_release_replaces_the_link_in_one_rename_and_touches_nothing_else(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``current`` exists before and after the rename; no unit, no reload, no command."""
    machine.git.publish(site(tmp_path / "v1", "one"))
    deploy_new(root, machine, deployer_class=StaticDeployer)
    first = active_id(root)
    machine.git.publish(site(tmp_path / "v2", "two"))
    current = root / "current"
    seen: list[tuple[str, bool, bool]] = []
    real_replace = os.replace

    def replace(source: Any, destination: Any) -> None:
        if Path(destination) == current:
            before = os.path.lexists(current)
            real_replace(source, destination)
            seen.append((os.path.basename(source), before, os.path.lexists(current)))
            return
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", replace)
    machine.runner.calls.clear()
    machine.services.restarts.clear()

    outcome = update(machine, monkeypatch)

    assert active_id(root) != first
    assert (current / "public" / "index.html").read_text() == "<h1>two</h1>"
    assert len(seen) == 1, "one rename of current, not a removal and a creation"
    temporary, existed_before, exists_after = seen[0]
    assert temporary.startswith("current.tmp-")
    assert (existed_before, exists_after) == (True, True), "never a moment without current"
    assert outcome.restarted == ()
    assert machine.services.restarts == []
    assert machine.services.units == {}
    systemd = [call for call in machine.runner.calls if call[0] in ("systemctl", "service")]
    assert systemd == [], "nothing asked of systemd"


def test_the_site_is_served_through_current_so_a_new_release_needs_no_reload(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    """nginx's root follows the link: the config does not change when a release does."""
    machine.git.publish(site(tmp_path / "v1", "one"))

    deploy_new(root, machine, deployer_class=StaticDeployer)

    context = machine.web.sites[DOMAIN]["context"]
    assert context["static_dir"].startswith(str(root / "current"))
    assert "releases" not in context["static_dir"]


def test_a_static_site_never_gets_the_zero_downtime_step(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no process to run twice: the default for new applications does not apply."""
    monkeypatch.setattr(
        delivery, "current", lambda **_k: delivery.DeliverySettings(zero_downtime=True)
    )
    machine.git.publish(site(tmp_path / "v1", "one"))
    deployer = wire(StaticDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout="releases")
    deployer.resolve_layout(None)
    deployer._is_new_deployment = True

    titles = [step.title for step in deployer.build_pipeline()]

    assert "Activating release" in titles
    assert "Enabling updates without downtime" not in titles
