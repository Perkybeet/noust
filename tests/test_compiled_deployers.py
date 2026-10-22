# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Go and Rust applications built on the server and served as a binary (checklist A-38).

A repository with a ``go.mod`` or a ``Cargo.toml`` used to be only ever a container. The
deployers compile it in the sandbox with the toolchain of the server, keep the modules and the
compiled dependencies in the application's cache, and run ``bin/<app>`` as the unit. What the
tests pin is each decision that is easy to get wrong silently: which package is built, which
program Cargo is asked for, that the build may run offline, that a Dockerfile keeps a repository
a container, and that nothing the repository names reaches a shell.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.package_family import PackageFamily
from noust.core.runner import FakeRunner
from noust.deployers.compiled import (
    GoDeployer,
    RustDeployer,
    choose_go_package,
    choose_rust_binary,
    go_main_packages,
    installed_go_version,
    required_go_version,
)
from noust.deployers.helpers import sandbox
from noust.deployers.registry import DeployerRegistry, detect_app_type

DOMAIN = "svc.example.com"


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


def go_project(root: Path, *, layout: str = "root") -> Path:
    """
    Args:
        root: Where to make it.
        layout: ``root`` (main.go), ``cmd`` (cmd/server), ``two`` (two programs), ``lib``.

    Returns:
        ``root``.
    """
    write(root, "go.mod", "module github.com/acme/server\n\ngo 1.22\n")
    body = "package main\n\nfunc main() {}\n"
    if layout == "root":
        write(root, "main.go", body)
    elif layout == "cmd":
        write(root, "cmd/server/main.go", body)
        write(root, "internal/app/app.go", "package app\n")
    elif layout == "two":
        write(root, "cmd/api/main.go", body)
        write(root, "cmd/migrate/main.go", body)
    elif layout == "lib":
        write(root, "lib.go", "package server\n")
    return root


class TestGoPackages:
    def test_a_main_at_the_root(self, tmp_path: Path) -> None:
        go_project(tmp_path)
        assert go_main_packages(tmp_path) == ["."]

    def test_a_program_under_cmd(self, tmp_path: Path) -> None:
        go_project(tmp_path, layout="cmd")
        assert go_main_packages(tmp_path) == ["./cmd/server"]

    def test_tests_vendor_and_testdata_are_not_programs(self, tmp_path: Path) -> None:
        go_project(tmp_path, layout="lib")
        write(tmp_path, "main_test.go", "package main\n")
        write(tmp_path, "vendor/x/main.go", "package main\n")
        write(tmp_path, "testdata/main.go", "package main\n")
        write(tmp_path, "_scratch/main.go", "package main\n")
        assert go_main_packages(tmp_path) == []

    def test_a_link_is_not_followed(self, tmp_path: Path) -> None:
        go_project(tmp_path, layout="lib")
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        write(outside, "main.go", "package main\n")
        (tmp_path / "cmd").symlink_to(outside, target_is_directory=True)
        assert go_main_packages(tmp_path) == []

    def test_the_root_wins_then_the_only_one(self) -> None:
        assert choose_go_package([".", "./cmd/x"], names=()) == "."
        assert choose_go_package(["./cmd/server"], names=()) == "./cmd/server"

    def test_several_are_told_apart_by_the_module_or_application_name(self) -> None:
        packages = ["./cmd/api", "./cmd/migrate"]
        assert choose_go_package(packages, names=("migrate", "")) == "./cmd/migrate"
        assert choose_go_package(packages, names=("", "api")) == "./cmd/api"

    def test_several_and_no_hint_is_refused_with_the_way_out(self) -> None:
        with pytest.raises(DeploymentError) as caught:
            choose_go_package(["./cmd/api", "./cmd/migrate"], names=("server",))
        assert "./cmd/api, ./cmd/migrate" in str(caught.value)
        assert "build.build" in (caught.value.details or "")

    def test_none_is_refused(self) -> None:
        with pytest.raises(DeploymentError, match="no package main"):
            choose_go_package([], names=())


class TestGoVersions:
    def test_the_toolchain_line_wins_over_the_go_line(self) -> None:
        assert required_go_version("module x\ngo 1.21.0\ntoolchain go1.22.5\n") == (1, 22)
        assert required_go_version("module x\ngo 1.20\n") == (1, 20)
        assert required_go_version("module x\n") is None

    def test_what_go_version_prints(self) -> None:
        assert installed_go_version("go version go1.19.8 linux/amd64") == (1, 19)
        assert installed_go_version("go version go1.23.0 linux/arm64") == (1, 23)
        assert installed_go_version("nonsense") is None


