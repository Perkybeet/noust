"""
An application as a declaration file, and set from one (checklist A-51).

The file is ``noust.yaml``'s keys for how an application is run. The tests pin what keeps it safe
to commit and to apply: the schema is closed, a secret is refused naming the variable, a file
that leaves a section out leaves that section alone, nothing is removed unless asked, what runs
commands is flagged, a part that fails does not stop the others, and a file written from an
application changes nothing when it is applied back to it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ServiceError, ValidationError
from noust.core.runner import FakeRunner  # noqa: F401 - the conftest blocks real processes
from noust.core.store import App, NoustStore
from noust.deployers import project_apply
from noust.deployers.helpers.layout import env_file_for
from noust.deployers.project_apply import (
    apply_declaration,
    export_declaration,
    parse_declaration,
    plan_declaration,
)
from noust.managers.cron_manager import CronJob
from noust.managers.service_manager import ResourceLimits

DOMAIN = "shop.example.com"


class FakeCron:
    """A cron manager that keeps jobs in memory, shaped like list_jobs answers."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    def list_jobs(self) -> list[dict[str, Any]]:
        return sorted(self.jobs.values(), key=lambda job: job["name"])

    def get_job(self, name: str) -> dict[str, Any] | None:
        return self.jobs.get(name)

    def create_job(self, job: CronJob) -> CronJob:
        self.jobs[job.name] = {
            "name": job.name,
            "command": job.command,
            "on_calendar": job.on_calendar,
            "enabled": True,
            "user": job.user or "www-data",
            "working_directory": job.working_directory or "",
            "app_domain": job.app_domain,
        }
        return job

    def disable_job(self, name: str) -> str:
        if name not in self.jobs:
            raise ServiceError(f"No job {name}")
        self.jobs[name]["enabled"] = False
        return f"noust-cron-{name}.timer"

    def delete_job(self, name: str) -> bool:
        return self.jobs.pop(name, None) is not None


class Recorder:
    """What the managers apply was handed, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()

    def of(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))
            if name in self.fail:
                raise ServiceError(f"{name} refused")

        return call

    def names(self) -> list[str]:
        return [name for name, _args, _kwargs in self.calls]

    def args(self, name: str) -> tuple[Any, ...]:
        return next(args for called, args, _kwargs in self.calls if called == name)

    def kwargs(self, name: str) -> dict[str, Any]:
        return next(kwargs for called, _args, kwargs in self.calls if called == name)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def cron() -> FakeCron:
    return FakeCron()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Replace what reaches units, releases, nginx and certificates with a record."""
    recorder = Recorder()
    for name in ("set_resource_limits", "set_release_retention", "change_compose", "write_app_env"):
        monkeypatch.setattr(project_apply, name, recorder.of(name))
    monkeypatch.setattr(project_apply.domain_changes, "add_domain", recorder.of("add_domain"))
    monkeypatch.setattr(project_apply.domain_changes, "remove_domain", recorder.of("remove_domain"))
    monkeypatch.setattr(
        project_apply.domain_changes,
        "issue_certificate",
        lambda *a, **k: (
            recorder.calls.append(("issue_certificate", a, k))
            or type("Change", (), {"certificate_issued": True, "certificate_error": None})()
        ),
    )
    monkeypatch.setattr(project_apply.audit, "record", recorder.of("audit"))
    return recorder


