"""
The ways an operator reaches the Coolify and Dokploy importers (checklist A-17).

``noust import --from coolify --url ...`` and ``POST /api/import/*`` are clients
of ``noust.deployers.importers``; what is tested here is what each adds: where
the token may come from (a file or the environment, never an argument), the
listing and the choice of an application, the import it hands to, and for the
API the permission, the scope and the sudo mode of each route.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.deployers.importers import coolify, dokploy
from tests.test_importers_remote import (
    TOKEN,
    coolify_server,
    dokploy_server,
)


@pytest.fixture(autouse=True)
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def servers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Point both importers at fake servers, and record what token they were given."""
    seen: dict[str, Any] = {"tokens": []}
    fakes = {"coolify": coolify_server(), "dokploy": dokploy_server()}
    for name, module in (("coolify", coolify), ("dokploy", dokploy)):
        real = module.connect

        def connect(
            url: str, token: str, *, _real: Any = real, _fake: Any = fakes[name], **_k: Any
        ):
            seen["tokens"].append(token)
            return _real(url, token, opener=_fake)

        monkeypatch.setattr(module, "connect", connect)
    seen["fakes"] = fakes
    return seen


@pytest.fixture
def cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from noust.core.config import Config
    from noust.core.runner import set_runner

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()
    set_runner(None)


def invoke(*args: str, env: dict[str, str] | None = None) -> Any:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli

    return CliRunner().invoke(root_cli, list(args), env=env)


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text(f"{TOKEN}\nanother line\n")
    return path


