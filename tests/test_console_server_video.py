# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The machine the product video is filmed on (``scripts/console_server.py --showcase --profile
video``, ``scripts/console_server/video.py``).

The video is the real console doing real things in the sandbox, so what makes them happen is a
claim worth holding: the profile is only a dataset of ``--showcase``, the version it shows is a
version, the agency's repositories answer ``git`` with the commits the story needs, the release
of Iker's push is the one that does not start, the pages show the server's paths and not the
sandbox's, and the databases a backup dumps are the agency's.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.runner import set_runner
from scripts.console_server import machine as machine_model
from scripts.console_server import runner as runner_model
from scripts.console_server import video
from scripts.console_server.arguments import parse_args
from scripts.console_server.sandbox import Sandbox
from tests.support.showcase_video import NEXT_COMMITS, VIDEO_DATABASES


def test_the_profile_is_a_dataset_of_the_showcase() -> None:
    assert parse_args(["--showcase", "--profile", "video"]).profile == "video"
    assert parse_args(["--showcase"]).profile == "docs"
    with pytest.raises(SystemExit):
        parse_args(["--profile", "video"])


@pytest.mark.parametrize("version", ["4.1", "v4.1.0", "4.1.0\n", "latest"])
def test_the_shown_version_is_a_version(version: str) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--shown-version", version])


def test_a_shown_version_has_no_update_to_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    import noust
    from scripts.console_server.settings_pages import pin_settings_update_check

    monkeypatch.setattr(noust, "__version__", "4.1.0")
    assert pin_settings_update_check(up_to_date=True) == "4.1.0"
    assert pin_settings_update_check() == "4.2.0"


def test_the_shop_push_is_the_commit_of_the_story() -> None:
    commit = video._video_commit("kelmoor-labs/brumaria-shop", "main")
    assert commit.startswith(NEXT_COMMITS["brumaria-shop"].commit.sha)
    assert len(commit) == 40 and int(commit, 16) >= 0
    assert commit == video._video_commit("kelmoor-labs/brumaria-shop", "main"), "stable"
    assert not video._video_commit("kelmoor-labs/brumaria-shop", "staging").startswith("d3a8e05")
    assert video._video_subject("kelmoor-labs/brumaria-shop", "main") == (
        "Bump the Node runtime to 24 LTS"
    )


def test_only_the_release_of_the_push_does_not_start(tmp_path: Path) -> None:
    shop = tmp_path / "shop"
    site = tmp_path / "site"
    video._video_tree("kelmoor-labs/brumaria-shop", "main", shop)
    video._video_tree("kelmoor-labs/tidewick-site", "main", site)
    assert (shop / video.CRASH_MARKER).exists()
    assert "Cannot find module" in (shop / video.CRASH_MARKER).read_text(encoding="utf-8")
    assert not (site / video.CRASH_MARKER).exists()
    assert '"next": "15.2.4"' in (site / "package.json").read_text(encoding="utf-8")
    assert (site / "package-lock.json").exists(), "it installs with npm ci"
    assert not (site / "server.js").exists()


def test_a_unit_runs_whatever_its_current_release_is(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path)
    sandbox.systemd_dir.mkdir(parents=True)
    root = tmp_path / "apps" / "shop"
    for name, crashes in (("good", False), ("bad", True)):
        release = root / "releases" / name
        release.mkdir(parents=True)
        if crashes:
            (release / video.CRASH_MARKER).write_text("Error\n", encoding="utf-8")
    (sandbox.systemd_dir / "shop.service").write_text(
        f"[Service]\nWorkingDirectory={root / 'current'}\n", encoding="utf-8"
    )
    (root / "current").symlink_to("releases/bad")
    assert video._crashing_release(sandbox, "shop") is not None
    (root / "current").unlink()
    (root / "current").symlink_to("releases/good")
    assert video._crashing_release(sandbox, "shop") is None
    assert video._crashing_release(sandbox, "nothing") is None


def test_logs_show_the_server_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from noust.deployers.recorder import DeploymentRecorder

    monkeypatch.setattr(DeploymentRecorder, "_scrub", lambda self, text: text)
    video.show_server_paths(Sandbox(tmp_path))
    line = f"Repository cache: {tmp_path}/var/www/apps/tidewick-io/repo"
    assert DeploymentRecorder._scrub(object(), line) == (  # type: ignore[arg-type]
        "Repository cache: /var/www/apps/tidewick-io/repo"
    )


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[runner_model.FakeRunner]:
    units = {"postgresql": machine_model.Unit(active="active", managed=False)}
    made = runner_model.make_runner(units, {}, {}, [], tmp_path / "systemd")
    set_runner(made)
    try:
        yield made
    finally:
        set_runner(None)


@pytest.mark.parametrize("node", list(VIDEO_DATABASES))
def test_the_database_servers_hold_the_agencys_databases(runner: object, node: str) -> None:
    video.model_agency_databases(node)
    held = set(runner._pg_databases) | set(runner._mysql_databases)  # type: ignore[attr-defined]
    wanted = {name for name, engine, *_ in VIDEO_DATABASES[node] if engine != "redis"}
    assert held - {"postgres"} == wanted
    assert "example_production" not in held
