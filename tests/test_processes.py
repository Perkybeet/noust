"""
Process types a repository declares (checklist A-52).

A Procfile or a ``processes:`` block names what a project runs: ``web`` becomes the unit's
command, ``release`` a ``pre_deploy`` hook, every other type is declared and reported. The
tests pin the decisions that protect an application that is already running: a Procfile only
counts for an application that honours it, a Procfile that cannot be read is reported and never
stops a deployment, a line that needs a shell is refused instead of run as arguments, and what
an operator or the repository's own build block says wins whole.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import app_build_store
from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.helpers.hooks import resolve_hooks
from noust.deployers.helpers.processes import (
    MAX_PROCESSES,
    PROCFILE,
    parse_processes_block,
    parse_procfile,
    read_procfile,
    resolve_processes,
)
from noust.deployers.helpers.project_file import load_project_file
from noust.deployers.nodejs import NodeJSDeployer
from noust.deployers.static import StaticDeployer
from noust.deployers.vite import ViteDeployer

DOMAIN = "shop.example.com"
NODE_PACKAGE = '{"name": "x", "scripts": {"start": "node server.js"}}'


def write(root: Path, name: str, text: str = "") -> Path:
    """
    Args:
        root: The project.
        name: A path inside it.
        text: Its content.

    Returns:
        The file.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Returns:
        A store installed as the process-wide singleton.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


class TestProcfile:
    def test_a_procfile(self) -> None:
        found = parse_procfile(
            "# what runs\n"
            "web: ./bin/server --port $PORT\n"
            "\n"
            "release: ./bin/migrate\n"
            "worker: ./bin/jobs --queues=default,mailers\n"
        )

        assert [process.name for process in found] == ["web", "release", "worker"]
        assert found.source == "Procfile"
        web = found.named("web")
        assert web is not None and web.argv == ("./bin/server", "--port", "$PORT")
        assert found.named("clock") is None
        assert [p.started_by_noust for p in found] == [True, True, False]

    def test_quotes_are_the_shells_and_nothing_more(self) -> None:
        found = parse_procfile("worker: ./bin/jobs --name 'two words' \"x y\"\n")
        worker = found.named("worker")
        assert worker is not None
        assert worker.argv == ("./bin/jobs", "--name", "two words", "x y")

    def test_leading_assignments_run_through_env(self) -> None:
        found = parse_procfile("web: RACK_ENV=production WORKERS=3 bundle exec puma\n")
        web = found.named("web")
        assert web is not None
        assert web.argv == ("env", "RACK_ENV=production", "WORKERS=3", "bundle", "exec", "puma")

    def test_assignments_alone_run_nothing(self) -> None:
        with pytest.raises(ValidationError, match="sets variables and runs nothing"):
            parse_procfile("web: A=1 B=2\n")

    @pytest.mark.parametrize(
        "line",
        [
            "web: ./start && ./other",
            "web: ./start | tee log",
            "web: ./start > /tmp/log",
            "web: ./start; ./other",
            "web: ./start $(whoami)",
            "web: ./start `id`",
            "web: ./start 2>&1",
            "web: ./start &",
        ],
    )
    def test_a_line_that_needs_a_shell_is_refused_with_the_way_out(self, line: str) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_procfile(line + "\n")
        assert "needs a shell" in caught.value.message
        assert "script" in (caught.value.details or "")

    @pytest.mark.parametrize(
        "text",
        [
            "web ./start\n",
            "web:\n",
            "Web: ./start\n",
            "1web: ./start\n",
            "web: ./a\nweb: ./b\n",
            "web: ./a 'unclosed\n",
        ],
    )
    def test_malformed_files(self, text: str) -> None:
        with pytest.raises(ValidationError):
            parse_procfile(text)

    def test_too_many_processes(self) -> None:
        text = "".join(f"p{index}: ./run\n" for index in range(MAX_PROCESSES + 1))
        with pytest.raises(ValidationError, match="more than"):
            parse_procfile(text)

    def test_an_empty_file_declares_nothing(self) -> None:
        found = parse_procfile("# nothing\n\n")
        assert found.source == "none" and not found.declared


