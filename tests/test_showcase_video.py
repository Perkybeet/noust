# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The "video" profile of the showcase (``tests/support/showcase_video.py``).

The product video is filmed from the real console over this seed, so the seed is a claim
about a believable server, and these tests hold it to it: four servers and nineteen
applications once the video has made its two, every application that can be on releases
is, with the newest good release active, histories written in working hours and in the
order they happened, the shop the video breaks still serving when the video starts, the
alert that fires only where its disk is full, and the people of the agency with accounts.
The "docs" profile, which ``panel/e2e/docs.spec.ts`` counts on, must not move.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from noust.core.alerts.rules import FIRING, AlertRules
from noust.core.store import NoustStore
from tests.support.showcase import (
    FRA_APPS,
    NODES,
    PROFILES,
    cert_alt_names,
    nodes_for,
    seed_showcase_state,
)
from tests.support.showcase_video import (
    NEXT_COMMITS,
    OPERATOR,
    VIDEO_FIRING,
    VIDEO_NEW_APPS,
    VIDEO_NODES,
    VIDEO_PEOPLE,
    VIDEO_READINGS,
    VIDEO_SSH_HOSTS,
    seed_video_people,
    video_rules_for,
)

#: Registrable domains checked NXDOMAIN before use (see the modules' docstrings): a new one
#: is checked the same way and added here, or this file fails.
CHECKED_DOMAINS = frozenset(
    {
        "wrenfield.io",
        "kestrelworks.io",
        "corvane.io",
        "nortewave.com",
        "fennwick.io",
        "brumaria.es",
        "kelmoor.dev",
        "thornfield.io",
        "palenbrook.io",
        "tidewick.io",
        "alderbrook.es",
        "marenbay.com",
        "quillfield.io",
    }
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        A store of its own.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


def _registrable(domain: str) -> str:
    return ".".join(domain.split(".")[-2:])


def test_the_profiles_are_the_two_the_seed_knows() -> None:
    assert PROFILES == ("docs", "video")
    assert nodes_for("docs") is NODES
    assert nodes_for("video") is VIDEO_NODES
    with pytest.raises(ValueError, match="Unknown showcase profile"):
        nodes_for("marketing")


def test_four_servers_and_nineteen_applications_once_the_video_made_its_two() -> None:
    assert list(VIDEO_NODES) == ["fra-1", "ams-3", "lon-2", "mad-1"]
    assert set(VIDEO_READINGS) == set(VIDEO_NODES)
    assert set(VIDEO_SSH_HOSTS) == set(VIDEO_NODES) - {"fra-1"}
    seeded = sum(len(apps) for apps in VIDEO_NODES.values())
    assert seeded + len(VIDEO_NEW_APPS) == 19
    assert len(VIDEO_NODES["fra-1"]) + 2 == 11
    domains = [app.domain for apps in VIDEO_NODES.values() for app in apps]
    assert len(domains) == len(set(domains))
    assert not set(VIDEO_NEW_APPS) & set(domains)


def test_every_name_is_an_invented_one_that_was_checked() -> None:
    domains = {app.domain for apps in VIDEO_NODES.values() for app in apps} | set(VIDEO_NEW_APPS)
    aliases = {app.alias for apps in VIDEO_NODES.values() for app in apps if app.alias}
    unchecked = {_registrable(name) for name in domains | aliases} - CHECKED_DOMAINS
    assert not unchecked, f"check these the way showcase.py says, then list them: {unchecked}"


def test_ams_3_has_the_full_disk_the_alert_is_about() -> None:
    disk = VIDEO_READINGS["ams-3"][2]
    rule = next(r for r in video_rules_for("ams-3") if r.name == VIDEO_FIRING["ams-3"][0])
    assert rule.metric == "disk"
    assert disk > rule.threshold
    assert VIDEO_FIRING["ams-3"][1] == disk
    for node, (_cpu, _memory, other_disk, _uptime) in VIDEO_READINGS.items():
        if node not in VIDEO_FIRING:
            assert other_disk < rule.threshold


@pytest.mark.parametrize("node", list(VIDEO_NODES))
def test_each_server_seeds_its_own_applications_on_releases(store: NoustStore, node: str) -> None:
    state = seed_showcase_state(store, node=node, profile="video", apps_root="/srv/apps")
    apps = {app.domain: app for app in store.list_apps()}
    expected = VIDEO_NODES[node]
    assert set(apps) == {app.domain for app in expected}
    for seeded in expected:
        app = apps[seeded.domain]
        assert app.app_path.startswith("/srv/apps/")
        # A Compose stack and a PHP-FPM site deploy in place; everything else is on releases.
        in_place = seeded.app_type in ("docker-compose", "php-fpm") or seeded.recipe is not None
        assert app.layout == ("inplace" if in_place else "releases")
        assert seeded.status == "running"
        if app.layout != "releases":
            continue
        assert app.id is not None
        releases = store.list_releases(app.id)
        assert {r.git_commit for r in releases} == {c.sha for c in seeded.commits}
        active = [r for r in releases if r.status == "active"]
        assert len(active) == 1
        assert active[0].git_commit == seeded.commits[-1].sha
        assert active[0].path == f"{app.app_path}/releases/{active[0].id}"
        assert active[0].id.endswith(f"-{seeded.commits[-1].sha}")
    assert not state.failed_domains


def test_histories_are_in_working_hours_and_recorded_in_the_order_they_happened(
    store: NoustStore,
) -> None:
    seed_showcase_state(store, node="fra-1", profile="video")
    deployments = sorted(store.list_deployments(limit=500), key=lambda d: d.id or 0)
    starts = [datetime.fromisoformat(d.started_at or "") for d in deployments]
    assert starts == sorted(starts), "ids follow time, as the Overview reads them"
    # (time of day, whole days back) of every commit; today's may be clamped to a minute ago
    # when the seed runs before that time of day, an earlier day's never is.
    planned = {
        (app.domain, commit.sha): (app.clock[index], app.deployed_days_ago[index])
        for app in VIDEO_NODES["fra-1"]
        if app.clock is not None
        for index, commit in enumerate(app.commits)
    }
    today = datetime.now().date()
    for deployment, started in zip(deployments, starts, strict=True):
        assert deployment.status == "success"
        clock, days = planned[(deployment.domain, deployment.git_commit or "")]
        assert (today - started.date()).days == days
        if days > 0:
            assert started.strftime("%H:%M") == clock
    durations = {d.duration_s for d in deployments}
    assert len(durations) > 3, "durations vary per commit instead of repeating one number"


def test_the_shop_the_video_breaks_is_serving_when_it_starts(store: NoustStore) -> None:
    seed_showcase_state(store, node="fra-1", profile="video")
    shop = store.get_app("shop.brumaria.es")
    assert shop is not None and shop.status == "running"
    assert shop.layout == "releases"
    history = store.list_deployments(domain="shop.brumaria.es", limit=10)
    assert [d.status for d in history] == ["success"] * 4
    nxt = NEXT_COMMITS["brumaria-shop"]
    assert nxt.crashes and nxt.journal
    assert nxt.commit.sha not in {d.git_commit for d in history}
    assert not NEXT_COMMITS["tidewick-site"].crashes


@pytest.mark.parametrize("node", list(VIDEO_NODES))
def test_alert_rules_on_every_server_and_the_one_that_fires(store: NoustStore, node: str) -> None:
    seed_showcase_state(store, node=node, profile="video")
    rules = AlertRules(store)
    assert {rule.name for rule in rules.rules()} == {rule.name for rule in video_rules_for(node)}
    firing = [state for state in rules.states() if state.state == FIRING]
    if node in VIDEO_FIRING:
        assert len(firing) == 1
        assert firing[0].last_value == VIDEO_FIRING[node][1]
        assert firing[0].acknowledged_by is None
    else:
        assert firing == []


def test_the_central_has_its_databases_and_the_n8n_it_installed(store: NoustStore) -> None:
    state = seed_showcase_state(store, node="fra-1", profile="video")
    assert state.database_names == ["wrenfield_prod", "brumaria_shop", "nortewave_wp", "sessions"]
    with store._transaction() as cursor:
        cursor.execute(
            "SELECT apps.domain, app_recipes.recipe FROM app_recipes JOIN apps ON apps.id = app_recipes.app_id"
        )
        installed = [tuple(row) for row in cursor.fetchall()]
    assert installed == [("automations.kelmoor.dev", "n8n")]


def test_the_people_of_the_agency_have_accounts(store: NoustStore) -> None:
    from noust.core.accounts.manager import AccountManager

    credentials = seed_video_people(store, second_factor=False)
    assert credentials.username == OPERATOR
    assert credentials.totp_secret is None
    accounts = {account.username: account for account in AccountManager(store).list_all()}
    assert set(accounts) == {person.username for person in VIDEO_PEOPLE}
    assert accounts[OPERATOR].display_name == "Marta Solano"
    assert "admin" in accounts[OPERATOR].roles


def test_the_docs_profile_is_left_as_its_screenshots_need_it(store: NoustStore) -> None:
    state = seed_showcase_state(store, node="fra-1")
    assert {app.domain for app in store.list_apps()} == {app.domain for app in FRA_APPS}
    assert state.failed_domains == ["shop.brumaria.es"]
    assert all(app.layout == "inplace" for app in store.list_apps())
    assert {d.duration_s for d in store.list_deployments(limit=100) if d.duration_s} <= {
        24.0,
        11.0,
    }
    assert cert_alt_names("fra-1") == {
        "wrenfield.io": "www.wrenfield.io",
        "blog.nortewave.com": "news.nortewave.com",
        "shop.brumaria.es": "tienda.brumaria.es",
    }
    assert cert_alt_names("mad-1", profile="video") == {"quillfield.io": "www.quillfield.io"}
