# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``build:`` block of ``noust.yaml``: schema, precedence and persistence.

The block is untrusted input like the rest of the repository, so these tests
are mostly refusals: a key nobody declared, a path that leaves the repository,
a link, a command that cannot be split. Each refusal names the field it is
about. Then the rule that makes the operator's block the one that wins, whole,
and the table that keeps it across a redeploy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import app_build_store
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import App, NoustStore
from noust.deployers.helpers.build_config import (
    BuildConfig,
    describe_build,
    project_dir,
    refuse_unsupported,
    resolve_build,
    set_operator_build,
)
from noust.deployers.helpers.project_file import (
    ProjectFile,
    load_project_file,
    parse_build_document,
)

DOMAIN = "app.example.com"

FULL = """\
build:
  base_directory: apps/web
  install: pnpm install --frozen-lockfile
  build: pnpm --filter web build
  start: node dist/server.js --port ${PORT}
  publish_directory: dist
  runtime: node@22
"""


def write(root: Path, text: str, name: str = "noust.yaml") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    return root


@pytest.fixture
def store(tmp_path: Path):
    """An isolated store with one application."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", source="x", app_path=str(tmp_path / "app"))
    )
    yield instance
    NoustStore.reset_instance()


class TestTheSchema:
    def test_the_whole_block_is_read_as_written(self, tmp_path: Path) -> None:
        project = load_project_file(write(tmp_path, FULL))

        build = project.build
        assert build.source == "repo"
        assert build.base_directory == "apps/web"
        assert build.install == ("pnpm", "install", "--frozen-lockfile")
        assert build.build == ("pnpm", "--filter", "web", "build")
        assert build.start == ("node", "dist/server.js", "--port", "${PORT}")
        assert build.publish_directory == "dist"
        assert build.runtime is not None and build.runtime.text == "node@22"
        assert build.declared_keys == (
            "base_directory",
            "install",
            "build",
            "start",
            "publish_directory",
            "runtime",
        )

    def test_a_file_without_a_build_block_declares_none(self, tmp_path: Path) -> None:
        project = load_project_file(write(tmp_path, "hooks:\n  pre_deploy: [{run: ./x.sh}]\n"))

        assert not project.build.declared
        assert project.build.source == "none"

    def test_no_file_is_the_empty_project(self, tmp_path: Path) -> None:
        assert load_project_file(tmp_path) == ProjectFile()

    def test_a_command_may_be_a_list_of_arguments(self) -> None:
        build = parse_build_document(
            "build:\n  start: [gunicorn, '-b', '127.0.0.1:${PORT}', 'app:app']\n",
            source="operator",
        )

        assert build.start == ("gunicorn", "-b", "127.0.0.1:${PORT}", "app:app")

    def test_a_command_is_split_like_a_shell_would_but_never_run_by_one(self) -> None:
        build = parse_build_document(
            "build:\n  build: make 'all targets' && rm -rf /\n", source="operator"
        )

        # The && is an argument of make, not a second command.
        assert build.build == ("make", "all targets", "&&", "rm", "-rf", "/")

    @pytest.mark.parametrize("value", [".", "./", "", "  "])
    def test_the_root_of_the_repository_is_no_base_directory(self, value: str) -> None:
        build = parse_build_document(f"build:\n  base_directory: '{value}'\n", source="operator")

        assert build.base_directory is None

    def test_paths_are_normalised(self) -> None:
        build = parse_build_document(
            "build:\n  base_directory: ./apps//web/\n  publish_directory: ./dist/\n",
            source="operator",
        )

        assert build.base_directory == "apps/web"
        assert build.publish_directory == "dist"


class TestRefusals:
    @pytest.mark.parametrize(
        ("document", "field"),
        [
            ("build:\n  nope: 1\n", "build.nope"),
            ("build:\n  base_directory: ../outside\n", "build.base_directory"),
            ("build:\n  base_directory: apps/../..\n", "build.base_directory"),
            ("build:\n  base_directory: /etc\n", "build.base_directory"),
            ("build:\n  base_directory: ~/x\n", "build.base_directory"),
            ("build:\n  base_directory: 'a b'\n", "build.base_directory"),
            ("build:\n  base_directory: 'a;b'\n", "build.base_directory"),
            ("build:\n  base_directory: 'a\\b'\n", "build.base_directory"),
            ("build:\n  base_directory: 12\n", "build.base_directory"),
            ("build:\n  publish_directory: /var/www\n", "build.publish_directory"),
            ("build:\n  publish_directory: dist/../../x\n", "build.publish_directory"),
            ("build:\n  install: ''\n", "build.install"),
            ('build:\n  build: "make \'unclosed"\n', "build.build"),
            ("build:\n  start: ../bin/server\n", "build.start"),
            ("build:\n  start: 7\n", "build.start"),
            ("build:\n  runtime: node\n", "build.runtime"),
            ("build:\n  runtime: ruby@3.2\n", "build.runtime"),
            ("build:\n  runtime: node@>=18\n", "build.runtime"),
            ("build: [a, b]\n", "build"),
        ],
    )
    def test_each_refusal_names_the_field(self, document: str, field: str) -> None:
        with pytest.raises(ValidationError) as excinfo:
            parse_build_document(document, source="operator")

        assert excinfo.value.field == field
        assert excinfo.value.details

    def test_a_repository_is_refused_in_the_same_way(self, tmp_path: Path) -> None:
        write(tmp_path, "build:\n  base_directory: ../x\n")

        with pytest.raises(ValidationError) as excinfo:
            load_project_file(tmp_path)

        assert excinfo.value.field == "build.base_directory"

    def test_the_operators_document_holds_only_the_build_block(self) -> None:
        for key in ("hooks", "backup"):
            with pytest.raises(ValidationError) as excinfo:
                parse_build_document(f"{key}: {{}}\nbuild: {{}}\n", source="operator")
            assert excinfo.value.field == key
        with pytest.raises(ValidationError):
            parse_build_document("other: 1\n", source="operator")

    def test_a_document_that_is_not_yaml_is_refused(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            parse_build_document("build: [unclosed\n", source="operator")

        assert excinfo.value.field == "build"


class TestProjectDirectory:
    def test_no_base_directory_is_the_tree_itself(self, tmp_path: Path) -> None:
        assert project_dir(tmp_path, None) == tmp_path

    def test_a_subdirectory_is_found_inside_the_tree(self, tmp_path: Path) -> None:
        (tmp_path / "apps" / "web").mkdir(parents=True)

        assert project_dir(tmp_path, "apps/web") == tmp_path / "apps" / "web"

    def test_a_link_is_never_followed(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        tree = tmp_path / "tree"
        (tree / "apps").mkdir(parents=True)
        (tree / "apps" / "web").symlink_to(outside)

        with pytest.raises(ValidationError) as excinfo:
            project_dir(tree, "apps/web")

        assert excinfo.value.field == "build.base_directory"
        assert "link" in excinfo.value.message

    def test_a_link_in_an_earlier_component_is_not_followed_either(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        (outside / "web").mkdir(parents=True)
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "apps").symlink_to(outside)

        with pytest.raises(ValidationError):
            project_dir(tree, "apps/web")

    def test_a_missing_directory_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError) as excinfo:
            project_dir(tmp_path, "apps/web")

        assert "not a directory" in excinfo.value.message

    def test_a_file_is_not_a_directory(self, tmp_path: Path) -> None:
        (tmp_path / "apps").write_text("x")

        with pytest.raises(ValidationError):
            project_dir(tmp_path, "apps")


class TestPrecedence:
    def test_the_repositorys_block_applies_without_the_operators(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        root = write(tmp_path / "tree", FULL)

        build = resolve_build(store.get_app(DOMAIN), root, store)

        assert build.source == "repo"
        assert build.base_directory == "apps/web"

    def test_the_operators_block_replaces_the_repositorys_whole(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        root = write(tmp_path / "tree", FULL)
        set_operator_build(DOMAIN, "build:\n  start: node other.js\n", actor="ops", store=store)

        build = resolve_build(store.get_app(DOMAIN), root, store)

        assert build.source == "operator"
        assert build.start == ("node", "other.js")
        # Not merged: what only the repository said does not apply.
        assert build.base_directory is None
        assert build.install is None
        assert build.runtime is None

    def test_an_operators_empty_block_still_wins(self, tmp_path: Path, store: NoustStore) -> None:
        root = write(tmp_path / "tree", FULL)
        set_operator_build(DOMAIN, "build: {}\n", actor="ops", store=store)

        build = resolve_build(store.get_app(DOMAIN), root, store)

        assert build.source == "operator"
        assert not build.declared

    def test_an_application_not_registered_yet_has_only_the_repositorys(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        root = write(tmp_path / "tree", FULL)

        assert resolve_build(None, root, store).source == "repo"

    def test_a_broken_document_that_applies_is_an_error_naming_the_field(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        root = write(tmp_path / "tree", 'build:\n  install: "make \'unclosed"\n')

        with pytest.raises(ValidationError) as excinfo:
            resolve_build(store.get_app(DOMAIN), root, store)

        assert excinfo.value.field == "build.install"


class TestTheOperatorsBlock:
    def test_setting_it_validates_stores_the_canonical_text_and_audits(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core import audit

        recorded: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            audit, "record", lambda event, **kwargs: recorded.append((event, kwargs))
        )

        build = set_operator_build(
            DOMAIN,
            "# a comment\nbuild:\n    start:   node   server.js\n    runtime: node@22\n",
            actor="ops",
            store=store,
        )

        assert build.start == ("node", "server.js")
        assert app_build_store.get_document(DOMAIN, store=store) == (
            "build:\n  start: node server.js\n  runtime: node@22\n"
        )
        assert recorded[0][0] == "apps.build"
        assert recorded[0][1]["target"] == f"app:{DOMAIN}"
        assert recorded[0][1]["details"]["action"] == "set"
        assert recorded[0][1]["details"]["start"] == "node server.js"

        cleared = set_operator_build(DOMAIN, None, actor="ops", store=store)

        assert not cleared.declared
        assert app_build_store.get_document(DOMAIN, store=store) is None
        assert recorded[1][1]["details"]["action"] == "clear"

    def test_an_invalid_document_stores_nothing(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError):
            set_operator_build(DOMAIN, "build:\n  base_directory: ..\n", actor="ops", store=store)

        assert app_build_store.get_document(DOMAIN, store=store) is None

    def test_an_unknown_application_is_an_error(self, store: NoustStore) -> None:
        with pytest.raises(NoustError):
            set_operator_build("nope.example.com", None, actor="ops", store=store)

    def test_a_key_the_type_cannot_honour_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError) as excinfo:
            set_operator_build(
                DOMAIN, "build:\n  publish_directory: dist\n", actor="ops", store=store
            )

        assert excinfo.value.field == "build.publish_directory"
        assert app_build_store.get_document(DOMAIN, store=store) is None

    def test_a_runtime_of_another_language_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError) as excinfo:
            set_operator_build(DOMAIN, "build:\n  runtime: python@3.12\n", actor="ops", store=store)

        assert excinfo.value.field == "build.runtime"

    def test_describing_says_where_the_block_comes_from(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        app = store.get_app(DOMAIN)
        assert app is not None
        write(Path(app.app_path), FULL)
        # The repository's block is for a type that cannot publish: the
        # describing does not judge it, the next deployment would.
        assert describe_build(DOMAIN, store=store).build.source == "repo"

        set_operator_build(DOMAIN, "build:\n  start: node s.js\n", actor="ops", store=store)
        described = describe_build(DOMAIN, store=store)

        assert described.build.source == "operator"
        assert described.document == "build:\n  start: node s.js\n"
        assert described.to_dict()["start"] == "node s.js"

    def test_a_repository_block_that_does_not_parse_is_reported_not_raised(
        self, store: NoustStore
    ) -> None:
        app = store.get_app(DOMAIN)
        assert app is not None
        write(Path(app.app_path), "build:\n  base_directory: /etc\n")

        described = describe_build(DOMAIN, store=store)

        assert described.repository_error is not None
        assert "build.base_directory" in described.repository_error or "base" in (
            described.repository_error
        )
        assert not described.build.declared

    def test_describing_an_unknown_application_is_an_error(self, store: NoustStore) -> None:
        with pytest.raises(NoustError):
            describe_build("nope.example.com", store=store)


class TestPersistence:
    def test_the_operators_block_survives_a_redeploys_full_row_write(
        self, store: NoustStore
    ) -> None:
        set_operator_build(DOMAIN, "build:\n  start: node s.js\n", actor="ops", store=store)
        row = store.get_app(DOMAIN)
        assert row is not None

        # A deploy writes back the row it read when it started.
        row.status = "running"
        store.update_app(row)

        assert app_build_store.get_document(DOMAIN, store=store) is not None

    def test_the_block_goes_with_the_application(self, store: NoustStore) -> None:
        set_operator_build(DOMAIN, "build:\n  start: node s.js\n", actor="ops", store=store)

        store.delete_app(DOMAIN)

        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM app_build")
            assert cursor.fetchone()[0] == 0

    def test_an_unknown_application_has_nothing_to_store(self, store: NoustStore) -> None:
        assert (
            app_build_store.set_document("nope.example.com", "build: {}\n", updated_by="x") is False
        )
        assert app_build_store.set_applied("nope.example.com", {"a": 1}, store=store) is False

    def test_an_oversized_document_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError):
            app_build_store.set_document(
                DOMAIN, "build: {}\n" + "#" * 70_000, updated_by="x", store=store
            )

    def test_what_the_unit_was_written_from_is_kept_apart_from_the_document(
        self, store: NoustStore
    ) -> None:
        set_operator_build(DOMAIN, "build:\n  start: node s.js\n", actor="ops", store=store)
        app_build_store.set_applied(DOMAIN, {"start": "/usr/bin/node s.js"}, store=store)

        # Changing the document does not forget what the unit runs.
        set_operator_build(DOMAIN, "build:\n  start: node t.js\n", actor="ops", store=store)

        assert app_build_store.get_applied(DOMAIN, store=store) == {"start": "/usr/bin/node s.js"}
        set_operator_build(DOMAIN, None, actor="ops", store=store)
        assert app_build_store.get_applied(DOMAIN, store=store) == {"start": "/usr/bin/node s.js"}
        app_build_store.set_applied(DOMAIN, None, store=store)
        assert app_build_store.get_applied(DOMAIN, store=store) is None
        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM app_build")
            assert cursor.fetchone()[0] == 0

    def test_the_users_of_a_runtime_are_found_by_what_their_units_were_written_for(
        self, store: NoustStore
    ) -> None:
        app_build_store.set_applied(
            DOMAIN, {"runtime": {"language": "node", "version": "22.11.0"}}, store=store
        )

        assert app_build_store.runtime_users("node", "22.11.0", store=store) == [DOMAIN]
        assert app_build_store.runtime_users("node", "20.1.0", store=store) == []
        assert app_build_store.runtime_users("python", "22.11.0", store=store) == []


class TestTypesThatCannotHonourTheBlock:
    def test_a_compose_stack_is_not_built_by_noust(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            refuse_unsupported(BuildConfig(start=("x",), source="operator"), "docker-compose")

        assert excinfo.value.field == "build"

    def test_an_empty_block_is_acceptable_everywhere(self) -> None:
        refuse_unsupported(BuildConfig(), "docker-compose")
        refuse_unsupported(BuildConfig(source="operator"), "static")


class TestTheTable:
    """The build lane's fragment of schema v14."""

    @staticmethod
    def _columns(db_path: Path) -> set[str]:
        import sqlite3

        conn = sqlite3.connect(db_path)
        try:
            return {row[1] for row in conn.execute("PRAGMA table_info(app_build)")}
        finally:
            conn.close()

    def test_a_fresh_store_has_it(self, tmp_path: Path) -> None:
        NoustStore.reset_instance()
        try:
            NoustStore(tmp_path / "fresh.db")
        finally:
            NoustStore.reset_instance()

        assert self._columns(tmp_path / "fresh.db") == {
            "app_id",
            "document",
            "applied",
            "updated_by",
            "updated_at",
        }

    def test_a_v13_store_gains_it_and_keeps_its_applications(self, tmp_path: Path) -> None:
        import sqlite3

        db_path = tmp_path / "old.db"
        NoustStore.reset_instance()
        try:
            NoustStore(db_path).create_app(
                App(domain=DOMAIN, app_type="nodejs", source="x", app_path="/opt/app")
            )
        finally:
            NoustStore.reset_instance()
        conn = sqlite3.connect(db_path)
        conn.execute("DROP TABLE app_build")
        conn.execute("UPDATE schema_version SET version = 13 WHERE version > 13")
        conn.commit()
        conn.close()
        assert self._columns(db_path) == set()

        try:
            reopened = NoustStore(db_path)
            assert reopened.get_app(DOMAIN) is not None
        finally:
            NoustStore.reset_instance()

        assert "document" in self._columns(db_path)