class TestGoDeployer:
    @pytest.fixture
    def deployer(self, tmp_path: Path) -> GoDeployer:
        go_project(tmp_path)
        runner = FakeRunner().script(["go", "version"], stdout="go version go1.22.3 linux/amd64")
        instance = GoDeployer(runner=runner)
        instance.configure(DOMAIN, "src", app_path=tmp_path)
        return instance

    def test_it_is_registered_below_python_and_above_node(self) -> None:
        order = [d.APP_TYPE for d in DeployerRegistry.in_detection_order()]
        assert order.index("python") < order.index("go") < order.index("nodejs")
        assert order.index("go") < order.index("container")

    @pytest.mark.parametrize("layout, expected", [("root", True), ("cmd", True), ("lib", False)])
    def test_detect(self, tmp_path: Path, layout: str, expected: bool) -> None:
        go_project(tmp_path, layout=layout)
        assert GoDeployer().detect(tmp_path) is expected

    def test_a_dockerfile_keeps_it_a_container(self, tmp_path: Path) -> None:
        go_project(tmp_path)
        write(tmp_path, "Dockerfile", "FROM scratch\n")
        assert GoDeployer().detect(tmp_path) is False
        assert detect_app_type(tmp_path) == "container"

    def test_auto_detection_picks_it(self, tmp_path: Path) -> None:
        go_project(tmp_path, layout="cmd")
        assert detect_app_type(tmp_path) == "go"

    def test_the_commands(self, deployer: GoDeployer) -> None:
        assert deployer.get_install_command() == ["go", "mod", "download"]
        assert deployer.get_build_command() == [
            "go",
            "build",
            "-trimpath",
            "-buildvcs=false",
            "-o",
            "bin/svc-example-com",
            ".",
        ]
        assert deployer.get_start_command() == str(deployer.build_path / "bin" / "svc-example-com")

    def test_a_cmd_layout_builds_that_package(self, tmp_path: Path) -> None:
        go_project(tmp_path, layout="cmd")
        deployer = GoDeployer(runner=FakeRunner())
        deployer.configure(DOMAIN, "src", app_path=tmp_path)
        assert deployer.get_build_command()[-1] == "./cmd/server"

    def test_vendored_modules_are_not_downloaded(self, deployer: GoDeployer) -> None:
        write(deployer.build_path, "vendor/modules.txt", "# github.com/x/y v1.0.0\n")
        assert deployer.get_install_command() == []

    def test_describing_the_build_never_fails_on_an_ambiguous_module(self, tmp_path: Path) -> None:
        go_project(tmp_path, layout="two")
        deployer = GoDeployer(runner=FakeRunner())
        deployer.configure(DOMAIN, "src", app_path=tmp_path)
        assert deployer.get_build_command()[-1] == "."
        with pytest.raises(DeploymentError, match="2 programs"):
            deployer.pre_build()

    def test_the_build_block_replaces_the_command_and_skips_the_check(self, tmp_path: Path) -> None:
        go_project(tmp_path, layout="two")
        write(
            tmp_path,
            "noust.yaml",
            "build:\n  build: [go, build, -o, bin/api, ./cmd/api]\n  start: [./bin/api]\n",
        )
        deployer = GoDeployer(runner=FakeRunner())
        deployer.configure(DOMAIN, "src", app_path=tmp_path)
        assert deployer.build_command() == ["go", "build", "-o", "bin/api", "./cmd/api"]
        assert deployer.pre_build() is True

    def test_runtime_is_not_a_key_of_a_go_application(self, tmp_path: Path) -> None:
        go_project(tmp_path)
        write(tmp_path, "noust.yaml", "build:\n  runtime: node@22\n")
        deployer = GoDeployer(runner=FakeRunner())
        deployer.configure(DOMAIN, "src", app_path=tmp_path)
        with pytest.raises(ValidationError, match=r"build\.runtime"):
            deployer.build_config()

    def test_the_unit_gets_the_port_and_the_loopback_address(self, deployer: GoDeployer) -> None:
        environment = deployer._unit_environment()
        assert environment == {"PORT": str(deployer.port), "HOST": "127.0.0.1"}

    def test_a_current_link_is_what_a_release_runs(self, deployer: GoDeployer) -> None:
        deployer._layout = "releases"
        release = deployer.app_path / "releases" / "20261001"
        release.mkdir(parents=True)
        deployer._staged = type("Staged", (), {"path": release})()  # type: ignore[assignment]
        assert deployer.get_start_command() == str(
            deployer.app_path / "current" / "bin" / "svc-example-com"
        )

    def test_install_runs_the_download_and_checks_the_toolchain_first(
        self, deployer: GoDeployer
    ) -> None:
        deployer.install_dependencies()
        runner = deployer.runner
        assert isinstance(runner, FakeRunner)
        assert runner.calls_to("go") == [("go", "version"), ("go", "mod", "download")]

    def test_an_old_go_cannot_build_a_newer_module(self, tmp_path: Path) -> None:
        go_project(tmp_path)
        runner = FakeRunner().script(["go", "version"], stdout="go version go1.19.8 linux/amd64")
        deployer = GoDeployer(runner=runner)
        deployer.configure(DOMAIN, "src", app_path=tmp_path)
        with pytest.raises(DeploymentError) as caught:
            deployer.install_dependencies()
        assert "asks for Go 1.22 and this server has 1.19" in str(caught.value)
        assert runner.calls_to("go") == [("go", "version")]

    def test_a_go_that_fetches_toolchains_is_left_to_do_it(self, tmp_path: Path) -> None:
        write(tmp_path, "go.mod", "module x\n\ngo 1.23\ntoolchain go1.23.4\n")
        write(tmp_path, "main.go", "package main\n")
        runner = FakeRunner().script(["go", "version"], stdout="go version go1.22.3 linux/amd64")
        deployer = GoDeployer(runner=runner)
        deployer.configure(DOMAIN, "src", app_path=tmp_path)
        assert deployer.pre_install() is True

    def test_a_server_without_go_is_told_how_to_install_it(self, deployer: GoDeployer) -> None:
        runner = FakeRunner().only_knows("apt-get")
        deployer._runner = runner
        message = deployer._missing_dependencies_issue(["go"])
        assert message == "Missing system dependencies: go. Install it with: apt install golang-go"

    def test_the_toolchain_packages_are_known_for_every_family(self) -> None:
        assert set(GoDeployer.TOOLCHAIN_PACKAGES) == {
            PackageFamily.APT,
            PackageFamily.DNF,
            PackageFamily.ZYPPER,
        }


