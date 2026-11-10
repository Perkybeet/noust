# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Token presets: "only deploy" for a pipeline, from one list (item 163).

A token's scope (read, deploy, admin) is coarse: a deploy token also reads the databases, the
server and the settings. Narrowing it to what a job needs was possible by naming permissions,
and nobody knows the thirty permissions by name, so the common ones are named once
(``noust.core.token_presets``) and the command line, the API and the console all take
the name: the console does not carry a copy of the list, it asks for it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.exceptions import SecurityError
from noust.core.token_presets import TOKEN_PRESETS, preset_permissions
from noust.web.auth import CSRF_HEADER_NAME, STATE_DIR_ENV
from noust.web.permissions import ALL_PERMISSIONS, Permission
from noust.web.server import get_token_manager
from tests.support.web_accounts import (  # noqa: F401
    PASSWORD,
    accounts,
    build,
    cheap_hashes,
    make_account,
    master_sign_in,
    sign_in,
    store,
)


class TestTheList:
    def test_every_preset_is_made_of_permissions_that_exist_and_holds_self(self) -> None:
        assert TOKEN_PRESETS
        for preset in TOKEN_PRESETS:
            assert preset.permissions <= ALL_PERMISSIONS, preset.id
            assert Permission.SELF in preset.permissions, preset.id

    def test_no_preset_reaches_the_machine_or_governs_anything(self) -> None:
        # A preset is for a job: nothing that is the server's, or who may do what.
        never = {
            Permission.SERVER_MANAGE,
            Permission.SERVER_HOST_ACCESS,
            Permission.SERVER_TERMINAL,
            Permission.ROOT_EQUIVALENT,
            Permission.SECRETS_REVEAL,
            Permission.ACCOUNTS_MANAGE,
            Permission.SECURITY_MANAGE,
            Permission.AUDIT_MANAGE,
            Permission.SETTINGS_MANAGE,
        }
        for preset in TOKEN_PRESETS:
            assert not preset.permissions & never, preset.id

    def test_ids_are_unique_and_stable_words(self) -> None:
        ids = [preset.id for preset in TOKEN_PRESETS]

        assert len(ids) == len(set(ids))
        assert "deploy_only" in ids
        assert all(word.replace("_", "").isalpha() and word.islower() for word in ids)

    def test_deploy_only_sees_the_application_and_deploys_it_and_nothing_else(self) -> None:
        assert preset_permissions("deploy_only") == frozenset(
            {Permission.SELF, Permission.APPS_READ, Permission.APPS_DEPLOY}
        )

    def test_a_name_nobody_made_is_refused_with_the_ones_there_are(self) -> None:
        with pytest.raises(SecurityError, match="deploy_only"):
            preset_permissions("do_everything")


class TestInTheManager:
    def test_a_preset_is_the_permissions_the_token_holds(self, sandbox: Path) -> None:
        build(sandbox)
        make_account("maria", "operator")

        issued = get_token_manager().create_api_token(
            "ci", "read", owner=accounts().find("maria"), preset="deploy_only"
        )

        assert set(issued["permissions"]) == set(preset_permissions("deploy_only"))

    def test_it_never_holds_more_than_its_owner_even_by_preset(self, sandbox: Path) -> None:
        build(sandbox)
        make_account("lucia", "viewer")

        with pytest.raises(SecurityError, match="more than its owner"):
            get_token_manager().create_api_token(
                "ci", "read", owner=accounts().find("lucia"), preset="deploy_only"
            )

    def test_a_preset_and_a_list_of_permissions_are_two_answers_to_one_question(
        self, sandbox: Path
    ) -> None:
        build(sandbox)
        make_account("maria", "operator")

        with pytest.raises(SecurityError, match="not both"):
            get_token_manager().create_api_token(
                "ci",
                "read",
                owner=accounts().find("maria"),
                preset="deploy_only",
                permissions=["apps.read"],
            )

    def test_a_token_nobody_owns_is_narrowed_too_and_no_further_than_who_issues_it(
        self, sandbox: Path
    ) -> None:
        build(sandbox)
        manager = get_token_manager()

        narrow = manager.create_api_token("ci", "admin", preset="deploy_only")

        # Until 4.3 the narrowing of an ownerless token was silently dropped, which made a
        # "deploy only" token a token for everything.
        assert set(narrow["permissions"]) == set(preset_permissions("deploy_only"))
        with pytest.raises(SecurityError, match=r"more than its owner|cannot hold more"):
            manager.create_api_token(
                "too-much",
                "read",
                preset="deploy_only",
                issuer_permissions=frozenset({Permission.SELF, Permission.APPS_READ}),
            )

    def test_the_token_does_only_that(self, sandbox: Path) -> None:
        client = build(sandbox)
        make_account("maria", "admin")
        issued = get_token_manager().create_api_token(
            "ci", "admin", owner=accounts().find("maria"), preset="deploy_only"
        )
        bearer = {"Authorization": f"Bearer {issued['token']}"}

        # It sees applications and may deploy them; it reads nothing of the server or the databases.
        assert client.get("/api/apps", headers=bearer).status_code == 200
        assert client.get("/api/databases/databases", headers=bearer).status_code == 403
        assert client.get("/api/server/identity", headers=bearer).status_code == 403


