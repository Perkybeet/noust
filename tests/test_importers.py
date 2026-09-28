# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for reading other platforms' configuration (``wasm.deployers.importers``).

Each importer is fed the files its platform keeps in a repository, as a fake
tree, and pinned on what it proposes and on what it warns about: the warnings
are the contract with the operator that nothing without an equivalent is
dropped silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wasm.core.exceptions import ValidationError
from wasm.deployers.importers import (
    PLATFORM_FILES,
    PLATFORMS,
    detect_platforms,
    propose,
    read_platform,
)
from wasm.deployers.importers import railway as railway_module
from wasm.deployers.importers.base import Proposal, declared_env, health_timeout


def tree(root: Path, files: dict[str, str]) -> Path:
    """Write a fake repository."""
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def env_of(proposal: Proposal) -> dict[str, dict[str, object]]:
    """Index the proposed variables by name."""
    return {
        v.name: {
            "value": v.value,
            "secret": v.secret,
            "generated": v.generated,
            "required": v.required,
        }
        for v in proposal.env
    }


def joined(proposal: Proposal) -> str:
    """Every warning in one string, to search."""
    return "\n".join(proposal.warnings)


# Registry ---------------------------------------------------------------------


def test_every_platform_and_file_is_listed() -> None:
    assert PLATFORMS == ("vercel", "railway", "render", "heroku")
    for name in ("vercel.json", ".vercel/project.json", "railway.toml", "railway.json"):
        assert name in PLATFORM_FILES
    for name in ("render.yaml", "app.json", "Procfile"):
        assert name in PLATFORM_FILES


