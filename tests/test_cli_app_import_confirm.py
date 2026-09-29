# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``wasm app import`` asks before a document creates cron jobs or previews.

Pinned: the plan the terminal prints names each cron job's user, directory
and command; without ``--yes`` the import asks at a terminal and refuses
without one, saying why; the first deployment is handed the document's
health check; a document without a ``domains`` section imports; and
``--with-secrets`` to the terminal warns on stderr, away from the document.
Also, through the API: the job deploys with the health check, records the
plan in its metadata and audits each cron job under the caller's name.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import app as app_module
from noust.core.exceptions import ValidationError
from noust.core.store import App, NoustStore
from noust.deployers import app_export
from noust.managers.cron_manager import CronJob
from tests.test_cli_app_export import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    STRIPE,
    _real_runner,
    created,
    invoke,
    log,
    shop,
    store,
)

JOB = {
    "name": "shop-example-com-sync",
    "schedule": "hourly",
    "command": "/usr/bin/true",
    "user": "root",
    "working_directory": "/etc",
}


class RecordingCron:
    """A cron manager that records what it creates."""

    def __init__(self) -> None:
        self.created: list[CronJob] = []

    def list_jobs(self) -> list[dict[str, Any]]:
        return []

    def get_job(self, name: str) -> None:
        return None

    def create_job(self, job: CronJob) -> CronJob:
        self.created.append(job)
        return job


def write_document(tmp_path: Path, **sections: Any) -> Path:
    doc: dict[str, Any] = {
        "format": "wasm-app",
        "version": 1,
        "app": {
            "domain": "store.example.org",
            "app_type": "nodejs",
            "source": "https://github.com/acme/shop.git",
            "health": {"path": "/up", "expect": "200", "timeout": 30},
        },
        **sections,
    }
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(doc))
    return path


@pytest.fixture
def cron(monkeypatch: pytest.MonkeyPatch) -> RecordingCron:
    manager = RecordingCron()
    monkeypatch.setattr(app_export, "CronManager", lambda: manager)
    return manager


def test_a_document_without_domains_imports_with_its_health_check(
    store: NoustStore, tmp_path: Path, created: list[dict[str, Any]], log: list[str]
) -> None:
    result = invoke(["app", "import", str(write_document(tmp_path))])

    assert result.exit_code == 0, result.output
    [call] = created
    assert call["initial_health"] == ("/up", "200", 30)


def test_without_a_terminal_a_document_with_cron_jobs_needs_yes(
    store: NoustStore,
    tmp_path: Path,
    created: list[dict[str, Any]],
    log: list[str],
    cron: RecordingCron,
) -> None:
    path = write_document(tmp_path, cron=[JOB])

    result = invoke(["app", "import", str(path)])

    assert isinstance(result.exception, ValidationError)
    assert "1 of them as root" in result.exception.message
    assert "--yes" in result.exception.details
    assert created == [] and cron.created == []
    printed = "\n".join(log)
    assert "as root (root: the whole server) in /etc (outside the application)" in printed
    assert ": /usr/bin/true" in printed


def test_yes_creates_them(
    store: NoustStore,
    tmp_path: Path,
    created: list[dict[str, Any]],
    log: list[str],
    cron: RecordingCron,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = write_document(tmp_path, cron=[JOB])

    def create(**kwargs: Any) -> int:
        # A cron job needs its application's row, which the real deploy writes.
        created.append(kwargs)
        store.create_app(
            App(domain=kwargs["domain"], app_type="nodejs", port=3100, app_path=str(tmp_path))
        )
        return 0

    monkeypatch.setattr(app_module, "_create_app", create)

    result = invoke(["app", "import", str(path), "--yes"])

    assert result.exit_code == 0, result.output
    assert [job.name for job in cron.created] == ["shop-example-com-sync"]


def test_at_a_terminal_the_operator_is_asked(
    store: NoustStore,
    tmp_path: Path,
    created: list[dict[str, Any]],
    log: list[str],
    cron: RecordingCron,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "_interactive", lambda: True)
    path = write_document(tmp_path, previews={"base_domain": "pr.example.com"})

    result = CliRunner().invoke(root_cli, ["app", "import", str(path)], input="n\n")

    assert result.exit_code == 1
    assert "enables pull request previews" in result.output
    assert created == []


def test_a_rehearsal_needs_no_confirmation(
    store: NoustStore,
    tmp_path: Path,
    created: list[dict[str, Any]],
    cron: RecordingCron,
    log: list[str],
) -> None:
    path = write_document(tmp_path, cron=[JOB])

    result = invoke(["--json", "app", "import", str(path), "--dry-run"])

    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)["plan"]
    assert plan["confirm"] and any("as root" in step for step in plan["steps"])
    assert created == []


def test_export_with_secrets_to_the_terminal_warns_on_stderr(shop: App) -> None:
    result = CliRunner().invoke(root_cli, ["app", "export", DOMAIN, "--with-secrets"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["env"]["STRIPE_KEY"]["value"] == STRIPE
    assert "-o FILE" in result.stderr


def test_export_without_secrets_does_not_warn(shop: App) -> None:
    result = CliRunner().invoke(root_cli, ["app", "export", DOMAIN])
    assert result.exit_code == 0 and result.stderr == ""


# Through the API --------------------------------------------------------------------


def test_the_import_job_deploys_with_the_health_check_and_audits_cron_jobs(
    store: NoustStore,
    tmp_path: Path,
    cron: RecordingCron,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from noust.web.api import app_export as api_module

    doc = json.loads(write_document(tmp_path, cron=[JOB]).read_text())
    plan = app_export.plan_import(doc)
    deployed: list[dict[str, Any]] = []

    def deploy_app_job(**kwargs: Any) -> None:
        deployed.append(kwargs)
        store.create_app(
            App(domain=kwargs["domain"], app_type="nodejs", port=3100, app_path=str(tmp_path))
        )

    monkeypatch.setattr(api_module, "deploy_app_job", deploy_app_job)
    with caplog.at_level(logging.INFO, logger="noust.audit"):
        api_module.import_app_job(plan, actor="token:ops")

    [call] = deployed
    assert (call["health_path"], call["health_expect"], call["health_timeout"]) == (
        "/up",
        "200",
        30,
    )
    audit = [r.getMessage() for r in caplog.records if r.name == "noust.audit"]
    assert audit == [
        "create_cron_job name=shop-example-com-sync schedule=hourly session=token:ops "
        "user=root via=import app=store.example.org"
    ]
