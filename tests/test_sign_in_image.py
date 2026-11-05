# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The picture an operator chose for the sign-in screen (``noust.core.sign_in_image``).

The manager is the one place the picture is kept and read (rule 3), and the one place it is judged
before it is kept (rule 4): the API and the command line only carry a file to it. What is pinned
here is what it owes: what is stored is the rewritten file under its digest, a replacement leaves
one picture and not two, a record that does not match its file is no picture at all, a dry run
writes nothing and records nothing, and every change is audited.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from noust.core import paths
from noust.core.exceptions import ValidationError
from noust.core.fs import DryRunFileSystem, RecordingFileSystem, set_fs
from noust.core.sign_in_image import SignInImage
from tests.support import sign_in_images as make


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    return tmp_path / "state" / "sign-in-image"


@pytest.fixture
def pictures(folder: Path, fs: RecordingFileSystem) -> SignInImage:
    return SignInImage(folder, fs=fs)


def code_of(error: ValidationError) -> str:
    assert error.coded is not None
    return error.coded.code


class TestKeepingAPicture:
    def test_with_nothing_chosen_there_is_no_picture(self, pictures: SignInImage) -> None:
        assert pictures.get() is None
        assert pictures.read("0" * 64) is None

    def test_a_picture_is_kept_as_the_clean_file_under_its_digest_with_its_settings(
        self, pictures: SignInImage, folder: Path
    ) -> None:
        dirty = make.png(300, 100, extra=(make.png_chunk(b"tEXt", b"Author\x00Someone"),))

        chosen = pictures.set(dirty, fit="contain", alt="Acme")

        assert (chosen.content_type, chosen.width, chosen.height) == ("image/png", 300, 100)
        assert (chosen.fit, chosen.alt) == ("contain", "Acme")
        stored = (folder / f"{chosen.sha256}.png").read_bytes()
        assert b"Someone" not in stored and hashlib.sha256(stored).hexdigest() == chosen.sha256
        assert chosen.size == len(stored)
        assert pictures.get() == chosen
        assert pictures.read(chosen.sha256) == (stored, "image/png")

    def test_what_is_written_says_nothing_but_what_the_screen_needs(
        self, pictures: SignInImage, folder: Path
    ) -> None:
        chosen = pictures.set(make.jpeg(640, 480), fit="cover", alt="")

        record = json.loads((folder / "appearance.json").read_text())

        assert set(record) == {
            "sha256",
            "content_type",
            "width",
            "height",
            "size",
            "fit",
            "alt",
            "updated_at",
        }
        assert record["sha256"] == chosen.sha256 and record["content_type"] == "image/jpeg"

    def test_the_files_are_written_through_the_filesystem_seam(
        self, pictures: SignInImage, fs: RecordingFileSystem
    ) -> None:
        chosen = pictures.set(make.png(), fit="cover", alt="")

        written = {path.name for kind, path in fs.changes if kind == "write"}
        assert written == {"appearance.json", f"{chosen.sha256}.png"}

    def test_a_replacement_leaves_one_picture_and_not_two(
        self, pictures: SignInImage, folder: Path
    ) -> None:
        first = pictures.set(make.png(10, 10), fit="cover", alt="")
        second = pictures.set(make.jpeg(20, 20), fit="contain", alt="Second")

        assert pictures.get() == second
        names = sorted(path.name for path in folder.iterdir())
        assert names == sorted(["appearance.json", f"{second.sha256}.jpg"])
        assert pictures.read(first.sha256) is None

    def test_the_same_picture_again_changes_nothing_but_its_settings(
        self, pictures: SignInImage
    ) -> None:
        first = pictures.set(make.png(), fit="cover", alt="")
        again = pictures.set(make.png(), fit="contain", alt="Logo")

        assert again.sha256 == first.sha256
        assert (again.fit, again.alt) == ("contain", "Logo")

    def test_the_fit_and_the_description_change_without_sending_the_picture_again(
        self, pictures: SignInImage
    ) -> None:
        first = pictures.set(make.png(), fit="cover", alt="")

        changed = pictures.set(None, fit="contain", alt="Acme")

        assert changed.sha256 == first.sha256 and changed.size == first.size
        assert (changed.fit, changed.alt) == ("contain", "Acme")
        assert pictures.get() == changed

    def test_with_no_picture_there_is_nothing_to_refit(self, pictures: SignInImage) -> None:
        with pytest.raises(ValidationError) as caught:
            pictures.set(None, fit="cover", alt="")

        assert code_of(caught.value) == "sign_in_image.no_image_yet"

    def test_a_refusal_leaves_what_was_there(self, pictures: SignInImage, folder: Path) -> None:
        first = pictures.set(make.png(), fit="cover", alt="First")

        for data, fit, alt in (
            (b"<svg onload=alert(1)>", "cover", ""),
            (make.png(), "stretch", ""),
            (make.png(), "cover", "two\nlines"),
            (make.png(5000, 5000), "cover", ""),
        ):
            with pytest.raises(ValidationError):
                pictures.set(data, fit=fit, alt=alt)

        assert pictures.get() == first
        assert sorted(path.name for path in folder.iterdir()) == sorted(
            ["appearance.json", f"{first.sha256}.png"]
        )

    def test_the_whole_picture_is_judged_here_whoever_calls(self, pictures: SignInImage) -> None:
        # The manager is the chokepoint: a caller that skipped the API's checks gets the same ones.
        with pytest.raises(ValidationError) as caught:
            pictures.set(b"GIF89a" + b"\x00" * 20, fit="cover", alt="")

        assert code_of(caught.value) == "sign_in_image.not_an_image"


