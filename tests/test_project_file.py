# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``noust.yaml``: the project file a repository declares its hooks in.

The file is untrusted input, exactly like the rest of the repository: a closed
schema, every refusal naming the field it is about and why, and nothing read
through a link the repository committed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.deployers.helpers.hooks import Hook, HookSet
from noust.deployers.helpers.project_file import (
    DEFAULT_HOOK_TIMEOUT,
    ProjectFile,
    StackDatabaseSpec,
    load_project_file,
    parse_hooks_document,
)

FULL = """\
hooks:
  pre_deploy:
    - run: /app/node_modules/.bin/prisma migrate deploy
      service: backend
      workdir: /app
      timeout: 600
      migrates: true
    - run: ./scripts/check-migrations.sh
  post_deploy:
    - run: ./scripts/purge-cache.sh
      service: backend
backup:
  databases: auto
"""


def write(root: Path, text: str, name: str = "noust.yaml") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return root


class TestLoading:
    def test_a_repository_without_a_project_file_declares_nothing(self, tmp_path: Path) -> None:
        project = load_project_file(tmp_path)

        assert project == ProjectFile(hooks=HookSet(source="none"), backup_databases="auto")
        assert not project.hooks.declared

    def test_the_example_of_the_design_is_read_as_written(self, tmp_path: Path) -> None:
        project = load_project_file(write(tmp_path, FULL))

        assert project.hooks.source == "repo"
        assert project.hooks.pre_deploy == (
            Hook(
                run=("/app/node_modules/.bin/prisma", "migrate", "deploy"),
                service="backend",
                workdir="/app",
                timeout=600,
                migrates=True,
            ),
            Hook(
                run=("./scripts/check-migrations.sh",),
                service=None,
                workdir=None,
                timeout=DEFAULT_HOOK_TIMEOUT,
                migrates=False,
            ),
        )
        assert project.hooks.post_deploy[0].service == "backend"
        assert project.backup_databases == "auto"

    def test_the_hidden_name_is_read_too(self, tmp_path: Path) -> None:
        project = load_project_file(
            write(tmp_path, "hooks:\n  pre_deploy:\n    - run: x\n", ".noust.yaml")
        )

        assert project.hooks.pre_deploy[0].run == ("x",)

    def test_both_names_at_once_is_ambiguous(self, tmp_path: Path) -> None:
        write(tmp_path, "hooks: {}\n")
        write(tmp_path, "hooks: {}\n", ".noust.yaml")

        with pytest.raises(ValidationError) as failure:
            load_project_file(tmp_path)
        assert failure.value.field == "noust.yaml"

    def test_a_linked_project_file_is_refused_unread(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret"
        secret.write_text("hooks: {}\n")
        root = tmp_path / "repo"
        root.mkdir()
        (root / "noust.yaml").symlink_to(secret)

        with pytest.raises(ValidationError) as failure:
            load_project_file(root)
        assert failure.value.field == "noust.yaml"
        assert "link" in failure.value.message

    def test_an_empty_file_declares_nothing(self, tmp_path: Path) -> None:
        project = load_project_file(write(tmp_path, ""))

        assert not project.hooks.declared
        assert project.backup_databases == "auto"

    def test_backup_databases_off_survives_yaml_reading_it_as_false(self, tmp_path: Path) -> None:
        project = load_project_file(write(tmp_path, "backup:\n  databases: off\n"))

        assert project.backup_databases == "off"

    def test_databases_can_be_declared(self, tmp_path: Path) -> None:
        text = (
            "backup:\n  databases:\n"
            "    - {service: db, engine: postgres, database: app, user: app}\n"
            "    - {service: mongo, engine: mongo}\n"
        )
        project = load_project_file(write(tmp_path, text))

        assert project.backup_databases == (
            StackDatabaseSpec(service="db", engine="postgres", database="app", user="app"),
            StackDatabaseSpec(service="mongo", engine="mongo", database=None, user=None),
        )


class TestTheClosedSchema:
    @pytest.mark.parametrize(
        ("text", "field"),
        [
            ("hoks: {}\n", "hoks"),
            (
                "hooks:\n  pre_deploy:\n    - run: x\n      sevice: web\n",
                "hooks.pre_deploy[0].sevice",
            ),
            ("hooks:\n  predeploy: []\n", "hooks.predeploy"),
            (
                "hooks:\n  pre_deploy:\n    - run: x\n      workdir: /srv\n",
                "hooks.pre_deploy[0].workdir",
            ),
            (
                "hooks:\n  pre_deploy:\n    - run: x\n      workdir: ../other\n",
                "hooks.pre_deploy[0].workdir",
            ),
            (
                "hooks:\n  pre_deploy:\n    - run: x\n      service: web\n      workdir: /app/../etc\n",
                "hooks.pre_deploy[0].workdir",
            ),
            ("hooks:\n  pre_deploy:\n    - run: ../../bin/evil\n", "hooks.pre_deploy[0].run"),
            (
                "hooks:\n  pre_deploy:\n    - run: x\n      timeout: 0\n",
                "hooks.pre_deploy[0].timeout",
            ),
            (
                "hooks:\n  pre_deploy:\n    - run: x\n      timeout: 3601\n",
                "hooks.pre_deploy[0].timeout",
            ),
            (
                "hooks:\n  pre_deploy:\n    - run: x\n      timeout: yes\n",
                "hooks.pre_deploy[0].timeout",
            ),
            (
                "hooks:\n  pre_deploy:\n    - run: x\n      migrates: maybe\n",
                "hooks.pre_deploy[0].migrates",
            ),
            ("hooks:\n  pre_deploy:\n    - workdir: app\n", "hooks.pre_deploy[0].run"),
            ("hooks:\n  pre_deploy:\n    - run: ''\n", "hooks.pre_deploy[0].run"),
            ("hooks:\n  pre_deploy:\n    - run: echo 'unclosed\n", "hooks.pre_deploy[0].run"),
            (
                "hooks:\n  pre_deploy:\n    - run: x\n      service: 'a b'\n",
                "hooks.pre_deploy[0].service",
            ),
            ("hooks:\n  post_deploy: run x\n", "hooks.post_deploy"),
            ("hooks: [1]\n", "hooks"),
            ("- 1\n", "noust.yaml"),
            ("hooks: {\n", "noust.yaml"),
            ("backup:\n  databases: sometimes\n", "backup.databases"),
            (
                "backup:\n  databases:\n    - {service: db, engine: oracle}\n",
                "backup.databases[0].engine",
            ),
            ("backup:\n  databases:\n    - {engine: postgres}\n", "backup.databases[0].service"),
            ("backup:\n  database: auto\n", "backup.database"),
        ],
    )
    def test_a_refusal_names_the_field(self, tmp_path: Path, text: str, field: str) -> None:
        with pytest.raises(ValidationError) as failure:
            load_project_file(write(tmp_path, text))

        assert failure.value.field == field
        assert failure.value.details

    def test_a_pipe_is_split_like_a_shell_would_but_never_given_to_one(self) -> None:
        hooks = parse_hooks_document(
            "hooks:\n  pre_deploy:\n    - run: \"sh -c 'a | b' && echo done\"\n", source="repo"
        )

        assert hooks.pre_deploy[0].run == ("sh", "-c", "a | b", "&&", "echo", "done")

    def test_a_list_is_taken_as_the_argv_itself(self) -> None:
        hooks = parse_hooks_document(
            "hooks:\n  pre_deploy:\n    - run: [node, scripts/migrate.js, '--yes please']\n",
            source="repo",
        )

        assert hooks.pre_deploy[0].run == ("node", "scripts/migrate.js", "--yes please")

    def test_a_relative_workdir_inside_the_app_is_fine(self) -> None:
        hooks = parse_hooks_document(
            "hooks:\n  pre_deploy:\n    - run: x\n      workdir: packages/api\n", source="repo"
        )

        assert hooks.pre_deploy[0].workdir == "packages/api"


class TestOperatorDocuments:
    def test_an_operator_document_has_the_same_shape_and_says_where_it_came_from(self) -> None:
        hooks = parse_hooks_document(
            "hooks:\n  post_deploy:\n    - run: ./notify.sh\n", source="operator"
        )

        assert hooks.source == "operator"
        assert hooks.post_deploy[0].run == ("./notify.sh",)
        assert hooks.declared

    def test_an_operator_document_does_not_carry_the_backup_setting(self) -> None:
        with pytest.raises(ValidationError) as failure:
            parse_hooks_document("backup:\n  databases: off\n", source="operator")

        assert failure.value.field == "backup"
        assert "backup-before-update" in (failure.value.details or "")

    def test_an_empty_operator_document_declares_no_hooks(self) -> None:
        hooks = parse_hooks_document("hooks: {}\n", source="operator")

        assert hooks == HookSet(source="operator")
        assert not hooks.declared
