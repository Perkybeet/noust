# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The keys that may push to an application's repository (checklist A-23).

What is pinned: that a pasted key is only ever a key (no options, no second key, a blob that is
what its type says, RSA of at least 2048 bits); that its line in the file sshd reads is
``restrict`` with a forced ``git-receive-pack`` on the one repository of its application; that a
label cannot end the comment or pass for another application; that a key serves one application
(sshd reads the first line a key has); and that Noust edits only the lines it owns.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.pushdeploy.keys import (
    COMMENT_PREFIX,
    KeyEntry,
    PushKeys,
    key_line,
    parse_key,
    validate_label,
)
from tests.push_support import DOMAIN, OTHER, PushWorld, ecdsa, ed25519, rsa

REPOSITORY = Path("/var/lib/noust-git/shop.example.com.git")


@pytest.fixture
def world(tmp_path: Path) -> Iterator[PushWorld]:
    yield PushWorld(tmp_path / "server")


class TestReadingAKey:
    def test_an_ed25519_key(self) -> None:
        key = parse_key(ed25519(1, "ana@laptop"))
        assert key.key_type == "ssh-ed25519"
        assert key.fingerprint.startswith("SHA256:")
        assert key.bare.startswith("ssh-ed25519 ")

    @pytest.mark.parametrize("line", [rsa(2048), rsa(4096), ecdsa()])
    def test_the_other_types_a_developer_has(self, line: str) -> None:
        assert parse_key(line).key_type in {"ssh-rsa", "ecdsa-sha2-nistp256"}

    def test_a_short_rsa_key_is_refused_with_the_way_out(self) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_key(rsa(1024))
        assert "1024 bits" in str(caught.value)
        assert "ed25519" in caught.value.details
        assert caught.value.field == "key"

    def test_the_comment_is_not_kept(self) -> None:
        assert "ana" not in parse_key(ed25519(1, "ana@laptop")).bare

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "   ",
            "not a key",
            "ssh-dss AAAAB3NzaC1kc3M=",
            'command="rm -rf /" ' + ed25519(1),
            "no-pty " + ed25519(1),
            ed25519(1) + "\n" + ed25519(2),
            ed25519(1) + "\x00",
            "ssh-ed25519 AAAA!!!!",
            "ssh-ed25519 " + base64.b64encode(b"short").decode(),
            "x" * 9000,
        ],
    )
    def test_anything_that_is_not_one_key_is_refused(self, line: str) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_key(line)
        assert caught.value.field == "key"

    def test_a_blob_whose_inside_type_is_another_is_refused(self) -> None:
        wrong = ed25519(1).replace("ssh-ed25519 ", "ecdsa-sha2-nistp256 ", 1)
        with pytest.raises(ValidationError, match="damaged"):
            parse_key(wrong)


class TestTheLabel:
    @pytest.mark.parametrize("label", ["laptop", "ana@work", "ci-2", "a.b_c", "x" * 40])
    def test_a_name_a_person_or_a_machine_has(self, label: str) -> None:
        assert validate_label(label) == label

    @pytest.mark.parametrize(
        "label", ["", " ", "-x", "a b", "a:b", "a\nb", "a/b", "x" * 41, "ünï", "a;b", "$(x)"]
    )
    def test_what_could_end_the_comment_or_pass_for_a_domain_is_refused(self, label: str) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_label(label)
        assert caught.value.field == "label"


class TestTheLine:
    def test_it_forces_one_command_on_one_repository_and_takes_everything_else_away(self) -> None:
        line = key_line(DOMAIN, "laptop", parse_key(ed25519(1)), REPOSITORY)

        assert line.startswith(f"restrict,command=\"git-receive-pack '{REPOSITORY}'\" ssh-ed25519 ")
        assert line.endswith(f" {COMMENT_PREFIX}{DOMAIN}:laptop")
        # No option that would give back what restrict took away.
        for option in ("pty", "port-forwarding", "agent-forwarding", "X11-forwarding", "user-rc"):
            assert f",{option}" not in line.split(" ssh-ed25519 ")[0]

    @pytest.mark.parametrize(
        "path", ["/a b/x.git", "/a'b/x.git", '/a"b.git', "relative.git", "/x;y"]
    )
    def test_a_path_that_could_change_the_command_is_not_written(self, path: str) -> None:
        with pytest.raises(ValidationError):
            key_line(DOMAIN, "laptop", parse_key(ed25519(1)), Path(path))


