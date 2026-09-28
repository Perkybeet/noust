# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``wasm app export``, ``wasm app import`` and ``wasm import --from``.

The document and the import are :mod:`wasm.deployers.app_export`'s
(``tests/test_app_export.py``) and the reading is the importers'
(``tests/test_importers.py``). Pinned here: what reaches ``wasm create``'s
own deploy function, that a rehearsal deploys nothing, that a file with
secrets is written 0600, and the refusals.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from wasm.cli.app import cli as root_cli
from wasm.cli.commands import app as app_module
from wasm.core.exceptions import ValidationError
from wasm.core.logger import Logger
from wasm.core.runner import set_runner
from wasm.core.store import App, WASMStore
from wasm.deployers import app_export
from wasm.deployers.helpers.layout import env_file_for

DOMAIN = "shop.example.com"
STRIPE = "sk_live_" + "51Habcdefghijklmn" + "opqrstuvwxyz"


class NoCron:
    """A cron manager with no jobs."""

    def list_jobs(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture(autouse=True)
def _real_runner() -> Iterator[None]:
    """--dry-run installs a rehearsing runner process-wide; put the default back."""
    yield
    set_runner(None)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[WASMStore]:
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "wasm.db")
    monkeypatch.setattr(app_export, "CronManager", NoCron)
    yield instance
    instance.close()
    WASMStore.reset_instance()


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


@pytest.fixture
def shop(store: WASMStore, tmp_path: Path) -> App:
    app = store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/acme/shop.git",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
            memory_max_mb=512,
        )
    )
    env = env_file_for(app)
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(f"APP_NAME=Shop\nSTRIPE_KEY={STRIPE}\n")
    return app


@pytest.fixture
def created(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stand in for wasm create's deploy, recording what reached it."""
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(app_module, "_create_app", create)
    return calls


def invoke(args: list[str]) -> Result:
    return CliRunner().invoke(root_cli, args)


def test_export_prints_the_document_without_secrets(shop: App) -> None:
    result = invoke(["app", "export", DOMAIN])

    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["format"] == "wasm-app"
    assert doc["env"]["STRIPE_KEY"] == {"secret": True, "value": None}
    assert STRIPE not in result.output


def test_export_with_secrets_to_a_file_is_0600(shop: App, tmp_path: Path, log: list[str]) -> None:
    target = tmp_path / "out" / "shop.json"
    result = invoke(["app", "export", DOMAIN, "--with-secrets", "-o", str(target)])

    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text())["env"]["STRIPE_KEY"]["value"] == STRIPE


def test_export_of_an_unknown_application_fails(store: WASMStore) -> None:
    result = invoke(["app", "export", "nothing.example.com"])
    assert result.exit_code != 0


def exported(shop: App, tmp_path: Path) -> Path:
    path = tmp_path / "shop.json"
    doc = app_export.export_app(DOMAIN)
    path.write_text(app_export.dumps(doc))
    return path


def test_import_rehearsal_deploys_nothing(
    shop: App, tmp_path: Path, created: list[dict[str, Any]], log: list[str]
) -> None:
    path = exported(shop, tmp_path)
    result = invoke(
        [
            "app",
            "import",
            str(path),
            "--domain",
            "store.example.org",
            "--env",
            f"STRIPE_KEY={STRIPE}",
            "--dry-run",
        ]
    )

    assert result.exit_code == 0, result.output
    assert created == []
    assert any("Rehearsal" in line for line in log)
    assert any("store.example.org" in line for line in log)


def test_import_stops_on_missing_secrets(
    shop: App, tmp_path: Path, created: list[dict[str, Any]]
) -> None:
    path = exported(shop, tmp_path)
    result = invoke(["app", "import", str(path), "--domain", "store.example.org"])

    assert isinstance(result.exception, ValidationError)
    assert "STRIPE_KEY" in result.exception.message
    assert created == []


def test_import_deploys_through_wasm_create(
    shop: App, tmp_path: Path, created: list[dict[str, Any]], log: list[str]
) -> None:
    path = exported(shop, tmp_path)
    env_file = tmp_path / "secrets.env"
    env_file.write_text(f"STRIPE_KEY={STRIPE}\n")
    result = invoke(
        [
            "--json",
            "app",
            "import",
            str(path),
            "--domain",
            "store.example.org",
            "--env-file",
            str(env_file),
        ]
    )

    assert result.exit_code == 0, result.output
    [call] = created
    assert call["domain"] == "store.example.org"
    assert call["app_type"] == "nodejs"
    assert call["source"] == "https://github.com/acme/shop.git"
    assert call["env_vars"] == {"APP_NAME": "Shop", "STRIPE_KEY": STRIPE}
    assert call["limits"].memory_max_mb == 512
    assert call["port"] is None, "3000 belongs to the exported application"
    payload = json.loads(result.output)
    assert payload["result"]["domain"] == "store.example.org"
    assert STRIPE not in result.output