class TestTheCommand:
    def test_without_an_application_it_lists_them(
        self, servers: dict, cli_config: Any, token_file: Path
    ) -> None:
        result = invoke(
            "import",
            "--from",
            "coolify",
            "--url",
            "https://c.example.com",
            "--token-file",
            str(token_file),
        )

        assert result.exit_code == 0, result.output
        assert "shop" in result.output and "abc123" in result.output
        assert "shop.example.com" in result.output
        assert servers["tokens"] == [TOKEN]
        assert TOKEN not in result.output

    def test_as_json(self, servers: dict, cli_config: Any, token_file: Path) -> None:
        result = invoke(
            "import",
            "--from",
            "dokploy",
            "--url",
            "https://d.example.com",
            "--token-file",
            str(token_file),
            "--json",
        )

        assert result.exit_code == 0, result.output
        (item,) = json.loads(result.output)
        assert item["ref"] == "app_1" and item["name"] == "Shop"

    def test_one_application_is_read_by_name_or_id(
        self, servers: dict, cli_config: Any, token_file: Path
    ) -> None:
        for name in ("shop", "SHOP", "abc123"):
            result = invoke(
                "import",
                "--from",
                "coolify",
                "--url",
                "https://c.example.com",
                "--token-file",
                str(token_file),
                "--app",
                name,
            )
            assert result.exit_code == 0, result.output
            assert "github:acme/shop" in result.output
            assert "Watch paths" in result.output
            assert "cron nightly" in result.output

    def test_an_application_the_server_does_not_have(
        self, servers: dict, cli_config: Any, token_file: Path
    ) -> None:
        result = invoke(
            "import",
            "--from",
            "coolify",
            "--url",
            "https://c.example.com",
            "--token-file",
            str(token_file),
            "--app",
            "nope",
        )
        assert result.exit_code != 0
        assert result.exception.field == "app"

    def test_the_token_may_come_from_the_environment(self, servers: dict, cli_config: Any) -> None:
        result = invoke(
            "import",
            "--from",
            "coolify",
            "--url",
            "https://c.example.com",
            env={"NOUST_IMPORT_TOKEN": TOKEN},
        )
        assert result.exit_code == 0, result.output
        assert servers["tokens"] == [TOKEN]

    def test_a_token_is_never_an_argument(self, servers: dict, cli_config: Any) -> None:
        result = invoke(
            "import", "--from", "coolify", "--url", "https://c.example.com", "--token", TOKEN
        )
        assert result.exit_code != 0
        assert "No such option" in result.output

    def test_without_a_token_it_says_where_to_put_one(
        self, servers: dict, cli_config: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOUST_IMPORT_TOKEN", raising=False)
        result = invoke("import", "--from", "coolify", "--url", "https://c.example.com")
        assert result.exit_code != 0
        assert "--token-file" in result.exception.details

    def test_the_address_is_needed(self, servers: dict, cli_config: Any, token_file: Path) -> None:
        result = invoke("import", "--from", "coolify", "--token-file", str(token_file))
        assert result.exit_code != 0 and "--url is required" in result.output

    def test_the_remote_options_are_for_the_remote_platforms(
        self, cli_config: Any, token_file: Path, tmp_path: Path
    ) -> None:
        result = invoke(
            "import", "--from", "vercel", "--url", "https://x.example.com", str(tmp_path)
        )
        assert result.exit_code != 0 and "for coolify and dokploy" in result.output

    def test_a_dry_run_deploys_nothing_and_shows_the_plan(
        self, servers: dict, cli_config: Any, token_file: Path, store: NoustStore
    ) -> None:
        result = invoke(
            "--dry-run",
            "import",
            "--from",
            "coolify",
            "--url",
            "https://c.example.com",
            "--token-file",
            str(token_file),
            "--app",
            "shop",
            "--deploy",
            "shop.example.com",
            "--env",
            "STRIPE_SECRET=sk",
        )

        assert result.exit_code == 0, result.output
        assert "Rehearsal" in result.output
        assert store.get_app("shop.example.com") is None

    def test_something_noust_builds_from_a_dockerfile_is_not_deployed_from_a_proposal(
        self, servers: dict, cli_config: Any, token_file: Path
    ) -> None:
        servers["fakes"]["coolify"].answers["/api/v1/applications/abc123"] = {
            **servers["fakes"]["coolify"].answers["/api/v1/applications/abc123"],
            "build_pack": "dockerfile",
        }

        result = invoke(
            "import",
            "--from",
            "coolify",
            "--url",
            "https://c.example.com",
            "--token-file",
            str(token_file),
            "--app",
            "shop",
            "--deploy",
            "shop.example.com",
        )

        assert result.exit_code != 0
        assert "--type container" in result.output


def api_client() -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from noust.web.api import import_remote
    from noust.web.api.deps import install_error_handlers, require_elevated

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(import_remote.router, prefix="/api/import")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[require_elevated] = lambda: session
    return TestClient(app)


class TestTheApi:
    def test_both_routes_are_declared_and_the_instances(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_import_remote import ROUTES
        from noust.web.permissions.scopes import INSTANCE, rule_for

        for route in (("POST", "/api/import/applications"), ("POST", "/api/import/proposal")):
            assert ROUTES[route] == Permission.APPS_MANAGE
            assert rule_for(*route).scoping == INSTANCE

    def test_it_lists(self, servers: dict) -> None:
        response = api_client().post(
            "/api/import/applications",
            json={"platform": "coolify", "url": "https://c.example.com", "token": TOKEN},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["platform"] == "coolify"
        assert body["applications"][0]["name"] == "shop"
        assert body["applications"][0]["domains"] == ["shop.example.com", "www.shop.example.com"]
        assert TOKEN not in response.text

    def test_it_proposes_with_the_document_the_import_review_takes(self, servers: dict) -> None:
        response = api_client().post(
            "/api/import/proposal",
            json={
                "platform": "coolify",
                "url": "https://c.example.com",
                "token": TOKEN,
                "ref": "abc123",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["deployable"] is True
        assert body["proposal"]["source"] == "github:acme/shop"
        assert body["proposal"]["watch_paths"] == ["apps/web/**", "!apps/web/docs/**"]
        assert body["document"]["format"] == "wasm-app"
        assert body["document"]["app"]["domain"] == "shop.example.com"
        assert body["document"]["app"]["build"].startswith("build:")

    def test_an_image_application_is_not_deployable_from_a_proposal(self, servers: dict) -> None:
        servers["fakes"]["dokploy"].answers["/api/application.one?applicationId=app_1"] = {
            "applicationId": "app_1",
            "name": "img",
            "sourceType": "docker",
            "dockerImage": "nginx:1.27",
            "domains": [{"host": "img.example.com"}],
        }
        response = api_client().post(
            "/api/import/proposal",
            json={
                "platform": "dokploy",
                "url": "https://d.example.com",
                "token": TOKEN,
                "ref": "app_1",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["deployable"] is False
        assert response.json()["proposal"]["equivalent"].startswith(
            "noust create -d DOMAIN --image"
        )

    def test_with_no_domain_there_it_asks_for_one(self, servers: dict) -> None:
        servers["fakes"]["coolify"].answers["/api/v1/applications/abc123"] = {
            **servers["fakes"]["coolify"].answers["/api/v1/applications/abc123"],
            "fqdn": "",
        }
        response = api_client().post(
            "/api/import/proposal",
            json={
                "platform": "coolify",
                "url": "https://c.example.com",
                "token": TOKEN,
                "ref": "abc123",
            },
        )
        assert response.status_code == 400
        chosen = api_client().post(
            "/api/import/proposal",
            json={
                "platform": "coolify",
                "url": "https://c.example.com",
                "token": TOKEN,
                "ref": "abc123",
                "domain": "new.example.com",
            },
        )
        assert chosen.status_code == 200
        assert chosen.json()["document"]["app"]["domain"] == "new.example.com"

    def test_a_platform_that_is_not_one(self, servers: dict) -> None:
        response = api_client().post(
            "/api/import/applications",
            json={"platform": "heroku", "url": "https://x.example.com", "token": TOKEN},
        )
        assert response.status_code == 400

    def test_a_bad_address_is_a_400(self) -> None:
        response = api_client().post(
            "/api/import/applications",
            json={"platform": "coolify", "url": "http://coolify.example.com", "token": TOKEN},
        )
        assert response.status_code == 400 and "plain http" in response.text


class TestThereIsOneImplementation:
    """Rule 3: the API and the command read another platform's server through the same code."""

    SURFACES = ("src/noust/web/api/import_remote.py", "src/noust/cli/commands/importer.py")
    REPOSITORY = Path(__file__).resolve().parent.parent

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_surface_opens_no_connection_of_its_own(self, surface: str) -> None:
        source = (self.REPOSITORY / surface).read_text(encoding="utf-8")
        for module in ("urllib", "http.client", "requests", "httpx", "socket"):
            assert f"import {module}" not in source and f"from {module}" not in source, (
                surface,
                module,
            )

    @pytest.mark.parametrize("surface", SURFACES)
    def test_a_surface_reaches_a_platform_only_through_the_one_registry(self, surface: str) -> None:
        source = (self.REPOSITORY / surface).read_text(encoding="utf-8")
        assert "REMOTE_PLATFORMS" in source and ".connect(" in source, surface
        # Neither names a platform's module: adding one is a line in the registry, not in a surface.
        assert "importers.coolify" not in source and "importers.dokploy" not in source, surface

    def test_the_client_and_each_platform_are_defined_once(self) -> None:
        importers = self.REPOSITORY / "src/noust/deployers/importers"
        clients = [
            path.name
            for path in importers.glob("*.py")
            if "class RemoteClient" in path.read_text(encoding="utf-8")
        ]
        assert clients == ["remote.py"]
        for platform in ("coolify", "dokploy"):
            listings = [
                path.relative_to(self.REPOSITORY).as_posix()
                for path in (self.REPOSITORY / "src/noust").rglob("*.py")
                if "def list_applications" in path.read_text(encoding="utf-8")
                and path.stem == platform
            ]
            assert listings == [f"src/noust/deployers/importers/{platform}.py"]
