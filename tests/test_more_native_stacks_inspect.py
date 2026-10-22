# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``noust inspect`` says about Java and Kotlin, .NET, Elixir and Deno repositories (A-40).

They are deployed natively since 4.0: the verdict names the type and the commands, says how to
install the toolchain when the server lacks it, and says "no program" (not "no deployer") for a
repository of the language that has nothing to run. The commands never name the scratch
directory the repository was inspected in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.deployers.inspect import inspect_source
from tests.test_native_stacks_inspect import GO, project, store  # noqa: F401

JAVA = {
    "pom.xml": "<project><parent>spring-boot-starter-parent</parent></project>\n",
    "src/main/java/App.java": "class App {}\n",
}
DOTNET = {
    "Shop.sln": "",
    "src/Web/Web.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n',
    "src/Web/Program.cs": "",
}
ELIXIR = {
    "mix.exs": "defmodule Shop.MixProject do\n  def application, do: [mod: {Shop.App, []}]\nend\n",
    "lib/shop.ex": "",
}
DENO = {
    "deno.json": '{"tasks": {"start": "deno run -A main.ts"}}\n',
    "main.ts": "Deno.serve(() => new Response('ok'))\n",
}


def test_a_spring_boot_application_is_deployed_natively(tmp_path: Path, store: NoustStore) -> None:  # noqa: F811
    set_runner(FakeRunner().only_knows("git", "java"))

    result = inspect_source(project(tmp_path / "p", JAVA))

    assert result.app_type == "java"
    assert result.compatible is True
    assert result.build_command == ["mvn", "-B", "-ntp", "-DskipTests", "package"]
    assert result.start_command == "java -jar ./dist/app.jar --server.port=${PORT}"
    assert result.default_port == 8080


def test_an_aspnet_core_project_is_deployed_natively(tmp_path: Path, store: NoustStore) -> None:  # noqa: F811
    set_runner(FakeRunner().only_knows("git", "dotnet"))

    result = inspect_source(project(tmp_path / "p", DOTNET))

    assert result.app_type == "dotnet"
    assert result.compatible is True
    assert result.install_command == ["dotnet", "restore", "src/Web/Web.csproj"]
    assert result.build_command[:3] == ["dotnet", "publish", "src/Web/Web.csproj"]
    assert result.start_command == "dotnet ./.publish/Web.dll"
    assert result.default_port == 5000


def test_a_phoenix_application_is_deployed_natively(tmp_path: Path, store: NoustStore) -> None:  # noqa: F811
    set_runner(FakeRunner().only_knows("git", "elixir", "mix"))

    result = inspect_source(project(tmp_path / "p", ELIXIR))

    assert result.app_type == "elixir"
    assert result.compatible is True
    assert result.build_command == ["mix", "release", "--overwrite"]
    assert result.start_command.startswith("./_build/prod/rel/")
    assert result.start_command.endswith(" start")
    assert result.default_port == 4000


def test_a_deno_program_is_deployed_natively(tmp_path: Path, store: NoustStore) -> None:  # noqa: F811
    set_runner(FakeRunner().only_knows("git", "deno"))

    result = inspect_source(project(tmp_path / "p", DENO))

    assert result.app_type == "deno"
    assert result.compatible is True
    assert result.install_command == ["deno", "install", "--entrypoint", "main.ts"]
    assert result.start_command == "deno task start"
    assert result.default_port == 8000


@pytest.mark.parametrize(
    ("files", "program", "install"),
    [
        (JAVA, "java", "apt install default-jdk-headless maven"),
        (DOTNET, "dotnet", "apt install dotnet-sdk-8.0"),
        (ELIXIR, "elixir, mix", "apt install elixir erlang-dev build-essential"),
    ],
)
def test_a_server_without_the_new_toolchains_is_told_how_to_install_them(
    tmp_path: Path,
    store: NoustStore,  # noqa: F811
    files: dict[str, str],
    program: str,
    install: str,
) -> None:
    set_runner(FakeRunner().only_knows("git", "apt-get"))

    result = inspect_source(project(tmp_path / "p", files))

    assert result.compatible is False
    assert program in result.verdict
    assert result.suggestion is not None
    assert install in result.suggestion


def test_a_server_without_deno_is_told_where_it_comes_from(
    tmp_path: Path,
    store: NoustStore,  # noqa: F811
) -> None:
    set_runner(FakeRunner().only_knows("git", "apt-get"))

    result = inspect_source(project(tmp_path / "p", DENO))

    assert result.compatible is False
    assert result.suggestion is not None
    assert "Deno 2" in result.suggestion


@pytest.mark.parametrize("files", [JAVA, DOTNET, ELIXIR, DENO])
def test_a_dockerfile_keeps_these_a_container_too(
    tmp_path: Path,
    store: NoustStore,  # noqa: F811
    files: dict[str, str],
) -> None:
    set_runner(FakeRunner().only_knows("git", "docker"))

    result = inspect_source(project(tmp_path / "p", {**files, "Dockerfile": "FROM scratch\n"}))

    assert result.app_type == "container"


@pytest.mark.parametrize(
    ("files", "language"),
    [
        ({"pom.xml": "<project><artifactId>util</artifactId></project>\n"}, "Java (Maven)"),
        ({"build.gradle": "plugins { id 'java-library' }\n"}, "Java (Gradle)"),
        ({"build.gradle.kts": "plugins { `java-library` }\n"}, "Kotlin (Gradle)"),
        (
            {"mix.exs": "defmodule Slug.MixProject do\n  def application, do: []\nend\n"},
            "Elixir",
        ),
        ({"deno.json": '{"name": "@acme/lib", "exports": "./mod.ts"}'}, "Deno"),
        ({"deno.jsonc": '{"name": "@acme/lib", "exports": "./mod.ts"}'}, "Deno"),
    ],
)
def test_a_library_of_these_languages_has_no_program_to_run(
    tmp_path: Path,
    store: NoustStore,  # noqa: F811
    files: dict[str, str],
    language: str,
) -> None:
    set_runner(FakeRunner().only_knows("git", "docker"))

    with pytest.raises(DeploymentError) as caught:
        inspect_source(project(tmp_path / "p", files))

    assert f"This is a {language} project" in caught.value.message
    assert "found no program to run" in caught.value.message
    assert "--type container" in caught.value.details


def test_a_dotnet_library_has_no_web_project_to_run(
    tmp_path: Path,
    store: NoustStore,  # noqa: F811
) -> None:
    set_runner(FakeRunner().only_knows("git", "docker"))
    library = {"Core.csproj": '<Project Sdk="Microsoft.NET.Sdk"></Project>\n'}

    with pytest.raises(DeploymentError) as caught:
        inspect_source(project(tmp_path / "p", library))

    assert "This is a .NET project (Core.csproj)" in caught.value.message
    assert "no web project" in caught.value.message
    assert "--type container" in caught.value.details


def test_commands_never_name_the_scratch_checkout(
    tmp_path: Path,
    store: NoustStore,  # noqa: F811
) -> None:
    """A path that is gone when the inspection returns is not one to show an operator."""
    set_runner(FakeRunner().only_knows("git", "go"))

    result = inspect_source(project(tmp_path / "p", GO))

    assert str(tmp_path) not in result.start_command
    assert result.start_command.startswith("./bin/")