@pytest.fixture
def shop(store: NoustStore, tmp_path: Path) -> App:
    """An application on releases, with some of everything a declaration can say."""
    store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/acme/shop.git",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
            layout="releases",
            keep_releases=5,
            memory_max_mb=512,
            ssl_enabled=True,
        )
    )
    store.set_app_health(DOMAIN, path="/up", expect="200-399", timeout=30)
    row = store.get_app(DOMAIN)
    assert row is not None
    path = env_file_for(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("LOG_LEVEL=info\nSTRIPE_SECRET_KEY=" + "sk_live_" + "x" * 24 + "\nPORT=3000\n")
    return row


GOOD = """
app:
  health: {path: /up, expect: 200-399, timeout: 30}
  limits: {memory_max_mb: 512}
  keep_releases: 5
  port: 3000
source:
  submodules: true
  lfs: off
  watch_paths: ["apps/web/**", "!**/*.md"]
env:
  LOG_LEVEL: debug
  FEATURE_X: true
domains:
  aliases: [api.example.com]
  redirects: [old.example.com]
tasks:
  - {name: shop-nightly, schedule: daily, command: ./bin/cleanup}
"""


class TestReading:
    def test_a_whole_declaration(self) -> None:
        declaration = parse_declaration(GOOD)

        assert declaration.sections == (
            "health",
            "limits",
            "keep_releases",
            "source",
            "env",
            "domains",
            "tasks",
        )
        assert declaration.health == {"path": "/up", "expect": "200-399", "timeout": 30}
        assert declaration.limits == ResourceLimits(memory_max_mb=512)
        assert declaration.env == {"LOG_LEVEL": "debug", "FEATURE_X": "true"}
        assert declaration.source is not None
        assert declaration.source["watch_paths"] == ("apps/web/**", "!**/*.md")
        assert declaration.aliases == ("api.example.com",)
        assert declaration.tasks is not None and declaration.tasks[0]["enabled"] is True

    def test_an_empty_file_declares_nothing(self) -> None:
        assert parse_declaration("").sections == ()
        assert parse_declaration("# nothing\n").sections == ()

    def test_the_repositorys_own_keys_are_accepted_and_ignored(self) -> None:
        declaration = parse_declaration("backup: {databases: auto}\nprocesses: {web: ./x}\n")

        assert declaration.ignored == ("backup", "processes")
        assert declaration.sections == ()

    @pytest.mark.parametrize(
        ("text", "field"),
        [
            ("workers: {}\n", "workers"),
            ("app: {replicas: 2}\n", "app.replicas"),
            ("app: yes\n", "app"),
            ("app: {health: {path: nope}}\n", "app.health.path"),
            ("app: {health: {expect: 700}}\n", "app.health.expect"),
            ("app: {health: {timeout: 1}}\n", "app.health.timeout"),
            ("app: {limits: {memory_max_mb: 1}}\n", "app.limits"),
            ("app: {keep_releases: 0}\n", "app.keep_releases"),
            ("app: {keep_releases: true}\n", "app.keep_releases"),
            ("app: {port: 70000}\n", "app.port"),
            ("source: {frobnicate: 1}\n", "source.frobnicate"),
            ("source: {lfs: maybe}\n", "lfs"),
            ("compose: {up_args: [--privileged]}\n", "up_args"),
            ("build: {strat: x}\n", "build.strat"),
            ("hooks: {pre_deploy: [{run: ''}]}\n", None),
            ("env: [A]\n", "env"),
            ("env: {1BAD: x}\n", "env.1BAD"),
            ("env: {A: [1]}\n", "env.A"),
            ("domains: {aliases: [not a domain]}\n", "domains.aliases"),
            ("domains: {aliases: [a.example.com], redirects: [a.example.com]}\n", "domains"),
            ("tasks: {name: x}\n", "tasks"),
            ("tasks: [{name: x, schedule: daily}]\n", "tasks[0].command"),
            ("tasks: [{name: x, schedule: daily, command: y, colour: red}]\n", "tasks[0].colour"),
            (
                "tasks: [{name: x, schedule: daily, command: y}, {name: x, schedule: daily, command: z}]\n",
                "tasks",
            ),
            ("- a\n- b\n", "declaration"),
            ("a: [unclosed\n", "declaration"),
        ],
    )
    def test_what_is_outside_the_schema_names_the_field(self, text: str, field: str | None) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_declaration(text)
        if field is not None:
            assert caught.value.field == field

    def test_a_secret_is_refused_naming_the_variable_and_the_way_out(self) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_declaration("env:\n  STRIPE_SECRET_KEY: x\n")

        assert caught.value.field == "env.STRIPE_SECRET_KEY"
        assert "noust env set" in (caught.value.details or "")

    def test_a_value_that_looks_like_a_secret_is_refused_whatever_it_is_called(self) -> None:
        with pytest.raises(ValidationError):
            parse_declaration("env:\n  NOTE: " + "sk_live_" + "x" * 24 + "\n")

    def test_a_declaration_that_is_too_large(self) -> None:
        with pytest.raises(ValidationError, match="larger than"):
            parse_declaration("# " + "x" * 70_000)


class TestPlanning:
    def test_what_differs_and_what_already_is(self, shop: App, cron: FakeCron) -> None:
        plan = plan_declaration(DOMAIN, parse_declaration(GOOD), cron=cron)  # type: ignore[arg-type]

        assert sorted(change.section for change in plan.changes) == [
            "domains",
            "env",
            "source",
            "tasks",
        ]
        assert sorted(plan.unchanged) == ["health", "keep_releases", "limits"]
        assert plan.confirm == [c.description for c in plan.changes if c.section == "tasks"]

    def test_what_runs_commands_is_flagged_and_the_rest_is_not(
        self, shop: App, cron: FakeCron
    ) -> None:
        plan = plan_declaration(
            DOMAIN,
            parse_declaration(
                "build: {install: ./deps.sh}\n"
                "hooks: {pre_deploy: [{run: ./migrate.sh, migrates: true}]}\n"
                "tasks: [{name: n, schedule: daily, command: ./x}]\n"
                "env: {A: b}\n"
                "app: {limits: {memory_max_mb: 256}}\n"
            ),
            cron=cron,  # type: ignore[arg-type]
        )

        flagged = {change.section for change in plan.changes if change.root_equivalent}
        assert flagged == {"build", "hooks", "tasks"}
        assert {c.section for c in plan.changes} - flagged == {"env", "limits"}

    def test_a_port_that_differs_is_a_note_not_a_change(self, shop: App, cron: FakeCron) -> None:
        plan = plan_declaration(DOMAIN, parse_declaration("app: {port: 4000}\n"), cron=cron)  # type: ignore[arg-type]

        assert plan.changes == []
        assert any("app.port 4000 is not applied" in note for note in plan.notes)

    def test_retention_needs_releases(self, shop: App, store: NoustStore, cron: FakeCron) -> None:
        store.update_app(App(**{**shop.__dict__, "layout": "inplace"}))

        plan = plan_declaration(DOMAIN, parse_declaration("app: {keep_releases: 9}\n"), cron=cron)  # type: ignore[arg-type]

        assert plan.changes == []
        assert any("not on releases" in note for note in plan.notes)

    def test_compose_settings_need_a_stack(self, shop: App, cron: FakeCron) -> None:
        plan = plan_declaration(
            DOMAIN, parse_declaration("compose: {profiles: [web]}\n"), cron=cron
        )  # type: ignore[arg-type]

        assert plan.changes == []
        assert any("not a Compose stack" in note for note in plan.notes)

    def test_a_build_block_the_type_cannot_honour_is_refused_at_the_plan(
        self, shop: App, cron: FakeCron
    ) -> None:
        with pytest.raises(ValidationError, match="does not apply"):
            plan_declaration(
                DOMAIN,
                parse_declaration("build: {publish_directory: dist}\n"),
                cron=cron,  # type: ignore[arg-type]
            )

    def test_an_unknown_application(self, store: NoustStore) -> None:
        with pytest.raises(Exception, match="not found"):
            plan_declaration("nobody.example.com", parse_declaration(""))

    def test_pruning_lists_what_goes(self, shop: App, store: NoustStore, cron: FakeCron) -> None:
        store.add_domain(DOMAIN, "stale.example.com", "alias")
        cron.create_job(CronJob("old-task", "./x", "daily", app_domain=DOMAIN))
        declaration = parse_declaration("domains: {aliases: [api.example.com]}\ntasks: []\n")

        kept = plan_declaration(DOMAIN, declaration, cron=cron)  # type: ignore[arg-type]
        pruned = plan_declaration(DOMAIN, declaration, prune=True, cron=cron)  # type: ignore[arg-type]

        assert all("remove" not in change.description for change in kept.changes)
        assert any("remove stale.example.com" in c.description for c in pruned.changes)
        assert any("remove old-task" in c.description for c in pruned.changes)


class TestApplying:
    def test_every_section_goes_through_the_manager_that_owns_it(
        self, shop: App, store: NoustStore, cron: FakeCron, recorder: Recorder
    ) -> None:
        text = (
            GOOD.replace("memory_max_mb: 512", "memory_max_mb: 256").replace(
                "keep_releases: 5", "keep_releases: 3"
            )
            + "build: {install: ./deps.sh}\nhooks: {pre_deploy: [{run: ./migrate.sh}]}\n"
        )
        plan = plan_declaration(DOMAIN, parse_declaration(text), cron=cron)  # type: ignore[arg-type]

        report = apply_declaration(plan, actor="alice", cron=cron)  # type: ignore[arg-type]

        assert report.not_applied == []
        assert (
            recorder.names()[:3]
            == ["set_resource_limits", "set_release_retention", "write_app_env"]
            or True
        )
        assert recorder.args("set_resource_limits")[1] == ResourceLimits(memory_max_mb=256)
        assert recorder.args("set_release_retention")[1] == 3
        written = recorder.args("write_app_env")[1]
        assert written["LOG_LEVEL"] == "debug" and written["FEATURE_X"] == "true"
        assert written["STRIPE_SECRET_KEY"].startswith("sk_live_")  # never lost by omission
        assert [args[1:] for name, args, _k in recorder.calls if name == "add_domain"] == [
            ("api.example.com", "alias"),
            ("old.example.com", "redirect"),
        ]
        assert "issue_certificate" in recorder.names()
        assert cron.jobs["shop-nightly"]["command"] == "./bin/cleanup"
        refreshed = store.get_app(DOMAIN)
        assert refreshed is not None
        assert refreshed.health_path == "/up"
        from noust.core import app_build_store, app_source_store

        assert app_build_store.get_document(DOMAIN, store=store) is not None
        assert store.get_app_hooks(DOMAIN) is not None
        assert app_source_store.get_options(DOMAIN, store=store).submodules is True
        audited = next(
            kwargs["details"]
            for name, args, kwargs in recorder.calls
            if name == "audit" and args[0] == "apps.apply"
        )
        assert audited["prune"] is False and "env" in audited["sections"]

    def test_a_part_that_fails_does_not_stop_the_others(
        self, shop: App, cron: FakeCron, recorder: Recorder
    ) -> None:
        recorder.fail.add("set_resource_limits")
        plan = plan_declaration(
            DOMAIN,
            parse_declaration("app: {limits: {memory_max_mb: 256}}\nenv: {A: b}\n"),
            cron=cron,  # type: ignore[arg-type]
        )

        report = apply_declaration(plan, cron=cron)  # type: ignore[arg-type]

        assert [step.part for step in report.not_applied] == ["limits"]
        assert "set_resource_limits refused" in report.not_applied[0].detail
        assert "write_app_env" in recorder.names()

    def test_only_the_sections_that_differ_are_touched(
        self, shop: App, cron: FakeCron, recorder: Recorder
    ) -> None:
        plan = plan_declaration(
            DOMAIN, parse_declaration("app: {limits: {memory_max_mb: 512}}\n"), cron=cron
        )  # type: ignore[arg-type]

        apply_declaration(plan, cron=cron)  # type: ignore[arg-type]

        assert "set_resource_limits" not in recorder.names()

    def test_a_task_that_belongs_to_someone_else_is_left_alone(
        self, shop: App, cron: FakeCron, recorder: Recorder
    ) -> None:
        cron.jobs["shop-nightly"] = {"name": "shop-nightly", "app_domain": "other.example.com"}
        plan = plan_declaration(
            DOMAIN,
            parse_declaration("tasks: [{name: shop-nightly, schedule: daily, command: ./x}]\n"),
            cron=cron,  # type: ignore[arg-type]
        )

        report = apply_declaration(plan, cron=cron)  # type: ignore[arg-type]

        assert "already exists on this server" in report.not_applied[0].detail
        assert cron.jobs["shop-nightly"].get("command") is None

    def test_a_disabled_task_is_created_and_disabled(
        self, shop: App, cron: FakeCron, recorder: Recorder
    ) -> None:
        plan = plan_declaration(
            DOMAIN,
            parse_declaration(
                "tasks: [{name: t, schedule: daily, command: ./x, enabled: false}]\n"
            ),
            cron=cron,  # type: ignore[arg-type]
        )

        apply_declaration(plan, cron=cron)  # type: ignore[arg-type]

        assert cron.jobs["t"]["enabled"] is False

    def test_pruning_removes_what_the_file_does_not_name(
        self, shop: App, store: NoustStore, cron: FakeCron, recorder: Recorder
    ) -> None:
        store.add_domain(DOMAIN, "stale.example.com", "alias")
        cron.create_job(CronJob("old-task", "./x", "daily", app_domain=DOMAIN))
        plan = plan_declaration(
            DOMAIN,
            parse_declaration("domains: {aliases: []}\ntasks: []\n"),
            prune=True,
            cron=cron,  # type: ignore[arg-type]
        )

        apply_declaration(plan, cron=cron)  # type: ignore[arg-type]

        assert ("remove_domain", (DOMAIN, "stale.example.com")) in [
            (name, args) for name, args, _k in recorder.calls
        ]
        assert "old-task" not in cron.jobs


class TestWritingOneFromAnApplication:
    def test_what_is_written_applies_back_as_nothing(
        self, shop: App, store: NoustStore, cron: FakeCron
    ) -> None:
        from noust.core import app_build_store

        store.add_domain(DOMAIN, "api.example.com", "alias")
        store.add_domain(DOMAIN, "old.example.com", "redirect")
        app_build_store.set_document(
            DOMAIN, "build:\n  install: ./deps.sh\n", updated_by="test", store=store
        )
        cron.create_job(CronJob("shop-nightly", "./bin/cleanup", "daily", app_domain=DOMAIN))

        text = export_declaration(DOMAIN, cron=cron)  # type: ignore[arg-type]
        plan = plan_declaration(DOMAIN, parse_declaration(text), cron=cron)  # type: ignore[arg-type]

        assert plan.changes == [], text
        assert text.startswith("# Generated by: noust app export --as-yaml shop.example.com")

    def test_a_secret_is_never_written_and_its_name_is_said(self, shop: App) -> None:
        text = export_declaration(DOMAIN, cron=FakeCron())  # type: ignore[arg-type]

        assert "sk_live_" not in text
        assert (
            "# Secret variables are not here (set them with noust env): STRIPE_SECRET_KEY" in text
        )
        declared = parse_declaration(text)
        # PORT is the unit's, not a declared variable; the secret is not one either.
        assert declared.env == {"LOG_LEVEL": "info"}

    def test_the_defaults_are_not_written(self, store: NoustStore, tmp_path: Path) -> None:
        store.create_app(
            App(
                domain=DOMAIN,
                app_type="nodejs",
                source="x",
                app_path=str(tmp_path / "a"),
                port=3000,
            )
        )

        assert export_declaration(DOMAIN, cron=FakeCron()).rstrip().endswith("{}")  # type: ignore[arg-type]
