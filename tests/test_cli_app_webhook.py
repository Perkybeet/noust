# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app webhook show|rotate|disable|deliveries`` (backlog 52).

The commands are clients of :mod:`noust.integrations.webhook`, like the
console's guided setup: what they print is that module's state, and the secret
is printed only where the operator asked for it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.core import webhook_deliveries as wd
from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.runner import set_runner
from noust.core.store import App, NoustStore

DOMAIN = "app.example.com"


@pytest.fixture(autouse=True)
def _real_runner() -> Iterator[None]:
    """--dry-run installs a rehearsing runner process-wide; put the default back."""
    yield
    set_runner(None)


@pytest.fixture(autouse=True)
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    Give the commands an empty configuration and no GitHub App.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        Nothing.
    """
    from noust.integrations.github import service

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    monkeypatch.setattr(service, "status", lambda: service.GitHubStatus(configured=False))
    Config.reset_instance()
    try:
        yield
    finally:
        Config.reset_instance()


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    Collect what the command reports to the operator.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The lines, as they are written.
    """
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Give the commands a store of their own.

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


@pytest.fixture
def app(store: NoustStore) -> App:
    """
    Deploy one application on paper.

    Args:
        store: The store fixture.

    Returns:
        The application, on releases and tracking ``main``.
    """
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/you/app",
            branch="main",
            port=3000,
            layout="releases",
        )
    )


def run(*args: str, input: str | None = None) -> Result:
    """
    Run ``noust app webhook ...``.

    Args:
        *args: Arguments after ``webhook``.
        input: What to pipe to standard input.

    Returns:
        Click's result.
    """
    return CliRunner().invoke(root_cli, ["app", "webhook", *args], input=input)


def expose(monkeypatch: pytest.MonkeyPatch, url: str | None) -> None:
    """
    Say whether ``noust web expose-hooks`` was run.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        url: The public hooks URL, or None.
    """
    monkeypatch.setattr("noust.integrations.hooks_site.public_hooks_url", lambda: url)


# -- the group ------------------------------------------------------------------------


def test_the_group_offers_the_four_commands() -> None:
    result = CliRunner().invoke(root_cli, ["app", "webhook", "--help"])

    assert result.exit_code == 0, result.output
    for name in ("show", "rotate", "disable", "deliveries"):
        assert name in result.output


def test_it_is_a_subgroup_of_app() -> None:
    result = CliRunner().invoke(root_cli, ["app", "--help"])

    assert "webhook" in result.output


def test_an_unknown_application_is_named(store: NoustStore, log: list[str]) -> None:
    for command in ("show", "rotate", "disable", "deliveries"):
        result = run(command, "nothing.example.com", *(["--yes"] if command == "rotate" else []))

        assert isinstance(result.exception, NoustError), command
        assert "Application not found: nothing.example.com" in str(result.exception), command


# -- show -----------------------------------------------------------------------------


