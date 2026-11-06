# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the new-application wizard is told about a source is a sentence with a code.

The verdict ("Noust can deploy this as Go", "this server does not have python3"), what to do
about it and the reason a source could not be recognised at all were English text that the wizard
drew in a Spanish console as it came. They are entries of the one catalog
(``noust.core.server_text``) now, carried beside the text the API always sent: ``verdict_coded``
and ``suggestion_coded`` on an inspection, and ``coded`` on the error of a source nothing
recognised. The rules of detection are in ``tests/test_inspect.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner, set_runner
from noust.core.server_text import CATALOG, HINT_SUFFIX
from noust.core.store import NoustStore
from noust.deployers.inspect import inspect_source

NEXT_PACKAGE = json.dumps(
    {
        "name": "example-app",
        "scripts": {"build": "next build", "start": "next start"},
        "dependencies": {"next": "^14.0.0", "react": "^18.0.0"},
    }
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def no_railpack(store: NoustStore) -> Iterator[None]:
    """A server that has docker and git, and not Railpack."""
    set_runner(FakeRunner().only_knows("git", "docker"))
    yield
    set_runner(None)


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _refused(project: Path) -> DeploymentError:
    with pytest.raises(DeploymentError) as caught:
        inspect_source(str(project))
    return caught.value


# -- a source that can be deployed ------------------------------------------------------------


def test_a_deployable_source_says_so_with_a_code(tmp_path: Path, store: NoustStore) -> None:
    project = _write(tmp_path / "p", {"next.config.js": "", "package.json": NEXT_PACKAGE})

    result = inspect_source(str(project))

    assert result.verdict_coded is not None
    assert result.verdict_coded.code == "inspect.can_deploy"
    assert result.verdict_coded.params == {"type": "Next.js"}
    assert result.verdict == result.verdict_coded.text == "Noust can deploy this as Next.js."
    assert result.suggestion is None and result.suggestion_coded is None


def test_a_source_that_looks_like_another_type_too_says_which(
    tmp_path: Path, store: NoustStore
) -> None:
    generic = json.dumps({"name": "x", "scripts": {"start": "node server.js"}})
    project = _write(tmp_path / "p", {"next.config.js": "", "package.json": generic})

    result = inspect_source(str(project))

    assert result.verdict_coded is not None
    assert result.verdict_coded.code == "inspect.can_deploy_also"
    assert set(result.verdict_coded.params) == {"type", "others"}
    assert result.verdict == result.verdict_coded.text
    assert "It also looks like" in result.verdict


def test_a_container_says_how_it_would_be_built(tmp_path: Path, store: NoustStore) -> None:
    project = _write(tmp_path / "p", {"Dockerfile": "FROM node:22\nEXPOSE 3000\n"})

    result = inspect_source(str(project))

    assert result.verdict_coded is not None
    assert result.verdict_coded.code == "inspect.container_dockerfile"
    assert result.verdict == "Noust can deploy this as a container, built from its Dockerfile."


# -- a source this server cannot build yet ----------------------------------------------------


def test_a_missing_program_and_what_to_do_about_it_are_two_sentences(
    tmp_path: Path, store: NoustStore
) -> None:
    set_runner(FakeRunner().only_knows("git"))
    try:
        project = _write(tmp_path / "p", {"requirements.txt": "flask\n"})

        result = inspect_source(str(project))
    finally:
        set_runner(None)

    assert result.compatible is False
    assert result.verdict_coded is not None
    assert result.verdict_coded.code == "inspect.missing_programs"
    assert str(result.verdict_coded.params["type"]).startswith("Python")
    assert "python3" in str(result.verdict_coded.params["programs"])
    assert result.suggestion_coded is not None
    assert result.suggestion_coded.code in {
        "inspect.install_toolchain",
        "inspect.install_setup_init",
    }
    assert result.suggestion == result.suggestion_coded.text
    # The command is a value, shown as it is and never translated.
    assert "command" in result.suggestion_coded.params


# -- a source nothing recognised --------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ({"README.md": "x"}, "inspect.no_match"),
        ({"package.json": json.dumps({"name": "x"})}, "inspect.package_json.no_start"),
        ({"package.json": "{"}, "inspect.package_json.unreadable"),
        (
            {"frontend/package.json": NEXT_PACKAGE, "backend/requirements.txt": "fastapi\n"},
            "inspect.subprojects",
        ),
        ({"App.sln": ""}, "inspect.dotnet.no_web_project"),
        ({"go.mod": "module example.com/app\n"}, "inspect.stack.no_program"),
        ({"turbo.json": "{}"}, "inspect.turbo.no_workspace_no_apps"),
        (
            {"turbo.json": "{}", "apps/web/package.json": NEXT_PACKAGE},
            "inspect.turbo.no_workspace_one_app",
        ),
        (
            {"turbo.json": "{}", "pnpm-workspace.yaml": "packages: ['apps/*']\n"},
            "inspect.turbo.no_apps",
        ),
        (
            {
                "turbo.json": "{}",
                "pnpm-workspace.yaml": "packages: ['apps/*']\n",
                "apps/web/package.json": NEXT_PACKAGE,
            },
            "inspect.turbo.one_app",
        ),
    ],
)
def test_a_source_nothing_recognised_says_why_and_what_would_help_with_a_code(
    tmp_path: Path, no_railpack: None, files: dict[str, str], code: str
) -> None:
    error = _refused(_write(tmp_path / "p", files))

    assert error.coded is not None
    assert error.coded.code == code
    # The error's words are the sentence's, and its fix is the sentence's own fix.
    assert error.message == error.coded.text
    assert code + HINT_SUFFIX in CATALOG
    assert error.details == error.coded.hint


def test_a_dockerfile_that_is_not_a_file_says_so(tmp_path: Path, no_railpack: None) -> None:
    # A link that leads nowhere is not a Dockerfile: it has to be a file of the repository.
    project = _write(tmp_path / "p", {})
    (project / "Dockerfile").symlink_to(tmp_path / "nowhere")

    error = _refused(project)

    assert error.coded is not None and error.coded.code == "inspect.dockerfile.unreadable"
    assert error.coded.params["dockerfile"] == "Dockerfile"


def test_the_way_to_deploy_a_container_depends_on_whether_railpack_is_here(
    tmp_path: Path, store: NoustStore
) -> None:
    files = {"go.mod": "module example.com/app\n"}

    set_runner(FakeRunner().only_knows("git", "docker"))
    try:
        without = _refused(_write(tmp_path / "a", files))
    finally:
        set_runner(None)
    set_runner(FakeRunner().only_knows("git", "docker", "railpack"))
    try:
        # With Railpack the source builds as a container, so nothing is refused.
        result = inspect_source(str(_write(tmp_path / "b", files)))
    finally:
        set_runner(None)

    assert without.coded is not None and without.coded.code == "inspect.stack.no_program"
    assert result.app_type == "container"
    assert result.verdict_coded is not None
    assert result.verdict_coded.code == "inspect.container_railpack"
