# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
ASP.NET Core applications published on the server and run with ``dotnet`` (checklist A-40).

A repository with a ``.csproj`` used to be only ever a container. The tests pin what a .NET
deployment turns on: which project of a tree is the web one, that restore is the install and
publish runs without a network, where the program ends up and what the unit tells it, and the
SDK check before anything is downloaded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.deployers.dotnet import (
    PUBLISH_DIR,
    DotnetDeployer,
    choose_project,
    published_assemblies,
    runtime_framework_major,
    runtime_majors,
    sdk_majors,
    target_major,
    web_projects,
)
from noust.deployers.registry import DeployerRegistry, detect_app_type

DOMAIN = "shop.example.com"

WEB = '<Project Sdk="Microsoft.NET.Sdk.Web">\n  <PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>\n</Project>\n'
LIB = '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>\n</Project>\n'
CONSOLE_WITH_ASPNET = (
    '<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>'
    '<FrameworkReference Include="Microsoft.AspNetCore.App" /></ItemGroup>\n</Project>\n'
)


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


def deployer_for(root: Path, runner: FakeRunner | None = None) -> DotnetDeployer:
    """
    Args:
        root: The project.
        runner: What runs its commands.

    Returns:
        A configured deployer.
    """
    instance = DotnetDeployer(runner=runner or FakeRunner())
    instance.configure(DOMAIN, "src", app_path=root)
    return instance


def solution(root: Path) -> Path:
    """
    Args:
        root: Where to make it.

    Returns:
        ``root``: a solution with a web project, a library and a test project.
    """
    write(root, "Shop.sln", "")
    write(root, "src/Shop.Web/Shop.Web.csproj", WEB)
    write(root, "src/Shop.Core/Shop.Core.csproj", LIB)
    write(root, "tests/Shop.Web.Tests/Shop.Web.Tests.csproj", WEB)
    return root


def published(root: Path, *names: str, framework: str = "8.0.0") -> None:
    """
    Args:
        root: The project.
        *names: Programs to leave in the publish directory.
        framework: The ASP.NET Core version their runtime config names.
    """
    for name in names:
        write(root, f"{PUBLISH_DIR}/{name}.dll", "MZ")
        config = {
            "runtimeOptions": {
                "tfm": "net8.0",
                "framework": {"name": "Microsoft.AspNetCore.App", "version": framework},
            }
        }
        write(root, f"{PUBLISH_DIR}/{name}.runtimeconfig.json", json.dumps(config))