def test_show_says_the_webhook_is_off_and_what_to_do(
    app: App, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    expose(monkeypatch, None)

    result = run("show", DOMAIN)

    assert result.exit_code == 0, result.output
    text = "\n".join(log)
    assert "disabled" in text
    assert f"noust app webhook rotate {DOMAIN}" in text
    assert "noust web expose-hooks" in text


def test_show_prints_what_the_forge_needs_and_never_the_secret(
    app: App, store: NoustStore, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.integrations.webhook import mint_secret

    expose(monkeypatch, "https://hooks.example.com/hooks")
    secret = mint_secret(DOMAIN)

    result = run("show", DOMAIN)

    assert result.exit_code == 0, result.output
    text = "\n".join(log)
    assert f"https://hooks.example.com/hooks/deploy/{DOMAIN}" in text
    assert "application/json" in text
    assert "push" in text
    assert "https://github.com/you/app/settings/hooks/new" in text
    assert "main" in text
    assert "waiting" in text
    assert secret not in text
    assert secret not in result.output
    assert "--reveal" in text


def test_show_reveal_prints_the_secret(
    app: App, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.integrations.webhook import mint_secret

    expose(monkeypatch, "https://hooks.example.com/hooks")
    secret = mint_secret(DOMAIN)

    result = run("show", DOMAIN, "--reveal")

    assert result.exit_code == 0, result.output
    assert secret in "\n".join(log)


def test_show_reveal_without_a_secret_says_how_to_create_one(
    app: App, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    expose(monkeypatch, None)

    result = run("show", DOMAIN, "--reveal")

    assert result.exit_code == 0, result.output
    assert "no webhook secret" in "\n".join(log).lower()


def test_show_warns_when_no_branch_is_pinned(
    app: App, store: NoustStore, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.integrations.webhook import mint_secret

    expose(monkeypatch, None)
    row = store.get_app(DOMAIN)
    assert row is not None
    row.branch = None
    store.update_app(row)
    mint_secret(DOMAIN)

    run("show", DOMAIN)

    assert "any branch" in "\n".join(log)


def test_show_warns_about_an_in_place_application(
    app: App, store: NoustStore, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.integrations.webhook import mint_secret

    expose(monkeypatch, None)
    row = store.get_app(DOMAIN)
    assert row is not None
    row.layout = "inplace"
    store.update_app(row)
    mint_secret(DOMAIN)

    run("show", DOMAIN)

    text = "\n".join(log)
    assert "in place" in text
    assert "noust app migrate" in text


def test_show_says_when_the_github_app_already_deploys_it(
    app: App, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from noust.integrations.github import service
    from noust.integrations.webhook import mint_secret

    expose(monkeypatch, None)
    mint_secret(DOMAIN)
    installation = SimpleNamespace(
        installation_id=1, account="you", repository_selection="all", settings_url=None
    )
    monkeypatch.setattr(
        service,
        "status",
        lambda: service.GitHubStatus(
            configured=True, hooks_active=True, installations=[installation]
        ),
    )

    run("show", DOMAIN)

    assert "GitHub App" in "\n".join(log)
    assert "twice" in "\n".join(log)


def test_show_json_is_the_state(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    from noust.integrations.webhook import mint_secret

    expose(monkeypatch, "https://hooks.example.com/hooks")
    secret = mint_secret(DOMAIN)

    result = run("show", DOMAIN, "--json")

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["state"] == "waiting"
    assert body["hooks"]["hook_url"] == f"https://hooks.example.com/hooks/deploy/{DOMAIN}"
    assert body["branch"]["pinned"] is True
    assert secret not in result.output
    assert "secret" not in body


def test_show_json_with_reveal_carries_the_secret(
    app: App, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.integrations.webhook import mint_secret

    expose(monkeypatch, None)
    secret = mint_secret(DOMAIN)

    body = json.loads(run("show", DOMAIN, "--json", "--reveal").output)

    assert body["secret"] == secret


# -- rotate ---------------------------------------------------------------------------


def test_rotate_creates_a_secret_and_prints_it_once_with_the_url(
    app: App, store: NoustStore, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    expose(monkeypatch, "https://hooks.example.com/hooks")

    result = run("rotate", DOMAIN)

    assert result.exit_code == 0, result.output
    secret = store.get_webhook_secret(DOMAIN)
    assert secret
    text = "\n".join(log)
    assert secret in text
    assert f"https://hooks.example.com/hooks/deploy/{DOMAIN}" in text
    assert "application/json" in text


def test_rotate_replacing_a_secret_asks_first_because_the_forge_stops_working(
    app: App, store: NoustStore, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.integrations.webhook import mint_secret

    expose(monkeypatch, None)
    old = mint_secret(DOMAIN)

    declined = run("rotate", DOMAIN, input="n\n")

    assert declined.exit_code != 0
    assert store.get_webhook_secret(DOMAIN) == old

    accepted = run("rotate", DOMAIN, input="y\n")

    assert accepted.exit_code == 0, accepted.output
    assert store.get_webhook_secret(DOMAIN) != old


def test_rotate_yes_does_not_ask(
    app: App, store: NoustStore, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.integrations.webhook import mint_secret

    expose(monkeypatch, None)
    old = mint_secret(DOMAIN)

    result = run("rotate", DOMAIN, "--yes")

    assert result.exit_code == 0, result.output
    assert store.get_webhook_secret(DOMAIN) != old


def test_rotate_dry_run_changes_nothing_and_prints_no_secret(
    app: App, store: NoustStore, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    expose(monkeypatch, None)

    result = run("rotate", DOMAIN, "--dry-run")

    assert result.exit_code == 0, result.output
    assert store.get_webhook_secret(DOMAIN) is None
    assert "Would" in "\n".join(log)


def test_rotate_json_carries_the_secret_and_the_url(
    app: App, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    expose(monkeypatch, "https://hooks.example.com/hooks")

    body = json.loads(run("rotate", DOMAIN, "--json").output)

    assert body["secret"] == store.get_webhook_secret(DOMAIN)
    assert body["hook_url"] == f"https://hooks.example.com/hooks/deploy/{DOMAIN}"


# -- disable --------------------------------------------------------------------------


def test_disable_discards_the_secret(
    app: App, store: NoustStore, log: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.integrations.webhook import mint_secret

    expose(monkeypatch, None)
    mint_secret(DOMAIN)

    result = run("disable", DOMAIN)

    assert result.exit_code == 0, result.output
    assert store.get_webhook_secret(DOMAIN) is None
    assert "disabled" in "\n".join(log).lower()


def test_disable_when_it_is_off_says_so(app: App, log: list[str]) -> None:
    result = run("disable", DOMAIN)

    assert result.exit_code == 0, result.output
    assert "already" in "\n".join(log).lower()


# -- deliveries -----------------------------------------------------------------------


def test_deliveries_lists_what_the_forge_sent_newest_first(app: App, log: list[str]) -> None:
    assert app.id is not None
    wd.record_delivery(app.id, wd.PING, provider="github", event="ping")
    wd.record_delivery(
        app.id, wd.DEPLOY_STARTED, provider="github", event="push", branch="main", job_id="j1"
    )
    wd.record_delivery(app.id, wd.IGNORED_BRANCH, provider="github", event="push", branch="dev")
    for _ in range(4):
        wd.record_delivery(app.id, wd.BAD_SIGNATURE, detail="signature verification failed")

    result = run("deliveries", DOMAIN)

    assert result.exit_code == 0, result.output
    text = "\n".join(log)
    lines = [line for line in text.splitlines() if "bad signature" in line]
    assert lines and "x4" in lines[0]
    assert text.index("bad signature") < text.index("ignored") < text.index("deployed")
    assert "ping" in text


def test_deliveries_with_none_says_nothing_arrived(app: App, log: list[str]) -> None:
    result = run("deliveries", DOMAIN)

    assert result.exit_code == 0, result.output
    assert "No deliveries" in "\n".join(log)


def test_deliveries_limit_and_json(app: App) -> None:
    assert app.id is not None
    for number in range(5):
        wd.record_delivery(app.id, wd.PING, delivery_id=f"d{number}")

    body = json.loads(run("deliveries", DOMAIN, "--limit", "2", "--json").output)

    assert [row["delivery_id"] for row in body["items"]] == ["d4", "d3"]
    assert body["items"][0]["outcome"] == "ping"


def test_deliveries_limit_is_bounded(app: App) -> None:
    assert run("deliveries", DOMAIN, "--limit", "0").exit_code == 2
