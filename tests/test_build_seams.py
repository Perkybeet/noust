# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The small seams the build block goes through: program resolution and the sandbox's directories.
"""

from __future__ import annotations

from pathlib import Path

from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.path_resolver import PathResolver
from noust.deployers.helpers.sandbox import BuildPhase, SandboxState


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


class TestPathResolver:
    def test_a_pinned_runtimes_program_comes_before_the_systems(self, tmp_path: Path) -> None:
        npm = executable(tmp_path / "runtime" / "bin" / "npm")

        resolved = PathResolver().resolve_command(
            "npm run start", prefer=[tmp_path / "runtime" / "bin"]
        )

        assert resolved == f"{npm} run start"

    def test_a_program_the_runtime_does_not_have_falls_through_to_the_system(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "bin").mkdir()

        resolved = PathResolver().resolve_command("sh -c x", prefer=[tmp_path / "bin"])

        assert resolved.endswith("/sh -c x")
        assert str(tmp_path) not in resolved

    def test_a_file_that_is_not_executable_is_not_a_program(self, tmp_path: Path) -> None:
        program = tmp_path / "bin" / "sh"
        program.parent.mkdir()
        program.write_text("")

        resolved = PathResolver().resolve_command("sh -c x", prefer=[tmp_path / "bin"])

        assert str(tmp_path) not in resolved

    def test_without_a_preference_nothing_changes(self) -> None:
        resolver = PathResolver()

        assert resolver.resolve_command("sh -c x") == resolver.resolve_command("sh -c x", prefer=[])

    def test_an_absolute_program_is_left_alone(self, tmp_path: Path) -> None:
        npm = executable(tmp_path / "runtime" / "bin" / "npm")

        resolved = PathResolver().resolve_command(f"{npm} ci", prefer=[tmp_path / "other"])

        assert resolved == f"{npm} ci"


class TestTheSandboxOfAProjectInASubdirectory:
    def spec(self, tmp_path: Path, **extra: Path) -> build_sandbox.SandboxSpec:
        release = tmp_path / "apps" / "shop" / "releases" / "r1"
        return build_sandbox.build_spec(
            app=None,
            app_name="shop",
            phase=BuildPhase.BUILD,
            state=SandboxState(domain="shop.example.com", mode="on"),
            user="noust-build",
            group="noust-build",
            build_path=release,
            apps_dir=tmp_path / "apps",
            cache=tmp_path / "cache" / "shop",
            env_file=None,
            shared=None,
            **extra,
        )

    def test_the_command_runs_in_the_project_and_may_write_the_whole_release(
        self, tmp_path: Path
    ) -> None:
        release = tmp_path / "apps" / "shop" / "releases" / "r1"

        spec = self.spec(tmp_path, work_dir=release / "apps" / "web")

        # A workspace installs and builds at its root.
        assert spec.working_dir == release / "apps" / "web"
        assert release in spec.writable_paths

    def test_without_a_subdirectory_it_runs_where_it_always_did(self, tmp_path: Path) -> None:
        release = tmp_path / "apps" / "shop" / "releases" / "r1"

        spec = self.spec(tmp_path)

        assert spec.working_dir == release
        assert release in spec.writable_paths