class TestInTheApi:
    def elevated(self, client: TestClient) -> str:
        codes = make_account("maria", "admin")
        csrf = str(sign_in(client, "maria", codes[0])["csrf_token"])
        confirmed = client.post(
            "/api/auth/elevate",
            json={"password": PASSWORD, "code": codes[1]},
            headers={CSRF_HEADER_NAME: csrf},
        )
        assert confirmed.status_code == 200, confirmed.text
        return csrf

    def test_the_roles_answer_serves_the_list_the_console_draws_its_choice_from(
        self, sandbox: Path
    ) -> None:
        client = build(sandbox)
        self.elevated(client)

        body = client.get("/api/auth/roles").json()

        served = {item["id"]: set(item["permissions"]) for item in body["token_presets"]}
        assert served == {preset.id: set(preset.permissions) for preset in TOKEN_PRESETS}
        # What a token bound to a team can ever hold, which the console shows beside the choice.
        assert "apps.deploy" in body["tenant_permissions"]
        assert "server.manage" not in body["tenant_permissions"]

    def test_a_token_is_issued_by_preset(self, sandbox: Path) -> None:
        client = build(sandbox)
        csrf = self.elevated(client)

        response = client.post(
            "/api/auth/tokens",
            json={"name": "ci", "scope": "read", "preset": "deploy_only"},
            headers={CSRF_HEADER_NAME: csrf},
        )

        assert response.status_code == 201, response.text
        assert set(response.json()["permissions"]) == set(preset_permissions("deploy_only"))

    def test_a_preset_nobody_made_is_a_400_that_says_which_there_are(self, sandbox: Path) -> None:
        client = build(sandbox)
        csrf = self.elevated(client)

        response = client.post(
            "/api/auth/tokens",
            json={"name": "ci", "scope": "read", "preset": "do_everything"},
            headers={CSRF_HEADER_NAME: csrf},
        )

        assert response.status_code == 400
        assert "deploy_only" in response.json()["hint"]

    def test_the_master_token_issues_one_narrowed_too(self, sandbox: Path) -> None:
        client = build(sandbox)
        token, csrf = master_sign_in(client)
        confirmed = client.post(
            "/api/auth/elevate", json={"token": token}, headers={CSRF_HEADER_NAME: csrf}
        )
        assert confirmed.status_code == 200, confirmed.text

        response = client.post(
            "/api/auth/tokens",
            json={"name": "ci", "scope": "admin", "preset": "deploy_only"},
            headers={CSRF_HEADER_NAME: csrf},
        )

        assert response.status_code == 201, response.text
        assert set(response.json()["permissions"]) == set(preset_permissions("deploy_only"))


class TestOnTheCommandLine:
    @pytest.fixture
    def state_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        directory = tmp_path / "state"
        directory.mkdir()
        monkeypatch.setenv(STATE_DIR_ENV, str(directory))
        return directory

    def test_a_token_is_issued_by_preset(self, cli_runner: CliRunner, state_dir: Path) -> None:
        result = cli_runner.invoke(
            root_cli, ["token", "create", "ci", "--scope", "read", "--preset", "deploy_only"]
        )

        assert result.exit_code == 0, result.output
        assert "Token: noust_" in result.output
        assert "apps.deploy" in result.output

    def test_the_help_names_the_presets_there_are(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(root_cli, ["token", "create", "--help"])

        assert result.exit_code == 0
        for preset in TOKEN_PRESETS:
            assert preset.id in result.output

    def test_a_preset_and_a_permission_together_are_refused(
        self, cli_runner: CliRunner, state_dir: Path
    ) -> None:
        result = cli_runner.invoke(
            root_cli,
            ["token", "create", "ci", "--preset", "deploy_only", "--permission", "apps.read"],
        )

        assert result.exit_code != 0
        assert "not both" in result.output

    def test_a_preset_nobody_made_is_refused_by_the_command_before_it_runs(
        self, cli_runner: CliRunner, state_dir: Path
    ) -> None:
        result = cli_runner.invoke(root_cli, ["token", "create", "ci", "--preset", "everything"])

        assert result.exit_code != 0
        assert "deploy_only" in result.output

    def test_a_dry_run_says_nothing_was_saved_and_what_it_would_hold(
        self, cli_runner: CliRunner, state_dir: Path
    ) -> None:
        result = cli_runner.invoke(
            root_cli, ["--dry-run", "token", "create", "ci", "--preset", "deploy_only"]
        )

        assert result.exit_code == 0, result.output
        assert "not saved" in result.output.lower() or "would" in result.output.lower()


def test_the_cli_is_given_every_preset_by_the_one_module(cli_runner: CliRunner) -> None:
    # A list the command line keeps for itself is the second copy this item is about.
    from noust.cli.commands import token

    source: Any = open(token.__file__, encoding="utf-8").read()
    assert "deploy_only" not in source
