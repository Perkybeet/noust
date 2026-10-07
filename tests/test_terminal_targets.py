"""
Tests for what a terminal, and a one-off command, run.

The plan is where "as whom" and "seeing what" are decided, so each test is a
question about reach: does the application's shell run as the application and
not as root, does it carry the application's secrets and nobody else's, does a
stack's terminal go into the right container, and does the server's shell
exist only when asked for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.runner import SANDBOX_PATH, FakeRunner
from noust.core.store import App, NoustStore
from noust.terminal import targets
from noust.terminal.targets import (
    SHELLS,
    plan_app_exec,
    plan_app_terminal,
    plan_server_terminal,
    validate_command,
    validate_service,
    validate_shell,
)

SECRET = "postgres://shop:hunter2-hunter2@db/shop"


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "apps"


def add_app(store: NoustStore, root: Path, domain: str = "shop.example.com", **fields: Any) -> App:
    path = root / domain.replace(".", "-")
    values: dict[str, Any] = {
        "domain": domain,
        "app_type": "nodejs",
        "app_path": str(path),
        "status": "running",
        "layout": "releases",
        "identity": "noust-app-shop",
    }
    values.update(fields)
    app = App(**values)
    store.create_app(app)
    (path / "shared").mkdir(parents=True, exist_ok=True)
    (path / "current").mkdir(parents=True, exist_ok=True)
    return store.get_app(domain) or app


@pytest.fixture(autouse=True)
def no_unit(monkeypatch):
    """The application's unit is not read from a machine that has none."""
    monkeypatch.setattr(
        targets, "_unit_variables", lambda app: {"PORT": "3001", "PATH": "/opt/node/bin:/usr/bin"}
    )


class TestValidation:
    def test_a_shell_is_a_name_from_a_short_list(self):
        assert validate_shell(None) is None
        assert validate_shell("") is None
        assert all(validate_shell(shell) == shell for shell in SHELLS)

    @pytest.mark.parametrize(
        "bad", ["/bin/bash", "bash -c id", "python3", "../sh", "BASH", "sh;id"]
    )
    def test_anything_else_is_refused(self, bad):
        with pytest.raises(ValidationError) as refused:
            validate_shell(bad)

        assert refused.value.field == "shell"

    def test_a_service_is_a_compose_name(self):
        assert validate_service(None) is None
        assert validate_service("web-1") == "web-1"
        for bad in ("web 1", "-web", "a;b", "../x", "x" * 80):
            with pytest.raises(ValidationError):
                validate_service(bad)

    def test_a_command_is_a_list_of_text_arguments(self):
        assert validate_command(["rails", "console"]) == ("rails", "console")

    @pytest.mark.parametrize(
        "bad",
        [[], [""], ["  "], ["ls", "a\x00b"], ["ls", 3], ["x"] * 300, ["x" * 70000], "ls -l"],
    )
    def test_anything_else_is_refused_as_a_command(self, bad):
        with pytest.raises(ValidationError):
            validate_command(bad)  # type: ignore[arg-type]


