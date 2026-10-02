# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for deploy hooks: which apply, how a phase runs, and what a deployment does with them.

The deployer tests drive a real release pipeline over a temporary tree with
the machine faked (the fixtures of test_release_pipeline.py), so every
assertion is on what a real deploy would run and leave behind.
"""

# The pipeline's fixtures are imported rather than replicated, as in
# test_build_sandbox.py; pytest resolves them by name.
# ruff: noqa: F811

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import CommandResult
from noust.core.store import App, NoustStore
from noust.deployers.helpers.hooks import (
    CommandHookExecutor,
    Hook,
    HookFailedError,
    HookSet,
    prisma_applied_migrations,
    resolve_hooks,
    run_hooks,
    set_operator_hooks,
)
from tests.test_release_pipeline import (  # noqa: F401 - fixtures used by name
    DOMAIN,
    GIT_URL,
    PORT,
    active_id,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    update,
    write_tree,
)


class Scripted:
    """An executor answering from a script, recording what it ran."""

    def __init__(self, *results: CommandResult) -> None:
        self.results = list(results)
        self.ran: list[Hook] = []

    def execute(self, hook: Hook) -> CommandResult:
        self.ran.append(hook)
        return self.results.pop(0)


def ok(stdout: str = "") -> CommandResult:
    return CommandResult(argv=("x",), exit_code=0, stdout=stdout)


def failed(stderr: str, code: int = 1) -> CommandResult:
    return CommandResult(argv=("x",), exit_code=code, stderr=stderr)


HOOKS = HookSet(
    pre_deploy=(
        Hook(run=("./migrate.sh",), migrates=True),
        Hook(run=("./check.sh",)),
    ),
    post_deploy=(Hook(run=("./purge.sh",)),),
    source="repo",
)


class TestRunningAPhase:
    def test_hooks_run_in_order_and_a_migrating_one_marks_the_schema(self) -> None:
        executor = Scripted(ok("Applied 2"), ok())

        outcome = run_hooks("pre_deploy", HOOKS, executor, Logger())

        assert [hook.run for hook in executor.ran] == [("./migrate.sh",), ("./check.sh",)]
        assert outcome.schema_changed is True
        assert [entry["run"] for entry in outcome.ran] == ["./migrate.sh", "./check.sh"]
        assert outcome.ran[0]["output"] == "Applied 2"
        assert all(entry["ok"] for entry in outcome.ran)

    @pytest.mark.parametrize(
        "said",
        [
            'Datasource "db": PostgreSQL database\n\n100 migrations found in prisma/migrations\n\nNo pending migrations to apply.',
            "Operations to perform:\n  Apply all migrations: auth, shop\nRunning migrations:\n  No migrations to apply.",
            "Nothing to migrate.",
            "Already up to date",
            "No migrations were executed, database schema was already up to date.",
            "No migrations are pending",
            "[notice] No migrations to execute.",
            "Schema `public` is up to date. No migration necessary.",
            "no change",
        ],
    )
    def test_a_migrating_hook_that_applied_nothing_changes_no_schema(self, said: str) -> None:
        # Found with Proggest in the harness: every update counted as a schema change, and a
        # failed one said the database had been changed when Prisma had applied nothing.
        executor = Scripted(ok(said), ok())

        outcome = run_hooks("pre_deploy", HOOKS, executor, Logger())

        assert outcome.schema_changed is False

    def test_a_migrating_hook_that_says_both_applied_something(self) -> None:
        executor = Scripted(
            ok("Applying migration `20261002_add_notes`\nNo pending migrations to apply."), ok()
        )

        assert run_hooks("pre_deploy", HOOKS, executor, Logger()).schema_changed is True

    def test_the_first_failure_stops_the_phase_with_its_output_verbatim(self) -> None:
        executor = Scripted(failed('ERROR: relation "users" does not exist', code=3))

        with pytest.raises(HookFailedError) as failure:
            run_hooks("pre_deploy", HOOKS, executor, Logger())

        assert len(executor.ran) == 1
        assert "exited with 3" in failure.value.message
        assert failure.value.output == 'ERROR: relation "users" does not exist'
        assert failure.value.outcome.schema_changed is False
        assert failure.value.outcome.ran[0]["ok"] is False
        assert "still serving" in (failure.value.details or "")

    def test_a_hook_out_of_time_is_a_failure_that_says_so(self) -> None:
        late = CommandResult(argv=("x",), exit_code=-9, timed_out=True)

        with pytest.raises(HookFailedError, match="ran out of its 600 seconds"):
            run_hooks("pre_deploy", HOOKS, Scripted(late), Logger())

    def test_a_migration_that_succeeded_before_a_later_failure_is_kept(self) -> None:
        with pytest.raises(HookFailedError) as failure:
            run_hooks("pre_deploy", HOOKS, Scripted(ok(), failed("no")), Logger())

        assert failure.value.outcome.schema_changed is True

    def test_a_refused_hook_is_a_hook_failure(self) -> None:
        class Refusing:
            def execute(self, hook: Hook) -> CommandResult:
                raise ValidationError("no services here", details="remove it")

        with pytest.raises(HookFailedError, match="could not run: no services here"):
            run_hooks("post_deploy", HOOKS, Refusing(), Logger())

    def test_an_empty_phase_runs_nothing(self) -> None:
        outcome = run_hooks("post_deploy", HookSet(), Scripted(), Logger())

        assert outcome.ran == []
        assert outcome.schema_changed is False


class TestTheExecutorForATree:
    def test_a_hook_runs_its_argv_in_the_tree_with_its_timeout(self, tmp_path: Path) -> None:
        calls: list[tuple[list[str], Path, int]] = []

        def run(argv: list[str], *, cwd: Path, timeout: int) -> CommandResult:
            calls.append((argv, cwd, timeout))
            return ok()

        (tmp_path / "packages" / "api").mkdir(parents=True)
        executor = CommandHookExecutor(run=run, root=tmp_path, app_type="nodejs")

        executor.execute(Hook(run=("sh", "-c", "a | b"), workdir="packages/api", timeout=30))

        assert calls == [(["sh", "-c", "a | b"], (tmp_path / "packages" / "api").resolve(), 30)]

    def test_a_workdir_that_a_link_takes_outside_the_tree_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        tree = tmp_path / "tree"
        tree.mkdir()
        (tree / "escape").symlink_to(outside)
        executor = CommandHookExecutor(
            run=lambda *a, **k: pytest.fail("must not run"), root=tree, app_type="nodejs"
        )

        with pytest.raises(ValidationError) as failure:
            executor.execute(Hook(run=("x",), workdir="escape"))
        assert failure.value.field == "workdir"

    def test_a_service_means_nothing_outside_a_stack(self, tmp_path: Path) -> None:
        executor = CommandHookExecutor(
            run=lambda *a, **k: pytest.fail("must not run"), root=tmp_path, app_type="python"
        )

        with pytest.raises(ValidationError, match="python application has no services"):
            executor.execute(Hook(run=("x",), service="web"))


class TestWhichHooksApply:
    def test_the_repository_s_apply_without_the_operator_s(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write_tree(tmp_path, {"noust.yaml": "hooks:\n  pre_deploy:\n    - run: ./repo.sh\n"})

        hooks = resolve_hooks(None, tmp_path, store)

        assert hooks.source == "repo"
        assert hooks.pre_deploy[0].run == ("./repo.sh",)

    def test_the_operator_s_win_whole(self, tmp_path: Path, store: NoustStore) -> None:
        write_tree(
            tmp_path,
            {
                "noust.yaml": "hooks:\n  pre_deploy:\n    - run: ./repo.sh\n"
                "  post_deploy:\n    - run: ./repo-post.sh\n"
            },
        )
        app = store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path=str(tmp_path)))
        store.set_app_hooks(
            DOMAIN, "hooks:\n  post_deploy:\n    - run: ./mine.sh\n", updated_by="ops"
        )

        hooks = resolve_hooks(app, tmp_path, store)

        assert hooks.source == "operator"
        assert hooks.pre_deploy == ()
        assert [hook.run for hook in hooks.post_deploy] == [("./mine.sh",)]


class TestTheOperatorsHooks:
    def test_setting_them_validates_stores_and_audits(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core import audit

        recorded: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            audit, "record", lambda event, **kwargs: recorded.append((event, kwargs))
        )
        store.create_app(App(domain=DOMAIN, app_type="nodejs"))

        hooks = set_operator_hooks(
            DOMAIN, "hooks:\n  pre_deploy:\n    - run: ./m.sh\n", actor="ops", store=store
        )

        assert hooks.pre_deploy[0].run == ("./m.sh",)
        assert store.get_app_hooks(DOMAIN) == "hooks:\n  pre_deploy:\n    - run: ./m.sh\n"
        [(event, kwargs)] = recorded
        assert event == "apps.hooks"
        assert kwargs["target"] == f"app:{DOMAIN}"
        assert kwargs["details"]["pre_deploy"] == ["./m.sh"]

        set_operator_hooks(DOMAIN, None, actor="ops", store=store)
        assert store.get_app_hooks(DOMAIN) is None

    def test_an_invalid_document_is_refused_before_anything_is_stored(
        self, store: NoustStore
    ) -> None:
        store.create_app(App(domain=DOMAIN, app_type="nodejs"))

        with pytest.raises(ValidationError) as failure:
            set_operator_hooks(
                DOMAIN,
                "hooks:\n  pre_deploy:\n    - run: x\n      timeout: 0\n",
                actor="ops",
                store=store,
            )
        assert failure.value.field == "hooks.pre_deploy[0].timeout"
        assert store.get_app_hooks(DOMAIN) is None

    def test_a_static_site_has_no_hooks_and_is_told_why(self, store: NoustStore) -> None:
        store.create_app(App(domain=DOMAIN, app_type="static", is_static=True))

        with pytest.raises(ValidationError, match="static applications have none") as failure:
            set_operator_hooks(
                DOMAIN, "hooks:\n  pre_deploy:\n    - run: x\n", actor="ops", store=store
            )
        assert "files the web server serves" in (failure.value.details or "")

    def test_an_unknown_application_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(NoustError, match="Application not found"):
            set_operator_hooks("nope.example.com", None, actor="ops", store=store)


class TestPrismasOwnWords:
    def test_an_applied_migration_changes_the_schema(self) -> None:
        output = (
            "3 migrations found in prisma/migrations\n\n"
            "Applying migration `20261001_add_invoices`\n\n"
            "All migrations have been successfully applied.\n"
        )

        assert prisma_applied_migrations(output) is True

    def test_no_pending_migrations_changes_nothing(self) -> None:
        output = "3 migrations found in prisma/migrations\n\nNo pending migrations to apply.\n"

        assert prisma_applied_migrations(output) is False

    def test_output_that_says_neither_is_treated_as_a_change(self) -> None:
        assert prisma_applied_migrations("done") is True


# ---------------------------------------------------------------------------
# A deployment with hooks, end to end
# ---------------------------------------------------------------------------

PRE_FAILS = "hooks:\n  pre_deploy:\n    - run: ./migrate.sh --check\n      migrates: true\n"
POST = "hooks:\n  post_deploy:\n    - run: ./purge.sh\n"


def hooked_tree(directory: Path, project: str, **files: str) -> Path:
    """A Node project with a noust.yaml."""
    node_tree(directory)
    return write_tree(directory, {"noust.yaml": project, **files})


def ran(machine: SimpleNamespace, program: str) -> list[tuple[str, ...]]:
    return [call for call in machine.runner.calls if call and call[0] == program]


class TestADeploymentWithHooks:
    def test_without_a_project_file_nothing_changes(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        machine.git.publish(node_tree(tmp_path / "v2"))

        outcome = update(machine, monkeypatch)

        row = store.list_deployments(DOMAIN)[0]
        assert row.status == "success"
        assert (row.hooks, row.schema_changed, row.warnings) == (None, False, None)
        assert outcome.warnings is None

    def test_a_failing_pre_deploy_hook_leaves_the_previous_release_serving(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        first = active_id(root)
        restarts = len(machine.services.restarts)
        machine.git.publish(hooked_tree(tmp_path / "v2", PRE_FAILS))
        machine.runner.script(
            ["./migrate.sh"], exit_code=2, stderr='ERROR: column "total" already exists'
        )

        with pytest.raises(HookFailedError) as failure:
            update(machine, monkeypatch)

        assert active_id(root) == first
        assert len(machine.services.restarts) == restarts
        assert failure.value.output == 'ERROR: column "total" already exists'
        row = store.list_deployments(DOMAIN)[0]
        assert row.status == "failed"
        assert 'ERROR: column "total" already exists' in (row.error or "")
        [entry] = json.loads(row.hooks or "[]")
        assert entry["ok"] is False and entry["run"] == "./migrate.sh --check"
        assert row.schema_changed is False
        # It ran in the new release, before activation, as the argv itself.
        [(argv, cwd)] = [(c, w) for c, w in machine.runner.where if c[0] == "./migrate.sh"]
        assert argv == ("./migrate.sh", "--check")
        assert cwd is not None and cwd.parent == root / "releases" and cwd.name != first

    def test_a_migrating_hook_that_succeeds_marks_the_schema(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        machine.git.publish(hooked_tree(tmp_path / "v2", PRE_FAILS))

        outcome = update(machine, monkeypatch)

        row = store.list_deployments(DOMAIN)[0]
        assert row.status == "success"
        assert row.schema_changed is True
        assert outcome.schema_changed is True
        assert [entry["run"] for entry in outcome.hooks] == ["./migrate.sh --check"]

    def test_a_failing_post_deploy_hook_deploys_with_warnings(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        machine.git.publish(hooked_tree(tmp_path / "v2", POST))
        machine.runner.script(["./purge.sh"], exit_code=1, stderr="cdn: 403 Forbidden")

        outcome = update(machine, monkeypatch)

        new = active_id(root)
        assert outcome.active is True
        row = store.list_deployments(DOMAIN)[0]
        assert row.status == "success"
        assert row.release_id == new
        assert "./purge.sh" in (row.warnings or "")
        assert outcome.warnings is not None and "exited with 1" in outcome.warnings
        # After activation: the restart of the new release came first.
        purge = next(i for i, (c, _) in enumerate(machine.runner.where) if c[0] == "./purge.sh")
        assert purge > 0
        assert machine.services.restarts[-1] == f"releases/{new}"

    def test_a_failing_post_deploy_hook_is_announced_as_deploy_hook_failed(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.core import notifier
        from tests.notifications_support import context

        built: list[Any] = []
        monkeypatch.setattr(
            notifier, "notify_composed", lambda build: built.append(build(context("en")))
        )
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        machine.git.publish(hooked_tree(tmp_path / "v2", POST))
        machine.runner.script(["./purge.sh"], exit_code=1, stderr="cdn: 403 Forbidden")

        outcome = update(machine, monkeypatch)

        [notification] = built
        assert notification.kind == "deploy_hook_failed"
        assert notification.code == "deploy.hook_failed"
        assert notification.excerpt is not None
        assert "cdn: 403 Forbidden" in notification.excerpt.lines
        assert notification.console_link is not None
        assert notification.console_link.url.endswith(f"/deployments/{outcome.deployment_id}")

    def test_in_place_post_deploy_hooks_run_after_a_restart_behind_the_gate(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.deployers import lifecycle
        from noust.deployers.nodejs import NodeJSDeployer
        from tests.test_release_pipeline import wire

        hooked_tree(root, POST)
        (root / ".git").mkdir()
        store.create_app(
            App(domain=DOMAIN, app_type="nodejs", source=GIT_URL, port=PORT, app_path=str(root))
        )
        order: list[str] = []
        monkeypatch.setattr(
            lifecycle,
            "RollbackManager",
            lambda verbose=False: SimpleNamespace(create_pre_deploy_backup=lambda **kw: None),
        )
        monkeypatch.setattr(
            lifecycle,
            "SourceManager",
            lambda verbose=False: SimpleNamespace(
                pull=lambda path, branch=None: None,
                get_repo_info=lambda path: {"branch": "main", "detached": False, "commit": None},
            ),
        )
        monkeypatch.setattr(
            lifecycle,
            "ServiceManager",
            lambda verbose=False: pytest.fail("the deployer restarts behind its own gate"),
        )
        monkeypatch.setattr(
            machine.services, "restart", lambda name: order.append(f"restart {name}")
        )
        original = machine.runner.run

        def run(argv: Any, **kwargs: Any) -> Any:
            if argv and argv[0] == "./purge.sh":
                order.append("purge")
            return original(argv, **kwargs)

        monkeypatch.setattr(machine.runner, "run", run)
        monkeypatch.setattr(
            lifecycle,
            "get_deployer",
            lambda app_type, verbose=False: wire(
                NodeJSDeployer(verbose=False, runner=machine.runner), machine
            ),
        )

        outcome = lifecycle.update_app(DOMAIN)

        assert order == [f"restart {root.name}", "purge"]
        assert outcome.restarted == (root.name,)

    def test_the_operators_hooks_replace_the_repositorys(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        store.set_app_hooks(
            DOMAIN, "hooks:\n  pre_deploy:\n    - run: ./mine.sh\n", updated_by="ops"
        )
        machine.git.publish(hooked_tree(tmp_path / "v2", PRE_FAILS))

        update(machine, monkeypatch)

        assert ran(machine, "./mine.sh") == [("./mine.sh",)]
        assert ran(machine, "./migrate.sh") == []

    def test_a_first_deploy_runs_its_hooks_around_the_start(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            hooked_tree(
                tmp_path / "v1",
                "hooks:\n  pre_deploy:\n    - run: ./seed.sh\n  post_deploy:\n    - run: ./warm.sh\n",
            )
        )

        deploy_new(root, machine)

        order = [c[0] for c, _ in machine.runner.where if c[0] in ("./seed.sh", "./warm.sh")]
        assert order == ["./seed.sh", "./warm.sh"]
        row = store.list_deployments(DOMAIN)[0]
        assert [entry["run"] for entry in json.loads(row.hooks or "[]")] == [
            "./seed.sh",
            "./warm.sh",
        ]

    def test_a_static_site_with_hooks_is_refused_and_told_why(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        from noust.deployers.static import StaticDeployer

        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {"index.html": "<p>hi</p>", "noust.yaml": "hooks:\n  pre_deploy:\n    - run: x\n"},
            )
        )

        with pytest.raises(ValidationError, match="static applications have none"):
            deploy_new(root, machine, deployer_class=StaticDeployer)
        assert ran(machine, "x") == []


class TestPrismaAutomatic:
    MIGRATIONS = {
        "prisma/schema.prisma": "datasource db {}\n",
        "prisma/migrations/1_init/migration.sql": "x",
    }

    def prisma_tree(self, directory: Path, project: str | None = None) -> Path:
        node_tree(directory)
        files = dict(self.MIGRATIONS)
        if project is not None:
            files["noust.yaml"] = project
        return write_tree(directory, files)

    def test_a_failing_migration_aborts_the_update(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        first = active_id(root)
        machine.git.publish(self.prisma_tree(tmp_path / "v2"))
        machine.runner.script(
            ["npx", "prisma", "migrate"], exit_code=1, stderr="Error: P3009 failed migrations"
        )

        with pytest.raises(DeploymentError, match="Prisma migrations failed") as failure:
            update(machine, monkeypatch)

        assert active_id(root) == first
        assert failure.value.output == "Error: P3009 failed migrations"
        row = store.list_deployments(DOMAIN)[0]
        assert row.status == "failed"
        assert "P3009" in (row.error or "")

    @pytest.mark.parametrize(
        ("said", "changed"),
        [
            ("No pending migrations to apply.", False),
            (
                "Applying migration `2_invoices`\nAll migrations have been successfully applied.",
                True,
            ),
        ],
    )
    def test_the_schema_is_marked_from_prismas_own_words(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        said: str,
        changed: bool,
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        machine.git.publish(self.prisma_tree(tmp_path / "v2"))
        machine.runner.script(["npx", "prisma", "migrate"], stdout=said)

        outcome = update(machine, monkeypatch)

        row = store.list_deployments(DOMAIN)[0]
        assert row.schema_changed is changed
        assert outcome.schema_changed is changed
        assert outcome.hooks[0]["automatic"] == "prisma"

    def test_declared_hooks_replace_the_automatic_migration(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        machine.git.publish(self.prisma_tree(tmp_path / "v2", POST))

        update(machine, monkeypatch)

        assert not any("migrate" in call for call in machine.runner.calls)
        assert ran(machine, "./purge.sh") == [("./purge.sh",)]


# ---------------------------------------------------------------------------
# noust app hooks
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An empty configuration, and the default runner put back after --dry-run."""
    from noust.core.config import Config
    from noust.core.runner import set_runner

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield
    Config.reset_instance()
    set_runner(None)


