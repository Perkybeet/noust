# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The double that prints what ``docker compose config`` resolves a stack to.

The guard over a Compose file judges the file and then the model Compose resolves it to
(``test_compose_guard_resolved``). A test that deploys a real stack through the deployer has no
Docker to ask, so ``tests/support/compose_model.py`` plays Compose: it interpolates the variables
and prints the volumes the way ``config --format json`` does. It is held here to the part of that
shape the guard reads, and to refusing what it does not know how to resolve instead of printing a
model that hides it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import is_read_only
from noust.deployers.helpers.compose_guard import inspect_compose
from tests.support.compose_model import (
    UnsupportedComposeFeature,
    compose_resolving,
    interpolate,
    resolve_like_compose,
)

APP = Path("/var/www/apps/stack-example-com")


class TestInterpolation:
    """Compose's own grammar for ``$`` in a value."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("plain", "plain"),
            ("${NAME}", "web"),
            ("$NAME", "web"),
            ("$NAME-suffix", "web-suffix"),
            ("pre-${NAME}-post", "pre-web-post"),
            ("$$NAME", "$NAME"),
            ("$$$$", "$$"),
            ("${MISSING}", ""),
            ("${MISSING:-fallback}", "fallback"),
            ("${EMPTY:-fallback}", "fallback"),
            ("${EMPTY-fallback}", ""),
            ("${NAME:-fallback}", "web"),
            ("${MISSING:-${NAME}}", "web"),
            ("${NAME:+given}", "given"),
            ("${EMPTY:+given}", ""),
            ("${EMPTY+given}", "given"),
            ("${MISSING+given}", ""),
        ],
    )
    def test_a_reference_is_replaced_the_way_compose_replaces_it(
        self, text: str, expected: str
    ) -> None:
        assert interpolate(text, {"NAME": "web", "EMPTY": ""}) == expected

    @pytest.mark.parametrize("text", ["cost: 5$", "${NAME", "${1ST}", "${}", "$ x"])
    def test_a_dollar_that_starts_no_reference_is_an_error_as_it_is_for_compose(
        self, text: str
    ) -> None:
        with pytest.raises(ValueError, match="interpolation"):
            interpolate(text, {"NAME": "web"})

    @pytest.mark.parametrize("text", ["${MISSING:?give it}", "${EMPTY:?give it}", "${MISSING?x}"])
    def test_a_required_variable_nothing_gives_stops_the_resolution(self, text: str) -> None:
        with pytest.raises(ValueError, match="required"):
            interpolate(text, {"EMPTY": ""})

    def test_an_empty_value_satisfies_a_required_variable_without_a_colon(self) -> None:
        assert interpolate("${EMPTY?given}", {"EMPTY": ""}) == ""


class TestTheModelTheGuardReads:
    """The shape of ``docker compose config --format json`` where the guard looks."""

    def test_a_short_bind_mount_is_a_long_entry_with_an_absolute_source(self) -> None:
        model = resolve_like_compose(
            {"services": {"web": {"image": "x", "volumes": ["./data:/data:ro", "/srv/a:/a"]}}},
            APP,
            {},
        )

        assert model["services"]["web"]["volumes"] == [
            {"type": "bind", "source": f"{APP}/data", "target": "/data", "read_only": True},
            {"type": "bind", "source": "/srv/a", "target": "/a"},
        ]

    def test_a_named_volume_stays_a_volume_and_an_anonymous_one_has_no_source(self) -> None:
        model = resolve_like_compose(
            {"services": {"web": {"image": "x", "volumes": ["data:/data", "/cache"]}}}, APP, {}
        )

        assert model["services"]["web"]["volumes"] == [
            {"type": "volume", "source": "data", "target": "/data"},
            {"type": "volume", "target": "/cache"},
        ]

    def test_a_variable_in_a_mount_is_resolved_before_the_mount_is_read(self) -> None:
        model = resolve_like_compose(
            {"services": {"web": {"image": "x", "volumes": ["${SOCK}:/sock"]}}},
            APP,
            {"SOCK": "/var/run/docker.sock"},
        )

        assert model["services"]["web"]["volumes"] == [
            {"type": "bind", "source": "/var/run/docker.sock", "target": "/sock"}
        ]

    def test_a_secrets_file_is_absolute_and_the_project_has_a_name(self) -> None:
        model = resolve_like_compose(
            {
                "services": {"web": {"image": "x", "secrets": ["token"]}},
                "secrets": {"token": {"file": "./token.txt"}},
            },
            APP,
            {},
        )

        assert model["secrets"]["token"]["file"] == f"{APP}/token.txt"
        assert model["name"] == "stack-example-com"

    def test_the_rest_of_a_service_is_as_the_file_wrote_it_with_its_variables_filled(self) -> None:
        model = resolve_like_compose(
            {
                "services": {
                    "web": {
                        "image": "x:${TAG:-1}",
                        "environment": {"A": "${A}"},
                        "ports": ["127.0.0.1:${PORT}:80"],
                        "privileged": True,
                    }
                }
            },
            APP,
            {"A": "a", "PORT": "8080"},
        )

        assert model["services"]["web"] == {
            "image": "x:1",
            "environment": {"A": "a"},
            "ports": ["127.0.0.1:8080:80"],
            "privileged": True,
        }

    def test_what_it_prints_is_judged_by_the_guard_like_what_compose_prints(self) -> None:
        model = resolve_like_compose(
            {"services": {"web": {"image": "x", "volumes": ["${SOCK}:/sock"]}}},
            APP,
            {"SOCK": "/var/run/docker.sock"},
        )

        assert inspect_compose(model, APP).refused == [
            "service web mounts the Docker socket (/var/run/docker.sock): it can start a "
            "container with this server's root filesystem"
        ]


class TestWhatItDoesNotPretendToResolve:
    """A stack that brings in text from elsewhere is not printed as if it were whole."""

    @pytest.mark.parametrize(
        "document",
        [
            {"include": ["db.yaml"], "services": {"web": {"image": "x"}}},
            {"services": {"web": {"extends": {"file": "base.yaml", "service": "web"}}}},
            {"services": {"web": {"image": "x", "extends": "base"}, "base": {"image": "y"}}},
        ],
    )
    def test_an_include_or_an_extends_is_refused(self, document: dict[str, Any]) -> None:
        with pytest.raises(UnsupportedComposeFeature):
            resolve_like_compose(document, APP, {})


class TestTheRunnerItScripts:
    """What a deployer's runner is given for the commands the guard sends."""

    def _stack(self, tmp_path: Path, text: str, env: str = "") -> Path:
        directory = tmp_path / "stack"
        directory.mkdir()
        (directory / "docker-compose.yml").write_text(text)
        if env:
            (directory / ".env").write_text(env)
        return directory

    def test_it_prints_the_model_of_the_file_on_disk_for_any_compose_command(
        self, tmp_path: Path
    ) -> None:
        directory = self._stack(
            tmp_path,
            "services:\n  web:\n    image: x\n    volumes:\n      - ${DATA}:/data\n",
            "DATA=./files\n",
        )
        runner = compose_resolving(directory, {})

        result = runner.run(
            [
                "docker",
                "compose",
                "-p",
                "stack",
                "-f",
                str(directory / "docker-compose.yml"),
                "--profile",
                "*",
                "config",
                "--format",
                "json",
            ]
        )

        assert json.loads(result.stdout)["services"]["web"]["volumes"] == [
            {"type": "bind", "source": f"{directory}/files", "target": "/data"}
        ]
        assert all(is_read_only(call) for call in runner.calls)

    def test_the_variables_it_is_given_win_over_the_files_and_the_files_over_nothing(
        self, tmp_path: Path
    ) -> None:
        directory = self._stack(
            tmp_path, "services:\n  web:\n    image: ${A}-${B}\n", "A=from-file\nB=from-file\n"
        )
        runner = compose_resolving(directory, {"A": "given"})

        result = runner.run(["docker", "compose", "config", "--format", "json"])

        assert json.loads(result.stdout)["services"]["web"]["image"] == "given-from-file"