class TestRemovingIt:
    def test_removing_puts_the_drawing_back_and_leaves_nothing_behind(
        self, pictures: SignInImage, folder: Path
    ) -> None:
        chosen = pictures.set(make.png(), fit="cover", alt="")

        removed = pictures.remove()

        assert removed == chosen
        assert pictures.get() is None and pictures.read(chosen.sha256) is None
        assert list(folder.iterdir()) == []

    def test_removing_when_there_is_none_says_so_and_is_not_an_error(
        self, pictures: SignInImage
    ) -> None:
        assert pictures.remove() is None


class TestWhatIsReadBack:
    def test_a_record_whose_file_is_gone_is_no_picture(
        self, pictures: SignInImage, folder: Path
    ) -> None:
        chosen = pictures.set(make.png(), fit="cover", alt="")
        (folder / f"{chosen.sha256}.png").unlink()

        assert pictures.get() is None

    def test_a_file_that_is_not_the_one_the_record_names_is_never_served(
        self, pictures: SignInImage, folder: Path
    ) -> None:
        chosen = pictures.set(make.png(), fit="cover", alt="")
        (folder / f"{chosen.sha256}.png").write_bytes(b"<svg onload=alert(1)>")

        assert pictures.read(chosen.sha256) is None

    def test_only_the_current_digest_is_served(self, pictures: SignInImage) -> None:
        chosen = pictures.set(make.png(), fit="cover", alt="")

        assert pictures.read(chosen.sha256) is not None
        assert pictures.read("f" * 64) is None
        assert pictures.read(chosen.sha256.upper()) is None
        assert pictures.read("../" + chosen.sha256) is None

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "{",
            "[]",
            "null",
            '{"sha256": 5}',
            json.dumps({"sha256": "a" * 64}),
        ],
    )
    def test_a_record_that_cannot_be_read_is_no_picture_and_the_screen_still_draws(
        self, pictures: SignInImage, folder: Path, text: str
    ) -> None:
        folder.mkdir(parents=True)
        (folder / "appearance.json").write_text(text)

        assert pictures.get() is None

    def test_a_record_naming_a_path_instead_of_a_digest_is_not_followed(
        self, pictures: SignInImage, folder: Path, tmp_path: Path
    ) -> None:
        folder.mkdir(parents=True)
        (tmp_path / "secret.png").write_bytes(make.png())
        record: dict[str, Any] = {
            "sha256": "../../secret",
            "content_type": "image/png",
            "width": 8,
            "height": 6,
            "size": len(make.png()),
            "fit": "cover",
            "alt": "",
            "updated_at": "2026-11-05T10:00:00+00:00",
        }
        (folder / "appearance.json").write_text(json.dumps(record))

        assert pictures.get() is None

    def test_a_record_with_a_setting_nobody_can_choose_is_no_picture(
        self, pictures: SignInImage, folder: Path
    ) -> None:
        chosen = pictures.set(make.png(), fit="cover", alt="")
        record = json.loads((folder / "appearance.json").read_text())
        record["fit"] = "stretch"
        (folder / "appearance.json").write_text(json.dumps(record))

        assert pictures.get() is None
        assert chosen.sha256 == record["sha256"]