class TestComposeDoesNotTakeABuildBlock:
    def test_a_stacks_repository_that_carries_one_is_refused_not_silently_ignored(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        from noust.core.runner import FakeRunner
        from noust.deployers.docker_compose import DockerComposeDeployer

        tree = tmp_path / "stack"
        tree.mkdir()
        (tree / "noust.yaml").write_text("build:\n  start: ./serve\n")
        deployer = DockerComposeDeployer(runner=FakeRunner())
        deployer.configure("stack.example.com", "src", app_path=tree)

        with pytest.raises(ValidationError) as excinfo:
            deployer._resolve_hooks()

        assert excinfo.value.field == "build"
        assert "docker-compose" in excinfo.value.message

    def test_a_stack_with_only_hooks_is_as_it_was(self, tmp_path: Path, store: NoustStore) -> None:
        from noust.core.runner import FakeRunner
        from noust.deployers.docker_compose import DockerComposeDeployer

        tree = tmp_path / "stack"
        tree.mkdir()
        (tree / "noust.yaml").write_text("hooks:\n  pre_deploy:\n    - run: ./m.sh\n")
        deployer = DockerComposeDeployer(runner=FakeRunner())
        deployer.configure("stack.example.com", "src", app_path=tree)

        hooks = deployer._resolve_hooks()

        assert hooks.declared


class TestJoiningACommand:
    @pytest.mark.parametrize(
        "argv",
        [
            ["node", "dist/server.js", "--port", "${PORT}"],
            ["make", "all targets"],
            ["echo", "it's", 'say "hi"', "a\\b", ""],
            ["sh", "-c", "a && b | c > d; e"],
            ["x", "$$", "#not-a-comment", "~", "*", "a=b"],
        ],
    )
    def test_what_is_written_splits_back_into_what_was_given(self, argv: list[str]) -> None:
        import shlex

        from noust.deployers.helpers.build_config import join_argv

        assert shlex.split(join_argv(argv)) == argv

    def test_the_port_variable_is_shown_as_it_was_written(self) -> None:
        from noust.deployers.helpers.build_config import join_argv

        assert join_argv(["node", "s.js", "--port", "${PORT}"]) == "node s.js --port ${PORT}"