class TestFindingTheWebProject:
    def test_a_project_at_the_root(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        assert web_projects(tmp_path) == ["Shop.csproj"]

    def test_a_solution_keeps_it_below_and_the_tests_are_left_out(self, tmp_path: Path) -> None:
        assert web_projects(solution(tmp_path)) == ["src/Shop.Web/Shop.Web.csproj"]

    def test_a_test_project_is_left_out_by_its_name_too(self, tmp_path: Path) -> None:
        write(tmp_path, "src/Shop.Tests/Shop.Tests.csproj", WEB)
        write(tmp_path, "src/Shop.IntegrationTests/x.csproj", WEB)
        write(tmp_path, "src/Shop.Web/Shop.Web.IntegrationTests.csproj", WEB)
        assert web_projects(tmp_path) == []

    def test_a_console_program_that_references_asp_net_core_is_a_web_project(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "Api.fsproj", CONSOLE_WITH_ASPNET)
        assert web_projects(tmp_path) == ["Api.fsproj"]

    def test_output_directories_are_not_searched(self, tmp_path: Path) -> None:
        write(tmp_path, "obj/Shop.csproj", WEB)
        write(tmp_path, "bin/Release/Shop.csproj", WEB)
        assert web_projects(tmp_path) == []

    def test_a_project_file_that_is_a_link_is_not_read(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-Shop.csproj"
        outside.write_text(WEB)
        (tmp_path / "Shop.csproj").symlink_to(outside)
        assert web_projects(tmp_path) == []

    def test_the_shallowest_comes_first(self, tmp_path: Path) -> None:
        write(tmp_path, "src/B/B.csproj", WEB)
        write(tmp_path, "A.csproj", WEB)
        assert web_projects(tmp_path) == ["A.csproj", "src/B/B.csproj"]


class TestChoosingTheProject:
    def test_the_only_one(self) -> None:
        assert choose_project(["src/Web/Web.csproj"], names=()) == "src/Web/Web.csproj"

    def test_none_is_refused_with_the_way_out(self) -> None:
        with pytest.raises(DeploymentError) as caught:
            choose_project([], names=())
        assert "no ASP.NET Core project" in str(caught.value)
        assert "build.build" in (caught.value.details or "")

    def test_the_applications_name_decides_by_file_or_directory(self) -> None:
        projects = ["src/Api/Api.csproj", "src/Admin/Admin.csproj"]
        assert choose_project(projects, names=("admin",)) == "src/Admin/Admin.csproj"
        assert choose_project(["a/X.csproj", "b/Y.csproj"], names=("b",)) == "b/Y.csproj"

    def test_the_only_shallowest_wins(self) -> None:
        assert choose_project(["Web.csproj", "src/Api/Api.csproj"], names=("zzz",)) == "Web.csproj"

    def test_several_at_the_same_depth_and_nothing_to_tell_them_apart_is_refused(self) -> None:
        with pytest.raises(DeploymentError) as caught:
            choose_project(["src/Api/Api.csproj", "src/Admin/Admin.csproj"], names=("shop",))
        assert "2 web projects" in str(caught.value)
        assert "build.start" in (caught.value.details or "")


class TestReadingWhatDotnetPrints:
    def test_the_target_framework(self) -> None:
        assert target_major(WEB) == 8
        assert target_major("<TargetFrameworks>net6.0;net8.0</TargetFrameworks>") == 8
        assert target_major("<TargetFramework>netcoreapp3.1</TargetFramework>") == 3
        assert target_major("<TargetFramework>netstandard2.0</TargetFramework>") is None
        assert target_major("<Project/>") is None

    def test_the_sdks_and_runtimes_the_server_has(self) -> None:
        sdks = "6.0.428 [/usr/lib/dotnet/sdk]\n8.0.404 [/usr/lib/dotnet/sdk]\n"
        assert sdk_majors(sdks) == [6, 8]
        runtimes = (
            "Microsoft.AspNetCore.App 8.0.11 [/usr/lib/dotnet/shared/Microsoft.AspNetCore.App]\n"
            "Microsoft.NETCore.App 9.0.0 [/usr/lib/dotnet/shared/Microsoft.NETCore.App]\n"
        )
        assert runtime_majors(runtimes) == [8]
        assert sdk_majors("nonsense") == []

    def test_the_framework_a_program_runs_on(self) -> None:
        config = json.dumps(
            {
                "runtimeOptions": {
                    "framework": {"name": "Microsoft.AspNetCore.App", "version": "8.0.0"}
                }
            }
        )
        assert runtime_framework_major(config) == 8
        frameworks = json.dumps(
            {
                "runtimeOptions": {
                    "frameworks": [{"name": "Microsoft.AspNetCore.App", "version": "9.0.1"}]
                }
            }
        )
        assert runtime_framework_major(frameworks) == 9
        console = json.dumps(
            {"runtimeOptions": {"framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"}}}
        )
        assert runtime_framework_major(console) is None
        assert runtime_framework_major("not json") is None
        assert runtime_framework_major("{}") is None

    def test_a_library_has_no_runtime_config_and_is_not_a_program(self, tmp_path: Path) -> None:
        write(tmp_path, f"{PUBLISH_DIR}/Core.dll", "MZ")
        published(tmp_path, "Web")
        write(tmp_path, f"{PUBLISH_DIR}/Orphan.runtimeconfig.json", "{}")
        assert published_assemblies(tmp_path / PUBLISH_DIR) == ["Web"]


class TestDetection:
    def test_it_sits_between_python_and_node(self) -> None:
        order = [d.APP_TYPE for d in DeployerRegistry.in_detection_order()]
        assert order.index("python") < order.index("dotnet") < order.index("nodejs")

    def test_a_web_project(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        assert DotnetDeployer().detect(tmp_path) is True
        assert detect_app_type(tmp_path) == "dotnet"

    def test_a_solution(self, tmp_path: Path) -> None:
        assert DotnetDeployer().detect(solution(tmp_path)) is True

    def test_a_library_has_nothing_to_serve(self, tmp_path: Path) -> None:
        write(tmp_path, "Core.csproj", LIB)
        assert DotnetDeployer().detect(tmp_path) is False

    def test_a_dockerfile_keeps_it_a_container(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        write(tmp_path, "Dockerfile", "FROM mcr.microsoft.com/dotnet/aspnet:8.0\n")
        assert DotnetDeployer().detect(tmp_path) is False
        assert detect_app_type(tmp_path) == "container"

    def test_the_sparse_checkout_of_an_inspection_fetches_the_projects_of_a_solution(self) -> None:
        patterns = set(DotnetDeployer.DETECTION_FILES) | set(DotnetDeployer.FRAMEWORK_CONFIG_FILES)
        assert "*.csproj" in patterns
        assert "*/*/*.csproj" in patterns
        assert "global.json" in patterns


class TestCommands:
    def test_restore_is_the_install_and_names_the_project(self, tmp_path: Path) -> None:
        deployer = deployer_for(solution(tmp_path))
        assert deployer.get_install_command() == [
            "dotnet",
            "restore",
            "src/Shop.Web/Shop.Web.csproj",
        ]

    def test_a_committed_lock_file_holds_the_restore_to_it(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        write(tmp_path, "packages.lock.json", "{}")
        assert deployer_for(tmp_path).get_install_command() == [
            "dotnet",
            "restore",
            "Shop.csproj",
            "--locked-mode",
        ]

    def test_publish_runs_offline_without_the_native_host_or_the_compiler_servers(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        assert deployer_for(tmp_path).get_build_command() == [
            "dotnet",
            "publish",
            "Shop.csproj",
            "-c",
            "Release",
            "-o",
            PUBLISH_DIR,
            "--no-restore",
            "--nologo",
            "-p:UseAppHost=false",
            "-p:UseSharedCompilation=false",
            "-nodeReuse:false",
        ]

    def test_describing_the_build_never_fails_on_an_ambiguous_tree(self, tmp_path: Path) -> None:
        write(tmp_path, "src/Api/Api.csproj", WEB)
        write(tmp_path, "src/Admin/Admin.csproj", WEB)
        deployer = deployer_for(tmp_path)
        assert deployer.get_build_command()[2] == "."
        with pytest.raises(DeploymentError, match="2 web projects"):
            deployer.pre_build()

    def test_the_build_block_replaces_the_command_and_skips_the_check(self, tmp_path: Path) -> None:
        write(tmp_path, "src/Api/Api.csproj", WEB)
        write(tmp_path, "src/Admin/Admin.csproj", WEB)
        write(
            tmp_path,
            "noust.yaml",
            "build:\n  build: [dotnet, publish, src/Api/Api.csproj, -c, Release, -o, .publish]\n"
            "  start: [dotnet, .publish/Api.dll]\n",
        )
        deployer = deployer_for(tmp_path)
        assert deployer.pre_build() is True
        assert deployer.build_command()[2] == "src/Api/Api.csproj"

    def test_the_unit_runs_the_dll_through_current(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        deployer = deployer_for(tmp_path)
        assert deployer.get_start_command() == f"dotnet {tmp_path}/{PUBLISH_DIR}/Shop.dll"

    def test_the_assembly_name_of_the_project_decides_before_it_is_published(
        self, tmp_path: Path
    ) -> None:
        write(
            tmp_path,
            "Shop.csproj",
            WEB.replace(
                "<PropertyGroup>", "<PropertyGroup><AssemblyName>Storefront</AssemblyName>"
            ),
        )
        assert deployer_for(tmp_path).get_start_command().endswith("/Storefront.dll")

    def test_the_unit_listens_on_the_loopback_address_with_the_publish_directory_as_content(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        deployer = deployer_for(tmp_path)
        environment = deployer._unit_environment()
        assert environment["ASPNETCORE_URLS"] == f"http://127.0.0.1:{deployer.port}"
        assert environment["ASPNETCORE_CONTENTROOT"] == str(tmp_path / PUBLISH_DIR)
        assert environment["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"


class TestBeforeAnythingIsDownloaded:
    def test_an_sdk_older_than_the_target_framework_is_refused_with_the_way_out(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        runner = (
            FakeRunner()
            .only_knows("dotnet", "apt-get")
            .script(["dotnet", "--list-sdks"], stdout="6.0.428 [/x]\n")
        )
        deployer = deployer_for(tmp_path, runner)
        with pytest.raises(DeploymentError) as caught:
            deployer.pre_install()
        assert "targets .NET 8 and this server's newest SDK is 6" in str(caught.value)
        assert "apt install dotnet-sdk-8.0" in (caught.value.details or "")

    def test_a_newer_sdk_builds_an_older_target(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        runner = FakeRunner().script(["dotnet", "--list-sdks"], stdout="9.0.100 [/x]\n")
        assert deployer_for(tmp_path, runner).pre_install() is True

    def test_install_checks_the_sdk_then_restores(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        runner = FakeRunner().script(["dotnet", "--list-sdks"], stdout="8.0.404 [/x]\n")
        deployer_for(tmp_path, runner).install_dependencies()
        assert runner.calls_to("dotnet") == [
            ("dotnet", "--list-sdks"),
            ("dotnet", "restore", "Shop.csproj"),
        ]

    def test_a_tree_with_two_web_projects_is_refused_before_restoring(self, tmp_path: Path) -> None:
        write(tmp_path, "src/Api/Api.csproj", WEB)
        write(tmp_path, "src/Admin/Admin.csproj", WEB)
        runner = FakeRunner()
        with pytest.raises(DeploymentError, match="2 web projects"):
            deployer_for(tmp_path, runner).install_dependencies()
        assert runner.calls_to("dotnet") == []

    def test_a_server_without_the_sdk_is_told_how_to_install_it(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        deployer = deployer_for(tmp_path)
        deployer._runner = FakeRunner().only_knows("zypper")
        assert deployer._missing_dependencies_issue(["dotnet"]) == (
            "Missing system dependencies: dotnet. Install it with: zypper install dotnet-sdk-8.0"
        )


class TestAfterThePublish:
    def test_the_program_is_the_assembly_with_a_runtime_config(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        published(tmp_path, "Shop")
        write(tmp_path, f"{PUBLISH_DIR}/Dependency.dll", "MZ")
        runner = FakeRunner().script(
            ["dotnet", "--list-runtimes"],
            stdout="Microsoft.AspNetCore.App 8.0.11 [/x]\n",
        )
        deployer = deployer_for(tmp_path, runner)

        deployer.post_build()

        assert deployer.assembly == "Shop"
        assert deployer.get_start_command().endswith(f"{PUBLISH_DIR}/Shop.dll")

    def test_the_project_names_which_of_several_programs_runs(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        published(tmp_path, "Shop", "Migrator")
        deployer = deployer_for(tmp_path)
        deployer.post_build()
        assert deployer.assembly == "Shop"

    def test_several_programs_and_nothing_to_tell_them_apart_is_refused(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        published(tmp_path, "One", "Two")
        with pytest.raises(DeploymentError) as caught:
            deployer_for(tmp_path).post_build()
        assert "2 programs" in str(caught.value)

    def test_a_publish_that_wrote_no_program_says_what_a_program_has(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        write(tmp_path, f"{PUBLISH_DIR}/Core.dll", "MZ")
        with pytest.raises(DeploymentError) as caught:
            deployer_for(tmp_path).post_build()
        assert "runtimeconfig.json" in (caught.value.details or "")

    def test_a_runtime_the_server_lacks_is_a_warning_not_a_refusal(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        published(tmp_path, "Shop", framework="8.0.0")
        runner = FakeRunner().script(
            ["dotnet", "--list-runtimes"], stdout="Microsoft.AspNetCore.App 9.0.0 [/x]\n"
        )
        deployer = deployer_for(tmp_path, runner)
        lines: list[str] = []
        deployer.logger.attach_sink(lines.append)

        assert deployer.post_build() is True

        assert any("runs on ASP.NET Core 8 and this server has 9" in line for line in lines)
        assert any("DOTNET_ROLL_FORWARD=Major" in line for line in lines)

    def test_a_dry_run_publishes_nothing(self, tmp_path: Path) -> None:
        write(tmp_path, "Shop.csproj", WEB)
        deployer = deployer_for(tmp_path)
        assert deployer.post_build() is True
        assert deployer.assembly is None