class TestTheKeyFile:
    def keys(self, world: PushWorld) -> PushKeys:
        return world.keys()

    def test_adding_lists_and_writes_what_sshd_reads(self, world: PushWorld) -> None:
        keys = self.keys(world)

        assert keys.add(DOMAIN, "laptop", parse_key(ed25519(1)), REPOSITORY) is True

        entries = keys.list(DOMAIN)
        assert [(entry.label, entry.key_type) for entry in entries] == [("laptop", "ssh-ed25519")]
        assert entries[0].fingerprint == parse_key(ed25519(1)).fingerprint
        assert entries[0].to_dict()["label"] == "laptop"
        text = world.keys_file.read_text()
        assert text.count("\n") == 1 and "git-receive-pack" in text
        # sshd reads it as the push account, so it is world-readable and root's.
        assert (world.keys_file.stat().st_mode & 0o777) == 0o644

    def test_adding_the_same_again_changes_nothing(self, world: PushWorld) -> None:
        keys = self.keys(world)
        keys.add(DOMAIN, "laptop", parse_key(ed25519(1)), REPOSITORY)
        before = world.keys_file.read_text()

        assert keys.add(DOMAIN, "laptop", parse_key(ed25519(1)), REPOSITORY) is False

        assert world.keys_file.read_text() == before

    def test_a_label_another_key_has_is_refused(self, world: PushWorld) -> None:
        keys = self.keys(world)
        keys.add(DOMAIN, "laptop", parse_key(ed25519(1)), REPOSITORY)
        with pytest.raises(ValidationError, match="already has a key called laptop"):
            keys.add(DOMAIN, "laptop", parse_key(ed25519(2)), REPOSITORY)

    def test_a_key_is_added_once_per_application_under_one_name(self, world: PushWorld) -> None:
        keys = self.keys(world)
        keys.add(DOMAIN, "laptop", parse_key(ed25519(1)), REPOSITORY)
        with pytest.raises(
            ValidationError, match=r"already one of shop\.example\.com's, as laptop"
        ):
            keys.add(DOMAIN, "desktop", parse_key(ed25519(1)), REPOSITORY)

    def test_a_key_serves_one_application_because_sshd_reads_its_first_line(
        self, world: PushWorld
    ) -> None:
        keys = self.keys(world)
        keys.add(DOMAIN, "laptop", parse_key(ed25519(1)), REPOSITORY)

        with pytest.raises(ValidationError) as caught:
            keys.add(OTHER, "laptop", parse_key(ed25519(1)), Path("/var/lib/noust-git/b.git"))

        assert "already pushes to shop.example.com" in str(caught.value)
        assert "ssh-keygen -t ed25519" in caught.value.details
        assert len(world.keys_file.read_text().splitlines()) == 1

    def test_two_applications_have_their_own_keys(self, world: PushWorld) -> None:
        keys = self.keys(world)
        keys.add(DOMAIN, "laptop", parse_key(ed25519(1)), REPOSITORY)
        keys.add(OTHER, "laptop", parse_key(ed25519(2)), Path("/var/lib/noust-git/b.git"))

        assert [entry.domain for entry in keys.list()] == [OTHER, DOMAIN]
        assert [entry.label for entry in keys.list(DOMAIN)] == ["laptop"]

    def test_removing_by_label_or_by_fingerprint(self, world: PushWorld) -> None:
        keys = self.keys(world)
        first, second = parse_key(ed25519(1)), parse_key(ed25519(2))
        keys.add(DOMAIN, "laptop", first, REPOSITORY)
        keys.add(DOMAIN, "ci", second, REPOSITORY)

        assert keys.remove(DOMAIN, "laptop") == 1
        assert [entry.label for entry in keys.list(DOMAIN)] == ["ci"]
        assert keys.remove(DOMAIN, second.fingerprint) == 1
        assert keys.list(DOMAIN) == []
        assert keys.remove(DOMAIN, "laptop") == 0

    def test_a_label_of_another_application_is_not_removed(self, world: PushWorld) -> None:
        keys = self.keys(world)
        keys.add(OTHER, "laptop", parse_key(ed25519(2)), Path("/var/lib/noust-git/b.git"))
        assert keys.remove(DOMAIN, "laptop") == 0
        assert len(keys.list(OTHER)) == 1

    def test_nothing_named_is_refused(self, world: PushWorld) -> None:
        with pytest.raises(ValidationError):
            self.keys(world).remove(DOMAIN, "  ")

    def test_removing_all_of_an_applications_keys(self, world: PushWorld) -> None:
        keys = self.keys(world)
        keys.add(DOMAIN, "laptop", parse_key(ed25519(1)), REPOSITORY)
        keys.add(DOMAIN, "ci", parse_key(ed25519(2)), REPOSITORY)
        keys.add(OTHER, "ana", parse_key(ed25519(3)), Path("/var/lib/noust-git/b.git"))

        assert keys.remove_all(DOMAIN) == 2

        assert [entry.domain for entry in keys.list()] == [OTHER]

    def test_a_line_that_is_not_ours_is_kept_exactly(self, world: PushWorld) -> None:
        world.keys_dir.mkdir(parents=True)
        foreign = f"{ed25519(9)} someone@else"
        world.keys_file.write_text(foreign + "\n# a comment\n")
        keys = self.keys(world)

        keys.add(DOMAIN, "laptop", parse_key(ed25519(1)), REPOSITORY)
        keys.remove(DOMAIN, "laptop")

        assert world.keys_file.read_text() == foreign + "\n# a comment\n"

    def test_a_damaged_line_of_ours_is_not_listed_and_not_a_crash(self, world: PushWorld) -> None:
        world.keys_dir.mkdir(parents=True)
        world.keys_file.write_text(f"restrict ssh-ed25519 !!! {COMMENT_PREFIX}{DOMAIN}:x\n")
        assert self.keys(world).list(DOMAIN) == []

    def test_an_entry_is_plain_data(self) -> None:
        entry = KeyEntry(DOMAIN, "laptop", "ssh-ed25519", "SHA256:abc")
        assert entry.to_dict() == {
            "label": "laptop",
            "key_type": "ssh-ed25519",
            "fingerprint": "SHA256:abc",
        }