def test_coolify_is_refused_with_the_reason(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as caught:
        read_platform("coolify", tmp_path)
    assert "Coolify" in caught.value.message
    assert "database" in caught.value.details


def test_an_unknown_platform_names_the_known_ones(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as caught:
        read_platform("netlify", tmp_path)
    assert "vercel" in caught.value.details


def test_a_repository_without_the_files_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as caught:
        read_platform("render", tmp_path)
    assert "render.yaml" in caught.value.details


def test_nothing_to_propose_without_platform_files(tmp_path: Path) -> None:
    tree(tmp_path, {"package.json": "{}"})
    assert detect_platforms(tmp_path) == []
    assert propose(tmp_path) is None


def test_propose_reads_the_first_and_names_the_others(tmp_path: Path) -> None:
    tree(tmp_path, {"vercel.json": "{}", "Procfile": "web: node server.js\n"})
    proposal = propose(tmp_path)
    assert proposal is not None and proposal.platform == "vercel"
    assert "wasm import --from heroku" in joined(proposal)


def test_propose_turns_an_unreadable_file_into_a_warning(tmp_path: Path) -> None:
    tree(tmp_path, {"vercel.json": "{not json"})
    proposal = propose(tmp_path)
    assert proposal is not None and proposal.platform == "vercel"
    assert "not valid JSON" in joined(proposal)


def test_a_linked_configuration_file_is_not_read(tmp_path: Path) -> None:
    secret = tmp_path / "outside.json"
    secret.write_text('{"framework": "nextjs"}')
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "vercel.json").symlink_to(secret)
    with pytest.raises(ValidationError, match="not a regular file"):
        read_platform("vercel", repo)


# Shared helpers -----------------------------------------------------------------


def test_a_secret_default_in_a_committed_file_is_withheld() -> None:
    variable = declared_env("API_SECRET", "abc" + "123")
    assert variable.value is None and variable.secret and variable.required


def test_a_plain_default_is_kept() -> None:
    variable = declared_env("APP_NAME", "Shop")
    assert variable.value == "Shop" and not variable.secret and not variable.required


def test_health_timeout_is_fitted_to_the_gate() -> None:
    proposal = Proposal(platform="railway")
    assert health_timeout(900, proposal, source="healthcheckTimeout") == 600
    assert health_timeout(1, proposal, source="healthcheckTimeout") == 5
    assert health_timeout(None, proposal, source="healthcheckTimeout") is None
    assert len(proposal.warnings) == 2


# Vercel -----------------------------------------------------------------------


def test_vercel_nextjs_with_edge_rules(tmp_path: Path) -> None:
    config = {
        "framework": "nextjs",
        "buildCommand": "next build",
        "installCommand": "pnpm install",
        "rewrites": [{"source": "/a", "destination": "/b"}],
        "redirects": [{"source": "/old", "destination": "/new"}],
        "headers": [{"source": "/(.*)", "headers": []}],
        "crons": [{"path": "/api/cron", "schedule": "0 5 * * *"}],
        "env": {"PUBLIC_URL": "https://example.com", "API_TOKEN": "@api-token"},
    }
    tree(tmp_path, {"vercel.json": json.dumps(config), "package.json": "{}"})
    proposal = read_platform("vercel", tmp_path)

    assert proposal.app_type == "nextjs"
    assert proposal.build_command == "next build"
    assert proposal.install_command == "pnpm install"
    warnings = joined(proposal)
    for key in ("rewrites", "redirects", "headers"):
        assert f"1 {key} rule(s)" in warnings
    assert "/api/cron" in warnings and "wasm cron create" in warnings
    assert "package.json's scripts" in warnings
    env = env_of(proposal)
    assert env["PUBLIC_URL"]["value"] == "https://example.com"
    assert env["API_TOKEN"] == {"value": None, "secret": True, "generated": False, "required": True}


def test_vercel_project_settings_are_read_and_overridden(tmp_path: Path) -> None:
    project = {
        "projectId": "prj",
        "settings": {"framework": "vite", "outputDirectory": "build", "rootDirectory": "web"},
    }
    tree(
        tmp_path,
        {
            ".vercel/project.json": json.dumps(project),
            "vercel.json": json.dumps({"buildCommand": "vite build"}),
            "package.json": "{}",
        },
    )
    proposal = read_platform("vercel", tmp_path)
    assert proposal.files == [".vercel/project.json", "vercel.json"]
    assert proposal.app_type == "vite"
    assert proposal.build_command == "vite build"
    assert "build.outDir" in joined(proposal)
    assert "web/" in joined(proposal)


def test_vercel_other_without_a_build_is_static(tmp_path: Path) -> None:
    tree(tmp_path, {"vercel.json": "{}", "index.html": "<p>hi</p>"})
    assert read_platform("vercel", tmp_path).app_type == "static"


def test_vercel_framework_without_a_deployer_is_left_to_detection(tmp_path: Path) -> None:
    tree(tmp_path, {"vercel.json": json.dumps({"framework": "sveltekit"}), "package.json": "{}"})
    proposal = read_platform("vercel", tmp_path)
    assert proposal.app_type is None
    assert "sveltekit" in joined(proposal)


# Railway ----------------------------------------------------------------------

RAILWAY_TOML = """
# Railway config as code
[build]
builder = "NIXPACKS"
buildCommand = "npm run build"
watchPatterns = ["src/**"]

[deploy]
startCommand = "npm start"
healthcheckPath = "/health"
healthcheckTimeout = 120
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
numReplicas = 2
"""


def test_railway_toml(tmp_path: Path) -> None:
    tree(tmp_path, {"railway.toml": RAILWAY_TOML})
    proposal = read_platform("railway", tmp_path)
    assert proposal.files == ["railway.toml"]
    assert proposal.app_type is None
    assert proposal.build_command == "npm run build"
    assert proposal.start_command == "npm start"
    assert proposal.health_path == "/health"
    assert proposal.health_timeout == 120
    warnings = joined(proposal)
    assert "ON_FAILURE" in warnings
    assert "2 replicas" in warnings
    assert "watchPatterns" in warnings
    assert "dashboard" in warnings


def test_railway_json_with_a_dockerfile(tmp_path: Path) -> None:
    config = {
        "build": {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"},
        "deploy": {"cronSchedule": "*/5 * * * *", "preDeployCommand": ["npm run migrate"]},
    }
    tree(tmp_path, {"railway.json": json.dumps(config)})
    proposal = read_platform("railway", tmp_path)
    warnings = joined(proposal)
    assert "compose.yaml" in warnings
    assert "wasm cron create" in warnings
    assert "preDeployCommand" in warnings


def test_the_toml_subset_reader_used_on_python_3_10() -> None:
    parsed = railway_module._load_simple_toml(RAILWAY_TOML, name="railway.toml")
    assert parsed["build"]["watchPatterns"] == ["src/**"]
    assert parsed["deploy"]["healthcheckTimeout"] == 120
    assert parsed["deploy"]["startCommand"] == "npm start"


def test_the_toml_subset_reader_refuses_what_it_cannot_read() -> None:
    with pytest.raises(ValidationError):
        railway_module._load_simple_toml("[build]\nthis is not toml\n", name="railway.toml")


def test_invalid_toml_is_a_validation_error(tmp_path: Path) -> None:
    tree(tmp_path, {"railway.toml": "[build\n"})
    with pytest.raises(ValidationError):
        read_platform("railway", tmp_path)


# Render -----------------------------------------------------------------------

RENDER_YAML = """
services:
  - type: web
    name: shop
    runtime: node
    buildCommand: npm ci && npm run build
    startCommand: npm start
    healthCheckPath: /healthz
    domains:
      - Shop.example.com
    disk:
      name: uploads
      mountPath: /opt/render/project/src/uploads
    envVars:
      - key: NODE_VERSION
        value: 20
      - key: PORT
        value: 8080
      - key: SESSION_SECRET
        generateValue: true
      - key: STRIPE_KEY
        sync: false
      - key: DATABASE_URL
        fromDatabase:
          name: shop-db
          property: connectionString
      - key: REDIS_URL
        fromService:
          type: keyvalue
          name: cache
          property: connectionString
      - fromGroup: shared-settings
  - type: worker
    name: jobs
    runtime: node
databases:
  - name: shop-db
"""


def test_render_blueprint(tmp_path: Path) -> None:
    tree(tmp_path, {"render.yaml": RENDER_YAML})
    proposal = read_platform("render", tmp_path)

    assert proposal.app_type is None
    assert proposal.build_command == "npm ci && npm run build"
    assert proposal.start_command == "npm start"
    assert proposal.health_path == "/healthz"
    assert proposal.port == 8080
    assert proposal.domains == ["shop.example.com"]
    assert proposal.persistent_paths == ["uploads"]
    assert proposal.databases == ["postgresql", "redis"]
    env = env_of(proposal)
    assert env["NODE_VERSION"]["value"] == "20"
    assert env["SESSION_SECRET"] == {
        "value": None,
        "secret": True,
        "generated": True,
        "required": False,
    }
    assert env["STRIPE_KEY"]["required"] and env["STRIPE_KEY"]["secret"]
    assert env["DATABASE_URL"]["required"] and env["DATABASE_URL"]["secret"]
    assert env["REDIS_URL"]["required"]
    assert "PORT" not in env
    warnings = joined(proposal)
    assert "jobs (worker)" in warnings
    assert "shared-settings" in warnings
    assert "shop-db" in warnings


def test_render_static_site(tmp_path: Path) -> None:
    tree(
        tmp_path,
        {
            "render.yaml": "services:\n  - type: web\n    runtime: static\n"
            "    staticPublishPath: ./dist\n    routes:\n      - type: rewrite\n"
            "        source: /*\n        destination: /index.html\n"
        },
    )
    proposal = read_platform("render", tmp_path)
    assert proposal.app_type == "static"
    assert proposal.output_directory == "./dist"
    assert "rewrite routes" in joined(proposal)


def test_render_docker_and_a_disk_elsewhere(tmp_path: Path) -> None:
    tree(
        tmp_path,
        {
            "render.yaml": "services:\n  - type: web\n    runtime: docker\n"
            "    disk:\n      mountPath: /var/data\n"
        },
    )
    proposal = read_platform("render", tmp_path)
    assert proposal.persistent_paths == []
    warnings = joined(proposal)
    assert "compose.yaml" in warnings and "/var/data" in warnings


def test_render_invalid_yaml(tmp_path: Path) -> None:
    tree(tmp_path, {"render.yaml": "services: [\n"})
    with pytest.raises(ValidationError, match="YAML"):
        read_platform("render", tmp_path)


# Heroku -----------------------------------------------------------------------


def test_heroku_manifest_and_procfile(tmp_path: Path) -> None:
    manifest = {
        "name": "shop",
        "env": {
            "SECRET_KEY_BASE": {"generator": "secret"},
            "WEB_CONCURRENCY": {"value": "2", "required": False},
            "SENTRY_DSN": {"description": "Where errors go"},
            "OPTIONAL_FLAG": {"required": False},
            "APP_NAME": "Shop",
        },
        "addons": ["heroku-postgresql:essential-0", {"plan": "heroku-redis:mini"}, "papertrail"],
        "buildpacks": [{"url": "heroku/python"}],
        "formation": {"web": {"quantity": 2}, "worker": {"quantity": 1}},
        "scripts": {"postdeploy": "python manage.py migrate"},
    }
    tree(
        tmp_path,
        {
            "app.json": json.dumps(manifest),
            "Procfile": "web: gunicorn app:app --bind 0.0.0.0:$PORT\n"
            "release: python manage.py migrate\nworker: celery -A app worker\n",
        },
    )
    proposal = read_platform("heroku", tmp_path)

    assert proposal.files == ["app.json", "Procfile"]
    assert proposal.app_type == "python"
    assert proposal.start_command == "gunicorn app:app --bind 0.0.0.0:$PORT"
    assert proposal.databases == ["postgresql", "redis"]
    env = env_of(proposal)
    assert env["SECRET_KEY_BASE"]["generated"]
    assert env["WEB_CONCURRENCY"] == {
        "value": "2",
        "secret": False,
        "generated": False,
        "required": False,
    }
    assert env["SENTRY_DSN"]["required"]
    assert not env["OPTIONAL_FLAG"]["required"]
    assert env["APP_NAME"]["value"] == "Shop"
    warnings = joined(proposal)
    for expected in ("release command", "worker process", "papertrail", "postdeploy", "2 web"):
        assert expected in warnings


def test_an_expo_app_json_is_not_heroku(tmp_path: Path) -> None:
    tree(tmp_path, {"app.json": json.dumps({"expo": {"name": "x"}, "env": {}})})
    assert detect_platforms(tmp_path) == []


def test_a_procfile_alone_is_heroku(tmp_path: Path) -> None:
    tree(tmp_path, {"Procfile": "# comment\nweb: node server.js\n"})
    proposal = read_platform("heroku", tmp_path)
    assert proposal.start_command == "node server.js"
    assert proposal.files == ["Procfile"]
