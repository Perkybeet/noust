# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a first deployment that failed leaves behind for its domain (item 141).

A first deployment that fails undoes the application's row on purpose, so the page of that domain
has nothing to say about it. ``FirstDeploys`` is the one reader and writer of the record that does:
what the operator asked for (to retry it), tied to the job that failed (for the verbatim output),
visible to the teams that own the project it was meant for and to nobody else.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.first_deploys import MAX_AGE_DAYS, FirstDeploys
from noust.core.store import App, JobRecord, NoustStore
from noust.core.teams import Scope, Target, TeamStore, ownership_target


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def attempts(store: NoustStore) -> FirstDeploys:
    return FirstDeploys(store)


def job(store: NoustStore, job_id: str, domain: str, status: str, error: str | None = None) -> None:
    store.create_job(
        JobRecord(
            id=job_id,
            type="deploy",
            name=f"Deploy {domain}",
            domain=domain,
            status=status,
            error=error,
            finished_at="2026-11-10T10:00:00" if status != "running" else None,
        )
    )


def ask(attempts: FirstDeploys, job_id: str, domain: str, **given: str | None) -> None:
    fields: dict[str, str | None] = {"origin": "source", "source": "https://github.com/you/app"}
    fields.update(given)
    attempts.record(job_id, domain, **fields)  # type: ignore[arg-type]


class TestWhatIsRemembered:
    def test_a_failed_attempt_comes_back_with_what_was_asked_and_how_it_failed(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "aa11", "shop.example.com", "failed", "Release 1 did not pass its health check")
        ask(attempts, "aa11", "shop.example.com", branch="main", app_type="nextjs")

        [found] = attempts.failed()

        assert found.job_id == "aa11"
        assert found.domain == "shop.example.com"
        assert (found.origin, found.source, found.branch, found.app_type) == (
            "source",
            "https://github.com/you/app",
            "main",
            "nextjs",
        )
        assert found.error == "Release 1 did not pass its health check"
        assert found.finished_at == "2026-11-10T10:00:00"

    def test_a_recipe_is_remembered_by_name_and_has_no_source(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "bb22", "kuma.example.com", "failed", "boom")
        ask(
            attempts, "bb22", "kuma.example.com", origin="recipe", source=None, recipe="uptime-kuma"
        )

        [found] = attempts.failed()

        assert (found.origin, found.recipe, found.source) == ("recipe", "uptime-kuma", None)

    def test_a_credential_in_the_source_is_never_kept(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "cc33", "shop.example.com", "failed", "boom")
        ask(
            attempts,
            "cc33",
            "shop.example.com",
            source="https://ana:hunter2@git.example.com/a/b.git",
        )

        [found] = attempts.failed()

        assert found.source is not None
        assert "hunter2" not in found.source
        assert found.source.startswith("https://ana:")

    def test_a_token_in_the_query_of_the_source_is_never_kept(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "dd44", "shop.example.com", "failed", "boom")
        ask(
            attempts,
            "dd44",
            "shop.example.com",
            source="https://host.example.com/a.zip?token=abc123",
        )

        [found] = attempts.failed()

        assert found.source is not None and "abc123" not in found.source

    def test_a_value_that_is_not_an_origin_is_refused(self, attempts: FirstDeploys) -> None:
        with pytest.raises(ValueError, match="origin"):
            ask(attempts, "ee55", "shop.example.com", origin="telepathy")


