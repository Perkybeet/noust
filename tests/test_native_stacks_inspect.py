# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``noust inspect`` says about Go, Rust and Ruby repositories (checklist A-38, A-39).

They are deployed natively since 4.0: the verdict names the type and the commands, says how to
install the toolchain when the server lacks it, and says "no program" (not "no deployer") for a
repository of the language that has nothing to run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.deployers.inspect import inspect_source
from tests.support.native_stacks_inspect import (  # noqa: F401
    GO,
    project,
    store,
)

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811


RUST = {
    "Cargo.toml": '[package]\nname = "app"\nversion = "0.1.0"\n',
    "src/main.rs": "fn main() {}\n",
}
RUBY = {
    "Gemfile": "gem 'rails'\ngem 'puma'\n",
    "config/application.rb": "",
    "config.ru": "",
    "app/assets/x.css": "",
}


def test_a_go_module_is_deployed_natively(tmp_path: Path, store: NoustStore) -> None:
    set_runner(FakeRunner().only_knows("git", "go"))

    result = inspect_source(project(tmp_path / "p", GO))

    assert result.app_type == "go"
    assert result.compatible is True
    assert result.verdict == "Noust can deploy this as Go."
    assert result.install_command == ["go", "mod", "download"]
    assert result.build_command[:2] == ["go", "build"]
    assert result.default_port == 8080


def test_a_rust_crate_is_deployed_natively(tmp_path: Path, store: NoustStore) -> None:
    set_runner(FakeRunner().only_knows("git", "cargo"))

    result = inspect_source(project(tmp_path / "p", RUST))

    assert result.app_type == "rust"
    assert result.compatible is True
    assert result.install_command == ["cargo", "fetch"]
    assert result.build_command == ["cargo", "build", "--release", "--offline"]


def test_a_rails_application_is_deployed_natively(tmp_path: Path, store: NoustStore) -> None:
    set_runner(FakeRunner().only_knows("git", "ruby", "bundle"))

    result = inspect_source(project(tmp_path / "p", RUBY))

    assert result.app_type == "ruby"
    assert result.compatible is True
    assert result.build_command == ["bundle", "exec", "rails", "assets:precompile"]
    assert result.start_command.startswith("bundle exec puma")


@pytest.mark.parametrize(
    ("files", "program", "install"),
    [
        (GO, "go", "apt install golang-go"),
        (RUST, "cargo", "apt install cargo"),
        (RUBY, "ruby, bundle", "apt install ruby ruby-dev bundler build-essential"),
    ],
)
def test_a_server_without_the_toolchain_is_told_how_to_install_it(
    tmp_path: Path, store: NoustStore, files: dict[str, str], program: str, install: str
) -> None:
    set_runner(FakeRunner().only_knows("git", "apt-get"))

    result = inspect_source(project(tmp_path / "p", files))

    assert result.compatible is False
    assert program in result.verdict
    assert result.suggestion is not None
    assert install in result.suggestion


def test_a_repository_that_ships_a_dockerfile_stays_a_container(
    tmp_path: Path, store: NoustStore
) -> None:
    set_runner(FakeRunner().only_knows("git", "docker", "go"))

    result = inspect_source(project(tmp_path / "p", {**GO, "Dockerfile": "FROM golang\n"}))

    assert result.app_type == "container"


@pytest.mark.parametrize(
    ("files", "language"),
    [
        ({"go.mod": "module example.com/lib\n", "lib.go": "package lib\n"}, "Go"),
        ({"Cargo.toml": '[package]\nname = "lib"\n', "src/lib.rs": ""}, "Rust"),
        ({"Gemfile": "gemspec\n", "lib/thing.rb": ""}, "Ruby"),
    ],
)
def test_a_library_has_no_program_to_run(
    tmp_path: Path, store: NoustStore, files: dict[str, str], language: str
) -> None:
    set_runner(FakeRunner().only_knows("git", "docker"))

    with pytest.raises(DeploymentError) as caught:
        inspect_source(project(tmp_path / "p", files))

    assert f"This is a {language} project" in caught.value.message
    assert "found no program to run" in caught.value.message
    assert "--type container" in caught.value.details
