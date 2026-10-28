# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_compose_document.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from noust.core.exceptions import NoustError
from noust.core.store import App, NoustStore
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir

DOMAIN = "stack.example.com"
APP_NAME = "stack-example-com"

DOCUMENT = """\
services:
  web:
    image: nginx:1.27
    ports:
      - "127.0.0.1:8080:80"
    environment:
      - DB_PASSWORD=${DB_PASSWORD:?give the database a password}
"""

NEWER = DOCUMENT.replace("nginx:1.27", "nginx:1.28")


def make_stack(store: NoustStore, tmp_path: Path, **fields: Any) -> App:
    """A Docker Compose stack deployed from a pasted document, with its .env."""
    directory = recipe_source_dir(APP_NAME, store)
    directory.mkdir(parents=True)
    (directory / COMPOSE_FILE).write_text(DOCUMENT)
    root = tmp_path / "apps" / APP_NAME
    root.mkdir(parents=True)
    (root / ".env").write_text("DB_PASSWORD=hunter2\n")
    values: dict[str, Any] = {
        "domain": DOMAIN,
        "app_type": "docker-compose",
        "source": str(directory),
        "app_path": str(root),
        "port": 8080,
        "layout": "inplace",
    }
    values.update(fields)
    store.create_app(App(**values))
    row = store.get_app(DOMAIN)
    assert row is not None
    return row


class Updates:
    """Stands in for the update, recording what it was asked and failing on demand."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail: NoustError | None = None
        self.put_back: list[dict[str, Any]] = []

    def update_app(self, domain: str, **kwargs: Any) -> Any:
        self.calls.append({"domain": domain, **kwargs})
        if self.fail is not None:
            raise self.fail
        return type("Outcome", (), {"deployment_id": 41, "warnings": None})()

    def put_stack_back(self, domain: str, app_name: str, snapshot: Any, failure: Any, **kw: Any):
        self.put_back.append({"domain": domain, "snapshot": dict(snapshot), "failure": failure})
