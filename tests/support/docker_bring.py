# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the tests of bringing a stack to Noust share: ``compose_adopt`` replaced by a stand-in that keeps what it
was asked for, so a test holds what the Docker zone gives adoption (the folder, the file, the name) and
leaves the adoption itself to its own tests (``tests/test_compose_adopt.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.deployers import compose_adopt
from noust.deployers.compose_adopt import AdoptionPlan, AdoptionResult


class Adoption:
    """Stands in for ``compose_adopt``: what it was asked for is what is held."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, **plan: Any) -> None:
        self.planned: list[dict[str, Any]] = []
        self.adopted: list[AdoptionPlan] = []
        self.plan = plan
        monkeypatch.setattr(compose_adopt, "plan_adoption", self._plan)
        monkeypatch.setattr(compose_adopt, "adopt", self._adopt)

    def _plan(self, domain: str, path: Path, **options: Any) -> AdoptionPlan:
        self.planned.append({"domain": domain, "path": path, **options})
        values: dict[str, Any] = {
            "domain": domain,
            "app_name": domain.replace(".", "-"),
            "app_path": path,
            "compose_file": options.get("compose_file") or "compose.yaml",
            "project": "nextcloud",
            "project_from": "containers",
            "containers": ("nextcloud-app-1", "nextcloud-db-1", "nextcloud-setup-1"),
            "running": True,
            "source": "",
            "branch": None,
            "commit": None,
            "site": None,
            "site_name": None,
            "ssl": False,
            "port": 8080,
            "headless": False,
            "dry_run": "[+] Running 3/0\n ✔ DRY-RUN MODE - Container nextcloud-app-1 Running",
            "changes": (),
            "warnings": ("no repository: an update pulls the images the compose file names",),
        }
        values.update(self.plan)
        return AdoptionPlan(**values)

    def _adopt(self, plan: AdoptionPlan, **options: Any) -> AdoptionResult:
        self.adopted.append(plan)
        return AdoptionResult(plan=plan, adopted=True, unit=plan.app_name, deployment_id=7)
