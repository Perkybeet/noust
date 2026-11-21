# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one writer of a stack's ``.env`` judges the stack with the new variables before it writes them.

A stack whose compose file takes a path or an option from a variable (``${DATA:-./data}:/data``) passes
the guard with its default, and then whoever can write the ``.env`` points it at ``/``. The writer of the
``.env`` is the chokepoint of every variable that ends in it (the console's editor, ``noust env``, a
shared variable, a rotated secret, a linked database): it resolves the model with the file it is about to
write, through ``docker compose --env-file``, and refuses what adds something the stack was not accepted
with.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import compose_acceptance
from noust.core.exceptions import ValidationError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, AppType, NoustStore
from noust.deployers.helpers import app_env as app_env_module
from noust.deployers.helpers.app_env import write_app_env
from noust.deployers.helpers.compose_guard import inspect_compose

DOMAIN = "stack.example.com"
FILE = "services:\n  web:\n    image: nginx\n    volumes:\n      - ${DATA:-./data}:/data\n"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


class Compose(FakeRunner):
    """A Compose that resolves the file with the ``.env`` it is given, as the real one does."""

    def __init__(self, app: Path) -> None:
        super().__init__()
        self.app = app
        self.env_files: list[str] = []
        self.env_texts: list[str] = []

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:  # type: ignore[override]
        words = [str(word) for word in argv]
        if words[:2] == ["docker", "compose"] and "config" in words:
            source = str(self.app / "data")
            if "--env-file" in words:
                path = Path(words[words.index("--env-file") + 1])
                self.env_files.append(str(path))
                text = path.read_text(encoding="utf-8")
                self.env_texts.append(text)
                for line in text.splitlines():
                    if line.startswith("DATA="):
                        source = line.partition("=")[2].strip("'\"")
            model = {
                "name": "stack-example-com",
                "services": {
                    "web": {
                        "image": "nginx",
                        "volumes": [{"type": "bind", "source": source, "target": "/data"}],
                    }
                },
            }
            self.calls.append(tuple(words))
            return CommandResult(tuple(words), 0, json.dumps(model), "")
        return super().run(argv, **kwargs)


@pytest.fixture
def stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: NoustStore
) -> Iterator[tuple[App, Compose]]:
    path = tmp_path / "apps" / "stack-example-com"
    path.mkdir(parents=True)
    (path / "docker-compose.yml").write_text(FILE)
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
        ),
    )
    runner = Compose(path)
    set_runner(runner)
    app = store.create_app(
        App(domain=DOMAIN, app_type=AppType.DOCKER_COMPOSE.value, app_path=str(path))
    )
    store.set_app_compose_project(DOMAIN, "stack-example-com")
    try:
        yield app, runner
    finally:
        set_runner(None)


def accepted(app: App, store: NoustStore) -> None:
    """The stack was accepted with a mount of its own ``data`` directory, which is nothing."""
    findings = inspect_compose(
        {
            "services": {
                "web": {
                    "image": "nginx",
                    "volumes": [
                        {"type": "bind", "source": f"{app.app_path}/data", "target": "/data"}
                    ],
                }
            }
        },
        Path(app.app_path),
    )
    compose_acceptance.record(
        DOMAIN, [findings.keys[s] for s in findings.refused], source="deploy", store=store
    )


class TestTheWriterJudgesFirst:
    def test_a_variable_that_points_the_mount_at_the_root_is_not_written(
        self, stack: tuple[App, Compose], store: NoustStore
    ) -> None:
        app, _ = stack
        accepted(app, store)

        with pytest.raises(ValidationError, match="was not changed"):
            write_app_env(app, {"DATA": "/"})

        assert not (Path(app.app_path) / ".env").exists()

    def test_the_refusal_says_the_line_and_how_to_go_on(
        self, stack: tuple[App, Compose], store: NoustStore
    ) -> None:
        app, _ = stack
        accepted(app, store)

        with pytest.raises(ValidationError) as raised:
            write_app_env(app, {"DATA": "/"})

        assert "service web mounts /" in (raised.value.details or "")
        assert f"noust app sandbox compose-exception {DOMAIN}" in (raised.value.details or "")

    def test_a_variable_that_adds_nothing_is_written(
        self, stack: tuple[App, Compose], store: NoustStore
    ) -> None:
        app, _ = stack
        accepted(app, store)

        written = write_app_env(app, {"API_KEY": "abc", "DATA": f"{app.app_path}/data"})

        assert "API_KEY" in written.read_text()

    def test_the_file_the_stack_is_judged_with_is_the_file_that_is_written(
        self, stack: tuple[App, Compose], store: NoustStore
    ) -> None:
        app, runner = stack
        accepted(app, store)

        written = write_app_env(app, {"API_KEY": "abc"})

        # Compose is asked more than once for each check (the model, the same without resolving the
        # env_file): every time with the file that is then written.
        assert runner.env_texts and set(runner.env_texts) == {written.read_text()}

    def test_the_file_it_is_judged_with_is_private_and_goes_away(
        self, stack: tuple[App, Compose], store: NoustStore
    ) -> None:
        app, runner = stack
        accepted(app, store)

        write_app_env(app, {"API_KEY": "abc"})

        assert runner.env_files and not Path(runner.env_files[0]).exists()
        assert Path(runner.env_files[0]).parent != Path(app.app_path)

    def test_a_change_of_nothing_asks_nothing(
        self, stack: tuple[App, Compose], store: NoustStore
    ) -> None:
        app, runner = stack
        accepted(app, store)
        write_app_env(app, {"API_KEY": "abc"})
        runner.calls.clear()

        write_app_env(app, {"API_KEY": "abc"})

        assert not [call for call in runner.calls if "config" in call]

    def test_a_stack_with_a_recorded_exception_may_have_it(
        self, stack: tuple[App, Compose], store: NoustStore
    ) -> None:
        from noust.deployers.helpers import sandbox

        app, _ = stack
        accepted(app, store)
        sandbox.set_compose_exception(
            DOMAIN, allowed=True, actor="admin", reason="it needs it", store=store
        )

        written = write_app_env(app, {"DATA": "/srv/shared"})

        assert "DATA" in written.read_text()


class TestWhatIsNotAskedAbout:
    def test_a_stack_nobody_recorded_is_written_without_asking_compose(
        self, stack: tuple[App, Compose]
    ) -> None:
        app, runner = stack

        write_app_env(app, {"DATA": "/"})

        assert not [call for call in runner.calls if "config" in call]

    def test_an_application_that_is_no_stack_is_written_without_asking_compose(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: NoustStore
    ) -> None:
        path = tmp_path / "apps" / "plain-example-com"
        path.mkdir(parents=True)
        monkeypatch.setattr(
            app_env_module,
            "Config",
            lambda: SimpleNamespace(
                apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
            ),
        )
        compose_acceptance.record("plain.example.com", [], source="deploy", store=store)
        app = store.create_app(
            App(domain="plain.example.com", app_type=AppType.NODEJS.value, app_path=str(path))
        )

        written = write_app_env(app, {"DATA": "/"})

        assert "DATA" in written.read_text()
