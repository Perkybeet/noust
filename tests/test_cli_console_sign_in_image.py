# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust console sign-in-image``: the picture of the sign-in screen from the command line.

A front end over the same :class:`~noust.core.sign_in_image.SignInImage` the console's routes call,
so what is accepted here is what is accepted there, and the same sentences refuse the rest. What
these tests pin is what an operator types and reads: that each command does what its help says, that a
file is read no further than the limit allows, and that a rehearsal writes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core import paths
from noust.core.sign_in_image import SignInImage
from noust.validators import sign_in_image as rules
from noust.web.auth import STATE_DIR_ENV
from tests.support import sign_in_images as make


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The picture's directory and the audit trail inside the test's own."""
    monkeypatch.setattr(paths, "STATE_DIR", tmp_path / "state-dir")
    audit_state = tmp_path / "state"
    audit_state.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(audit_state))
    return tmp_path / "state-dir"


@pytest.fixture
def cli() -> CliRunner:
    return CliRunner()


@pytest.fixture
def logo(tmp_path: Path) -> Path:
    path = tmp_path / "logo.png"
    path.write_bytes(make.png(300, 100, extra=(make.png_chunk(b"tEXt", b"Author\x00Someone"),)))
    return path


def run(cli: CliRunner, *args: str, code: int = 0) -> str:
    result = cli.invoke(root_cli, list(args))
    assert result.exit_code == code, f"{result.output} {result.exception}"
    return f"{result.output}{result.exception or ''}"


def as_json(cli: CliRunner, *args: str) -> dict[str, Any]:
    return json.loads(run(cli, "--json", *args))


class TestShow:
    def test_with_nothing_set_it_says_the_drawing_is_shown_and_how_to_change_it(
        self, cli: CliRunner
    ) -> None:
        output = run(cli, "console", "sign-in-image", "show")

        assert "Noust's own drawing" in output
        assert "noust console sign-in-image set FILE" in output

    def test_as_json_it_is_null_or_the_picture(self, cli: CliRunner, logo: Path) -> None:
        assert as_json(cli, "console", "sign-in-image", "show") == {"image": None}
        run(cli, "console", "sign-in-image", "set", str(logo), "--alt", "Acme")

        image = as_json(cli, "console", "sign-in-image", "show")["image"]

        assert (image["content_type"], image["width"], image["height"]) == ("image/png", 300, 100)
        assert (image["fit"], image["alt"]) == ("contain", "Acme")
        assert image["sha256"] == SignInImage().get().sha256  # type: ignore[union-attr]


class TestSet:
    def test_a_picture_is_set_with_the_fit_and_the_description(
        self, cli: CliRunner, logo: Path
    ) -> None:
        output = run(
            cli,
            "console",
            "sign-in-image",
            "set",
            str(logo),
            "--fit",
            "cover",
            "--alt",
            "Acme Corp",
        )

        chosen = SignInImage().get()
        assert chosen is not None and (chosen.fit, chosen.alt) == ("cover", "Acme Corp")
        assert "image/png, 300 x 100" in output and "fills the space" in output
        assert b"Someone" not in (state_file(chosen)).read_bytes()

    def test_a_new_picture_is_shown_whole_and_decoration_unless_told_otherwise(
        self, cli: CliRunner, logo: Path
    ) -> None:
        run(cli, "console", "sign-in-image", "set", str(logo))

        chosen = SignInImage().get()
        assert chosen is not None and (chosen.fit, chosen.alt) == ("contain", "")

    def test_without_a_file_only_the_fit_changes_and_the_description_stays(
        self, cli: CliRunner, logo: Path
    ) -> None:
        run(cli, "console", "sign-in-image", "set", str(logo), "--alt", "Acme")
        before = SignInImage().get()

        run(cli, "console", "sign-in-image", "set", "--fit", "cover")

        after = SignInImage().get()
        assert before is not None and after is not None
        assert after.sha256 == before.sha256 and after.fit == "cover" and after.alt == "Acme"

    def test_without_a_file_and_without_a_picture_it_says_what_to_do(self, cli: CliRunner) -> None:
        output = run(cli, "console", "sign-in-image", "set", "--fit", "cover", code=1)

        assert "no sign-in image" in output.lower() or "no picture" in output.lower()
        assert "Send the image itself too" in output

    def test_an_svg_is_refused_with_the_sentence_and_its_fix_and_nothing_is_kept(
        self, cli: CliRunner, tmp_path: Path
    ) -> None:
        svg = tmp_path / "logo.png"  # called a png: its name is not what decides
        svg.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>')

        output = run(cli, "console", "sign-in-image", "set", str(svg), code=1)

        assert "An SVG is not accepted" in output and "Export it as a PNG" in output
        assert SignInImage().get() is None

    def test_a_file_that_is_not_there_is_refused_by_the_command_line_itself(
        self, cli: CliRunner, tmp_path: Path
    ) -> None:
        run(cli, "console", "sign-in-image", "set", str(tmp_path / "missing.png"), code=2)

    def test_a_file_far_over_the_limit_is_refused_without_being_read_whole(
        self, cli: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        big = tmp_path / "big.png"
        big.write_bytes(make.PNG_SIGNATURE + b"\x00" * (rules.MAX_IMAGE_BYTES * 3))
        asked: list[int] = []
        original = Path.open

        def spy(self: Path, *args: Any, **kwargs: Any) -> Any:
            handle = original(self, *args, **kwargs)
            if self == big:
                real_read = handle.read
                handle.read = lambda size=-1: (asked.append(size), real_read(size))[1]  # type: ignore[method-assign]
            return handle

        monkeypatch.setattr(Path, "open", spy)

        output = run(cli, "console", "sign-in-image", "set", str(big), code=1)

        assert "larger than 1 MB" in output
        assert asked == [rules.MAX_IMAGE_BYTES + 1]

    def test_a_rehearsal_writes_and_records_nothing(
        self, cli: CliRunner, logo: Path, state: Path
    ) -> None:
        output = run(cli, "--dry-run", "console", "sign-in-image", "set", str(logo))

        assert "Would put this picture" in output
        assert not state.exists() or list(state.iterdir()) == []
        assert SignInImage().get() is None

    def test_as_json_it_prints_the_picture(self, cli: CliRunner, logo: Path) -> None:
        image = as_json(cli, "console", "sign-in-image", "set", str(logo))["image"]

        assert image["content_type"] == "image/png"


class TestRemove:
    def test_it_brings_the_drawing_back(self, cli: CliRunner, logo: Path) -> None:
        run(cli, "console", "sign-in-image", "set", str(logo))

        output = run(cli, "console", "sign-in-image", "remove")

        assert "Noust's own drawing again" in output
        assert SignInImage().get() is None and list(SignInImage().directory.iterdir()) == []

    def test_removing_what_is_not_there_says_so_and_is_not_an_error(self, cli: CliRunner) -> None:
        assert "There was no picture" in run(cli, "console", "sign-in-image", "remove")
        assert as_json(cli, "console", "sign-in-image", "remove") == {"removed": False}

    def test_a_rehearsal_leaves_the_picture(self, cli: CliRunner, logo: Path) -> None:
        run(cli, "console", "sign-in-image", "set", str(logo))

        output = run(cli, "--dry-run", "console", "sign-in-image", "remove")

        assert "Would take the picture off" in output
        assert SignInImage().get() is not None


class TestTheHelpIsTruthful:
    def test_the_group_and_its_commands_are_listed_under_console(self, cli: CliRunner) -> None:
        output = run(cli, "console", "--help")

        assert "sign-in-image" in output and "expose" in output

    def test_each_command_says_what_it_takes(self, cli: CliRunner) -> None:
        output = run(cli, "console", "sign-in-image", "set", "--help")

        assert "--fit" in output and "--alt" in output and "FILE" in output
        assert "cover" in output and "contain" in output
        assert "SVG" in output


def state_file(chosen: Any) -> Path:
    return SignInImage().directory / f"{chosen.sha256}.png"
