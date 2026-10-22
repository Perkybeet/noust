# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Update awareness: which installed recipes are behind, and updating one through update_app."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, RolledBackError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.recipes import RecipeError, RecipeNotFoundError, parse_recipe
from noust.recipes import updates as updates_module
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir
from noust.recipes.installed import get_installed, record_install
from noust.recipes.model import ChangelogEntry
from noust.recipes.updates import (
    installed_statuses,
    is_newer,
    outdated,
    status_of,
    update_recipe,
)
from tests.recipes.factory import (
    compose_document,
    compose_recipe,
    native_recipe,
    use_demo_assets,
)

DOMAIN = "site.example.com"
APP = "site-example-com"


@pytest.fixture(autouse=True)
def demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the recipe named ``demo`` use the assets of a shipped recipe."""
    use_demo_assets(monkeypatch)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "installed", "newer"),
    [
        (("1.37.4", 1), ("1.37.3", 1), True),
        (("1.37.4", 1), ("1.37.4", 1), False),
        (("1.37.3", 1), ("1.37.4", 1), False),
        (("1.37.4", 2), ("1.37.4", 1), True),
        (("1.37.4", 1), ("1.37.4", 2), False),
        (("1.10.0", 1), ("1.9.9", 1), True),
        (("2.0", 1), ("1.99.99", 1), True),
        (("12.2", 1), ("12.1", 1), True),
        (("v0.31.0", 1), ("0.30.9", 1), True),
        (("1.2.0", 1), ("1.2", 1), False),
        (("1.2.1", 1), ("1.2", 1), True),
        (("1.2.0", 1), ("1.2.0-rc1", 1), True),
        (("1.2.0-rc1", 1), ("1.2.0", 1), False),
        (("sha-abc1234", 1), ("sha-def5678", 1), True),
        (("sha-abc1234", 1), ("sha-abc1234", 1), False),
        (("1.0.0", 1), ("sha-abc1234", 1), True),
    ],
)
def test_a_version_is_newer_by_its_numbers_then_its_revision(
    candidate: tuple[str, int], installed: tuple[str, int], newer: bool
) -> None:
    """The application's version decides; the recipe's revision breaks a tie; commits differ."""
    assert (
        is_newer(candidate[0], candidate[1], than_version=installed[0], than_revision=installed[1])
        is newer
    )


# ---------------------------------------------------------------------------
# What is behind
# ---------------------------------------------------------------------------


def catalogue(monkeypatch: pytest.MonkeyPatch, **recipes: Any) -> None:
    """Make these documents the catalogue the updates read."""
    parsed = {name: parse_recipe(name, document) for name, document in recipes.items()}

    def get(name: str) -> Any:
        if name not in parsed:
            raise RecipeNotFoundError(f"No recipe named {name!r}")
        return parsed[name]

    monkeypatch.setattr(updates_module, "get_recipe", get)


def install(store: NoustStore, recipe_name: str, version: str, *, domain: str = DOMAIN) -> None:
    """An application installed from a recipe at a version."""
    store.create_app(App(domain=domain, app_type="docker-compose", ssl_enabled=True, port=8123))
    document = parse_recipe("demo", compose_recipe(version=version))
    record_install(domain, dataclasses.replace(document, name=recipe_name), store=store)


def test_an_installed_recipe_is_behind_when_the_catalogue_has_a_newer_version(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With what changed in between, and nothing for versions it already has."""
    install(recipe_store, "demo", "1.0.0")
    catalogue(
        monkeypatch,
        demo=compose_recipe(
            name="demo",
            version="1.2.0",
            changelog=[
                {"version": "1.2.0", "summary": "Fixes the thing."},
                {"version": "1.1.0", "summary": "Adds the other."},
                {"version": "1.0.0", "summary": "Already installed."},
            ],
        ),
    )
    installed = get_installed(DOMAIN, store=recipe_store)
    assert installed is not None

    status = status_of(installed)

    assert status.update_available is True
    assert (status.installed_version, status.latest_version) == ("1.0.0", "1.2.0")
    assert [change.version for change in status.changes] == ["1.2.0", "1.1.0"]
    assert status.to_dict()["changes"][0] == {"version": "1.2.0", "summary": "Fixes the thing."}
    assert [item.domain for item in outdated(store=recipe_store)] == [DOMAIN]


