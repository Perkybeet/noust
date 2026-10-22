# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's source options (checklist A-12, A-13, A-28): the table that keeps them.

Kept in a table of its own so a deploy's full-row write of ``apps`` cannot put
back what an operator changed while it ran; validated at the one setter.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core import app_source_store
from noust.core.app_source_store import SourceOptions, get_options, reset_options, set_options
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import App, NoustStore

DOMAIN = "app.example.com"


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", source="x", app_path=str(tmp_path / "app"))
    )
    yield instance
    NoustStore.reset_instance()


class TestDefaults:
    def test_no_row_is_what_applications_always_did(self, store: NoustStore) -> None:
        options = get_options(DOMAIN, store=store)
        assert options == SourceOptions()
        assert options.submodules is True
        assert options.shallow is True
        assert options.lfs == "auto"
        assert options.watch_paths == ()
        assert options.skip_markers is True
        assert options.customised is False

    def test_an_unknown_application_has_the_defaults_too(self, store: NoustStore) -> None:
        assert get_options("nobody.example.com", store=store) == SourceOptions()


class TestSetting:
    def test_changes_one_key_and_keeps_the_rest(self, store: NoustStore) -> None:
        set_options(DOMAIN, shallow=False, store=store)
        set_options(DOMAIN, watch_paths="apps/web/**\n!apps/web/docs/**", store=store)
        options = get_options(DOMAIN, store=store)
        assert options.shallow is False
        assert options.submodules is True
        assert options.watch_paths == ("apps/web/**", "!apps/web/docs/**")
        assert options.customised is True

    def test_an_empty_list_clears_the_watch_paths(self, store: NoustStore) -> None:
        set_options(DOMAIN, watch_paths=["a/**"], store=store)
        set_options(DOMAIN, watch_paths=[], store=store)
        assert get_options(DOMAIN, store=store).watch_paths == ()

    def test_lfs_takes_three_words(self, store: NoustStore) -> None:
        for word in ("on", "off", "auto"):
            assert set_options(DOMAIN, lfs=word, store=store).lfs == word
        with pytest.raises(ValidationError) as caught:
            set_options(DOMAIN, lfs="sometimes", store=store)
        assert caught.value.field == "lfs"

    @pytest.mark.parametrize("key", ["submodules", "shallow", "skip_markers"])
    def test_switches_must_be_booleans(self, store: NoustStore, key: str) -> None:
        with pytest.raises(ValidationError) as caught:
            set_options(DOMAIN, store=store, **{key: "yes"})
        assert caught.value.field == key

    def test_a_bad_watch_path_is_refused_whole(self, store: NoustStore) -> None:
        set_options(DOMAIN, watch_paths=["ok/**"], store=store)
        with pytest.raises(ValidationError):
            set_options(DOMAIN, watch_paths=["fine/**", "../out"], store=store)
        assert get_options(DOMAIN, store=store).watch_paths == ("ok/**",)

    def test_an_unknown_application_is_an_error(self, store: NoustStore) -> None:
        with pytest.raises(NoustError):
            set_options("nobody.example.com", shallow=False, store=store)

    def test_reset_goes_back_to_the_defaults(self, store: NoustStore) -> None:
        set_options(DOMAIN, shallow=False, store=store)
        assert reset_options(DOMAIN, store=store) is True
        assert get_options(DOMAIN, store=store) == SourceOptions()
        assert reset_options(DOMAIN, store=store) is False


class TestSurvivesARedeploy:
    def test_the_full_row_write_of_a_deploy_does_not_touch_it(self, store: NoustStore) -> None:
        set_options(DOMAIN, shallow=False, watch_paths=["a/**"], store=store)
        app = store.get_app(DOMAIN)
        assert app is not None
        store.update_app(app)
        assert get_options(DOMAIN, store=store).shallow is False
        assert get_options(DOMAIN, store=store).watch_paths == ("a/**",)

    def test_it_goes_with_the_application(self, store: NoustStore) -> None:
        set_options(DOMAIN, shallow=False, store=store)
        store.delete_app(DOMAIN)
        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM app_source")
            assert cursor.fetchone()[0] == 0


def test_the_module_exports_what_its_callers_use() -> None:
    assert set(app_source_store.__all__) >= {"get_options", "set_options", "reset_options"}