class TestAnApplicationOnThisServer:
    def test_the_shell_runs_as_the_applications_own_account_in_its_running_code(self, store, root):
        app = add_app(store, root)

        plan = plan_app_terminal(app.domain, service=None, shell_name=None, runner=FakeRunner())

        assert plan.sandbox is not None
        assert plan.sandbox.user == "noust-app-shop"
        assert plan.sandbox.group == "noust-app-shop"
        assert plan.sandbox.interactive is True
        assert plan.cwd == Path(app.app_path) / "current"
        assert plan.sandbox.working_dir == Path(app.app_path) / "current"
        assert plan.target.account == "noust-app-shop"
        assert plan.target.label == app.domain
        assert plan.target.kind == "app"

    def test_it_is_the_regime_of_a_migration_not_a_build_and_never_root(self, store, root):
        app = add_app(store, root)

        plan = plan_app_terminal(app.domain, service=None, shell_name=None, runner=FakeRunner())

        assert plan.sandbox is not None
        assert plan.sandbox.strict is False  # the application's own regime
        assert plan.sandbox.user != "root"
        assert plan.sandbox.tasks_max == targets.TERMINAL_TASKS_MAX

    def test_an_application_without_its_own_account_runs_as_the_shared_one(self, store, root):
        app = add_app(store, root, identity=None)

        plan = plan_app_terminal(app.domain, service=None, shell_name=None, runner=FakeRunner())

        assert plan.sandbox is not None
        assert plan.sandbox.user == "www-data"

    def test_it_reads_the_applications_own_dotenv_and_nobody_elses(self, store, root):
        app = add_app(store, root)
        other = add_app(store, root, "blog.example.com", identity="noust-app-blog")
        (Path(app.app_path) / "shared" / ".env").write_text("A=1\n", encoding="utf-8")
        (Path(other.app_path) / "shared" / ".env").write_text("B=2\n", encoding="utf-8")

        plan = plan_app_terminal(app.domain, service=None, shell_name=None, runner=FakeRunner())

        assert plan.sandbox is not None
        assert plan.sandbox.env_files == (Path(app.app_path) / "shared" / ".env",)

    def test_without_a_dotenv_there_is_no_file_to_read(self, store, root):
        app = add_app(store, root)

        plan = plan_app_terminal(app.domain, service=None, shell_name=None, runner=FakeRunner())

        assert plan.sandbox is not None and plan.sandbox.env_files == ()

    def test_nothing_of_the_consoles_environment_is_inherited(self, store, root):
        app = add_app(store, root)

        plan = plan_app_terminal(app.domain, service=None, shell_name=None, runner=FakeRunner())

        assert plan.sandbox is not None
        assert set(plan.sandbox.env_allow) <= {"LANG", "LC_*", "TZ"}

    def test_the_environment_is_the_units_with_a_terminal_and_a_prompt(self, store, root):
        app = add_app(store, root)

        plan = plan_app_terminal(app.domain, service=None, shell_name=None, runner=FakeRunner())

        assert plan.env["TERM"] == "xterm-256color"
        assert plan.env["PORT"] == "3001"
        assert plan.env["PATH"] == "/opt/node/bin:/usr/bin"
        assert plan.env["PS1"] == "\\u@shop.example.com:\\w\\$ "

    def test_without_a_unit_the_path_is_the_systems(self, store, root, monkeypatch):
        monkeypatch.setattr(targets, "_unit_variables", lambda app: {})
        app = add_app(store, root)

        plan = plan_app_terminal(app.domain, service=None, shell_name=None, runner=FakeRunner())

        assert plan.env["PATH"] == SANDBOX_PATH

    def test_the_shell_is_a_program_not_a_line(self, store, root):
        app = add_app(store, root)

        plan = plan_app_terminal(app.domain, service=None, shell_name="sh", runner=FakeRunner())

        assert len(plan.argv) == 1 and plan.argv[0].endswith("/sh")

    def test_bash_does_not_read_the_startup_files_of_whoever_is_there(self, store, root):
        app = add_app(store, root)

        plan = plan_app_terminal(app.domain, service=None, shell_name="bash", runner=FakeRunner())

        assert plan.argv[1:] == ("--norc", "--noprofile", "-i")

    def test_a_shell_this_server_lacks_is_said_so(self, store, root, monkeypatch):
        monkeypatch.setattr(targets.shutil, "which", lambda *args, **kwargs: None)
        app = add_app(store, root)

        with pytest.raises(DeploymentError, match="no zsh"):
            plan_app_terminal(app.domain, service=None, shell_name="zsh", runner=FakeRunner())

    def test_the_recording_knows_the_applications_secrets(self, store, root):
        app = add_app(store, root, env_vars={"DATABASE_URL": SECRET})

        plan = plan_app_terminal(app.domain, service=None, shell_name=None, runner=FakeRunner())

        assert "hunter2-hunter2" not in plan.scrubber.scrub(f"url={SECRET}")

    def test_a_static_site_has_nothing_to_open_a_terminal_in(self, store, root):
        app = add_app(store, root, app_type="static", is_static=True)

        with pytest.raises(ValidationError, match="static"):
            plan_app_terminal(app.domain, service=None, shell_name=None, runner=FakeRunner())

    def test_an_application_that_does_not_exist_is_not_found(self, store):
        with pytest.raises(ValidationError, match="not found"):
            plan_app_terminal(
                "nope.example.com", service=None, shell_name=None, runner=FakeRunner()
            )

    def test_a_service_means_nothing_to_an_application_that_is_not_a_stack(self, store, root):
        app = add_app(store, root)

        with pytest.raises(ValidationError, match="no service"):
            plan_app_terminal(app.domain, service="web", shell_name=None, runner=FakeRunner())

    def test_a_one_off_command_is_the_same_account_without_a_terminal(self, store, root):
        app = add_app(store, root)

        plan = plan_app_exec(
            app.domain, ["rails", "runner", "puts 1"], service=None, runner=FakeRunner()
        )

        assert plan.argv == ("rails", "runner", "puts 1")
        assert plan.sandbox is not None
        assert plan.sandbox.user == "noust-app-shop"
        assert plan.sandbox.interactive is False
        assert plan.sandbox.strict is False
        assert plan.cwd == Path(app.app_path) / "current"
        assert "PS1" not in plan.env and "TERM" not in plan.env