def invoke(*args: str, stdin: str | None = None) -> Any:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli

    return CliRunner().invoke(root_cli, list(args), input=stdin)


class TestTheCommands:
    def test_show_says_the_repository_s_apply_and_lists_them(
        self, tmp_path: Path, store: NoustStore, cli_config: Any
    ) -> None:
        tree = write_tree(
            tmp_path / "app",
            {"noust.yaml": "hooks:\n  pre_deploy:\n    - run: ./m.sh\n      migrates: true\n"},
        )
        store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path=str(tree)))

        result = invoke("app", "hooks", "show", DOMAIN, "--json")

        assert result.exit_code == 0, result.output
        shown = json.loads(result.output)
        assert shown["source"] == "repo"
        assert shown["pre_deploy"][0]["run"] == ["./m.sh"]
        assert shown["pre_deploy"][0]["migrates"] is True
        assert shown["document"] is None

    def test_set_from_stdin_then_clear(
        self, tmp_path: Path, store: NoustStore, cli_config: Any
    ) -> None:
        store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path=str(tmp_path)))
        document = "hooks:\n  post_deploy:\n    - run: ./warm.sh\n"

        result = invoke("app", "hooks", "set", DOMAIN, "--file", "-", stdin=document)

        assert result.exit_code == 0, result.output
        assert store.get_app_hooks(DOMAIN) == document
        shown = json.loads(invoke("app", "hooks", "show", DOMAIN, "--json").output)
        assert shown["source"] == "operator"
        assert shown["post_deploy"][0]["run"] == ["./warm.sh"]

        result = invoke("app", "hooks", "clear", DOMAIN)
        assert result.exit_code == 0, result.output
        assert store.get_app_hooks(DOMAIN) is None

    def test_an_invalid_document_is_refused_with_its_field(
        self, tmp_path: Path, store: NoustStore, cli_config: Any
    ) -> None:
        store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path=str(tmp_path)))

        result = invoke(
            "app",
            "hooks",
            "set",
            DOMAIN,
            "--file",
            "-",
            stdin="hooks:\n  pre_deploy:\n    - run: x\n      timeout: 3601\n",
        )

        assert isinstance(result.exception, ValidationError)
        assert result.exception.field == "hooks.pre_deploy[0].timeout"
        assert store.get_app_hooks(DOMAIN) is None


