# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The state the guided webhook setup is built on (backlog 52, spec section 9).

One function answers "what does the operator need to see about this webhook":
whether the public hooks URL exists, the exact payload URL, content type and
events, whether the secret is set, which branch deploys (and the warning when
none is pinned, so any push deploys), whether an in-place application is
auto-deployed, whether the GitHub App already covers the repository, and what
the forge has been sending. The console and ``noust app webhook show`` are both
clients of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core import webhook_deliveries as wd
from noust.core.config import Config
from noust.core.exceptions import DeploymentError
from noust.core.store import App, NoustStore
from noust.integrations import webhook

DOMAIN = "app.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Give the module a store of its own.

    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """
    Point the configuration at an empty sandbox.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The (absent) configuration file.
    """
    path = tmp_path / "etc" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@pytest.fixture(autouse=True)
def no_github_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Start every test on a server without a GitHub App.

    Args:
        monkeypatch: Patching helper, scoped to the test.
    """
    from noust.integrations.github import service

    monkeypatch.setattr(service, "status", lambda: service.GitHubStatus(configured=False))


def make_app(store: NoustStore, **fields: object) -> App:
    """
    Deploy an application on paper.

    Args:
        store: The store.
        **fields: Columns to set.

    Returns:
        The stored application.
    """
    values: dict[str, object] = {
        "domain": DOMAIN,
        "app_type": "nodejs",
        "source": "https://github.com/you/app",
        "branch": "main",
        "port": 3000,
        "layout": "releases",
    }
    values.update(fields)
    return store.create_app(App(**values))  # type: ignore[arg-type]


def expose(
    monkeypatch: pytest.MonkeyPatch, url: str | None = "https://hooks.example.com/hooks"
) -> None:
    """
    Say whether ``noust web expose-hooks`` was run.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        url: The public hooks URL, or None for not exposed.
    """
    monkeypatch.setattr("noust.integrations.hooks_site.public_hooks_url", lambda: url)


# -- the secret -----------------------------------------------------------------------


def test_minting_stores_a_fresh_secret_each_time(store: NoustStore) -> None:
    make_app(store)

    first = webhook.mint_secret(DOMAIN)
    second = webhook.mint_secret(DOMAIN)

    assert first != second
    assert len(second) >= 40
    assert store.get_webhook_secret(DOMAIN) == second


def test_minting_for_an_unknown_application_says_so(store: NoustStore) -> None:
    with pytest.raises(DeploymentError, match="not found"):
        webhook.mint_secret("nothing.example.com")


def test_disabling_forgets_the_secret(store: NoustStore) -> None:
    make_app(store)
    webhook.mint_secret(DOMAIN)

    webhook.disable_secret(DOMAIN)

    assert store.get_webhook_secret(DOMAIN) is None


def test_revealing_returns_the_stored_secret(store: NoustStore) -> None:
    make_app(store)
    minted = webhook.mint_secret(DOMAIN)

    assert webhook.reveal_secret(DOMAIN) == minted


def test_revealing_without_a_secret_says_how_to_create_one(store: NoustStore) -> None:
    make_app(store)

    with pytest.raises(DeploymentError) as raised:
        webhook.reveal_secret(DOMAIN)

    assert "no webhook secret" in raised.value.message.lower()
    assert "rotate" in str(raised.value.details)


# -- the state ------------------------------------------------------------------------


def test_a_disabled_webhook_says_what_is_missing(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    expose(monkeypatch, None)
    app = make_app(store)

    status = webhook.status(app)

    assert status.state == "disabled"
    assert status.enabled is False
    assert status.hooks.exposed is False
    assert status.hooks.base_url is None
    assert status.hooks.hook_url is None
    assert status.hooks.content_type == "application/json"
    assert status.hooks.events == ["push"]


def test_the_exact_payload_url_is_the_public_one_once_exposed(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    expose(monkeypatch, "https://hooks.example.com/hooks")
    app = make_app(store)

    status = webhook.status(app)

    assert status.hooks.exposed is True
    assert status.hooks.hook_url == f"https://hooks.example.com/hooks/deploy/{DOMAIN}"
    assert status.hooks.hook_url_public is True


def test_without_a_public_url_the_callers_own_address_is_offered_as_not_public(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    expose(monkeypatch, None)
    app = make_app(store)

    status = webhook.status(app, fallback_base="https://localhost:8080/hooks")

    assert status.hooks.exposed is False
    assert status.hooks.hook_url == f"https://localhost:8080/hooks/deploy/{DOMAIN}"
    assert status.hooks.hook_url_public is False


def test_a_secret_with_no_deliveries_is_waiting_for_the_first_one(store: NoustStore) -> None:
    make_app(store)
    webhook.mint_secret(DOMAIN)

    status = webhook.status(store.get_app(DOMAIN))

    assert status.enabled is True
    assert status.state == "waiting"
    assert status.deliveries.total == 0
    assert status.deliveries.last is None


def test_a_verified_delivery_makes_it_connected(store: NoustStore) -> None:
    app = make_app(store)
    webhook.mint_secret(DOMAIN)
    wd.record_delivery(app.id, wd.PING, provider="github", event="ping")

    status = webhook.status(store.get_app(DOMAIN))

    assert status.state == "connected"
    assert status.deliveries.last is not None
    assert status.deliveries.last.outcome == "ping"
    assert status.deliveries.last_verified_at is not None
    assert status.deliveries.last_push_at is None


def test_the_last_push_that_deployed_is_reported(store: NoustStore) -> None:
    app = make_app(store)
    webhook.mint_secret(DOMAIN)
    wd.record_delivery(app.id, wd.DEPLOY_STARTED, provider="github", branch="main", job_id="j1")
    wd.record_delivery(app.id, wd.IGNORED_BRANCH, provider="github", branch="dev")

    status = webhook.status(store.get_app(DOMAIN))

    assert status.state == "connected"
    assert status.deliveries.last_push_at is not None


def test_wrong_signatures_after_the_last_good_delivery_are_a_problem(store: NoustStore) -> None:
    app = make_app(store)
    webhook.mint_secret(DOMAIN)
    wd.record_delivery(app.id, wd.PING, provider="github")
    for _ in range(3):
        wd.record_delivery(app.id, wd.BAD_SIGNATURE)

    status = webhook.status(store.get_app(DOMAIN))

    assert status.state == "problem"
    assert status.deliveries.refused_since_last_verified == 3


def test_a_good_delivery_after_the_refusals_clears_the_problem(store: NoustStore) -> None:
    app = make_app(store)
    webhook.mint_secret(DOMAIN)
    wd.record_delivery(app.id, wd.BAD_SIGNATURE)
    wd.record_delivery(app.id, wd.PING, provider="github")

    assert webhook.status(store.get_app(DOMAIN)).state == "connected"


# -- the branch -----------------------------------------------------------------------


def test_a_pinned_branch_is_the_only_one_that_deploys(store: NoustStore) -> None:
    make_app(store, branch="release")

    status = webhook.status(store.get_app(DOMAIN))

    assert (status.branch.tracked, status.branch.pinned, status.branch.any_push_deploys) == (
        "release",
        True,
        False,
    )


def test_without_a_pinned_branch_any_push_deploys(store: NoustStore) -> None:
    make_app(store, branch=None)

    status = webhook.status(store.get_app(DOMAIN))

    assert (status.branch.tracked, status.branch.pinned, status.branch.any_push_deploys) == (
        None,
        False,
        True,
    )


# -- the layout -----------------------------------------------------------------------


def test_auto_deploy_on_an_in_place_application_carries_the_warning(store: NoustStore) -> None:
    make_app(store, layout="inplace")
    webhook.mint_secret(DOMAIN)

    status = webhook.status(store.get_app(DOMAIN))

    assert status.layout == "inplace"
    assert status.inplace_warning is True


def test_no_warning_on_releases_or_without_a_webhook(store: NoustStore) -> None:
    make_app(store, layout="releases")
    webhook.mint_secret(DOMAIN)
    assert webhook.status(store.get_app(DOMAIN)).inplace_warning is False

    store.delete_app(DOMAIN)
    make_app(store, layout="inplace")
    assert webhook.status(store.get_app(DOMAIN)).inplace_warning is False


# -- the forge ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "forge", "host", "repository", "settings_url"),
    [
        (
            "https://github.com/you/app",
            "github",
            "github.com",
            "you/app",
            "https://github.com/you/app/settings/hooks/new",
        ),
        (
            "git@github.com:you/app.git",
            "github",
            "github.com",
            "you/app",
            "https://github.com/you/app/settings/hooks/new",
        ),
        (
            "https://ghp_secrettoken@github.com/you/app.git#dev",
            "github",
            "github.com",
            "you/app",
            "https://github.com/you/app/settings/hooks/new",
        ),
        (
            "https://gitlab.com/group/sub/app.git",
            "gitlab",
            "gitlab.com",
            "group/sub/app",
            "https://gitlab.com/group/sub/app/-/hooks",
        ),
        (
            "https://codeberg.org/you/app",
            "gitea",
            "codeberg.org",
            "you/app",
            "https://codeberg.org/you/app/settings/hooks/gitea/new",
        ),
        ("https://git.example.com/team/app.git", None, "git.example.com", "team/app", None),
        ("/srv/checkout", None, None, None, None),
        ("", None, None, None, None),
    ],
)
def test_the_forge_side_names_the_repository_and_where_its_hooks_are(
    store: NoustStore,
    source: str,
    forge: str | None,
    host: str | None,
    repository: str | None,
    settings_url: str | None,
) -> None:
    make_app(store, source=source)

    status = webhook.status(store.get_app(DOMAIN))

    assert (
        status.forge.forge,
        status.forge.host,
        status.forge.repository,
        status.forge.settings_url,
    ) == (forge, host, repository, settings_url)


def test_a_token_in_the_source_never_reaches_the_state(store: NoustStore) -> None:
    make_app(store, source="https://ghp_secrettoken@github.com/you/app.git")
    webhook.mint_secret(DOMAIN)

    status = webhook.status(store.get_app(DOMAIN))

    assert "ghp_secrettoken" not in repr(status.to_dict())


# -- the GitHub App -------------------------------------------------------------------


def github_status(*, active: bool = True, installations: list[object] | None = None) -> object:
    """
    Build what the GitHub integration's status reports.

    Args:
        active: Whether the App's webhook points at this server's hooks URL.
        installations: The accounts the App is installed on.

    Returns:
        A status object with the fields the state reads.
    """
    from noust.integrations.github import service

    return service.GitHubStatus(
        configured=True,
        slug="noust-app",
        hooks_active=active,
        installations=installations or [],
    )


def installation(installation_id: int, account: str, selection: str = "all") -> SimpleNamespace:
    """
    Build an installation as the status lists it.

    Args:
        installation_id: GitHub's id.
        account: The account.
        selection: ``all`` or ``selected``.

    Returns:
        The installation.
    """
    return SimpleNamespace(
        installation_id=installation_id,
        account=account,
        repository_selection=selection,
        settings_url=f"https://github.com/settings/installations/{installation_id}",
    )


def with_github_app(monkeypatch: pytest.MonkeyPatch, status: object) -> None:
    """
    Install a GitHub integration status for the test.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        status: What ``service.status()`` answers.
    """
    from noust.integrations.github import service

    monkeypatch.setattr(service, "status", lambda: status)


def test_without_an_app_nothing_covers_the_repository(store: NoustStore) -> None:
    make_app(store)

    covers = webhook.status(store.get_app(DOMAIN)).github_app

    assert (covers.configured, covers.covers_repository) == (False, False)


def test_an_app_installed_on_all_of_the_owners_repositories_covers_it(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_app(store)
    with_github_app(monkeypatch, github_status(installations=[installation(7, "You")]))

    covers = webhook.status(store.get_app(DOMAIN)).github_app

    assert covers.configured is True
    assert covers.covers_repository is True
    assert covers.account == "You"
    assert covers.repository_selection == "all"
    assert covers.settings_url == "https://github.com/settings/installations/7"


def test_an_app_on_selected_repositories_covers_only_a_repository_it_is_linked_to(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_app(store)
    with_github_app(monkeypatch, github_status(installations=[installation(7, "you", "selected")]))
    unlinked = webhook.status(store.get_app(DOMAIN)).github_app
    assert unlinked.covers_repository is False
    assert unlinked.repository_selection == "selected"

    store.set_github_installation(DOMAIN, 7)
    linked = webhook.status(store.get_app(DOMAIN)).github_app
    assert linked.covers_repository is True


def test_an_app_whose_webhook_is_not_switched_on_covers_nothing(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_app(store)
    with_github_app(
        monkeypatch, github_status(active=False, installations=[installation(7, "you")])
    )

    covers = webhook.status(store.get_app(DOMAIN)).github_app

    assert (covers.configured, covers.hooks_active, covers.covers_repository) == (
        True,
        False,
        False,
    )


def test_another_owners_repository_is_not_covered(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_app(store, source="https://github.com/someone-else/app")
    with_github_app(monkeypatch, github_status(installations=[installation(7, "you")]))

    assert webhook.status(store.get_app(DOMAIN)).github_app.covers_repository is False


def test_a_source_that_is_not_on_github_is_not_covered(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_app(store, source="https://gitlab.com/you/app")
    with_github_app(monkeypatch, github_status(installations=[installation(7, "you")]))

    assert webhook.status(store.get_app(DOMAIN)).github_app.covers_repository is False


def test_an_integration_that_cannot_answer_does_not_hide_the_rest(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.exceptions import IntegrationError
    from noust.integrations.github import service

    def broken() -> object:
        raise IntegrationError("GitHub is unreachable")

    monkeypatch.setattr(service, "status", broken)
    make_app(store)
    webhook.mint_secret(DOMAIN)

    status = webhook.status(store.get_app(DOMAIN))

    assert status.state == "waiting"
    assert status.github_app.configured is False


# -- the shape ------------------------------------------------------------------------


def test_the_state_serialises_to_plain_data(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    expose(monkeypatch)
    app = make_app(store)
    webhook.mint_secret(DOMAIN)
    wd.record_delivery(app.id, wd.PING, provider="github", event="ping")

    payload = webhook.status(store.get_app(DOMAIN)).to_dict()

    json.dumps(payload)
    assert set(payload) == {
        "domain",
        "enabled",
        "state",
        "layout",
        "inplace_warning",
        "hooks",
        "branch",
        "forge",
        "github_app",
        "deliveries",
    }
    assert payload["deliveries"]["last"]["outcome"] == "ping"
    assert store.get_webhook_secret(DOMAIN) not in json.dumps(payload)