class TestAStack:
    PS_FIELDS = "{id}|{service}|running\n"

    def stack(self, store, root, **fields):
        return add_app(
            store,
            root,
            app_type="docker-compose",
            identity=None,
            compose_project="shop",
            **fields,
        )

    def runner(self, *containers: tuple[str, str]) -> FakeRunner:
        runner = FakeRunner()
        runner.script(
            ["docker", "ps"],
            stdout="".join(f"{cid}|{svc}|running\n" for cid, svc in containers),
        )
        return runner

    def test_the_terminal_goes_into_the_container_of_the_service_asked_for(self, store, root):
        app = self.stack(store, root)
        runner = self.runner(("aaa111", "web"), ("bbb222", "db"))

        plan = plan_app_terminal(app.domain, service="db", shell_name=None, runner=runner)

        assert plan.argv == (
            "docker",
            "exec",
            "-it",
            "-e",
            "TERM",
            "-e",
            "COLORTERM",
            "bbb222",
            "sh",
        )
        assert plan.target.service == "db"
        assert plan.target.label == "shop.example.com (db)"
        assert plan.target.account == "container"
        assert plan.sandbox is None

    def test_containers_are_found_by_the_project_the_store_records(self, store, root):
        app = self.stack(store, root)
        runner = self.runner(("aaa111", "web"))

        plan_app_terminal(app.domain, service=None, shell_name=None, runner=runner)

        assert runner.calls[0][:4] == (
            "docker",
            "ps",
            "--filter",
            "label=com.docker.compose.project=shop",
        )

    def test_when_the_project_finds_nothing_the_directory_it_runs_from_does(self, store, root):
        app = self.stack(store, root)
        runner = FakeRunner()
        runner.script(
            ["docker", "ps", "--filter", "label=com.docker.compose.project=shop"], stdout=""
        )
        runner.script(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project.working_dir={app.app_path}",
            ],
            stdout="ccc333|web|running\n",
        )

        plan = plan_app_terminal(app.domain, service=None, shell_name=None, runner=runner)

        assert plan.target.container == "ccc333"

    def test_the_only_service_is_chosen_without_being_asked(self, store, root):
        app = self.stack(store, root)

        plan = plan_app_terminal(
            app.domain, service=None, shell_name=None, runner=self.runner(("aaa111", "web"))
        )

        assert plan.target.service == "web"

    def test_several_services_need_one_chosen_and_the_message_lists_them(self, store, root):
        app = self.stack(store, root)

        with pytest.raises(ValidationError) as refused:
            plan_app_terminal(
                app.domain,
                service=None,
                shell_name=None,
                runner=self.runner(("aaa111", "web"), ("bbb222", "db")),
            )

        assert refused.value.field == "service"
        assert "db, web" in str(refused.value.details)

    def test_a_service_that_is_not_running_is_refused(self, store, root):
        app = self.stack(store, root)

        with pytest.raises(ValidationError, match="no running service named 'worker'"):
            plan_app_terminal(
                app.domain, service="worker", shell_name=None, runner=self.runner(("aaa111", "web"))
            )

    def test_a_stack_that_is_not_running_says_how_to_start_it(self, store, root):
        app = self.stack(store, root)

        with pytest.raises(DeploymentError) as refused:
            plan_app_terminal(app.domain, service=None, shell_name=None, runner=self.runner())

        assert "service start" in str(refused.value.details)

    def test_docker_failing_is_said_in_dockers_words(self, store, root):
        app = self.stack(store, root)
        runner = FakeRunner().script(
            ["docker", "ps"], exit_code=1, stderr="Cannot connect to the Docker daemon"
        )

        with pytest.raises(DeploymentError) as refused:
            plan_app_terminal(app.domain, service=None, shell_name=None, runner=runner)

        assert "Cannot connect" in str(refused.value.details)

    def test_a_server_without_docker_says_so(self, store, root):
        app = self.stack(store, root)

        with pytest.raises(DeploymentError, match="Docker is not installed"):
            plan_app_terminal(
                app.domain, service=None, shell_name=None, runner=FakeRunner().only_knows("ls")
            )

    def test_a_container_application_goes_into_its_app_service(self, store, root):
        app = add_app(
            store, root, app_type="container", identity=None, compose_project="noust-shop"
        )
        runner = self.runner(("aaa111", "app"), ("bbb222", "sidecar"))

        plan = plan_app_terminal(app.domain, service=None, shell_name="bash", runner=runner)

        assert plan.target.service == "app"
        assert plan.argv[-1] == "bash"

    def test_a_one_off_command_in_a_stack_is_docker_exec_without_a_terminal(self, store, root):
        app = self.stack(store, root)
        runner = self.runner(("aaa111", "web"))

        plan = plan_app_exec(app.domain, ["rails", "console"], service=None, runner=runner)

        assert plan.argv == ("docker", "exec", "aaa111", "rails", "console")
        assert plan.sandbox is None
        assert "-t" not in plan.argv and "-it" not in plan.argv

    def test_the_command_is_not_given_to_a_shell_in_the_container_either(self, store, root):
        app = self.stack(store, root)
        runner = self.runner(("aaa111", "web"))

        plan = plan_app_exec(app.domain, ["echo", "a; rm -rf /"], service=None, runner=runner)

        assert plan.argv[-2:] == ("echo", "a; rm -rf /")
        assert "sh" not in plan.argv and "-c" not in plan.argv


