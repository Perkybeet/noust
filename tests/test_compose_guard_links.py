# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A path of a compose file is judged by what Docker will mount, not by how it is spelled.

A repository can hold links, and the daemon follows them: ``./host`` that points at ``/`` is
``/``. Every path the guard judges (a bind mount, the file of a secret) is resolved first, and a
link that leaves the application is refused saying where it leads. What cannot be resolved is
refused rather than guessed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from noust.deployers.helpers.compose_guard import inspect_compose


@pytest.fixture
def app(tmp_path: Path) -> Path:
    """An application directory with the links a repository can hold."""
    root = tmp_path / "evil-example-org"
    root.mkdir()
    (root / "data").mkdir()
    (root / "data" / "inside").mkdir()
    os.symlink("/", root / "host")
    os.symlink("/var/run", root / "run")
    os.symlink("/etc/noust", root / "noust")
    os.symlink("/var/www/apps/other-example-com", root / "other")
    os.symlink("data/inside", root / "alias")
    os.symlink("../../../../..", root / "climb")
    os.symlink("/var/run/docker.sock", root / "sock")
    os.symlink("hop2", root / "hop1")
    os.symlink("/", root / "hop2")
    os.symlink("loop-b", root / "loop-a")
    os.symlink("loop-a", root / "loop-b")
    (root / "data" / "out").symlink_to("/")
    return root


def mount(source: object, **service: Any) -> dict[str, Any]:
    """A document whose one service mounts ``source``."""
    return {"services": {"w": {"image": "x", "volumes": [source], **service}}}


def verdict(document: dict[str, Any], app: Path) -> str:
    """``refused``, ``warned`` or ``nothing``."""
    found = inspect_compose(document, app)
    return "refused" if found.refused else ("warned" if found.warned else "nothing")


class TestALinkThatLeavesTheApplication:
    """The repository's own link carries the mount out of the tree."""

    @pytest.mark.parametrize(
        "source",
        [
            "./host",
            "./host/etc",
            "./run",
            "./hop1",
            "./climb",
            "./data/out",
            "./data/out/etc/shadow",
        ],
    )
    def test_is_refused(self, app: Path, source: str) -> None:
        assert verdict(mount(f"{source}:/mnt"), app) == "refused"

    def test_says_where_the_link_leads(self, app: Path) -> None:
        found = inspect_compose(mount("./run:/mnt"), app)

        assert len(found.refused) == 1
        assert "./run" in found.refused[0]
        assert "link ./run that leads to /run" in found.refused[0]
        assert "outside the application" in found.refused[0]

    def test_a_link_to_a_place_that_holds_noust_says_so_and_names_the_link(self, app: Path) -> None:
        found = inspect_compose(mount("./host:/mnt"), app)

        assert len(found.refused) == 1
        assert "configuration and secrets" in found.refused[0]
        assert "a link that leads to /" in found.refused[0]

    def test_applies_to_the_long_syntax_too(self, app: Path) -> None:
        document = mount({"type": "bind", "source": "./host", "target": "/mnt"})

        assert verdict(document, app) == "refused"

    def test_a_link_to_noust_own_state_is_noust_own_state(self, app: Path) -> None:
        found = inspect_compose(mount("./noust:/n"), app)

        assert len(found.refused) == 1
        assert "Noust's own configuration" in found.refused[0]

    def test_a_link_to_another_application_is_that_application(self, app: Path) -> None:
        found = inspect_compose(mount("./other:/o"), app)

        assert len(found.refused) == 1
        assert "other application" in found.refused[0]

    @pytest.mark.parametrize("source", ["./sock", "./run/docker.sock"])
    def test_a_link_to_the_docker_socket_is_the_socket(self, app: Path, source: str) -> None:
        found = inspect_compose(mount(f"{source}:/var/run/docker.sock"), app)

        assert len(found.refused) == 1
        assert "Docker socket" in found.refused[0]

    def test_a_link_that_cannot_be_followed_is_refused_not_guessed(self, app: Path) -> None:
        found = inspect_compose(mount("./loop-a:/x"), app)

        assert len(found.refused) == 1
        assert "cannot be followed" in found.refused[0]

    def test_a_path_that_is_not_a_path_is_refused(self, app: Path) -> None:
        found = inspect_compose(mount("./a\x00b:/x"), app)

        assert len(found.refused) == 1
        assert "cannot be followed" in found.refused[0]

    def test_a_secret_file_behind_a_link_is_judged_as_well(self, app: Path) -> None:
        document = {
            "secrets": {"s": {"file": "./host/etc/shadow"}},
            "services": {"w": {"image": "x", "secrets": ["s"]}},
        }

        assert verdict(document, app) == "refused"


class TestALinkThatStaysInside:
    """A link to somewhere in the application is the application's own."""

    @pytest.mark.parametrize("source", ["./alias", "./alias/deeper", "./data/inside", "./data"])
    def test_is_not_a_finding(self, app: Path, source: str) -> None:
        assert verdict(mount(f"{source}:/d"), app) == "nothing"

    def test_an_application_behind_a_link_of_its_own_directory_is_still_itself(
        self, tmp_path: Path
    ) -> None:
        real = tmp_path / "real" / "shop-example-com"
        real.mkdir(parents=True)
        (real / "data").mkdir()
        alias = tmp_path / "apps"
        alias.mkdir()
        os.symlink(real, alias / "shop-example-com")

        assert verdict(mount("./data:/d"), alias / "shop-example-com") == "nothing"


class TestWhatIsNotAPathOfTheFile:
    """Variables are the model's to resolve, never guessed at here."""

    def test_a_path_made_of_a_variable_is_left_to_the_resolved_model(self, app: Path) -> None:
        assert verdict(mount("${DATA_DIR:-./data}:/d"), app) == "nothing"

    def test_a_named_volume_is_never_a_path(self, app: Path) -> None:
        # `host` is also the name of a link in the tree: a name without a slash is a volume.
        assert verdict(mount({"type": "volume", "source": "host", "target": "/d"}), app) == (
            "nothing"
        )