SOLO = {
    "packages": [
        {
            "name": "solo",
            "id": "path+file:///p#solo@0.1.0",
            "manifest_path": "/p/Cargo.toml",
            "default_run": None,
            "targets": [{"kind": ["bin"], "name": "solo"}],
        }
    ],
    "workspace_members": ["path+file:///p#solo@0.1.0"],
    "workspace_default_members": ["path+file:///p#solo@0.1.0"],
    "target_directory": "/cache/cargo-target",
}


def workspace(*members: tuple[str, str, str | None, str]) -> dict:
    """
    Args:
        *members: ``(package, directory, default_run, target kind)`` of each member.

    Returns:
        The metadata of a workspace with them, as Cargo prints it.
    """
    packages = []
    ids = []
    for name, directory, default_run, kind in members:
        identifier = f"path+file:///p/{directory}#{name}@0.1.0"
        ids.append(identifier)
        packages.append(
            {
                "name": name,
                "id": identifier,
                "manifest_path": f"/p/{directory}/Cargo.toml".replace("/./", "/"),
                "default_run": default_run,
                "targets": [{"kind": [kind], "name": name}],
            }
        )
    return {
        "packages": packages,
        "workspace_members": ids,
        "workspace_default_members": ids,
        "target_directory": "/cache/cargo-target",
    }


