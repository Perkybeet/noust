# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The cloud-init library: what may be saved, and the one document a new server boots with."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from noust.core.exceptions import NodeError, ValidationError
from noust.core.store import NoustStore
from noust.fleet.cloud.init import (
    BUILTIN_ENTRIES,
    KIND_CLOUD_CONFIG,
    KIND_SCRIPT,
    WITHHELD,
    CloudInitLibrary,
    LibraryEntry,
    preview,
    render_user_data,
    validate_entry,
)

HOST_PRIVATE = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\nBBBB\n-----END OPENSSH PRIVATE KEY-----\n"
)
HOST_PUBLIC = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample"


@pytest.fixture
def library(tmp_path: Path) -> CloudInitLibrary:
    NoustStore.reset_instance()
    return CloudInitLibrary(NoustStore(tmp_path / "noust.db"))


def entry(name: str, kind: str, content: str) -> LibraryEntry:
    return LibraryEntry(name=name, kind=kind, description="", content=content)


def parsed(user_data: str) -> dict:
    assert user_data.startswith("#cloud-config\n")
    return yaml.safe_load(user_data)


class TestWhatMayBeSaved:
    def test_a_script_must_name_its_interpreter(self):
        with pytest.raises(ValidationError) as caught:
            validate_entry(KIND_SCRIPT, "echo hi\n")

        assert "interpreter" in caught.value.message

    def test_a_fragment_must_start_with_the_cloud_config_line(self):
        with pytest.raises(ValidationError):
            validate_entry(KIND_CLOUD_CONFIG, "packages: [git]\n")

    def test_a_fragment_must_be_a_mapping(self):
        with pytest.raises(ValidationError):
            validate_entry(KIND_CLOUD_CONFIG, "#cloud-config\n- a\n- b\n")

    def test_a_fragment_that_is_not_yaml_says_so(self):
        with pytest.raises(ValidationError) as caught:
            validate_entry(KIND_CLOUD_CONFIG, "#cloud-config\npackages: [git\n")

        assert "YAML" in caught.value.message

    @pytest.mark.parametrize(
        "key",
        [
            "ssh_keys",
            "ssh_authorized_keys",
            "ssh_pwauth",
            "disable_root",
            "chpasswd",
            "ssh_deletekeys",
        ],
    )
    def test_a_fragment_cannot_set_what_noust_owns(self, key: str):
        with pytest.raises(ValidationError) as caught:
            validate_entry(KIND_CLOUD_CONFIG, f"#cloud-config\n{key}: x\n")

        assert key in caught.value.message
        assert "host key" in caught.value.details

    def test_a_fragment_cannot_configure_root_or_put_a_password_in_a_user(self):
        with pytest.raises(ValidationError):
            validate_entry(KIND_CLOUD_CONFIG, "#cloud-config\nusers:\n  - name: root\n")
        with pytest.raises(ValidationError):
            validate_entry(
                KIND_CLOUD_CONFIG, "#cloud-config\nusers:\n  - name: deploy\n    passwd: abc\n"
            )

    def test_a_user_with_a_key_is_fine(self):
        text = validate_entry(
            KIND_CLOUD_CONFIG,
            "#cloud-config\nusers:\n  - name: deploy\n    ssh_authorized_keys:\n      - ssh-ed25519 AAAA x\n",
        )

        assert "deploy" in text

    def test_a_script_with_a_cloud_token_in_it_is_refused(self):
        token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        with pytest.raises(ValidationError) as caught:
            validate_entry(KIND_SCRIPT, f"#!/bin/sh\ncurl -H 'Authorization: token {token}' x\n")

        assert "credential" in caught.value.message
        assert "metadata" in caught.value.details
        assert token not in caught.value.message

    def test_a_password_assignment_is_refused_but_a_variable_expansion_is_not(self):
        with pytest.raises(ValidationError):
            validate_entry(KIND_SCRIPT, "#!/bin/sh\nDB_PASSWORD=hunter2hunter2\n")

        validate_entry(KIND_SCRIPT, '#!/bin/sh\nDB_PASSWORD="$(cat /root/pw)"\nFOO=$BAR\n')

    def test_a_private_key_is_refused(self):
        with pytest.raises(ValidationError):
            validate_entry(KIND_SCRIPT, "#!/bin/sh\ncat <<K\n" + HOST_PRIVATE + "K\n")

    def test_too_large_or_empty_or_nul_is_refused(self):
        with pytest.raises(ValidationError):
            validate_entry(KIND_SCRIPT, "#!/bin/sh\n" + "# x\n" * 6000)
        with pytest.raises(ValidationError):
            validate_entry(KIND_SCRIPT, "   \n")
        with pytest.raises(ValidationError):
            validate_entry(KIND_SCRIPT, "#!/bin/sh\n\x00\n")

    def test_line_endings_are_normalised_and_a_final_newline_added(self):
        assert validate_entry(KIND_SCRIPT, "#!/bin/sh\r\nexit 0") == "#!/bin/sh\nexit 0\n"

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(ValidationError):
            validate_entry("python", "#!/bin/sh\n")


