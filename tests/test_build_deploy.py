# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a deployment does with a build block: the directory, the commands, the runtime.

These drive a real release pipeline over a temporary tree with the machine
faked (the fixtures of test_release_pipeline.py), so every assertion is on
what a real deploy would run and leave behind: the directory a command ran in,
the working directory and ``ExecStart`` of the unit, the root of the site, what
the unit was last written from, and whether an update that changed the block
reaches the process (and puts the unit back when the release does not answer).
"""

# The pipeline's fixtures are imported rather than replicated, as in
# test_hooks.py; pytest resolves them by name.
# ruff: noqa: F811

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from jinja2 import Environment, PackageLoader

from noust.core import app_build_store
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.store import NoustStore
from noust.deployers.helpers.build_config import set_operator_build
from noust.deployers.nodejs import NodeJSDeployer
from noust.deployers.python import PythonDeployer
from noust.deployers.static import StaticDeployer
from noust.deployers.vite import ViteDeployer
from noust.managers.runtimes import RuntimeManager
from tests.test_release_pipeline import (  # noqa: F401 - fixtures used by name
    BROKEN_SERVER,
    DOMAIN,
    GIT_URL,
    GOOD_SERVER,
    LOCKFILE,
    PACKAGE_JSON,
    PORT,
    RELEASES,
    FakeServices,
    active_id,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    update,
    wire,
    write_tree,
)


class UnitServices(FakeServices):
    """A FakeServices that also keeps the unit's text, so a unit can be read and rewritten."""

    def __init__(self, app_root: Path) -> None:
        super().__init__(app_root)
        self.texts: dict[str, str] = {}
        self.edits: list[tuple[str, str]] = []

    def create_service(self, name: str, **kwargs: Any) -> None:
        super().create_service(name, **kwargs)
        unit = Environment(  # noqa: S701 - unit files, escaped by their own macros
            loader=PackageLoader("noust", "templates/systemd"), trim_blocks=True, lstrip_blocks=True
        ).get_template("app.service.j2")
        self.texts[name] = unit.render(
            name=f"wasm-{name}",
            user="www-data",
            **{key: value for key, value in kwargs.items() if key != "limits"},
        )

    def get_service_config(self, name: str) -> str | None:
        return self.texts.get(name)

    def update_config(self, name: str, content: str) -> str:
        previous = self.texts[name]
        self.texts[name] = content
        self.edits.append((name, content))
        return previous

    def execstart(self, name: str = "rel-example-com") -> str:
        line = next(line for line in self.texts[name].splitlines() if line.startswith("ExecStart="))
        return line.removeprefix("ExecStart=")


@pytest.fixture
def units(root: Path, machine: SimpleNamespace) -> UnitServices:
    """The machine's services, now able to keep and rewrite a unit's text."""
    machine.services = UnitServices(root)
    return machine.services


def pinned_runtime(tmp_path: Path, machine: SimpleNamespace, version: str = "22.11.0") -> Path:
    """
    Install a Node of the given version where the runtime manager looks.

    Returns:
        The runtime's ``bin`` directory.
    """
    home = tmp_path / "runtimes" / "node" / version
    (home / "bin").mkdir(parents=True)
    for program in ("node", "npm"):
        (home / "bin" / program).write_text("")
        (home / "bin" / program).chmod(0o755)
    return home / "bin"


def use_runtimes(deployer: Any, tmp_path: Path, machine: SimpleNamespace) -> None:
    """Point a deployer's runtime manager at the temporary directory."""
    deployer._runtime_manager = RuntimeManager(
        runner=machine.runner, root=tmp_path / "runtimes", fetch=lambda url, limit: b""
    )


def monorepo(directory: Path, *, noust_yaml: str = "", server: str = GOOD_SERVER) -> Path:
    """A Node project in ``apps/web`` of a workspace whose lockfile is at its root."""
    files = {
        "package.json": '{"name": "root", "private": true, "workspaces": ["apps/*"]}\n',
        "package-lock.json": LOCKFILE,
        "apps/web/package.json": PACKAGE_JSON,
        "apps/web/server.js": server,
        "apps/web/.env.example": "SESSION_SECRET=\n",
    }
    if noust_yaml:
        files["noust.yaml"] = noust_yaml
    return write_tree(directory, files)


BASE = "build:\n  base_directory: apps/web\n"


