# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Previews of a monorepo's applications (checklist B-31).

A monorepo is deployed as one application per workspace (``docs/workspaces.md``), each with
previews of its own. Two things make the pull request, and not the repository, the unit:
an application builds a preview only for the pull requests that change its directory (its
watch paths weighed against what the whole pull request changes), and the web application
can point at the API's preview of the same pull request with ``{{peer:api.example.com/URL}}``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_source_store
from noust.core.exceptions import IntegrationError, ValidationError
from noust.core.forge_events import Forge, PullRequestAction
from noust.core.store import App, NoustStore
from noust.core.variables import expressions
from noust.core.variables.predefined import Deployment
from noust.core.variables.resolver import Resolver, UnresolvedReferenceError, context_for
from noust.integrations.github import pulls
from noust.managers import previews
from tests.test_previews import (
    BASE,
    clock,
    event,
    jobs,
    posted,
    timer,
)

FIXTURES = (clock, jobs, posted, timer)

WEB = "web.example.com"
API = "api.example.com"
MONO = "https://github.com/Acme/Shop.git"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A web and an API of one repository, and an unrelated application."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    for domain, source in (
        (WEB, MONO),
        (API, MONO),
        ("other.example.com", "https://github.com/x/y"),
    ):
        instance.create_app(
            App(
                domain=domain,
                app_type="nodejs",
                source=source,
                branch="main",
                port=3000,
                app_path=str(tmp_path / domain),
                layout="releases",
            )
        )
    yield instance
    instance.close()
    NoustStore.reset_instance()


class TestThePeerReference:
    def test_it_is_a_reference_with_a_domain_and_a_field(self) -> None:
        [ref] = expressions.find_references("{{peer:api.example.com/URL}}")

        assert (ref.kind, ref.peer_domain, ref.peer_field) == ("peer", "api.example.com", "URL")
        expressions.check_reference(ref)

    @pytest.mark.parametrize(
        "text",
        [
            "{{peer:api.example.com/PORT}}",
            "{{peer:api.example.com}}",
            "{{peer:/URL}}",
            "{{peer:single/URL}}",
        ],
    )
    def test_what_names_no_application_or_no_field_is_refused(self, text: str) -> None:
        [ref] = expressions.find_references(text)

        with pytest.raises(ValidationError):
            expressions.check_reference(ref)

    def test_with_a_space_in_the_domain_it_is_just_text(self) -> None:
        assert expressions.find_references("{{peer:not a domain/URL}}") == []

    def test_with_a_dot_instead_of_a_colon_it_is_just_text(self) -> None:
        assert expressions.find_references("{{peer.api.example.com/URL}}") == []

    def test_an_escaped_one_stays_as_text(self) -> None:
        assert expressions.substitute(r"\{{peer:api.example.com/URL}}", lambda r: "x") == (
            "{{peer:api.example.com/URL}}"
        )


def resolver(store: NoustStore, domain: str, **deployment: Any) -> Resolver:
    return Resolver(context_for(domain, Deployment(domain=domain, **deployment), store=store))


class TestResolvingIt:
    def test_in_production_it_is_the_peers_own_address(self, store: NoustStore) -> None:
        assert (
            resolver(store, WEB).resolve("{{peer:api.example.com/URL}}")
            == "https://api.example.com"
        )
        assert resolver(store, WEB).resolve("{{peer:api.example.com/DOMAIN}}") == API

    def test_an_application_without_ssl_answers_on_http(self, store: NoustStore) -> None:
        api = store.get_app(API)
        assert api is not None
        api.ssl_enabled = False
        store.update_app(api)

        assert (
            resolver(store, WEB).resolve("{{peer:api.example.com/URL}}") == "http://api.example.com"
        )

    def test_an_application_that_is_not_there_says_so(self, store: NoustStore) -> None:
        with pytest.raises(UnresolvedReferenceError, match="not deployed here"):
            resolver(store, WEB).resolve("{{peer:gone.example.com/URL}}")

    def test_in_a_preview_it_is_the_peers_preview_of_the_same_pull_request(
        self, store: NoustStore
    ) -> None:
        previews.enable_previews(API, BASE)
        preview_of_web = resolver(
            store, f"pr-7-web-example-com.{BASE}", pull_request=7, preview_of=WEB
        )

        assert preview_of_web.resolve("{{peer:api.example.com/URL}}") == (
            f"https://pr-7-api-example-com.{BASE}"
        )

    def test_a_template_on_the_peer_names_it(self, store: NoustStore) -> None:
        previews.enable_previews(API, BASE, domain_template="{{pr_id}}-api.{{base}}")
        preview = resolver(store, f"x.{BASE}", pull_request=12, preview_of=WEB)

        assert preview.resolve("{{peer:api.example.com/DOMAIN}}") == f"12-api.{BASE}"

    def test_the_preview_that_exists_is_the_one_pointed_at(self, store: NoustStore) -> None:
        from noust.core.store import PreviewRecord

        previews.enable_previews(API, BASE, domain_template="{{random}}.{{base}}")
        store.save_preview(
            PreviewRecord(
                parent_domain=API,
                domain=f"k3j2h1g0.{BASE}",
                number=7,
                branch="feature",
                provider="github",
                expires_at="2099-01-01T00:00:00+00:00",
            )
        )
        preview = resolver(store, f"x.{BASE}", pull_request=7, preview_of=WEB)

        assert preview.resolve("{{peer:api.example.com/DOMAIN}}") == f"k3j2h1g0.{BASE}"

    def test_a_name_with_a_random_part_cannot_be_known_before_it_exists(
        self, store: NoustStore
    ) -> None:
        previews.enable_previews(API, BASE, domain_template="{{random}}.{{base}}")
        preview = resolver(store, f"x.{BASE}", pull_request=7, preview_of=WEB)

        with pytest.raises(UnresolvedReferenceError, match="random part"):
            preview.resolve("{{peer:api.example.com/URL}}")

    def test_a_peer_without_previews_is_pointed_at_in_production(self, store: NoustStore) -> None:
        preview = resolver(store, f"x.{BASE}", pull_request=7, preview_of=WEB)

        assert preview.resolve("{{peer:api.example.com/URL}}") == "https://api.example.com"

    def test_a_peer_of_another_repository_is_pointed_at_in_production(
        self, store: NoustStore
    ) -> None:
        previews.enable_previews("other.example.com", BASE)
        preview = resolver(store, f"x.{BASE}", pull_request=7, preview_of=WEB)

        assert preview.resolve("{{peer:other.example.com/URL}}") == "https://other.example.com"

    def test_a_preview_of_a_branch_has_no_pull_request_so_production_it_is(
        self, store: NoustStore
    ) -> None:
        previews.enable_previews(API, BASE)
        preview = resolver(store, f"x-1-web.{BASE}", preview_of=WEB)

        assert preview.resolve("{{peer:api.example.com/URL}}") == "https://api.example.com"