class TestTheDocument:
    def test_it_carries_the_host_key_and_turns_passwords_off(self):
        document = parsed(render_user_data(HOST_PRIVATE, HOST_PUBLIC, []))

        assert document["ssh_keys"] == {
            "ed25519_private": HOST_PRIVATE,
            "ed25519_public": HOST_PUBLIC,
        }
        assert document["ssh_deletekeys"] is True
        assert document["ssh_pwauth"] is False
        assert document["disable_root"] is False

    def test_it_carries_no_fleet_token_and_no_password(self):
        text = render_user_data(HOST_PRIVATE, HOST_PUBLIC, [])

        assert "noust_tok_" not in text
        assert "passwd" not in text

    def test_the_host_key_reads_as_a_block_and_not_an_escaped_line(self):
        text = render_user_data(HOST_PRIVATE, HOST_PUBLIC, [])

        assert "ed25519_private: |" in text

    def test_fragments_are_merged_in_order_and_lists_are_joined(self):
        first = entry(
            "a", KIND_CLOUD_CONFIG, "#cloud-config\npackages: [git]\nruncmd:\n  - [echo, one]\n"
        )
        second = entry(
            "b",
            KIND_CLOUD_CONFIG,
            "#cloud-config\npackages: [htop]\nruncmd:\n  - [echo, two]\ntimezone: UTC\n",
        )

        document = parsed(render_user_data(HOST_PRIVATE, HOST_PUBLIC, [first, second]))

        assert document["packages"] == ["git", "htop"]
        assert document["runcmd"] == [["echo", "one"], ["echo", "two"]]
        assert document["timezone"] == "UTC"

    def test_a_script_is_written_and_run_after_the_fragments_commands(self):
        fragment = entry("pkgs", KIND_CLOUD_CONFIG, "#cloud-config\nruncmd:\n  - [echo, frag]\n")
        script = entry("swap", KIND_SCRIPT, "#!/bin/sh\nswapon -a\n")

        document = parsed(render_user_data(HOST_PRIVATE, HOST_PUBLIC, [script, fragment]))

        (written,) = document["write_files"]
        assert written["path"] == "/var/lib/noust/cloud-init/01-swap.sh"
        assert written["permissions"] == "0700"
        assert written["content"] == "#!/bin/sh\nswapon -a\n"
        assert document["runcmd"] == [["echo", "frag"], ["/var/lib/noust/cloud-init/01-swap.sh"]]

    def test_two_scripts_keep_the_order_they_were_given(self):
        one = entry("one", KIND_SCRIPT, "#!/bin/sh\n:\n")
        two = entry("two", KIND_SCRIPT, "#!/bin/sh\n:\n")

        document = parsed(render_user_data(HOST_PRIVATE, HOST_PUBLIC, [two, one]))

        assert [c[0] for c in document["runcmd"]] == [
            "/var/lib/noust/cloud-init/01-two.sh",
            "/var/lib/noust/cloud-init/02-one.sh",
        ]

    def test_an_entry_is_checked_again_when_the_document_is_built(self):
        bad = entry("evil", KIND_CLOUD_CONFIG, "#cloud-config\nssh_keys: {}\n")

        with pytest.raises(ValidationError):
            render_user_data(HOST_PRIVATE, HOST_PUBLIC, [bad])

    def test_the_preview_withholds_the_host_key(self):
        text = preview([])

        assert WITHHELD in text
        assert "BEGIN OPENSSH" not in text

    def test_every_built_in_is_valid_and_renders(self):
        for builtin in BUILTIN_ENTRIES:
            validate_entry(builtin.kind, builtin.content)
        parsed(render_user_data(HOST_PRIVATE, HOST_PUBLIC, list(BUILTIN_ENTRIES)))