class TestAudit:
    def test_setting_it_is_audited_with_what_a_reader_of_the_trail_needs(
        self, pictures: SignInImage, audited: list[dict[str, Any]]
    ) -> None:
        chosen = pictures.set(make.png(), fit="contain", alt="Acme")

        assert len(audited) == 1
        event = audited[0]
        assert event["event"] == "auth.sign_in_image.set"
        assert event["target"] == "console:sign-in-image"
        assert event["details"] == {
            "sha256": chosen.sha256,
            "content_type": "image/png",
            "bytes": chosen.size,
            "width": chosen.width,
            "height": chosen.height,
            "fit": "contain",
            "described": True,
        }

    def test_changing_only_its_settings_is_audited_too(
        self, pictures: SignInImage, audited: list[dict[str, Any]]
    ) -> None:
        pictures.set(make.png(), fit="cover", alt="")
        audited.clear()

        pictures.set(None, fit="contain", alt="")

        assert [event["event"] for event in audited] == ["auth.sign_in_image.set"]

    def test_removing_it_is_audited_and_removing_nothing_is_not(
        self, pictures: SignInImage, audited: list[dict[str, Any]]
    ) -> None:
        pictures.remove()
        assert audited == []
        chosen = pictures.set(make.png(), fit="cover", alt="")
        audited.clear()

        pictures.remove()

        assert [event["event"] for event in audited] == ["auth.sign_in_image.remove"]
        assert audited[0]["details"] == {"sha256": chosen.sha256}

    def test_a_refusal_is_not_recorded_as_a_change(
        self, pictures: SignInImage, audited: list[dict[str, Any]]
    ) -> None:
        with pytest.raises(ValidationError):
            pictures.set(b"nope", fit="cover", alt="")

        assert audited == []

    def test_the_description_itself_is_not_put_in_the_trail(
        self, pictures: SignInImage, audited: list[dict[str, Any]]
    ) -> None:
        pictures.set(make.png(), fit="cover", alt="Acme Corp, since 1987")

        assert "Acme" not in json.dumps(audited, default=str)


class TestADryRun:
    def test_nothing_is_written_and_nothing_is_recorded(
        self, folder: Path, audited: list[dict[str, Any]]
    ) -> None:
        skipped: list[str] = []
        set_fs(DryRunFileSystem(on_skip=skipped.append))
        try:
            pictures = SignInImage(folder)
            pictures.set(make.png(), fit="cover", alt="")
        finally:
            set_fs(None)

        assert not folder.exists() or list(folder.iterdir()) == []
        assert skipped and audited == []


class TestWhereItLives:
    def test_the_directory_is_under_the_state_directory(self) -> None:
        assert paths.sign_in_image_dir() == paths.state_dir() / "sign-in-image"
        assert SignInImage().directory == paths.sign_in_image_dir()