def test_a_failed_create_fails_the_import(
    shop: App, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log: list[str]
) -> None:
    monkeypatch.setattr(app_module, "_create_app", lambda **_: 1)
    path = exported(shop, tmp_path)
    result = invoke(
        ["app", "import", str(path), "--domain", "x.example.org", "--env", f"STRIPE_KEY={STRIPE}"]
    )
    assert result.exit_code != 0
    assert "did not start" in str(result.exception)


def test_env_pairs_must_be_name_value(shop: App, tmp_path: Path) -> None:
    path = exported(shop, tmp_path)
    result = invoke(["app", "import", str(path), "--env", "NOVALUE"])
    assert result.exit_code == 2
    assert "NAME=VALUE" in result.output


# wasm import --from ----------------------------------------------------------------

RENDER = (
    "services:\n  - type: web\n    runtime: python\n    healthCheckPath: /healthz\n"
    "    envVars:\n      - key: SESSION_SECRET\n        generateValue: true\n"
    "      - key: APP_NAME\n        value: Shop\n"
)


def test_import_from_prints_the_proposal(tmp_path: Path, log: list[str]) -> None:
    (tmp_path / "render.yaml").write_text(RENDER)
    result = invoke(["import", "--from", "render", str(tmp_path)])

    assert result.exit_code == 0, result.output
    text = "\n".join(log)
    assert "render" in text and "python" in text and "/healthz" in text
    assert "generated secret" in text


def test_import_from_as_json(tmp_path: Path) -> None:
    (tmp_path / "render.yaml").write_text(RENDER)
    result = invoke(["import", "--from", "render", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    proposal = json.loads(result.output)
    assert proposal["platform"] == "render"
    assert proposal["health_path"] == "/healthz"


def test_import_from_coolify_explains(tmp_path: Path) -> None:
    result = invoke(["import", "--from", "coolify", str(tmp_path)])
    assert isinstance(result.exception, ValidationError)
    assert "database" in result.exception.details


def test_import_from_options_need_deploy(tmp_path: Path) -> None:
    (tmp_path / "render.yaml").write_text(RENDER)
    result = invoke(["import", "--from", "render", str(tmp_path), "--source", "x"])
    assert result.exit_code == 2


def test_import_from_deploys_through_wasm_create(
    store: WASMStore, tmp_path: Path, created: list[dict[str, Any]], log: list[str]
) -> None:
    (tmp_path / "render.yaml").write_text(RENDER)
    result = invoke(
        [
            "import",
            "--from",
            "render",
            str(tmp_path),
            "--deploy",
            "shop.example.org",
            "--source",
            "https://github.com/acme/shop.git",
        ]
    )

    assert result.exit_code == 0, result.output
    [call] = created
    assert call["domain"] == "shop.example.org"
    assert call["app_type"] == "python"
    assert call["env_vars"]["APP_NAME"] == "Shop"
    assert len(call["env_vars"]["SESSION_SECRET"]) >= 32


def test_create_hands_an_imports_marks_and_limits_to_the_deployer(
    store: WASMStore, monkeypatch: pytest.MonkeyPatch, log: list[str]
) -> None:
    """wasm create's deploy function takes what an import adds; a plain create does not."""
    from wasm.cli.commands import webapp
    from wasm.managers.service_manager import ResourceLimits

    configured: list[dict[str, Any]] = []

    class Spy:
        def configure(self, **kwargs: Any) -> None:
            configured.append(kwargs)

        def deploy(self) -> bool:
            return True

    monkeypatch.setattr(webapp, "get_deployer", lambda app_type, verbose=False: Spy())
    monkeypatch.setattr(webapp, "check_deployment_ready", lambda **_: (True, [], []))
    common = {"logger": Logger(), "source": "https://github.com/acme/shop.git", "port": 3100}

    assert webapp._create_app(domain="plain.example.com", **common) == 0
    assert (
        webapp._create_app(
            domain="imported.example.com",
            env_vars={"APP_NAME": "Shop"},
            env_secret_marks={"APP_NAME": False},
            limits=ResourceLimits(memory_max_mb=512),
            **common,
        )
        == 0
    )

    plain, imported = configured
    assert "resource_limits_given" not in plain and "env_secret_marks" not in plain
    assert imported["env_vars"] == {"APP_NAME": "Shop"}
    assert imported["env_secret_marks"] == {"APP_NAME": False}
    assert imported["memory_max_mb"] == 512 and imported["resource_limits_given"] is True