class TestTheLibrary:
    def test_the_built_ins_come_first_and_are_marked(self, library: CloudInitLibrary):
        listed = library.list()

        assert [e.name for e in listed[: len(BUILTIN_ENTRIES)]] == [b.name for b in BUILTIN_ENTRIES]
        assert all(e.builtin for e in listed)

    def test_an_entry_is_saved_listed_read_and_replaced(self, library: CloudInitLibrary):
        saved = library.save(
            "firewall", KIND_SCRIPT, "#!/bin/sh\nufw allow 22\n", description="ssh", actor="ana"
        )

        assert saved.created_by == "ana"
        assert library.get("firewall").content == "#!/bin/sh\nufw allow 22\n"
        library.save("firewall", KIND_SCRIPT, "#!/bin/sh\nufw allow 22/tcp\n")
        assert library.get("firewall").content.endswith("22/tcp\n")
        assert [e.name for e in library.list() if not e.builtin] == ["firewall"]

    def test_replacing_keeps_who_created_it(self, library: CloudInitLibrary):
        library.save("x", KIND_SCRIPT, "#!/bin/sh\n:\n", actor="ana")

        library.save("x", KIND_SCRIPT, "#!/bin/sh\n:\n:\n", actor="bo")

        assert library.get("x").created_by == "ana"

    def test_a_built_in_cannot_be_overwritten_or_deleted(self, library: CloudInitLibrary):
        with pytest.raises(ValidationError):
            library.save("swap-2g", KIND_SCRIPT, "#!/bin/sh\n:\n")
        with pytest.raises(ValidationError):
            library.delete("swap-2g")

    def test_deleting_removes_it_and_an_unknown_name_is_an_error(self, library: CloudInitLibrary):
        library.save("x", KIND_SCRIPT, "#!/bin/sh\n:\n")

        library.delete("x")

        with pytest.raises(NodeError):
            library.get("x")
        with pytest.raises(NodeError):
            library.delete("x")

    def test_resolving_keeps_the_order_and_refuses_a_repeat_or_an_unknown_name(
        self, library: CloudInitLibrary
    ):
        library.save("x", KIND_SCRIPT, "#!/bin/sh\n:\n")

        assert [e.name for e in library.resolve(["x", "swap-2g"])] == ["x", "swap-2g"]
        with pytest.raises(ValidationError):
            library.resolve(["x", "x"])
        with pytest.raises(NodeError):
            library.resolve(["nope"])

    def test_a_bad_name_or_description_is_refused(self, library: CloudInitLibrary):
        with pytest.raises(ValidationError):
            library.save("Bad Name", KIND_SCRIPT, "#!/bin/sh\n:\n")
        with pytest.raises(ValidationError):
            library.save("ok", KIND_SCRIPT, "#!/bin/sh\n:\n", description="two\nlines")

    def test_saving_and_deleting_are_audited_without_the_content(self, library: CloudInitLibrary):
        from noust.core import audit

        library.save("x", KIND_SCRIPT, "#!/bin/sh\necho unique-marker-123\n")
        library.delete("x")

        text = Path(audit.get_log().path).read_text()
        assert "fleet.cloud.script.save" in text
        assert "fleet.cloud.script.delete" in text
        assert "unique-marker-123" not in text