class TestTheBaseDirectory:
    def test_the_project_is_built_and_run_inside_it(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(monorepo(tmp_path / "v1", noust_yaml=BASE))

        deploy_new(root, machine)

        release = root / "releases" / active_id(root)
        project = release / "apps" / "web"
        # The install ran where the project is; npm finds the workspace above it.
        assert machine.runner.cwd_of("npm", "install") == [project] or machine.runner.cwd_of(
            "npm", "ci"
        ) == [project]
        # The unit, the site and the store all say where the project is, through current.
        unit = machine.services.units["rel-example-com"]
        assert unit["working_directory"] == str(root / "current" / "apps" / "web")
        assert machine.web.sites[DOMAIN]["context"]["app_path"] == str(
            root / "current" / "apps" / "web"
        )
        service = store.get_service("rel-example-com")
        assert service is not None
        assert service.working_directory == str(root / "current" / "apps" / "web")
        assert (root / "current" / "apps" / "web" / "server.js").is_file()

    def test_the_lockfile_of_the_workspace_decides_the_install(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(monorepo(tmp_path / "v1", noust_yaml=BASE))

        deploy_new(root, machine)

        # package-lock.json is at the root of the repository, not in apps/web:
        # npm ci would refuse the project directory without one of its own,
        # and the install is the workspace's.
        assert machine.runner.cwd_of("npm", "ci") != []

    def test_the_type_is_detected_inside_it(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        from noust.deployers.auto import AutoDeployer

        # The root is a workspace with no start script; the project is Next.js.
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    "package.json": '{"name": "root", "private": true}\n',
                    "noust.yaml": BASE,
                    "apps/web/package.json": '{"dependencies": {"next": "14.0.0"}}\n',
                    "apps/web/next.config.js": "module.exports = {}\n",
                },
            )
        )
        auto = AutoDeployer(verbose=False)
        auto.source_manager = machine.git  # type: ignore[assignment]
        auto.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        delegate = auto.resolve()

        assert auto.resolved_type == "nextjs"
        assert delegate.APP_TYPE == "nextjs"

    def test_a_base_directory_that_is_a_link_is_refused_and_nothing_is_left(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        tree = write_tree(tmp_path / "v1", {"noust.yaml": BASE, "package.json": PACKAGE_JSON})
        (tree / "apps").mkdir()
        (tree / "apps" / "web").symlink_to(outside)
        machine.git.publish(tree)
        # A repository's export keeps its links (a copy that follows them would
        # hide what is being tested).
        machine.git.export_commit = lambda repository, commit, destination: shutil.copytree(
            machine.git.commits[commit], destination, symlinks=True, dirs_exist_ok=True
        )
        deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(ValidationError) as excinfo:
            deployer.deploy()

        assert excinfo.value.field == "build.base_directory"
        assert not root.exists()
        assert machine.runner.cwd_of("npm", "ci") == []

    def test_a_base_directory_that_is_not_there_is_refused(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            write_tree(tmp_path / "v1", {"noust.yaml": BASE, "package.json": PACKAGE_JSON})
        )
        deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(ValidationError) as excinfo:
            deployer.deploy()

        assert "not a directory of the repository" in excinfo.value.message

    def test_dependencies_are_reused_from_the_same_subdirectory_of_the_active_release(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The project keeps its own lockfile here, so the install is its own.
        files = {
            "noust.yaml": BASE,
            "apps/web/package.json": PACKAGE_JSON,
            "apps/web/package-lock.json": LOCKFILE,
            "apps/web/server.js": GOOD_SERVER,
        }
        machine.git.publish(write_tree(tmp_path / "v1", files))
        deploy_new(root, machine)
        first = active_id(root)

        machine.git.publish(
            write_tree(tmp_path / "v2", {**files, "apps/web/server.js": GOOD_SERVER + "// v2\n"})
        )
        update(machine, monkeypatch)
        second = active_id(root)

        assert (
            "cp",
            "-a",
            "--reflink=auto",
            str(root / "releases" / first / "apps" / "web" / "node_modules"),
            str(root / "releases" / second / "apps" / "web" / "node_modules"),
        ) in machine.runner.calls
        assert machine.runner.cwd_of("npm", "ci") == [root / "releases" / first / "apps" / "web"]

    def test_the_whole_release_is_handed_to_the_service_not_just_the_project(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
    ) -> None:
        machine.git.publish(monorepo(tmp_path / "v1", noust_yaml=BASE))

        deploy_new(root, machine)

        release = root / "releases" / active_id(root)
        owned = [call[-1] for call in machine.runner.calls if call[:2] == ("chown", "-R")]
        # The workspace's files live above the project: the whole release goes.
        assert str(release) in owned
        assert str(release / "apps" / "web") not in owned

    def test_blue_green_cannot_take_a_subdirectory(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(monorepo(tmp_path / "v1", noust_yaml=BASE))
        deployer = deploy_new(root, machine)
        row = store.get_app(DOMAIN)
        assert row is not None
        store.set_zero_downtime(DOMAIN, True)

        with pytest.raises(DeploymentError) as excinfo:
            deployer._refuse_blue_green_in_subdirectory()

        assert "apps/web" in excinfo.value.message


class TestCustomCommands:
    NOUST_YAML = (
        "build:\n"
        "  install: npm install --no-audit\n"
        "  build: npm run compile\n"
        "  start: ./bin/serve --port ${PORT} --title 'my app'\n"
    )

    def test_the_commands_replace_the_types_and_run_where_the_build_does(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        tree = node_tree(tmp_path / "v1")
        write_tree(tree, {"noust.yaml": self.NOUST_YAML, "bin/serve": "#!/bin/sh\n"})
        machine.git.publish(tree)

        deploy_new(root, machine)

        release = root / "releases" / active_id(root)
        assert machine.runner.cwd_of("npm", "install", "--no-audit") == [release]
        assert machine.runner.cwd_of("npm", "run", "compile") == [release]
        # What the type would have run did not.
        assert ("npm", "ci") not in machine.runner.calls
        assert ("npm", "run", "build") not in machine.runner.calls

    def test_the_start_command_is_the_units_with_its_program_inside_the_project(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, units: Any
    ) -> None:
        tree = node_tree(tmp_path / "v1")
        write_tree(tree, {"noust.yaml": self.NOUST_YAML, "bin/serve": "#!/bin/sh\n"})
        machine.git.publish(tree)

        deploy_new(root, machine)

        # An absolute program through current, systemd's own ${PORT}, and the
        # argument with a space quoted so systemd reads it back as one.
        assert units.execstart() == (f'{root}/current/bin/serve --port ${{PORT}} --title "my app"')
        assert not store.get_app(DOMAIN).is_static

    def test_a_failing_custom_install_is_not_retried_as_something_else(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        tree = node_tree(tmp_path / "v1")
        write_tree(tree, {"noust.yaml": "build:\n  install: pnpm install --frozen-lockfile\n"})
        machine.git.publish(tree)
        machine.runner.script(["pnpm"], exit_code=1, stderr="ERR_PNPM_NO_LOCKFILE")
        deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(DeploymentError):
            deployer.deploy()

        pnpm_calls = [call for call in machine.runner.calls if call and call[0] == "pnpm"]
        assert len(pnpm_calls) == 1
        assert "--frozen-lockfile" in pnpm_calls[0]

    def test_a_bare_program_is_found_in_the_projects_own_tools(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, units: Any
    ) -> None:
        tree = node_tree(tmp_path / "v1")
        write_tree(
            tree,
            {
                "noust.yaml": "build:\n  start: serve -l ${PORT}\n",
                "node_modules/.bin/serve": "#!/usr/bin/env node\n",
            },
        )
        machine.git.publish(tree)

        deploy_new(root, machine)

        assert units.execstart() == f"{root}/current/node_modules/.bin/serve -l ${{PORT}}"

    def test_a_percent_in_an_argument_stays_a_percent(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, units: Any
    ) -> None:
        tree = node_tree(tmp_path / "v1")
        write_tree(tree, {"noust.yaml": "build:\n  start: ./run --format=%s\n", "run": ""})
        machine.git.publish(tree)

        deploy_new(root, machine)

        # systemd reads %% as one %; a single % would be a specifier.
        assert units.execstart().endswith("--format=%%s")

    def test_the_operators_block_replaces_the_repositorys_on_the_next_update(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        units: UnitServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tree = node_tree(tmp_path / "v1")
        write_tree(tree, {"noust.yaml": self.NOUST_YAML, "bin/serve": ""})
        machine.git.publish(tree)
        deploy_new(root, machine)
        machine.runner.calls.clear()
        set_operator_build(DOMAIN, "build:\n  build: npm run other\n", actor="ops", store=store)
        machine.git.publish(node_tree(tmp_path / "v2", server=GOOD_SERVER + "// v2\n"))

        update(machine, monkeypatch)

        # The operator's block is the whole: no install of the repository's, no compile.
        assert ("npm", "run", "other") in machine.runner.calls
        assert ("npm", "run", "compile") not in machine.runner.calls
        assert ("npm", "install", "--no-audit") not in machine.runner.calls

    def test_a_key_the_type_cannot_honour_is_refused_before_anything_is_built(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        tree = node_tree(tmp_path / "v1")
        write_tree(tree, {"noust.yaml": "build:\n  publish_directory: dist\n"})
        machine.git.publish(tree)
        deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(ValidationError) as excinfo:
            deployer.deploy()

        assert excinfo.value.field == "build.publish_directory"
        assert machine.runner.cwd_of("npm", "ci") == []
        assert not root.exists()

    def test_start_and_publish_directory_are_exclusive_in_the_one_block(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        tree = write_tree(
            tmp_path / "v1",
            {
                "noust.yaml": "build:\n  start: ./x\n  publish_directory: dist\n",
                "vite.config.js": "export default {}\n",
                "package.json": '{"devDependencies": {"vite": "5"}}',
            },
        )
        machine.git.publish(tree)
        deployer = wire(ViteDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(ValidationError) as excinfo:
            deployer.deploy()

        assert (
            "both" in excinfo.value.message.lower() or "exclusive" in excinfo.value.message.lower()
        )


class TestStaticOutput:
    def site(self, tmp_path: Path, machine: SimpleNamespace, noust_yaml: str) -> Path:
        tree = write_tree(
            tmp_path / "site",
            {
                "noust.yaml": noust_yaml,
                "package.json": '{"scripts": {"build": "eleventy"}}',
                "package-lock.json": LOCKFILE,
                "src/index.md": "# hi",
            },
        )
        machine.git.publish(tree)
        original = machine.runner.stream

        def build(argv: Any, **kwargs: Any) -> Any:
            if tuple(argv) == ("npm", "run", "build"):
                write_tree(Path(kwargs["cwd"]), {"_site/index.html": "<h1>ok</h1>"})
            return original(argv, **kwargs)

        machine.runner.stream = build
        return tree

    def test_a_static_site_can_be_built_and_published_from_a_directory(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        self.site(
            tmp_path,
            machine,
            "build:\n  install: npm ci\n  build: npm run build\n  publish_directory: _site\n",
        )

        deploy_new(root, machine, deployer_class=StaticDeployer)

        release = root / "releases" / active_id(root)
        assert machine.runner.cwd_of("npm", "ci") == [release]
        site = machine.web.sites[DOMAIN]
        assert site["template"] == "static"
        assert site["context"]["static_dir"] == str(root / "current" / "_site")
        assert machine.services.units == {}

    def test_a_static_site_without_a_build_block_is_exactly_what_it_was(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(write_tree(tmp_path / "site", {"public/index.html": "<h1>hi</h1>"}))

        deploy_new(root, machine, deployer_class=StaticDeployer)

        assert machine.runner.cwd_of("npm", "ci") == []
        assert machine.web.sites[DOMAIN]["context"]["static_dir"] == str(
            root / "current" / "public"
        )

    def test_a_static_site_has_nothing_to_start(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            write_tree(
                tmp_path / "site",
                {"noust.yaml": "build:\n  start: ./serve\n", "index.html": "<h1>hi</h1>"},
            )
        )
        deployer = wire(StaticDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(ValidationError) as excinfo:
            deployer.deploy()

        assert excinfo.value.field == "build.start"

    def test_vite_publishes_the_directory_the_block_names(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            write_tree(
                tmp_path / "site",
                {
                    "noust.yaml": "build:\n  publish_directory: out\n",
                    "vite.config.ts": "export default {}",
                    "package.json": '{"devDependencies": {"vite": "5"}}',
                    "package-lock.json": LOCKFILE,
                },
            )
        )
        original = machine.runner.stream

        def build(argv: Any, **kwargs: Any) -> Any:
            if tuple(argv) == ("npm", "run", "build"):
                write_tree(Path(kwargs["cwd"]), {"out/index.html": "<h1>ok</h1>"})
            return original(argv, **kwargs)

        machine.runner.stream = build

        deploy_new(root, machine, deployer_class=ViteDeployer)

        assert machine.web.sites[DOMAIN]["context"]["static_dir"] == str(root / "current" / "out")


class TestAPinnedRuntime:
    NODE_22 = "build:\n  runtime: node@22\n"

    def deploy_pinned(
        self, tmp_path: Path, root: Path, machine: SimpleNamespace, noust_yaml: str = NODE_22
    ) -> Any:
        pinned_runtime(tmp_path, machine)
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    **{
                        "package.json": PACKAGE_JSON,
                        "package-lock.json": LOCKFILE,
                        "server.js": GOOD_SERVER,
                    },
                    "noust.yaml": noust_yaml,
                },
            )
        )
        deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
        use_runtimes(deployer, tmp_path, machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)
        assert deployer.deploy() is True
        return deployer

    def test_commands_run_the_pinned_programs_with_the_runtime_first_on_the_path(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        self.deploy_pinned(tmp_path, root, machine)

        bin_dir = tmp_path / "runtimes" / "node" / "22.11.0" / "bin"
        install = next(call for call in machine.runner.calls if call[1:] == ("ci",))
        # By absolute path: the system's fixed PATH would find the distribution's npm.
        assert install[0] == str(bin_dir / "npm")
        index = machine.runner.calls.index(install)
        path = machine.runner.envs[index]["PATH"]  # type: ignore[index]
        assert path.split(":")[0] == str(bin_dir)

    def test_the_unit_runs_on_it_and_says_so(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        self.deploy_pinned(tmp_path, root, machine)

        bin_dir = tmp_path / "runtimes" / "node" / "22.11.0" / "bin"
        unit = machine.services.units["rel-example-com"]
        assert unit["command"] == f"{bin_dir / 'npm'} run start"
        assert unit["environment"]["PATH"].startswith(f"{bin_dir}:")
        assert app_build_store.get_applied(DOMAIN, store=store) == {
            "start": f"{bin_dir / 'npm'} run start",
            "working_directory": str(root / "current"),
            "path": unit["environment"]["PATH"],
            "runtime": {"language": "node", "version": "22.11.0"},
        }

    def test_what_a_version_file_asks_that_the_server_satisfies_changes_nothing(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        # The server's own Node is 22.9.0; .nvmrc asks for 22.
        machine.runner.script(["node", "--version"], stdout="v22.9.0\n")
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    "package.json": PACKAGE_JSON,
                    "package-lock.json": LOCKFILE,
                    "server.js": GOOD_SERVER,
                    ".nvmrc": "v22\n",
                },
            )
        )
        deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
        use_runtimes(deployer, tmp_path, machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        assert deployer.deploy() is True

        unit = machine.services.units["rel-example-com"]
        assert "PATH" not in unit["environment"]
        assert ("npm", "ci") in machine.runner.calls
        assert app_build_store.get_applied(DOMAIN, store=store) is None

    def test_an_application_without_any_of_this_keeps_its_unit_exactly(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))

        deploy_new(root, machine)

        unit = machine.services.units["rel-example-com"]
        assert unit["command"] == "/usr/bin/npm run start" or unit["command"].endswith(
            "npm run start"
        )
        assert set(unit["environment"]) == {"PORT", "NODE_ENV"}
        assert app_build_store.get_applied(DOMAIN, store=store) is None

    def test_a_pinned_runtime_that_cannot_be_had_fails_the_deploy_before_building(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    "package.json": PACKAGE_JSON,
                    "package-lock.json": LOCKFILE,
                    "noust.yaml": "build:\n  runtime: node@22\n",
                },
            )
        )
        deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
        use_runtimes(deployer, tmp_path, machine)  # nothing installed, nothing to fetch
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(DeploymentError):
            deployer.deploy()

        assert not root.exists()
        assert all(call[1:] != ("ci",) for call in machine.runner.calls)

    def test_installed_dependencies_are_stamped_with_the_pinned_runtimes_versions(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        pinned_runtime(tmp_path, machine)
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    "package.json": PACKAGE_JSON,
                    "package-lock.json": LOCKFILE,
                    "server.js": GOOD_SERVER,
                    "noust.yaml": self.NODE_22,
                    # What an install leaves: the stamp is written inside it.
                    "node_modules/.keep": "",
                },
            )
        )
        deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
        use_runtimes(deployer, tmp_path, machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)
        assert deployer.deploy() is True

        # The version commands that make the stamp ran with the runtime first
        # on their PATH, not the server's own.
        index = next(
            i
            for i, call in enumerate(machine.runner.calls)
            if call == ("node", "--version") and (machine.runner.envs[i] or {}).get("PATH")
        )
        assert machine.runner.envs[index]["PATH"].split(":")[0].endswith("22.11.0/bin")  # type: ignore[index]


class TestAnUpdateThatChangesTheBlock:
    def deployed(
        self, tmp_path: Path, root: Path, machine: SimpleNamespace, units: UnitServices
    ) -> None:
        tree = node_tree(tmp_path / "v1")
        write_tree(
            tree, {"noust.yaml": "build:\n  start: ./bin/one\n", "bin/one": "", "bin/two": ""}
        )
        machine.git.publish(tree)
        deploy_new(root, machine)

    def test_a_new_start_command_reaches_the_unit_without_a_redeploy(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        units: UnitServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.deployed(tmp_path, root, machine, units)
        assert units.execstart() == f"{root}/current/bin/one"
        set_operator_build(DOMAIN, "build:\n  start: ./bin/two --fast\n", actor="ops", store=store)
        machine.git.publish(node_tree(tmp_path / "v2", server=GOOD_SERVER + "// v2\n"))
        write_tree(tmp_path / "v2", {"bin/two": ""})

        update(machine, monkeypatch)

        assert units.execstart() == f"{root}/current/bin/two --fast"
        assert [name for name, _ in units.edits] == ["rel-example-com"]
        # Nothing but the command changed in the unit.
        assert "WorkingDirectory=" + str(root / "current") in units.texts["rel-example-com"]
        applied = app_build_store.get_applied(DOMAIN, store=store)
        assert applied is not None and applied["start"] == f"{root}/current/bin/two --fast"
        service = store.get_service("rel-example-com")
        assert service is not None and service.command == f"{root}/current/bin/two --fast"

    def test_an_update_that_changes_nothing_does_not_touch_the_unit(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        units: UnitServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.deployed(tmp_path, root, machine, units)
        machine.git.publish(node_tree(tmp_path / "v2", server=GOOD_SERVER + "// v2\n"))
        write_tree(tmp_path / "v2", {"noust.yaml": "build:\n  start: ./bin/one\n", "bin/one": ""})

        update(machine, monkeypatch)

        assert units.edits == []

    def test_an_application_with_no_block_never_has_its_unit_rewritten(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        units: UnitServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        machine.git.publish(node_tree(tmp_path / "v2", server=GOOD_SERVER + "// v2\n"))

        update(machine, monkeypatch)

        assert units.edits == []

    def test_removing_the_block_puts_the_types_command_back(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        units: UnitServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.deployed(tmp_path, root, machine, units)
        set_operator_build(DOMAIN, "build: {}\n", actor="ops", store=store)
        machine.git.publish(node_tree(tmp_path / "v2", server=GOOD_SERVER + "// v2\n"))

        update(machine, monkeypatch)

        assert units.execstart().endswith("npm run start")
        assert app_build_store.get_applied(DOMAIN, store=store) is None

    def test_a_release_that_does_not_answer_puts_the_previous_unit_back(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        units: UnitServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.deployed(tmp_path, root, machine, units)
        before = units.texts["rel-example-com"]
        first = active_id(root)
        set_operator_build(DOMAIN, "build:\n  start: ./bin/two\n", actor="ops", store=store)
        machine.git.publish(node_tree(tmp_path / "v2", server=BROKEN_SERVER))
        restarts_before = len(units.restarts)

        with pytest.raises(DeploymentError):
            update(machine, monkeypatch)

        # What served before is what serves: the release, the unit, what the
        # store says the unit runs.
        assert active_id(root) == first
        assert units.texts["rel-example-com"] == before
        assert next(content for _, content in units.edits) != before
        assert app_build_store.get_applied(DOMAIN, store=store) is not None
        assert app_build_store.get_applied(DOMAIN, store=store)["start"].endswith("bin/one")  # type: ignore[index]
        # Restarted on it again.
        assert len(units.restarts) > restarts_before + 1

    def test_a_new_runtime_reaches_the_unit(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        units: UnitServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pinned_runtime(tmp_path, machine, "22.11.0")
        pinned_runtime(tmp_path, machine, "20.18.0")
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    "package.json": PACKAGE_JSON,
                    "package-lock.json": LOCKFILE,
                    "server.js": GOOD_SERVER,
                    "noust.yaml": "build:\n  runtime: node@22\n",
                },
            )
        )
        deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
        use_runtimes(deployer, tmp_path, machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)
        assert deployer.deploy() is True
        assert "22.11.0" in units.texts["rel-example-com"]

        set_operator_build(DOMAIN, "build:\n  runtime: node@20\n", actor="ops", store=store)
        machine.git.publish(node_tree(tmp_path / "v2", server=GOOD_SERVER + "// v2\n"))
        monkeypatch.setattr(
            "noust.deployers.base.RuntimeManager",
            lambda **kwargs: RuntimeManager(
                runner=machine.runner, root=tmp_path / "runtimes", fetch=lambda url, limit: b""
            ),
        )

        update(machine, monkeypatch)

        text = units.texts["rel-example-com"]
        assert "20.18.0" in text and "22.11.0" not in text
        assert app_build_store.runtime_users("node", "20.18.0", store=store) == [DOMAIN]
        assert app_build_store.runtime_users("node", "22.11.0", store=store) == []


class TestPython:
    def python_tree(self, directory: Path, extra: dict[str, str] | None = None) -> Path:
        return write_tree(
            directory,
            {
                "requirements.txt": "flask\n",
                "app.py": "from flask import Flask\napp = Flask(__name__)\n",
                **(extra or {}),
            },
        )

    def test_the_default_start_binds_the_loopback_address_nginx_proxies_to(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(self.python_tree(tmp_path / "v1"))

        deploy_new(root, machine, deployer_class=PythonDeployer)

        unit = machine.services.units["rel-example-com"]
        assert f"-b 127.0.0.1:{PORT}" in unit["command"]
        assert "0.0.0.0" not in unit["command"]  # noqa: S104 - what it must not bind
        assert unit["environment"]["WEB_CONCURRENCY"] == "4"
        assert " -w " not in unit["command"]

    def test_the_start_command_is_the_projects_own(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, units: Any
    ) -> None:
        machine.git.publish(
            self.python_tree(
                tmp_path / "v1",
                {
                    "noust.yaml": (
                        "build:\n  start: hypercorn app:app -b 127.0.0.1:${PORT} -w 2\n"
                    ),
                    "venv/bin/hypercorn": "#!/x/python\n",
                },
            )
        )

        deploy_new(root, machine, deployer_class=PythonDeployer)

        release = root / "releases" / active_id(root)
        # Found in the environment its install made, and run through that
        # environment's interpreter: the script's first line names one release.
        assert (
            units.execstart()
            == f"{root}/current/venv/bin/python {root}/current/venv/bin/hypercorn "
            f"app:app -b 127.0.0.1:${{PORT}} -w 2"
        )
        # The server the project chose is its own dependency, not installed over it.
        assert not any(
            call[-2:] == ("install", "gunicorn") or "gunicorn" in call
            for call in machine.runner.calls
            if "pip" in " ".join(call)
        )
        assert release.is_dir()

    def test_a_project_that_depends_on_hypercorn_is_started_with_it(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {"requirements.txt": "quart\nhypercorn\n", "app.py": "app = None\n"},
            )
        )

        deploy_new(root, machine, deployer_class=PythonDeployer)

        command = machine.services.units["rel-example-com"]["command"]
        assert "-m hypercorn" in command
        assert "${WEB_CONCURRENCY}" in command
        assert any("hypercorn" in call for call in machine.runner.calls)

    def test_a_project_with_only_a_pyproject_installs_itself(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {"pyproject.toml": "[project]\nname = 'x'\ndependencies = ['flask']\n"},
            )
        )

        deploy_new(root, machine, deployer_class=PythonDeployer)

        release = root / "releases" / active_id(root)
        install = [c for c in machine.runner.calls if c[-2:] == ("install", ".")]
        assert install and install[0][0] == str(release / "venv" / "bin" / "python")

    def test_django_finds_its_own_wsgi_not_the_environments(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        # The virtual environment of a real install holds Django's own
        # wsgi.py; the project's is the one a unit starts.
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    "requirements.txt": "django\n",
                    "manage.py": "",
                    "mysite/__init__.py": "",
                    "mysite/wsgi.py": "application = None\n",
                    "mysite/settings.py": "STATIC_ROOT = 'static'\n",
                    "venv/lib/python3.12/site-packages/django/core/handlers/wsgi.py": "",
                },
            )
        )

        deploy_new(root, machine, deployer_class=PythonDeployer)

        command = machine.services.units["rel-example-com"]["command"]
        assert " mysite.wsgi:application " in command
        assert "site-packages" not in command

    def test_django_in_a_subdirectory_runs_from_there(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    "requirements.txt": "django\n",
                    "backend/manage.py": "",
                    "backend/shop/wsgi.py": "application = None\n",
                },
            )
        )

        deploy_new(root, machine, deployer_class=PythonDeployer)

        command = machine.services.units["rel-example-com"]["command"]
        assert "--chdir backend shop.wsgi:application" in command

    def test_collectstatic_runs_only_for_a_project_that_says_where_to_put_the_files(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(
            write_tree(
                tmp_path / "v1",
                {
                    "requirements.txt": "django\n",
                    "manage.py": "",
                    "site/wsgi.py": "",
                    "site/settings.py": "DEBUG = False\n",
                },
            )
        )

        deploy_new(root, machine, deployer_class=PythonDeployer)

        assert not any("collectstatic" in call for call in machine.runner.calls)

    def test_the_python_version_file_pins_the_interpreter_that_makes_the_venv(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        home = tmp_path / "runtimes" / "python" / "3.11.9" / "cpython-3.11.9-linux-x86_64-gnu"
        (home / "bin").mkdir(parents=True)
        (home / "bin" / "python3").write_text("")
        machine.git.publish(self.python_tree(tmp_path / "v1", {".python-version": "3.11\n"}))
        machine.runner.script(["python3", "--version"], stdout="Python 3.12.3\n")
        deployer = wire(PythonDeployer(verbose=False, runner=machine.runner), machine)
        use_runtimes(deployer, tmp_path, machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        assert deployer.deploy() is True

        venv_call = next(call for call in machine.runner.calls if call[1:3] == ("-m", "venv"))
        # The server's 3.12 does not satisfy 3.11: the managed 3.11 makes it.
        assert venv_call[0] == str(home / "bin" / "python3")
        assert "3.11.9" in machine.services.units["rel-example-com"]["environment"]["PATH"]


def test_the_os_module_is_used() -> None:
    # Keeps the import honest for the environment-dependent tests above.
    assert os.sep == "/"


class TestWhatTheBlockChangesAboutReuseAndErrors:
    def test_a_different_install_command_never_reuses_what_another_installed(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        machine.runner.calls.clear()
        # Same lockfile, same runtime: only the way it is installed differs.
        set_operator_build(
            DOMAIN, "build:\n  install: npm ci --omit=dev\n", actor="ops", store=store
        )
        machine.git.publish(node_tree(tmp_path / "v2", server=GOOD_SERVER + "// v2\n"))

        update(machine, monkeypatch)

        assert not any(call[:2] == ("cp", "-a") for call in machine.runner.calls)
        assert ("npm", "ci", "--omit=dev") in machine.runner.calls

    def test_the_same_install_command_still_reuses_dependencies(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        block = {"noust.yaml": "build:\n  install: npm ci --omit=dev\n"}
        tree = node_tree(tmp_path / "v1")
        # What an install leaves, so there is something to carry over (the fake
        # install only marks the two commands it knows).
        write_tree(tree, {**block, "node_modules/.keep": ""})
        machine.git.publish(tree)
        deploy_new(root, machine)
        machine.runner.calls.clear()
        second = node_tree(tmp_path / "v2", server=GOOD_SERVER + "// v2\n")
        write_tree(second, block)
        machine.git.publish(second)

        update(machine, monkeypatch)

        assert any(call[:2] == ("cp", "-a") for call in machine.runner.calls)
        assert ("npm", "ci", "--omit=dev") not in machine.runner.calls

    def test_a_start_program_that_is_nowhere_fails_before_a_unit_is_written(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        tree = node_tree(tmp_path / "v1")
        write_tree(
            tree, {"noust.yaml": "build:\n  start: no-such-program-anywhere --port ${PORT}\n"}
        )
        machine.git.publish(tree)
        deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(DeploymentError) as excinfo:
            deployer.deploy()

        assert "no-such-program-anywhere" in excinfo.value.message
        assert "./bin/server" in excinfo.value.details
        assert machine.services.units == {}
        assert not root.exists()

    def test_the_applications_detail_shows_the_build_command_its_block_declares(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        from noust.web.api.apps import _deployer_build_command

        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        app = store.get_app(DOMAIN)
        assert app is not None
        assert _deployer_build_command(app) == []

        set_operator_build(DOMAIN, "build:\n  build: make release\n", actor="ops", store=store)

        assert _deployer_build_command(app) == ["make", "release"]


class TestACreationWithABuildBlock:
    """``noust create --build-file``: how several applications come from one repository."""

    WEB = "build:\n  base_directory: apps/web\n"
    API = "build:\n  base_directory: apps/api\n  start: ./bin/api --port ${PORT}\n"

    def repository(self, tmp_path: Path) -> Path:
        return write_tree(
            tmp_path / "repo",
            {
                "package.json": '{"name": "root", "private": true}\n',
                "package-lock.json": LOCKFILE,
                "apps/web/package.json": PACKAGE_JSON,
                "apps/web/server.js": GOOD_SERVER,
                "apps/api/package.json": PACKAGE_JSON,
                "apps/api/server.js": GOOD_SERVER,
                "apps/api/bin/api": "#!/bin/sh\n",
            },
        )

    def deploy(
        self,
        machine: SimpleNamespace,
        root: Path,
        domain: str,
        document: str,
        deployer_class: type = NodeJSDeployer,
    ) -> Any:
        deployer = wire(deployer_class(verbose=False, runner=machine.runner), machine)
        deployer.configure(
            domain,
            GIT_URL,
            port=PORT,
            ssl=False,
            app_path=root,
            layout=RELEASES,
            build_document=document,
        )
        assert deployer.deploy() is True
        return deployer

    def test_two_applications_come_from_one_repository_each_in_its_own_directory(
        self, tmp_path: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        machine.git.publish(self.repository(tmp_path))
        web_root = tmp_path / "apps" / "web-example-com"
        api_root = tmp_path / "apps" / "api-example-com"

        self.deploy(machine, web_root, "web.example.com", self.WEB)
        self.deploy(machine, api_root, "api.example.com", self.API)

        web = machine.services.units["web-example-com"]
        api = machine.services.units["api-example-com"]
        assert web["working_directory"] == str(web_root / "current" / "apps" / "web")
        assert api["working_directory"] == str(api_root / "current" / "apps" / "api")
        assert api["command"] == f"{api_root}/current/apps/api/bin/api --port ${{PORT}}"
        # Each keeps its own block, which is what its updates build with.
        assert "apps/web" in (app_build_store.get_document("web.example.com", store=store) or "")
        assert "apps/api" in (app_build_store.get_document("api.example.com", store=store) or "")
        assert store.get_app("web.example.com").source == store.get_app("api.example.com").source

    def test_the_block_given_at_creation_replaces_the_repositorys_and_is_audited(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.core import audit

        recorded: list[str] = []
        monkeypatch.setattr(audit, "record", lambda event, **kwargs: recorded.append(event))
        repo = self.repository(tmp_path)
        write_tree(repo, {"noust.yaml": "build:\n  base_directory: apps/api\n"})
        machine.git.publish(repo)

        self.deploy(machine, root, DOMAIN, self.WEB)

        unit = machine.services.units["rel-example-com"]
        assert unit["working_directory"].endswith("/apps/web")
        assert "apps.build" in recorded

    def test_an_update_builds_with_the_stored_block(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(self.repository(tmp_path))
        self.deploy(machine, root, DOMAIN, self.WEB)
        machine.runner.calls.clear()
        machine.git.publish(
            write_tree(
                self.repository(tmp_path / "again"),
                {"apps/web/server.js": GOOD_SERVER + "// v2\n"},
            )
        )

        update(machine, monkeypatch)

        assert (root / "current" / "apps" / "web" / "server.js").read_text().endswith("// v2\n")
        cwds = [cwd for _, cwd in machine.runner.where if cwd is not None]
        assert any(str(cwd).endswith("apps/web") for cwd in cwds)

    def test_the_type_is_detected_in_the_directory_the_block_names(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        from noust.deployers.auto import AutoDeployer

        machine.git.publish(
            write_tree(
                tmp_path / "repo",
                {
                    "package.json": '{"name": "root", "private": true}\n',
                    "site/index.html": "<h1>hi</h1>",
                },
            )
        )
        auto = AutoDeployer(verbose=False)
        auto.source_manager = machine.git  # type: ignore[assignment]
        auto.configure(
            DOMAIN,
            GIT_URL,
            port=PORT,
            ssl=False,
            app_path=root,
            layout=RELEASES,
            build_document="build:\n  base_directory: site\n  publish_directory: .\n",
        )

        auto.resolve()

        assert auto.resolved_type == "static"

    def test_a_block_that_is_not_valid_is_refused_before_anything_is_fetched(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        deployer = NodeJSDeployer(verbose=False, runner=machine.runner)

        with pytest.raises(ValidationError) as excinfo:
            deployer.configure(
                DOMAIN, GIT_URL, port=PORT, app_path=root, build_document="build:\n  start: 7\n"
            )

        assert excinfo.value.field == "build.start"
        assert machine.git.calls == []

    def test_a_block_a_type_cannot_honour_is_refused_at_once(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        deployer = StaticDeployer(verbose=False, runner=machine.runner)

        with pytest.raises(ValidationError) as excinfo:
            deployer.configure(
                DOMAIN, GIT_URL, port=PORT, app_path=root, build_document="build:\n  start: ./x\n"
            )

        assert excinfo.value.field == "build.start"

    def test_a_type_that_has_no_build_block_does_not_silently_ignore_one(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        from noust.deployers.auto import AutoDeployer

        machine.git.publish(
            write_tree(
                tmp_path / "repo", {"docker-compose.prod.yml": "services:\n  web:\n    build: .\n"}
            )
        )
        auto = AutoDeployer(verbose=False)
        auto.source_manager = machine.git  # type: ignore[assignment]
        auto.configure(
            DOMAIN,
            GIT_URL,
            app_path=root,
            layout=RELEASES,
            build_document="build:\n  start: ./x\n",
        )

        with pytest.raises(DeploymentError) as excinfo:
            auto.resolve()

        assert "docker-compose" in excinfo.value.message

    def test_the_command_reads_and_checks_the_file_before_deploying(self, tmp_path: Path) -> None:
        from noust.cli.commands.webapp import _read_build_file

        good = tmp_path / "good.yaml"
        good.write_text(self.WEB)
        bad = tmp_path / "bad.yaml"
        bad.write_text("build:\n  base_directory: ../x\n")
        huge = tmp_path / "huge.yaml"
        huge.write_text("build: {}\n" + "#" * 70_000)

        assert _read_build_file(good) == self.WEB
        with pytest.raises(ValidationError) as excinfo:
            _read_build_file(bad)
        assert excinfo.value.field == "build.base_directory"
        with pytest.raises(ValidationError):
            _read_build_file(huge)


class TestAPublishedDirectoryThatIsALink:
    def linked_site(self, tmp_path: Path, machine: SimpleNamespace, noust_yaml: str) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "index.html").write_text("<h1>not yours</h1>")
        tree = write_tree(tmp_path / "site", {"noust.yaml": noust_yaml, "index.html": "x"})
        (tree / "out").symlink_to(outside)
        machine.git.publish(tree)
        machine.git.export_commit = lambda repository, commit, destination: shutil.copytree(
            machine.git.commits[commit], destination, symlinks=True, dirs_exist_ok=True
        )

    def test_a_static_site_does_not_serve_what_a_link_leads_to(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        self.linked_site(tmp_path, machine, "build:\n  publish_directory: out\n")
        deployer = wire(StaticDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(ValidationError) as excinfo:
            deployer.deploy()

        assert excinfo.value.field == "build.publish_directory"
        assert machine.web.sites == {}

    def test_a_framework_whose_build_left_a_link_is_refused_after_the_build(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        from noust.deployers.astro import AstroDeployer

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "index.html").write_text("<h1>not yours</h1>")
        machine.git.publish(
            write_tree(
                tmp_path / "site",
                {
                    "package.json": '{"dependencies": {"astro": "^4"}, "scripts": {"build": "x"}}',
                    "package-lock.json": LOCKFILE,
                },
            )
        )
        original = machine.runner.stream

        def build(argv: Any, **kwargs: Any) -> Any:
            if tuple(argv) == ("npm", "run", "build"):
                (Path(kwargs["cwd"]) / "dist").symlink_to(outside)
            return original(argv, **kwargs)

        machine.runner.stream = build
        deployer = wire(AstroDeployer(verbose=False, runner=machine.runner), machine)
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        with pytest.raises(ValidationError) as excinfo:
            deployer.deploy()

        assert excinfo.value.field == "build.publish_directory"
        assert machine.web.sites == {}


class TestDjangoMigrationsAreADecision:
    FILES = {
        "requirements.txt": "django\n",
        "manage.py": "",
        "shop/wsgi.py": "application = None\n",
        "shop/settings.py": "STATIC_ROOT = 'static'\n",
        "orders/__init__.py": "",
        "orders/migrations/__init__.py": "",
        "orders/migrations/0001_initial.py": "",
    }

    def deploy_django(
        self, tmp_path: Path, root: Path, machine: SimpleNamespace, extra: dict[str, str]
    ) -> list[str]:
        machine.git.publish(write_tree(tmp_path / "v1", {**self.FILES, **extra}))
        said: list[str] = []
        deployer = wire(PythonDeployer(verbose=False, runner=machine.runner), machine)
        deployer.logger.substep = said.append  # type: ignore[method-assign]
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)
        assert deployer.deploy() is True
        return said

    def test_a_project_with_migrations_and_no_hook_is_told_how_to_run_them(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        said = self.deploy_django(tmp_path, root, machine, {})

        assert any("migrations are not run for you" in line for line in said)
        assert not any("migrate" in " ".join(call) for call in machine.runner.calls)

    def test_a_project_that_declares_its_migration_is_not_told(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        hook = (
            "hooks:\n  pre_deploy:\n    - run: ./venv/bin/python manage.py migrate --noinput\n"
            "      migrates: true\n"
        )

        said = self.deploy_django(tmp_path, root, machine, {"noust.yaml": hook})

        assert not any("migrations are not run for you" in line for line in said)
        # The hook runs where a migration runs, as the application, in the release.
        assert any(
            call[1:] == ("manage.py", "migrate", "--noinput") for call in machine.runner.calls
        )

    def test_a_project_without_migrations_is_not_told(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        files = {key: value for key, value in self.FILES.items() if "migrations" not in key}
        machine.git.publish(write_tree(tmp_path / "v1", files))
        said: list[str] = []
        deployer = wire(PythonDeployer(verbose=False, runner=machine.runner), machine)
        deployer.logger.substep = said.append  # type: ignore[method-assign]
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        assert deployer.deploy() is True

        assert not any("migrations are not run for you" in line for line in said)

    def test_the_virtual_environments_own_migrations_are_not_the_projects(
        self, tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        files = {key: value for key, value in self.FILES.items() if "migrations" not in key}
        files["venv/lib/python3.12/site-packages/django/contrib/auth/migrations/__init__.py"] = ""
        machine.git.publish(write_tree(tmp_path / "v1", files))
        said: list[str] = []
        deployer = wire(PythonDeployer(verbose=False, runner=machine.runner), machine)
        deployer.logger.substep = said.append  # type: ignore[method-assign]
        deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=False, app_path=root, layout=RELEASES)

        assert deployer.deploy() is True

        assert not any("migrations are not run for you" in line for line in said)