# ---------------------------------------------------------------------------
# /api/apps/{domain}/hooks
# ---------------------------------------------------------------------------


def hooks_client(*, elevated: bool) -> Any:
    """A client for the hooks router alone, authenticated, elevated or not."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from noust.web.api import app_hooks
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(app_hooks.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


class TestTheApi:
    def test_every_route_declares_its_permission_and_writing_is_root_equivalent(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_app_hooks import ROUTES

        assert ROUTES[("PUT", "/api/apps/{domain}/hooks")] == Permission.ROOT_EQUIVALENT
        assert ROUTES[("GET", "/api/apps/{domain}/hooks")] == Permission.APPS_READ

    def test_get_set_and_clear(self, tmp_path: Path, store: NoustStore) -> None:
        store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path=str(tmp_path)))
        client = hooks_client(elevated=True)

        assert client.get(f"/api/apps/{DOMAIN}/hooks").json()["source"] == "none"

        response = client.put(
            f"/api/apps/{DOMAIN}/hooks",
            json={
                "document": "hooks:\n  pre_deploy:\n    - run: ./m.sh --yes\n      migrates: true\n"
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"] == "operator"
        assert body["pre_deploy"] == [
            {
                "run": ["./m.sh", "--yes"],
                "service": None,
                "workdir": None,
                "timeout": 600,
                "migrates": True,
            }
        ]

        cleared = client.delete(f"/api/apps/{DOMAIN}/hooks")
        assert cleared.status_code == 200
        assert cleared.json()["source"] == "none"
        assert store.get_app_hooks(DOMAIN) is None

    def test_writing_needs_sudo_mode(self, tmp_path: Path, store: NoustStore) -> None:
        store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path=str(tmp_path)))

        response = hooks_client(elevated=False).put(
            f"/api/apps/{DOMAIN}/hooks", json={"document": "hooks: {}\n"}
        )

        assert response.status_code == 403
        assert store.get_app_hooks(DOMAIN) is None

    def test_an_invalid_document_is_400_with_its_field(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path=str(tmp_path)))

        response = hooks_client(elevated=True).put(
            f"/api/apps/{DOMAIN}/hooks",
            json={"document": "hooks:\n  pre_deploy:\n    - run: x\n      workdir: /etc\n"},
        )

        assert response.status_code == 400
        assert "hooks.pre_deploy[0].workdir" in response.json().get("fields", {})

    def test_an_unknown_application_is_404(self, store: NoustStore) -> None:
        assert (
            hooks_client(elevated=True).get("/api/apps/nope.example.com/hooks").status_code == 404
        )