class TestOnlyThePullRequestsThatChangeTheDirectory:
    @pytest.fixture(autouse=True)
    def on(self, store: NoustStore) -> None:
        previews.enable_previews(WEB, BASE)
        previews.enable_previews(API, BASE)
        app_source_store.set_options(
            WEB, watch_paths=["apps/web/**", "packages/ui/**"], store=store
        )

    @pytest.fixture
    def files(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, Any]]:
        asked: list[tuple[str, int, Any]] = []
        monkeypatch.setattr(
            pulls,
            "changed_files",
            lambda repository, number, installation=None: (
                asked.append((repository, number, installation))
                or ["apps/api/server.ts", "README.md"]
            ),
        )
        return asked

    def handle(self, domain: str, **kw: Any) -> list[str]:
        return previews.handle_pull_request(event(repository="acme/shop", **kw), app_domain=domain)

    def test_a_pull_request_that_touches_none_of_its_paths_gets_no_preview(
        self,
        store: NoustStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
        files: list,
    ) -> None:
        assert self.handle(WEB) == []

        assert store.get_preview(WEB, 7) is None
        assert files and files[0][:2] == ("acme/shop", 7)
        app = store.get_app(WEB)
        assert app is not None and app.id is not None
        from noust.core import webhook_deliveries

        row = webhook_deliveries.list_deliveries(app.id, limit=1)[0]
        assert row.outcome == webhook_deliveries.IGNORED_PATHS
        assert "changes no file under the watch paths" in (row.detail or "")

    def test_the_one_it_does_touch_gets_one(
        self,
        store: NoustStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
        files: list,
    ) -> None:
        # The API declares no watch paths: every pull request of the repository is its own.
        assert len(self.handle(API)) == 1
        assert store.get_preview(API, 7) is not None
        assert files == []  # nothing asked of the code host for an application without paths

    def test_a_later_push_that_adds_a_watched_file_builds_it(
        self,
        store: NoustStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(pulls, "changed_files", lambda *a, **k: ["apps/web/page.tsx"])

        assert len(self.handle(WEB)) == 1

    def test_a_renamed_file_that_left_a_watched_directory_counts(
        self,
        store: NoustStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            pulls, "changed_files", lambda *a, **k: ["docs/page.md", "apps/web/old.md"]
        )

        assert len(self.handle(WEB)) == 1

    def test_when_the_files_cannot_be_read_the_pull_request_is_built_as_before(
        self,
        store: NoustStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail(*a: Any, **k: Any) -> list[str]:
            raise IntegrationError("no App reaches it")

        monkeypatch.setattr(pulls, "changed_files", fail)

        assert len(self.handle(WEB)) == 1

    def test_the_other_code_hosts_cannot_be_asked_so_they_build(
        self,
        store: NoustStore,
        jobs: list[dict[str, Any]],
        posted: list[dict[str, Any]],
        files: list,
    ) -> None:
        built = previews.handle_pull_request(
            event(
                repository="acme/shop",
                forge=Forge.GITLAB,
                clone_url="https://gitlab.com/acme/shop.git",
                action=PullRequestAction.OPENED,
            ),
            app_domain=WEB,
        )

        assert files == [] and len(built) == 1


class TestTheFilesOfAPullRequest:
    def test_every_page_is_read_and_a_rename_names_both_paths(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeApp:
            def paginate_as_installation(self, installation: int, path: str) -> list[Any]:
                assert installation == 9
                assert path == "/repos/acme/shop/pulls/7/files"
                return [
                    {"filename": "a.ts"},
                    {"filename": "b/new.ts", "previous_filename": "a/old.ts"},
                    {"filename": "a.ts"},
                    {"nothing": True},
                ]

        monkeypatch.setattr(pulls, "_app_for", lambda repository, installation: (FakeApp(), 9))

        assert pulls.changed_files("acme/shop", 7) == ["a.ts", "b/new.ts", "a/old.ts"]
