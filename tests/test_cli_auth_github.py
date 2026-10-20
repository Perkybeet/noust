# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``noust auth oidc set --kind github``: signing in with GitHub, set up from the root CLI."""

from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("cryptography")

from noust.cli.app import cli as root_cli
from noust.core.accounts import passwords
from noust.core.accounts.oidc import OidcManager
from noust.core.accounts.oidc_client import OidcClient, OidcHttp, set_client
from noust.core.store import NoustStore
from noust.web.auth import STATE_DIR_ENV
from tests.github_login_support import FakeGitHub

SECRET = "gh-secret-value"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return directory


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    hub = FakeGitHub(client_secret=SECRET)
    set_client(OidcClient(OidcHttp(opener=hub.opener)))
    yield hub
    set_client(None)


def _run(*args: str, stdin: str | None = None) -> str:
    result = CliRunner().invoke(root_cli, list(args), input=stdin)
    assert result.exit_code == 0, result.output
    return result.output


def test_github_is_added_with_a_client_id_and_a_secret_from_stdin(
    github: FakeGitHub, store: NoustStore
) -> None:
    output = _run(
        "auth", "oidc", "set", "github", "--kind", "github", "--client-id", github.client_id,
        "--client-secret-stdin", "--label", "GitHub", stdin=SECRET + "\n",
    )  # fmt: skip

    provider = OidcManager().provider("github")
    assert provider is not None and provider.kind == "github"
    assert provider.issuer == "https://github.com"
    assert SECRET not in output
    path = store.db_path.parent / "secrets" / "oidc" / "github"
    assert path.read_text() == SECRET and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "kind" in output and "github" in output


def test_the_provider_is_shown_with_its_kind_as_json(github: FakeGitHub) -> None:
    _run(
        "auth", "oidc", "set", "github", "--kind", "github", "--client-id", github.client_id,
        "--client-secret-stdin", stdin=SECRET + "\n",
    )  # fmt: skip

    shown = json.loads(_run("--json", "auth", "oidc", "show"))

    assert [p["kind"] for p in shown["providers"]] == ["github"]
    assert SECRET not in json.dumps(shown)


def test_an_enterprise_server_is_named_with_the_issuer_option(github: FakeGitHub) -> None:
    _run(
        "auth", "oidc", "set", "ghe", "--kind", "github", "--client-id", "x",
        "--issuer", "https://ghe.example.test", "--client-secret-stdin", stdin=SECRET + "\n",
    )  # fmt: skip

    provider = OidcManager().provider("ghe")
    assert provider is not None and provider.issuer == "https://ghe.example.test"


def test_changing_the_kind_of_a_provider_is_refused(github: FakeGitHub) -> None:
    _run(
        "auth", "oidc", "set", "github", "--kind", "github", "--client-id", github.client_id,
        "--client-secret-stdin", stdin=SECRET + "\n",
    )  # fmt: skip

    result = CliRunner().invoke(root_cli, ["auth", "oidc", "set", "github", "--kind", "oidc"])

    assert result.exit_code != 0
    assert "kind" in (result.output + str(result.exception)).lower()


def test_testing_a_github_provider_reaches_the_api(github: FakeGitHub) -> None:
    _run(
        "auth", "oidc", "set", "github", "--kind", "github", "--client-id", github.client_id,
        "--client-secret-stdin", stdin=SECRET + "\n",
    )  # fmt: skip

    output = _run("auth", "oidc", "test", "github")

    assert "api.github.com" in output