class TestChoosingTheRustProgram:
    def test_one_program(self) -> None:
        chosen, target = choose_rust_binary(json.dumps(SOLO), project=Path("/p"), names=())
        assert chosen == ("solo", "solo")
        assert target == Path("/cache/cargo-target")

    def test_a_library_has_no_program(self) -> None:
        metadata = workspace(("lib", ".", None, "lib"))
        with pytest.raises(DeploymentError, match="builds no program"):
            choose_rust_binary(json.dumps(metadata), project=Path("/p"), names=())

    def test_a_workspace_member_that_is_not_a_default_member_is_left_out(self) -> None:
        metadata = workspace(("a", "a", None, "bin"), ("b", "b", None, "bin"))
        metadata["workspace_default_members"] = [metadata["workspace_members"][1]]
        chosen, _ = choose_rust_binary(json.dumps(metadata), project=Path("/p"), names=())
        assert chosen == ("b", "b")

    def test_default_run_of_the_root_package_decides(self) -> None:
        metadata = workspace(("root", ".", "second", "bin"))
        metadata["packages"][0]["targets"] = [
            {"kind": ["bin"], "name": "first"},
            {"kind": ["bin"], "name": "second"},
        ]
        chosen, _ = choose_rust_binary(json.dumps(metadata), project=Path("/p"), names=())
        assert chosen == ("root", "second")

    def test_the_application_name_decides_among_workspace_members(self) -> None:
        metadata = workspace(("api", "api", None, "bin"), ("worker", "worker", None, "bin"))
        chosen, _ = choose_rust_binary(json.dumps(metadata), project=Path("/p"), names=("worker",))
        assert chosen == ("worker", "worker")

    def test_several_and_nothing_to_tell_them_apart_is_refused(self) -> None:
        metadata = workspace(("api", "api", None, "bin"), ("worker", "worker", None, "bin"))
        with pytest.raises(DeploymentError) as caught:
            choose_rust_binary(json.dumps(metadata), project=Path("/p"), names=("other",))
        assert "api:api, worker:worker" in str(caught.value)
        assert "default-run" in (caught.value.details or "")

    @pytest.mark.parametrize("text", ["", "not json", "[]", '{"packages": []}'])
    def test_something_that_is_not_metadata_is_refused(self, text: str) -> None:
        with pytest.raises(DeploymentError):
            choose_rust_binary(text, project=Path("/p"), names=())