class TestProcessesBlock:
    def test_text_lists_and_the_long_form(self) -> None:
        found = parse_processes_block(
            {
                "web": "./bin/server --port ${PORT}",
                "release": ["./bin/migrate", "--yes"],
                "worker": {"run": "./bin/jobs"},
            }
        )

        assert found.source == "noust.yaml"
        assert found.named("web").argv == ("./bin/server", "--port", "${PORT}")  # type: ignore[union-attr]
        assert found.named("release").argv == ("./bin/migrate", "--yes")  # type: ignore[union-attr]
        assert found.named("worker").argv == ("./bin/jobs",)  # type: ignore[union-attr]

    @pytest.mark.parametrize(
        ("value", "field"),
        [
            ("web: ./x", "processes"),
            ({"Web": "./x"}, "processes.Web"),
            ({"web": {"run": "./x", "scale": 2}}, "processes.web.scale"),
            ({"web": 3}, "processes.web"),
            ({"web": ""}, "processes.web"),
            ({"web": "./x && ./y"}, "processes.web"),
            ({"web": "../outside"}, None),
        ],
    )
    def test_what_is_refused_names_the_field(self, value: object, field: str | None) -> None:
        if field is None:
            # A program that climbs is an ordinary relative path to run: no refusal.
            assert parse_processes_block(value).named("web") is not None
            return
        with pytest.raises(ValidationError) as caught:
            parse_processes_block(value)
        assert caught.value.field == field

    def test_nothing_is_nothing(self) -> None:
        assert not parse_processes_block(None).declared


class TestWhereTheyAreRead:
    def test_noust_yaml_wins_over_the_procfile_whole(self, tmp_path: Path) -> None:
        write(tmp_path, PROCFILE, "web: ./from-procfile\nworker: ./jobs\n")
        write(tmp_path, "noust.yaml", "processes:\n  web: ./from-yaml\n")

        found = resolve_processes(tmp_path)

        assert found.source == "noust.yaml"
        assert [process.name for process in found] == ["web"]

    def test_the_procfile_when_noust_yaml_declares_none(self, tmp_path: Path) -> None:
        write(tmp_path, PROCFILE, "web: ./from-procfile\n")
        write(tmp_path, "noust.yaml", "build:\n  install: ./deps.sh\n")

        assert resolve_processes(tmp_path).source == "Procfile"

    def test_nothing_declared(self, tmp_path: Path) -> None:
        assert resolve_processes(tmp_path).source == "none"

    def test_a_procfile_that_cannot_be_used_is_reported_not_raised(self, tmp_path: Path) -> None:
        write(tmp_path, PROCFILE, "web: ./a && ./b\n")

        found = resolve_processes(tmp_path)

        assert not found.declared
        assert "needs a shell" in found.problem

    def test_a_procfile_that_is_a_link_is_not_read(self, tmp_path: Path) -> None:
        target = write(tmp_path / "elsewhere", "secret", "web: ./leak\n")
        (tmp_path / PROCFILE).symlink_to(target)

        found = read_procfile(tmp_path)

        assert not found.declared
        assert "link" in found.problem

    def test_an_oversized_procfile_is_not_read(self, tmp_path: Path) -> None:
        write(tmp_path, PROCFILE, "web: ./x\n" + "# padding\n" * 4000)
        assert "larger than" in read_procfile(tmp_path).problem

    def test_a_noust_yaml_block_that_is_not_valid_is_an_error(self, tmp_path: Path) -> None:
        write(tmp_path, "noust.yaml", "processes:\n  web: ./a | ./b\n")
        with pytest.raises(ValidationError):
            resolve_processes(tmp_path)

    def test_the_project_file_accepts_the_blocks_a_deployment_does_not_apply(
        self, tmp_path: Path
    ) -> None:
        write(
            tmp_path,
            "noust.yaml",
            "processes:\n  web: ./x\napp:\n  health: {path: /up}\nenv:\n  A: b\n"
            "domains:\n  aliases: [www.example.com]\ntasks:\n  - name: nightly\n"
            "source:\n  watch_paths: ['src/**']\ncompose:\n  profiles: [web]\n",
        )

        project = load_project_file(tmp_path)

        assert sorted(project.declared) == [
            "app",
            "compose",
            "domains",
            "env",
            "processes",
            "source",
            "tasks",
        ]
        assert not project.hooks.declared

    def test_a_block_that_is_a_scalar_is_refused(self, tmp_path: Path) -> None:
        write(tmp_path, "noust.yaml", "app: yes\n")
        with pytest.raises(ValidationError) as caught:
            load_project_file(tmp_path)
        assert caught.value.field == "app"

    def test_a_key_that_is_not_a_block_of_the_schema_is_still_refused(self, tmp_path: Path) -> None:
        write(tmp_path, "noust.yaml", "workers: {}\n")
        with pytest.raises(ValidationError):
            load_project_file(tmp_path)


