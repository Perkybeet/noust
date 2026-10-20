# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where an application's variables go depends on what the application is (checklist B-43, B-46).

A process application has its ``.env`` and a file of Noust's beside each release. A Compose stack
and a container read the project's own ``.env`` and nothing else, so what Noust says about the
deployment is written there, kept out of everything an operator edits or exports, and put back by
every write. An application that builds nothing refuses a variable only the build is given.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.container_store import ContainerSettings, ContainerStore
from noust.core.store import App, NoustStore
from noust.core.variables.placement import (
    app_builds,
    keeps_predefined_in_env,
    require_buildable,
    with_predefined,
    without_predefined,
)
from noust.deployers.helpers import app_env as app_env_module
from noust.deployers.helpers.app_env import read_app_env, read_runtime_env, write_app_env
from noust.deployers.helpers.env_manager import EnvManager
from noust.validators.environment import EnvironmentValidationError

DOMAIN = "shop.example.com"
PREDEFINED = {
    "NOUST_URL": "https://shop.example.com",
    "NOUST_DOMAIN": "shop.example.com",
    "NOUST_COMMIT": "abc1234",
}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def account(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
        ),
    )


def make_app(store: NoustStore, tmp_path: Path, app_type: str) -> App:
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True, exist_ok=True)
    store.create_app(App(domain=DOMAIN, app_path=str(root), app_type=app_type, port=3001))
    found = store.get_app(DOMAIN)
    assert found is not None
    return found


def file_of(app: App) -> dict[str, str]:
    return EnvManager().read_env_file(Path(app.app_path) / ".env")


class TestWhichTypesKeepThemInTheFile:
    @pytest.mark.parametrize("kind", ["docker-compose", "container"])
    def test_the_containers_of_a_stack_read_the_project_env(self, kind: str) -> None:
        assert keeps_predefined_in_env(kind)

    @pytest.mark.parametrize("kind", ["nodejs", "python", "monorepo", "static", None])
    def test_the_others_have_a_file_of_their_own(self, kind: str | None) -> None:
        assert not keeps_predefined_in_env(kind)

    def test_what_an_operator_sees_leaves_out_the_names_noust_owns(self) -> None:
        shown = without_predefined("container", {"API_KEY": "k", **PREDEFINED})

        assert shown == {"API_KEY": "k"}

    def test_a_process_application_is_shown_its_file_as_it_is(self) -> None:
        mine = {"NOUST_URL": "typed by hand", "API_KEY": "k"}

        assert without_predefined("nodejs", mine) == mine

    def test_a_write_carries_the_names_the_file_has_through(self) -> None:
        written = with_predefined("docker-compose", {"API_KEY": "k"}, {"OLD": "x", **PREDEFINED})

        assert written == {"API_KEY": "k", **PREDEFINED}


class TestAnApplicationThatBuildsNothing:
    def test_a_registry_image_is_not_built(self, store: NoustStore, tmp_path: Path) -> None:
        app = make_app(store, tmp_path, "container")
        ContainerStore(store).save(
            DOMAIN, ContainerSettings(method="image", image="ghcr.io/owner/app:1"), actor="t"
        )

        assert not app_builds(app)

    def test_a_dockerfile_build_and_every_other_type_are(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        app = make_app(store, tmp_path, "container")
        ContainerStore(store).save(DOMAIN, ContainerSettings(), actor="t")

        assert app_builds(app)
        assert app_builds(App(domain="x.example.com", app_type="nodejs"))

    def test_a_variable_for_the_build_alone_is_refused_there_with_the_way_out(self) -> None:
        with pytest.raises(EnvironmentValidationError) as caught:
            require_buildable(DOMAIN, ["NPM_TOKEN"], builds=False)

        assert "NPM_TOKEN" in caught.value.message
        assert "--runtime-only" in (caught.value.details or "")

    def test_nothing_is_refused_when_there_is_no_such_variable(self) -> None:
        require_buildable(DOMAIN, [], builds=False)
        require_buildable(DOMAIN, ["NPM_TOKEN"], builds=True)


class TestTheFile:
    def test_the_reader_leaves_the_names_noust_wrote_out_of_what_it_hands_over(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        app = make_app(store, tmp_path, "docker-compose")
        EnvManager().write_env_file(Path(app.app_path) / ".env", {"API_KEY": "k", **PREDEFINED})

        assert read_runtime_env(app) == {"API_KEY": "k"}
        assert read_app_env(app) == {"API_KEY": "k"}

    def test_an_edit_does_not_take_them_away_from_the_containers(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        app = make_app(store, tmp_path, "container")
        EnvManager().write_env_file(Path(app.app_path) / ".env", {"API_KEY": "k", **PREDEFINED})

        write_app_env(app, {"API_KEY": "changed", "NEW": "1"})

        assert file_of(app) == {"API_KEY": "changed", "NEW": "1", **PREDEFINED}

    def test_an_operator_cannot_set_one_of_them(self, store: NoustStore, tmp_path: Path) -> None:
        app = make_app(store, tmp_path, "container")
        EnvManager().write_env_file(Path(app.app_path) / ".env", dict(PREDEFINED))

        with pytest.raises(EnvironmentValidationError):
            write_app_env(app, {"NOUST_URL": "https://elsewhere.example"})

    def test_a_registry_image_refuses_a_variable_only_the_build_is_given(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        app = make_app(store, tmp_path, "container")
        ContainerStore(store).save(
            DOMAIN, ContainerSettings(method="image", image="ghcr.io/owner/app:1"), actor="t"
        )

        with pytest.raises(EnvironmentValidationError) as caught:
            write_app_env(app, {"NPM_TOKEN": "t"}, audience={"NPM_TOKEN": "build"})

        assert "nothing is built" in caught.value.message
        assert not (Path(app.app_path) / ".env").exists()

    def test_a_stack_that_builds_keeps_it_out_of_the_file_and_in_the_store(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        app = make_app(store, tmp_path, "docker-compose")

        write_app_env(app, {"NPM_TOKEN": "t", "API_KEY": "k"}, audience={"NPM_TOKEN": "build"})

        assert file_of(app) == {"API_KEY": "k"}
        assert read_app_env(app) == {"API_KEY": "k", "NPM_TOKEN": "t"}