def test_a_current_installation_is_not_listed_as_outdated(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Up to date: in the full list, not in the outdated one."""
    install(recipe_store, "demo", "1.2.0")
    catalogue(monkeypatch, demo=compose_recipe(name="demo", version="1.2.0"))

    assert outdated(store=recipe_store) == []
    only = installed_statuses(store=recipe_store)
    assert [(status.domain, status.update_available) for status in only] == [(DOMAIN, False)]
    assert only[0].changes == ()


def test_a_recipe_revision_alone_is_an_update(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recipe fixed, the application's version the same."""
    install(recipe_store, "demo", "1.2.0")
    catalogue(monkeypatch, demo=compose_recipe(name="demo", version="1.2.0", revision=2))

    assert [status.domain for status in outdated(store=recipe_store)] == [DOMAIN]


def test_a_recipe_that_left_the_catalogue_or_is_unavailable_is_never_updatable(
    recipe_store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason is said instead."""
    install(recipe_store, "gone", "1.0.0")
    install(recipe_store, "paused", "1.0.0", domain="b.example.com")
    catalogue(
        monkeypatch,
        paused={
            "schema": 2,
            "name": "paused",
            "title": "Paused",
            "slogan": "Paused.",
            "description": "Paused.",
            "category": "other",
            "homepage": "https://example.com",
            "available": False,
            "unavailable_reason": "Upstream archived the project.",
        },
    )

    statuses = {status.recipe: status for status in installed_statuses(store=recipe_store)}

    assert statuses["gone"].update_available is False
    assert "no longer in the catalogue" in (statuses["gone"].reason or "")
    assert statuses["paused"].update_available is False
    assert statuses["paused"].reason == "Upstream archived the project."


# ---------------------------------------------------------------------------
# Updating
# ---------------------------------------------------------------------------


class Lifecycle:
    """update_app and the health gate, recorded."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None
        self.gate_answers: tuple[bool, str] = (True, "")
        self.gates = 0
        self.env_writes: list[dict[str, str]] = []
        self.env: dict[str, str] = {}
        self.source_during_update: dict[str, str] = {}

    def update_app(self, domain: str, **kwargs: Any) -> Any:
        self.updates.append({"domain": domain, **kwargs})
        directory = Path(kwargs["source"]) if kwargs.get("source", "").startswith("/") else None
        if directory is not None and directory.is_dir():
            self.source_during_update = {
                path.name: path.read_text() for path in directory.iterdir() if path.is_file()
            }
        if self.fail_with is not None:
            raise self.fail_with
        return SimpleNamespace(deployment_id=41, warnings=None)

    def health_gate_for(self, app: App, store: NoustStore, log: Logger) -> Any:
        self.gates += 1
        return SimpleNamespace(restart_and_probe=lambda: self.gate_answers)

    def read_app_env(self, app: App) -> dict[str, str]:
        return dict(self.env)

    def write_app_env(self, app: App, values: dict[str, str], **_kwargs: Any) -> Path:
        self.env_writes.append(dict(values))
        self.env = dict(values)
        return Path("/nonexistent/.env")


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> Lifecycle:
    """The deployers' entry points, replaced by recorders."""
    fake = Lifecycle()
    monkeypatch.setattr("noust.deployers.lifecycle.update_app", fake.update_app)
    monkeypatch.setattr("noust.deployers.lifecycle.health_gate_for", fake.health_gate_for)
    monkeypatch.setattr("noust.deployers.helpers.app_env.read_app_env", fake.read_app_env)
    monkeypatch.setattr("noust.deployers.helpers.app_env.write_app_env", fake.write_app_env)
    return fake


def stack(store: NoustStore, version: str, **extra: Any) -> Any:
    """An installed Compose recipe at ``version``, whose files are on disk as it ran."""
    document = compose_recipe(version=version, **extra)
    recipe = parse_recipe("demo", document)
    store.create_app(
        App(
            domain=DOMAIN,
            app_type="docker-compose",
            ssl_enabled=True,
            port=8123,
            app_path=str(store.db_path.parent / "apps" / APP),
        )
    )
    record_install(DOMAIN, recipe, store=store)
    directory = recipe_source_dir(APP, store)
    directory.mkdir(parents=True)
    (directory / COMPOSE_FILE).write_text(recipe.compose or "")
    return recipe


def test_updating_something_not_installed_from_a_recipe_says_how_to_update_it(
    recipe_store: NoustStore, lifecycle: Lifecycle
) -> None:
    """Nothing is touched."""
    recipe_store.create_app(App(domain=DOMAIN, app_type="nodejs"))

    with pytest.raises(RecipeError, match="was not installed from a recipe") as failure:
        update_recipe(DOMAIN, store=recipe_store)

    assert f"noust update {DOMAIN}" in failure.value.details
    assert lifecycle.updates == []


def test_a_hub_updates_nothing(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The role is checked before the store is read."""
    from noust.core.exceptions import NoustError

    monkeypatch.setattr("noust.central.is_hub", lambda config=None: True)

    with pytest.raises(NoustError, match="not available on this central"):
        update_recipe(DOMAIN, store=recipe_store)

    assert lifecycle.updates == []


def test_an_installation_that_is_current_is_left_alone(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """updated is False, update_app is not called."""
    stack(recipe_store, "1.2.0")
    catalogue(monkeypatch, demo=compose_recipe(name="demo", version="1.2.0"))

    outcome = update_recipe(DOMAIN, store=recipe_store)

    assert outcome.updated is False and outcome.to_version == "1.2.0"
    assert lifecycle.updates == []


def test_a_compose_update_goes_through_update_app_with_the_new_document(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one update, from the directory the new document was written to."""
    stack(recipe_store, "1.0.0")
    new_text = compose_document().replace("1.2.3", "1.3.0")
    catalogue(monkeypatch, demo=compose_recipe(name="demo", version="1.3.0", compose=new_text))
    lifecycle.env = {"SECRET": "kept-secret", "PORT": "8123"}

    outcome = update_recipe(DOMAIN, trigger="panel", job_id="job-9", store=recipe_store)

    call = lifecycle.updates[0]
    assert call["domain"] == DOMAIN and call["trigger"] == "panel" and call["job_id"] == "job-9"
    assert call["source"] == str(recipe_source_dir(APP, recipe_store)) and call["branch"] is None
    assert (
        lifecycle.source_during_update[COMPOSE_FILE].endswith("\n")
        and "1.3.0" in lifecycle.source_during_update[COMPOSE_FILE]
    )
    assert (outcome.updated, outcome.from_version, outcome.to_version) == (True, "1.0.0", "1.3.0")
    assert outcome.deployment_id == 41
    installed = get_installed(DOMAIN, store=recipe_store)
    assert installed is not None and installed.version == "1.3.0"


def test_what_the_application_has_is_never_regenerated_and_what_is_new_is_added(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The secret stays, the variable the new version declares is generated, once."""
    stack(recipe_store, "1.0.0")
    text = compose_document().replace(
        'SECRET: "${SECRET}"', 'SECRET: "${SECRET}"\n      NEWKEY: "${NEWKEY}"'
    )
    catalogue(
        monkeypatch,
        demo=compose_recipe(
            name="demo",
            version="1.1.0",
            compose=text,
            variables={"SECRET": {"generate": "password32"}, "NEWKEY": {"generate": "hex32"}},
        ),
    )
    lifecycle.env = {"SECRET": "edited-by-the-operator", "PORT": "8123"}

    outcome = update_recipe(DOMAIN, store=recipe_store)

    written = lifecycle.env_writes[0]
    assert written["SECRET"] == "edited-by-the-operator" and written["PORT"] == "8123"
    assert len(written["NEWKEY"]) == 32
    assert outcome.added_variables == ("NEWKEY",)
    app = recipe_store.get_app(DOMAIN)
    assert app is not None and app.env_secret_marks.get("NEWKEY") is True


def test_nothing_is_written_when_no_variable_is_new(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An update that changes only the image leaves the .env alone."""
    stack(recipe_store, "1.0.0")
    catalogue(monkeypatch, demo=compose_recipe(name="demo", version="1.1.0"))
    lifecycle.env = {"SECRET": "s" * 32, "PORT": "8123"}

    update_recipe(DOMAIN, store=recipe_store)

    assert lifecycle.env_writes == []


def test_a_new_required_variable_stops_the_update_before_anything_is_touched(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """${VAR:?required} applies to an update as to an install."""
    stack(recipe_store, "1.0.0")
    text = compose_document().replace(
        'SECRET: "${SECRET}"', 'SECRET: "${SECRET}"\n      LICENSE: "${LICENSE:?Your licence key}"'
    )
    catalogue(monkeypatch, demo=compose_recipe(name="demo", version="1.1.0", compose=text))
    lifecycle.env = {"SECRET": "s" * 32, "PORT": "8123"}
    before = (recipe_source_dir(APP, recipe_store) / COMPOSE_FILE).read_text()

    with pytest.raises(RecipeError, match="needs a value for LICENSE"):
        update_recipe(DOMAIN, store=recipe_store)

    assert lifecycle.updates == [] and lifecycle.env_writes == []
    assert (recipe_source_dir(APP, recipe_store) / COMPOSE_FILE).read_text() == before

    update_recipe(DOMAIN, overrides={"LICENSE": "k-1"}, store=recipe_store)
    assert lifecycle.env_writes[0]["LICENSE"] == "k-1"


def test_a_version_that_needs_a_service_the_installed_one_never_had_is_refused(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Services are provisioned at install; an update does not invent a database."""
    recipe_store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path="/x", port=3000))
    installed = parse_recipe("demo", native_recipe(version="1.0.0"))
    record_install(DOMAIN, installed, store=recipe_store)
    catalogue(
        monkeypatch,
        demo=native_recipe(
            name="demo",
            version="2.0.0",
            requires=["postgres"],
            variables={"DATABASE_URL": {"from": "postgres.url"}},
        ),
    )

    with pytest.raises(
        RecipeError, match="needs postgres, which the installed version did not use"
    ):
        update_recipe(DOMAIN, store=recipe_store)

    assert lifecycle.updates == []


def test_a_native_git_recipe_updates_from_the_new_tag(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The repository and the ref, as a creation passes them."""
    recipe_store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path="/x", port=3000))
    record_install(DOMAIN, parse_recipe("demo", native_recipe(version="1.0.0")), store=recipe_store)
    catalogue(
        monkeypatch,
        demo=native_recipe(
            name="demo",
            version="1.1.0",
            source={"git": "https://github.com/o/r.git", "ref": "v1.1.0"},
        ),
    )

    update_recipe(DOMAIN, store=recipe_store)

    call = lifecycle.updates[0]
    assert (call["source"], call["branch"]) == ("https://github.com/o/r.git", "v1.1.0")


def test_a_native_archive_recipe_updates_from_the_new_archive_with_its_checksum(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The checksum travels in the URL, where every download verifies it."""
    recipe_store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path="/x", port=3000))
    record_install(DOMAIN, parse_recipe("demo", native_recipe(version="1.0.0")), store=recipe_store)
    catalogue(
        monkeypatch,
        demo=native_recipe(
            name="demo",
            version="1.1.0",
            source={"archive": "https://h/app-1.1.0.tar.gz", "sha256": "ab" * 32},
        ),
    )

    update_recipe(DOMAIN, store=recipe_store)

    assert lifecycle.updates[0]["source"] == f"https://h/app-1.1.0.tar.gz#sha256={'ab' * 32}"


def test_a_failed_compose_update_puts_the_previous_files_back_and_says_so(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A digest has no name for the deployer to go back to: the files are what goes back."""
    old = stack(recipe_store, "1.0.0")
    app = recipe_store.get_app(DOMAIN)
    assert app is not None
    app_directory = Path(app.app_path)
    app_directory.mkdir(parents=True)
    (app_directory / COMPOSE_FILE).write_text("the new version's file, copied by the update")
    new_text = compose_document().replace("1.2.3", "1.3.0")
    catalogue(monkeypatch, demo=compose_recipe(name="demo", version="1.3.0", compose=new_text))
    lifecycle.fail_with = DeploymentError(
        "The update of site.example.com did not pass its health check",
        details="Probes: 502",
    )
    lifecycle.gate_answers = (True, "")

    with pytest.raises(DeploymentError) as failure:
        update_recipe(DOMAIN, store=recipe_store)

    assert (app_directory / COMPOSE_FILE).read_text() == old.compose
    assert (recipe_source_dir(APP, recipe_store) / COMPOSE_FILE).read_text() == old.compose
    assert lifecycle.gates == 1
    assert "Probes: 502" in failure.value.details
    assert "previous version is running again and answering" in failure.value.details
    installed = get_installed(DOMAIN, store=recipe_store)
    assert installed is not None and installed.version == "1.0.0"


def test_when_even_the_previous_version_does_not_answer_the_error_carries_the_evidence(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never a claim that everything is fine."""
    stack(recipe_store, "1.0.0")
    app = recipe_store.get_app(DOMAIN)
    assert app is not None
    Path(app.app_path).mkdir(parents=True)
    catalogue(monkeypatch, demo=compose_recipe(name="demo", version="1.3.0"))
    lifecycle.fail_with = DeploymentError("did not pass", details="first")
    lifecycle.gate_answers = (False, "connection refused on 8123")

    with pytest.raises(DeploymentError) as failure:
        update_recipe(DOMAIN, store=recipe_store)

    assert "not answering either" in failure.value.details
    assert "connection refused on 8123" in failure.value.details


def failing_update(
    store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> tuple[Any, Path]:
    """An installed stack at 1.0.0 whose update to 1.3.0 raises ``error``."""
    old = stack(store, "1.0.0")
    app = store.get_app(DOMAIN)
    assert app is not None
    app_directory = Path(app.app_path)
    app_directory.mkdir(parents=True)
    (app_directory / COMPOSE_FILE).write_text("the new version's file, copied by the update")
    new_text = compose_document().replace("1.2.3", "1.3.0")
    catalogue(monkeypatch, demo=compose_recipe(name="demo", version="1.3.0", compose=new_text))
    lifecycle.fail_with = error
    return old, app_directory


def test_a_rollback_the_deployer_did_puts_the_files_back_and_restarts_nothing(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RolledBackError: the previous containers serve; the compose file is still the new one."""
    old, app_directory = failing_update(
        recipe_store, lifecycle, monkeypatch, RolledBackError("rolled back", details="x")
    )

    with pytest.raises(RolledBackError) as failure:
        update_recipe(DOMAIN, store=recipe_store)

    assert (app_directory / COMPOSE_FILE).read_text() == old.compose
    assert (recipe_source_dir(APP, recipe_store) / COMPOSE_FILE).read_text() == old.compose
    assert lifecycle.gates == 0
    assert "files of the previous version were put back" in failure.value.details
    installed = get_installed(DOMAIN, store=recipe_store)
    assert installed is not None and installed.version == "1.0.0"


def test_a_hook_that_failed_before_anything_was_recreated_restores_files_and_restarts_nothing(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pre_deploy runs before the containers are touched: no restart for it."""
    from noust.deployers.helpers.hooks import HookFailedError

    # A hook's failure carries the hook and what the phase ran; this one needs neither.
    hook_failed = HookFailedError.__new__(HookFailedError)
    DeploymentError.__init__(hook_failed, "a hook failed", details="exit 1")
    old, app_directory = failing_update(recipe_store, lifecycle, monkeypatch, hook_failed)

    with pytest.raises(HookFailedError):
        update_recipe(DOMAIN, store=recipe_store)

    assert (app_directory / COMPOSE_FILE).read_text() == old.compose
    assert lifecycle.gates == 0


def test_a_docker_failure_while_recreating_restarts_the_previous_version(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose failed halfway: the stack is in an unknown state, so it is brought up from the files."""
    from noust.core.exceptions import DockerError

    old, app_directory = failing_update(
        recipe_store,
        lifecycle,
        monkeypatch,
        DockerError("Failed to update containers", "pull failed"),
    )

    with pytest.raises(DockerError) as failure:
        update_recipe(DOMAIN, store=recipe_store)

    assert (app_directory / COMPOSE_FILE).read_text() == old.compose
    assert lifecycle.gates == 1
    assert "previous version is running again" in failure.value.details


def test_an_update_refused_before_the_deployer_touches_only_the_recipes_own_directory(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another operation, a failed backup: the application's files and containers are not ours to touch."""
    from noust.core.applock import AppBusyError

    old, app_directory = failing_update(
        recipe_store, lifecycle, monkeypatch, AppBusyError(DOMAIN, "update", None)
    )

    with pytest.raises(AppBusyError):
        update_recipe(DOMAIN, store=recipe_store)

    assert (
        app_directory / COMPOSE_FILE
    ).read_text() == "the new version's file, copied by the update"
    assert (recipe_source_dir(APP, recipe_store) / COMPOSE_FILE).read_text() == old.compose
    assert lifecycle.gates == 0


def test_force_deploys_the_catalogues_version_again(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebuild that re-pulls the pinned images."""
    stack(recipe_store, "1.2.0")
    catalogue(monkeypatch, demo=compose_recipe(name="demo", version="1.2.0"))
    lifecycle.env = {"SECRET": "s" * 32, "PORT": "8123"}

    outcome = update_recipe(DOMAIN, force=True, store=recipe_store)

    assert outcome.updated is True and len(lifecycle.updates) == 1


def test_a_recipe_in_the_2_3_format_cannot_be_updated_through_the_catalogue(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It has no version to compare; the ordinary update is the way."""
    recipe_store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path="/x"))
    legacy = {
        "name": "demo",
        "title": "Demo",
        "description": "A demo.",
        "homepage": "https://example.com",
        "app_type": "nodejs",
        "source": {"git": "https://github.com/o/r.git", "ref": "v1.0.0"},
    }
    parsed = parse_recipe("demo", legacy)
    record_install(DOMAIN, parsed, store=recipe_store)
    monkeypatch.setattr(updates_module, "get_recipe", lambda name: parsed)

    with pytest.raises(RecipeError, match=r"2\.3 recipe format"):
        update_recipe(DOMAIN, force=True, store=recipe_store)


def test_the_changelog_entries_are_what_an_update_shows() -> None:
    """The records are plain."""
    assert ChangelogEntry("1.0.0", "x") == ChangelogEntry(version="1.0.0", summary="x")


# ---------------------------------------------------------------------------
# A new version that answers on another address (Typebot's viewer, Logto's console)
# ---------------------------------------------------------------------------

VIEWER = "view.example.com"


def two_addresses(version: str) -> dict[str, Any]:
    """A catalogue document with a second public address."""
    from tests.recipes.test_entrypoints import two_address_recipe

    return two_address_recipe(name="demo", version=version)


def free_ports(monkeypatch: pytest.MonkeyPatch, *taken: int) -> None:
    """Let the entrypoint ports be chosen on a machine where only ``taken`` are busy."""
    from noust.recipes import deploy as deploy_module

    def pick(
        start: int = 3000, end: int = 9000, preferred: int | None = None, exclude: Any = ()
    ) -> int:
        return next(
            port
            for port in range(preferred or start, end)
            if port not in exclude and port not in taken
        )

    monkeypatch.setattr(deploy_module, "find_available_port", pick)


def test_an_update_that_adds_an_address_asks_for_it_and_adds_it_once_the_update_passed(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new required variable stops it first; then the port is chosen and the name added."""
    from noust.deployers import domains

    stack(recipe_store, "1.0.0")
    catalogue(monkeypatch, demo=two_addresses("1.1.0"))
    lifecycle.env = {"SECRET": "kept", "PORT": "8123"}
    free_ports(monkeypatch)
    added: list[tuple[str, str, int | None]] = []
    monkeypatch.setattr(
        domains,
        "add_domain",
        lambda app, name, kind="alias", **kw: added.append((app, name, kw.get("port"))),
    )

    with pytest.raises(RecipeError, match="needs a value for VIEWER_DOMAIN"):
        update_recipe(DOMAIN, store=recipe_store)
    assert lifecycle.updates == [] and added == []

    outcome = update_recipe(DOMAIN, overrides={"VIEWER_DOMAIN": VIEWER}, store=recipe_store)

    written = lifecycle.env_writes[0]
    assert written["VIEWER_DOMAIN"] == VIEWER and written["VIEWER_PORT"] == "3101"
    assert written["VIEWER_URL"] == f"https://{VIEWER}"
    assert added == [(DOMAIN, VIEWER, 3101)]
    assert outcome.updated and outcome.notes == ()


def test_an_update_leaves_an_address_the_application_already_answers_on_alone(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No second certificate order, no site rendered again, no variable rewritten."""
    from noust.deployers import domains

    stack(recipe_store, "1.0.0")
    recipe_store.add_domain(DOMAIN, VIEWER, "alias")
    recipe_store.set_domain_port(DOMAIN, VIEWER, 3101)
    catalogue(monkeypatch, demo=two_addresses("1.1.0"))
    lifecycle.env = {
        "PORT": "8123",
        "VIEWER_DOMAIN": VIEWER,
        "VIEWER_PORT": "3101",
        "VIEWER_URL": f"https://{VIEWER}",
        "SCHEME": "https",
    }
    free_ports(monkeypatch)
    monkeypatch.setattr(
        domains, "add_domain", lambda *a, **k: pytest.fail("the address is already served")
    )

    outcome = update_recipe(DOMAIN, store=recipe_store)

    assert outcome.updated and lifecycle.env_writes == []


def test_an_address_that_cannot_be_added_after_an_update_is_a_note(
    recipe_store: NoustStore, lifecycle: Lifecycle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The update itself passed its gate: the rest is said with the command that repeats it."""
    from noust.core.exceptions import DomainError
    from noust.deployers import domains

    stack(recipe_store, "1.0.0")
    catalogue(monkeypatch, demo=two_addresses("1.1.0"))
    lifecycle.env = {"PORT": "8123"}
    free_ports(monkeypatch)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise DomainError("Another site of the web server already answers on view.example.com")

    monkeypatch.setattr(domains, "add_domain", refuse)

    outcome = update_recipe(DOMAIN, overrides={"VIEWER_DOMAIN": VIEWER}, store=recipe_store)

    assert outcome.updated
    assert any(f"noust domain add {DOMAIN} {VIEWER} --port 3101" in note for note in outcome.notes)