class TestWhichAttemptsAreFailures:
    def test_one_that_is_still_running_or_that_worked_is_not(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "r1", "a.example.com", "running")
        job(store, "ok", "b.example.com", "completed")
        job(store, "cx", "c.example.com", "cancelled")
        for job_id, domain in (
            ("r1", "a.example.com"),
            ("ok", "b.example.com"),
            ("cx", "c.example.com"),
        ):
            ask(attempts, job_id, domain)

        assert attempts.failed() == []

    def test_a_domain_that_has_an_application_now_is_not(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "f1", "shop.example.com", "failed", "boom")
        ask(attempts, "f1", "shop.example.com")
        store.create_app(App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop"))

        assert attempts.failed() == []

    def test_only_the_newest_attempt_of_a_domain_counts(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "old", "shop.example.com", "failed", "the first reason")
        ask(attempts, "old", "shop.example.com")
        job(store, "new", "shop.example.com", "completed")
        ask(attempts, "new", "shop.example.com")

        # The retry is running or worked: the earlier failure is history, not news.
        assert attempts.failed() == []

        job(store, "newer", "shop.example.com", "failed", "the second reason")
        ask(attempts, "newer", "shop.example.com")
        assert [found.error for found in attempts.failed()] == ["the second reason"]

    def test_the_domain_filter_picks_one(self, store: NoustStore, attempts: FirstDeploys) -> None:
        for job_id, domain in (("a1", "a.example.com"), ("b1", "b.example.com")):
            job(store, job_id, domain, "failed", "boom")
            ask(attempts, job_id, domain)

        assert [found.domain for found in attempts.failed(domain="b.example.com")] == [
            "b.example.com"
        ]
        assert len(attempts.failed()) == 2

    def test_an_attempt_older_than_the_limit_is_not_news_any_more(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "old", "shop.example.com", "failed", "boom")
        ask(attempts, "old", "shop.example.com")
        long_ago = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS + 1)).isoformat()
        with store._transaction() as cursor:
            cursor.execute("UPDATE first_deploys SET created_at = ?", (long_ago,))

        assert attempts.failed() == []

    def test_recording_forgets_what_is_far_older_than_the_limit(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "old", "a.example.com", "failed", "boom")
        ask(attempts, "old", "a.example.com")
        with store._transaction() as cursor:
            cursor.execute(
                "UPDATE first_deploys SET created_at = ?",
                ((datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),),
            )

        job(store, "fresh", "b.example.com", "failed", "boom")
        ask(attempts, "fresh", "b.example.com")

        with store._transaction() as cursor:
            cursor.execute("SELECT job_id FROM first_deploys")
            assert [row[0] for row in cursor.fetchall()] == ["fresh"]


class TestPuttingAnAttemptAway:
    def test_a_dismissed_attempt_is_no_longer_listed(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "f1", "shop.example.com", "failed", "boom")
        ask(attempts, "f1", "shop.example.com")

        assert attempts.dismiss("f1") is True

        assert attempts.failed() == []

    def test_dismissing_what_is_not_there_says_so(self, attempts: FirstDeploys) -> None:
        assert attempts.dismiss("nope") is False

    def test_a_later_failure_of_the_same_domain_is_listed_again(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "f1", "shop.example.com", "failed", "boom")
        ask(attempts, "f1", "shop.example.com")
        attempts.dismiss("f1")
        job(store, "f2", "shop.example.com", "failed", "boom again")
        ask(attempts, "f2", "shop.example.com")

        assert [found.job_id for found in attempts.failed()] == ["f2"]


class TestWhoSeesIt:
    @pytest.fixture
    def world(self, store: NoustStore) -> dict[str, Target]:
        teams = TeamStore(store)
        placed: dict[str, Target] = {}
        for slug in ("acme", "globex"):
            teams.create_team(slug.title(), slug=slug)
            placed[slug] = teams.resolve_target(Scope.everything(), team=slug)
        return placed

    def test_a_team_sees_what_was_meant_for_its_project_and_nothing_else(
        self, store: NoustStore, attempts: FirstDeploys, world: dict[str, Target]
    ) -> None:
        for slug in ("acme", "globex"):
            domain = f"{slug}.example.com"
            job(store, slug, domain, "failed", "boom")
            with ownership_target(world[slug].project_id, world[slug].environment_id):
                ask(attempts, slug, domain)
        acme = Scope(team_roles={world["acme"].team_id: "admin"}, sees_all=False)

        assert [found.domain for found in attempts.failed(scope=acme)] == ["acme.example.com"]
        assert len(attempts.failed(scope=Scope.everything())) == 2
        assert attempts.failed(scope=Scope.nothing()) == []

    def test_a_team_cannot_dismiss_what_is_not_its_own(
        self, store: NoustStore, attempts: FirstDeploys, world: dict[str, Target]
    ) -> None:
        job(store, "g1", "globex.example.com", "failed", "boom")
        with ownership_target(world["globex"].project_id, world["globex"].environment_id):
            ask(attempts, "g1", "globex.example.com")
        acme = Scope(team_roles={world["acme"].team_id: "admin"}, sees_all=False)

        assert attempts.dismiss("g1", scope=acme) is False
        assert len(attempts.failed()) == 1

    def test_without_a_target_it_belongs_to_the_default_project(
        self, store: NoustStore, attempts: FirstDeploys
    ) -> None:
        job(store, "d1", "shop.example.com", "failed", "boom")
        ask(attempts, "d1", "shop.example.com")

        with store._transaction() as cursor:
            cursor.execute("SELECT project_id, environment_id FROM first_deploys")
            row = cursor.fetchone()
        assert row[0] is not None and row[1] is not None