class TestTheServer:
    def test_it_is_a_login_shell_as_root_from_roots_home(self):
        plan = plan_server_terminal(shell_name=None)

        assert plan.target.kind == "server"
        assert plan.target.account == "root"
        assert plan.target.domain is None
        assert plan.sandbox is None  # nothing confines root: that is the point and the danger
        assert plan.cwd == Path("/root")
        assert plan.argv[0].endswith("/bash") and plan.argv[1:] == ("-l",)
        assert plan.env["HOME"] == "/root" and plan.env["USER"] == "root"

    def test_it_starts_from_nothing_of_the_consoles_environment(self):
        plan = plan_server_terminal(shell_name=None)

        assert set(plan.env) == {
            "TERM",
            "COLORTERM",
            "LANG",
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "PATH",
        }

    def test_the_shell_is_chosen_by_name_only(self):
        assert plan_server_terminal(shell_name="sh").argv[0].endswith("/sh")
        with pytest.raises(ValidationError):
            plan_server_terminal(shell_name="/bin/evil")

    def test_the_recording_of_it_knows_noust_own_credentials(self, monkeypatch):
        monkeypatch.setattr(targets, "known_credentials", lambda: ["smtp-password-value"])

        plan = plan_server_terminal(shell_name=None)

        assert "smtp-password-value" not in plan.scrubber.scrub("smtp-password-value")