def node(
    project: Path, *, existing: bool = False, store: NoustStore | None = None
) -> NodeJSDeployer:
    """
    Args:
        project: The tree.
        existing: Whether the application already has a row, as an update has.
        store: The store holding it.

    Returns:
        A Node deployer for the tree.
    """
    write(project, "package.json", NODE_PACKAGE)
    if existing:
        assert store is not None
        if store.get_app(DOMAIN) is None:
            store.create_app(
                App(domain=DOMAIN, app_type="nodejs", source="x", app_path=str(project), port=3000)
            )
    deployer = NodeJSDeployer(runner=FakeRunner())
    deployer.configure(DOMAIN, "src", app_path=project)
    return deployer


class TestTheWebProcessIsTheUnitsCommand:
    def test_a_new_application_starts_with_the_procfile(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write(tmp_path, PROCFILE, "web: ./bin/server --port $PORT\n")

        deployer = node(tmp_path)

        assert deployer.build_config().start == ("./bin/server", "--port", "$PORT")
        assert deployer.start_command() == f"{tmp_path}/bin/server --port $PORT"
        assert deployer.build_config().source == "repo"

    def test_noust_yaml_processes_apply_to_an_application_that_exists_too(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write(tmp_path, "noust.yaml", "processes:\n  web: ./bin/server\n")

        deployer = node(tmp_path, existing=True, store=store)

        assert deployer.build_config().start == ("./bin/server",)

    def test_an_application_that_exists_does_not_start_reading_a_procfile(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        """Deploying it already worked without: an update must not change how it starts."""
        write(tmp_path, PROCFILE, "web: ./bin/server\n")

        deployer = node(tmp_path, existing=True, store=store)

        assert deployer.build_config().start is None
        assert deployer.start_command() == "npm run start"
        assert deployer._launch_record() is None

    def test_one_that_was_deployed_from_its_procfile_keeps_reading_it(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write(tmp_path, PROCFILE, "web: ./bin/server\n")
        deployer = node(tmp_path, existing=True, store=store)
        app_build_store.set_applied(DOMAIN, {"procfile": True}, store=store)

        deployer = node(tmp_path, existing=True, store=store)

        assert deployer.build_config().start == ("./bin/server",)
        record = deployer._launch_record()
        assert record is not None and record["procfile"] is True

    def test_a_new_application_records_that_it_honours_its_procfile(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write(tmp_path, PROCFILE, "worker: ./bin/jobs\n")

        record = node(tmp_path)._launch_record()

        assert record is not None and record["procfile"] is True

    def test_the_build_blocks_start_wins(self, tmp_path: Path, store: NoustStore) -> None:
        write(tmp_path, PROCFILE, "web: ./from-procfile\n")
        write(tmp_path, "noust.yaml", "build:\n  start: ./from-build\n")

        assert node(tmp_path).build_config().start == ("./from-build",)

    def test_the_operators_block_wins_whole(self, tmp_path: Path, store: NoustStore) -> None:
        write(tmp_path, "noust.yaml", "processes:\n  web: ./from-yaml\n")
        deployer = node(tmp_path, existing=True, store=store)
        app_build_store.set_document(
            DOMAIN, "build:\n  install: ./deps.sh\n", updated_by="test", store=store
        )

        deployer = node(tmp_path, existing=True, store=store)

        assert deployer.build_config().start is None

    def test_a_site_has_no_process_to_start(self, tmp_path: Path, store: NoustStore) -> None:
        write(tmp_path, "index.html", "<p>hi</p>")
        write(tmp_path, PROCFILE, "web: ./bin/server\n")
        deployer = StaticDeployer(runner=FakeRunner())
        deployer.configure(DOMAIN, "src", app_path=tmp_path)

        assert deployer.build_config().start is None
        assert deployer.start_command() == ""

    def test_a_publish_directory_makes_it_files(self, tmp_path: Path, store: NoustStore) -> None:
        write(tmp_path, PROCFILE, "web: ./bin/server\n")
        write(tmp_path, "noust.yaml", "build:\n  publish_directory: dist\n")
        write(tmp_path, "package.json", '{"devDependencies": {"vite": "5"}}')
        deployer = ViteDeployer(runner=FakeRunner())
        deployer.configure(DOMAIN, "src", app_path=tmp_path)

        assert deployer.build_config().publish_directory == "dist"
        assert deployer.build_config().start is None

    def test_a_procfile_that_is_wrong_warns_and_the_deployment_goes_on(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write(tmp_path, PROCFILE, "web: ./a && ./b\n")
        deployer = node(tmp_path)
        lines: list[str] = []
        deployer.logger.attach_sink(lines.append)

        assert deployer.build_config().start is None
        assert any("The Procfile is not used" in line for line in lines)

    def test_a_base_directory_is_where_the_procfile_is_read(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write(tmp_path, PROCFILE, "web: ./root-server\n")
        write(tmp_path, "apps/web/Procfile", "web: ./bin/web\n")
        write(tmp_path, "apps/web/package.json", NODE_PACKAGE)
        write(tmp_path, "noust.yaml", "build:\n  base_directory: apps/web\n")
        deployer = NodeJSDeployer(runner=FakeRunner())
        deployer.configure(DOMAIN, "src", app_path=tmp_path)

        assert deployer.build_config().start == ("./bin/web",)

    def test_the_declared_processes_are_listed(self, tmp_path: Path, store: NoustStore) -> None:
        write(tmp_path, PROCFILE, "web: ./a\nworker: ./b\n")

        found = node(tmp_path).processes()

        assert [process.name for process in found] == ["web", "worker"]


class TestTheReleaseProcessIsAHook:
    def test_it_is_the_only_pre_deploy_hook_when_nothing_else_is_declared(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write(tmp_path, PROCFILE, "release: ./bin/migrate --yes\n")

        hooks = node(tmp_path)._hook_set()

        assert [hook.run for hook in hooks.pre_deploy] == [("./bin/migrate", "--yes")]
        assert hooks.pre_deploy[0].migrates is False
        assert hooks.source == "repo"

    def test_hooks_the_repository_declares_win_whole(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write(tmp_path, PROCFILE, "release: ./bin/migrate\n")
        write(tmp_path, "noust.yaml", "hooks:\n  post_deploy:\n    - run: ./notify.sh\n")

        hooks = node(tmp_path)._hook_set()

        assert hooks.pre_deploy == ()
        assert [hook.run for hook in hooks.post_deploy] == [("./notify.sh",)]

    def test_the_operators_hooks_win_even_when_empty(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write(tmp_path, "noust.yaml", "processes:\n  release: ./bin/migrate\n")
        deployer = node(tmp_path, existing=True, store=store)
        store.set_app_hooks(DOMAIN, "hooks: {}\n", updated_by="test")

        deployer = node(tmp_path, existing=True, store=store)

        assert deployer._hook_set().pre_deploy == ()

    def test_an_existing_application_does_not_start_running_a_procfile_release(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        write(tmp_path, PROCFILE, "release: ./bin/migrate\n")

        deployer = node(tmp_path, existing=True, store=store)

        assert deployer._hook_set().pre_deploy == ()

    def test_a_static_site_has_no_hooks_to_add(self, tmp_path: Path, store: NoustStore) -> None:
        write(tmp_path, "index.html", "<p>hi</p>")
        write(tmp_path, PROCFILE, "release: ./bin/migrate\n")
        deployer = StaticDeployer(runner=FakeRunner())
        deployer.configure(DOMAIN, "src", app_path=tmp_path)

        assert deployer._hook_set().pre_deploy == ()

    def test_resolve_hooks_itself_is_unchanged(self, tmp_path: Path, store: NoustStore) -> None:
        write(tmp_path, PROCFILE, "release: ./bin/migrate\n")
        assert not resolve_hooks(None, tmp_path, store).declared