class TestRustDeployer:
    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        write(tmp_path, "Cargo.toml", '[package]\nname = "solo"\nversion = "0.1.0"\n')
        write(tmp_path, "src/main.rs", "fn main() {}\n")
        return tmp_path

    def deployer(self, project: Path, runner: FakeRunner | None = None) -> RustDeployer:
        instance = RustDeployer(runner=runner or FakeRunner())
        instance.configure(DOMAIN, "src", app_path=project)
        return instance

    def test_detect(self, project: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
        assert RustDeployer().detect(project) is True
        assert RustDeployer().detect(tmp_path_factory.mktemp("empty")) is False
        write(project, "Containerfile", "FROM scratch\n")
        assert RustDeployer().detect(project) is False
        assert detect_app_type(project) == "container"

    def test_auto_detection_picks_it(self, project: Path) -> None:
        assert detect_app_type(project) == "rust"

    def test_without_a_lock_file_nothing_is_held_to_one(self, project: Path) -> None:
        deployer = self.deployer(project)
        assert deployer.get_install_command() == ["cargo", "fetch"]
        assert deployer.get_build_command() == ["cargo", "build", "--release", "--offline"]

    def test_with_a_lock_file_both_are_held_to_it(self, project: Path) -> None:
        write(project, "Cargo.lock", "version = 3\n")
        deployer = self.deployer(project)
        assert deployer.get_install_command() == ["cargo", "fetch", "--locked"]
        assert deployer.get_build_command() == [
            "cargo",
            "build",
            "--release",
            "--offline",
            "--locked",
        ]

    def test_install_asks_cargo_which_program_and_the_build_names_it(self, project: Path) -> None:
        write(project, "Cargo.lock", "version = 3\n")
        runner = FakeRunner().script(["cargo", "metadata"], stdout=json.dumps(SOLO))
        deployer = self.deployer(project, runner)

        deployer.install_dependencies()

        assert runner.calls_to("cargo") == [
            ("cargo", "fetch", "--locked"),
            ("cargo", "metadata", "--format-version", "1", "--no-deps"),
        ]
        assert deployer.get_build_command() == [
            "cargo",
            "build",
            "--release",
            "--offline",
            "--locked",
            "-p",
            "solo",
            "--bin",
            "solo",
        ]

    def test_the_binary_is_copied_from_cargos_target_directory_into_the_release(
        self, project: Path
    ) -> None:
        runner = FakeRunner().script(["cargo", "metadata"], stdout=json.dumps(SOLO))
        deployer = self.deployer(project, runner)
        deployer.install_dependencies()

        deployer.post_build()

        assert runner.calls_to("install") == [
            (
                "install",
                "-D",
                "-m",
                "0755",
                "/cache/cargo-target/release/solo",
                str(project / "bin" / "svc-example-com"),
            )
        ]

    def test_a_failed_copy_says_where_cargo_said_the_program_was(self, project: Path) -> None:
        runner = (
            FakeRunner()
            .script(["cargo", "metadata"], stdout=json.dumps(SOLO))
            .script(["install"], stderr="No such file", exit_code=1)
        )
        deployer = self.deployer(project, runner)
        deployer.install_dependencies()
        with pytest.raises(DeploymentError, match="/cache/cargo-target/release/solo"):
            deployer.post_build()

    def test_a_project_cargo_cannot_read_stops_with_cargos_own_words(self, project: Path) -> None:
        runner = FakeRunner().script(
            ["cargo", "metadata"], stderr="error: failed to parse manifest", exit_code=101
        )
        deployer = self.deployer(project, runner)
        with pytest.raises(DeploymentError) as caught:
            deployer.install_dependencies()
        assert "failed to parse manifest" in (caught.value.details or "")

    def test_a_dry_run_has_no_metadata_to_read(self, project: Path) -> None:
        deployer = self.deployer(project, FakeRunner())
        assert deployer.post_install() is True
        assert deployer.target is None

    def test_a_build_block_that_says_both_commands_needs_no_metadata(self, project: Path) -> None:
        write(
            project,
            "noust.yaml",
            "build:\n  build: [cargo, build, --release]\n  start: [./target/release/solo]\n",
        )
        runner = FakeRunner()
        deployer = self.deployer(project, runner)
        deployer.post_install()
        assert runner.calls_to("cargo") == []
        assert deployer.post_build() is True
        assert runner.calls_to("install") == []

    def test_a_server_without_cargo_is_told_how_to_install_it(self, project: Path) -> None:
        deployer = self.deployer(project)
        deployer._runner = FakeRunner().only_knows("dnf")
        assert deployer._missing_dependencies_issue(["cargo"]) == (
            "Missing system dependencies: cargo. Install it with: dnf install cargo"
        )


class TestTheSandboxCache:
    def test_every_toolchain_keeps_what_it_downloads_in_the_applications_cache(self) -> None:
        cache = Path("/var/cache/noust/build/svc-example-com")
        environment = sandbox.build_environment(cache)

        assert environment["GOMODCACHE"] == str(cache / "gopath" / "pkg" / "mod")
        assert environment["GOPATH"] == str(cache / "gopath")
        assert environment["CARGO_HOME"] == str(cache / "cargo")
        assert environment["CARGO_TARGET_DIR"] == str(cache / "cargo-target")
        for name in ("GOMODCACHE", "GOCACHE", "CARGO_HOME", "CARGO_TARGET_DIR"):
            assert environment[name].startswith(str(cache))

    def test_the_jvm_dotnet_and_elixir_toolchains_do_too(self) -> None:
        cache = Path("/var/cache/noust/build/svc-example-com")
        environment = sandbox.build_environment(cache)

        for name in (
            "HOME",  # the wrapper scripts and every tool that reads $HOME
            "GRADLE_USER_HOME",
            "DOTNET_CLI_HOME",
            "NUGET_PACKAGES",
            "MIX_HOME",
            "HEX_HOME",
        ):
            assert environment[name].startswith(str(cache)), name
        # A build nobody watches has no use for a banner or a telemetry ping.
        assert environment["DOTNET_NOLOGO"] == "1"
        assert environment["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"

    def test_a_jvm_finds_its_home_in_the_cache_and_not_in_the_password_file(self) -> None:
        # A JVM takes user.home from the password file and ignores $HOME, and the build
        # account's home is /nonexistent: Maven then failed with "Could not create local
        # repository at /nonexistent/.m2/repository" on the first real build.
        cache = Path("/var/cache/noust/build/svc-example-com")
        environment = sandbox.build_environment(cache)

        assert environment["MAVEN_OPTS"] == f"-Duser.home={cache}"
        assert environment["GRADLE_OPTS"] == f"-Duser.home={cache}"

    def test_the_applications_own_jvm_options_are_kept_and_win(self) -> None:
        cache = Path("/var/cache/noust/build/svc-example-com")
        state = sandbox.SandboxState(domain=DOMAIN, mode="on")

        composed = sandbox.command_environment(
            state,
            sandbox.BuildPhase.BUILD,
            configured={"MAVEN_OPTS": "-Xmx512m -Duser.home=/srv/me", "NODE_ENV": "production"},
            given=None,
            cache=cache,
        )

        # Ours first, theirs after: the JVM reads the last -D of a property, so an operator who
        # chose a home on purpose keeps it, and the heap they asked for is not lost.
        assert composed["MAVEN_OPTS"] == f"-Duser.home={cache} -Xmx512m -Duser.home=/srv/me"
        assert composed["GRADLE_OPTS"] == f"-Duser.home={cache}"
        assert composed["NODE_ENV"] == "production"
