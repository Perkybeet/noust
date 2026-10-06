# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Per-application deploy keys (checklist A-02).

A key is generated or imported for a repository, shown as a public key,
assigned, rotated without a moment when the repository has none that works,
and removed. What matters most is checked as plainly as possible: the private
half is a 0600 file and never appears in an argument or in the audit trail, a
key is found by repository, and a rotation keeps the old key until the code
host has answered with the new one.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import audit
from noust.core.deploy_keys import DeployKeyStore, validate_name
from noust.core.exceptions import SourceError, ValidationError
from noust.core.repositories import repository_identity
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers.source_keys import (
    DeployKeyManager,
    parse_public_key_line,
    secret_name,
    ssh_command,
)
from tests.deploy_key_support import (
    DeployKeyRunner,
    ed25519_line,
    fingerprint,
    private_key_text,
    rsa_line,
)

DOMAIN = "shop.example.com"
SSH_SOURCE = "git@github.com:Acme/Shop.git"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of its own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    """The secret store beside the test's store."""
    return SecretStore(root=tmp_path / "state" / "secrets")


@pytest.fixture
def keygen() -> DeployKeyRunner:
    """A runner whose ssh-keygen writes keys."""
    return DeployKeyRunner()


@pytest.fixture
def manager(store: NoustStore, secrets: SecretStore, keygen: DeployKeyRunner) -> DeployKeyManager:
    """The manager under test."""
    return DeployKeyManager(store=store, secrets=secrets, runner=keygen)


@pytest.fixture
def app(store: NoustStore) -> App:
    """An application built from a private repository over ssh."""
    return store.create_app(
        App(domain=DOMAIN, app_type="nodejs", source=SSH_SOURCE, branch="main", port=3000)
    )


def events(name: str) -> list[dict]:
    """The audit entries of one event."""
    return [entry for entry in audit.get_log().read(limit=200) if entry["action"] == name]


class TestRepositoryIdentity:
    """A key is found by the address of the repository, however it is spelled."""

    @pytest.mark.parametrize(
        "source",
        [
            "git@github.com:Acme/Shop.git",
            "ssh://git@github.com/acme/shop",
            "ssh://git@github.com:22/acme/shop.git",
            "https://github.com/acme/shop.git",
            "https://user:token@github.com/ACME/shop",
            "github:acme/shop",
            "git@github.com:acme/shop.git#main",
        ],
    )
    def test_every_spelling_of_one_repository_is_one_identity(self, source: str) -> None:
        assert repository_identity(source) == "github.com/acme/shop"

    @pytest.mark.parametrize("source", [None, "", "/srv/code", "https://example.com/a.tar.gz"])
    def test_a_source_that_is_not_a_repository_has_none(self, source: str | None) -> None:
        assert repository_identity(source) is None


class TestPublicKeyLine:
    """A line that merely looks like a key is not accepted."""

    def test_an_ed25519_line_is_read_with_its_fingerprint(self) -> None:
        line = ed25519_line(7, "me@laptop")
        key = parse_public_key_line(line)
        assert key.key_type == "ssh-ed25519"
        assert key.comment == "me@laptop"
        assert key.fingerprint == fingerprint(line)

    def test_an_rsa_line_is_read(self) -> None:
        assert parse_public_key_line(rsa_line(3)).key_type == "ssh-rsa"

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "ssh-dss AAAA",
            "ssh-ed25519",
            "ssh-ed25519 not-base64!!",
            "ssh-ed25519 AAAA",
            "ssh-ed25519 " + rsa_line(1).split()[1],
            "ssh-ed25519 AAAA\nssh-ed25519 BBBB",
        ],
    )
    def test_anything_else_is_refused(self, text: str) -> None:
        with pytest.raises(ValidationError):
            parse_public_key_line(text)

    def test_a_control_character_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not one line"):
            parse_public_key_line(ed25519_line(1) + " ok\x07bell")


class TestStore:
    """The tables: one key per repository, a key may serve several."""

    def add(self, keys: DeployKeyStore, name: str, seed: int = 1) -> None:
        line = ed25519_line(seed)
        keys.create(
            name=name,
            origin="generated",
            key_type="ssh-ed25519",
            fingerprint=fingerprint(line),
            public_key=line,
            created_by="test",
        )

    def test_a_key_is_found_by_repository(self, store: NoustStore) -> None:
        keys = DeployKeyStore(store)
        self.add(keys, "shop")
        keys.assign("shop", "github.com/acme/shop")
        found = keys.for_repository("github.com/acme/shop")
        assert found is not None and found.name == "shop"
        assert found.repositories == ("github.com/acme/shop",)
        assert keys.for_repository("github.com/acme/other") is None

    def test_a_repository_has_one_key(self, store: NoustStore) -> None:
        keys = DeployKeyStore(store)
        self.add(keys, "one", 1)
        self.add(keys, "two", 2)
        keys.assign("one", "github.com/acme/shop")
        with pytest.raises(ValidationError, match="already uses the deploy key one"):
            keys.assign("two", "github.com/acme/shop")

    def test_one_key_may_serve_several_repositories(self, store: NoustStore) -> None:
        keys = DeployKeyStore(store)
        self.add(keys, "machine")
        keys.assign("machine", "github.com/acme/a")
        keys.assign("machine", "github.com/acme/b")
        found = keys.get("machine")
        assert found is not None
        assert found.repositories == ("github.com/acme/a", "github.com/acme/b")

    def test_assigning_twice_is_harmless(self, store: NoustStore) -> None:
        keys = DeployKeyStore(store)
        self.add(keys, "shop")
        keys.assign("shop", "github.com/acme/shop")
        keys.assign("shop", "github.com/acme/shop")
        found = keys.get("shop")
        assert found is not None and len(found.repositories) == 1

    def test_a_name_is_taken_once(self, store: NoustStore) -> None:
        keys = DeployKeyStore(store)
        self.add(keys, "shop")
        with pytest.raises(ValidationError, match="already exists"):
            self.add(keys, "shop", 2)

    def test_deleting_a_key_takes_its_assignments_with_it(self, store: NoustStore) -> None:
        keys = DeployKeyStore(store)
        self.add(keys, "shop")
        keys.assign("shop", "github.com/acme/shop")
        keys.delete("shop")
        assert keys.for_repository("github.com/acme/shop") is None
        assert keys.any_assigned() is False

    def test_assigning_an_unknown_key_is_refused(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError, match="no deploy key named ghost"):
            DeployKeyStore(store).assign("ghost", "github.com/acme/shop")

    @pytest.mark.parametrize("name", ["", "Has Space", "../x", ".hidden", "a/b", "x" * 64])
    def test_a_name_is_a_simple_name(self, name: str) -> None:
        with pytest.raises(ValidationError):
            validate_name(name)

    def test_a_pending_key_is_promoted_in_one_step(self, store: NoustStore) -> None:
        keys = DeployKeyStore(store)
        self.add(keys, "shop", 1)
        new = ed25519_line(2)
        keys.set_pending(
            "shop", key_type="ssh-ed25519", fingerprint=fingerprint(new), public_key=new
        )
        waiting = keys.get("shop")
        assert waiting is not None and waiting.pending is not None
        assert waiting.pending.public_key == new
        keys.promote_pending("shop")
        done = keys.get("shop")
        assert done is not None
        assert done.public_key == new and done.pending is None and done.rotated_at


class TestGenerate:
    """A new key: ed25519, no passphrase, a 0600 file, never in argv."""

    def test_generating_makes_a_private_file_and_a_public_key(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore, keygen: DeployKeyRunner
    ) -> None:
        key = manager.generate(DOMAIN)

        assert key.name == "shop-example-com"
        assert key.origin == "generated"
        assert key.key_type == "ssh-ed25519"
        assert key.public_key.startswith("ssh-ed25519 ")
        assert key.repositories == ("github.com/acme/shop",)
        private = secrets.path(secret_name(key.name, "key"))
        assert stat.S_IMODE(private.stat().st_mode) == 0o600
        assert stat.S_IMODE(private.parent.stat().st_mode) == 0o700
        assert (private.parent / "key.pub").read_text().strip().startswith("ssh-ed25519 ")
        (call,) = [c for c in keygen.calls if c[0] == "ssh-keygen"]
        assert call[call.index("-t") + 1] == "ed25519"
        assert call[call.index("-N") + 1] == ""
        assert call[call.index("-f") + 1] == str(private)

    def test_the_private_key_never_reaches_an_argument_or_the_audit_trail(
        self, manager: DeployKeyManager, app: App, keygen: DeployKeyRunner, secrets: SecretStore
    ) -> None:
        key = manager.generate(DOMAIN)
        private = secrets.read(secret_name(key.name, "key"))
        assert private
        body = private.splitlines()[1]
        assert all(body not in part for call in keygen.calls for part in call)
        trail = " ".join(str(entry) for entry in events("apps.deploy_key.generate"))
        assert body not in trail
        assert key.fingerprint in trail

    def test_it_is_audited_with_the_fingerprint(self, manager: DeployKeyManager, app: App) -> None:
        key = manager.generate(DOMAIN)
        (entry,) = events("apps.deploy_key.generate")
        assert entry["resource"] == f"app:{DOMAIN}"
        assert entry["details"]["fingerprint"] == key.fingerprint

    def test_an_application_has_one_key(self, manager: DeployKeyManager, app: App) -> None:
        manager.generate(DOMAIN)
        with pytest.raises(ValidationError, match="already has the deploy key"):
            manager.generate(DOMAIN, name="another")

    def test_two_applications_of_one_repository_share_its_key(
        self, manager: DeployKeyManager, app: App, store: NoustStore
    ) -> None:
        store.create_app(
            App(domain="api.example.com", app_type="nodejs", source=SSH_SOURCE, port=3001)
        )
        key = manager.generate(DOMAIN)
        view = manager.view("api.example.com")
        assert view.key is not None and view.key.name == key.name
        assert view.shared_with == (DOMAIN,)

    def test_a_name_that_is_taken_is_refused_and_leaves_no_files(
        self, manager: DeployKeyManager, app: App, store: NoustStore, secrets: SecretStore
    ) -> None:
        other = store.create_app(
            App(domain="other.example.com", app_type="nodejs", source="git@github.com:a/b.git")
        )
        manager.generate(other.domain, name="taken")
        with pytest.raises(ValidationError, match="already exists"):
            manager.generate(DOMAIN, name="taken")
        assert manager.view(DOMAIN).key is None

    def test_an_application_that_is_not_a_repository_has_no_key(
        self, manager: DeployKeyManager, store: NoustStore
    ) -> None:
        store.create_app(App(domain="static.example.com", app_type="static", source="/srv/site"))
        with pytest.raises(ValidationError, match="not built from a git repository"):
            manager.generate("static.example.com")

    def test_an_unknown_application_is_refused(self, manager: DeployKeyManager) -> None:
        with pytest.raises(ValidationError, match="Application not found"):
            manager.generate("nobody.example.com")

    def test_a_failing_ssh_keygen_leaves_nothing_behind(
        self, manager: DeployKeyManager, app: App, keygen: DeployKeyRunner
    ) -> None:
        keygen.script(["ssh-keygen"], exit_code=1, stderr="boom")
        with pytest.raises(SourceError, match="Could not generate"):
            manager.generate(DOMAIN)
        assert manager.view(DOMAIN).key is None
        assert manager.keys() == []

    def test_in_a_dry_run_no_key_is_claimed(
        self, store: NoustStore, secrets: SecretStore, app: App
    ) -> None:
        from noust.core.fs import DryRunFileSystem
        from noust.core.runner import DryRunRunner, FakeRunner

        rehearsal = DeployKeyManager(
            store=store,
            secrets=SecretStore(root=secrets.root, fs=DryRunFileSystem()),
            runner=DryRunRunner(FakeRunner()),
        )
        with pytest.raises(SourceError, match="dry run"):
            rehearsal.generate(DOMAIN)
        assert not secrets.root.exists()
        assert DeployKeyManager(store=store, secrets=secrets).keys() == []


class TestImport:
    """An existing key: checked by ssh-keygen, never taken on trust."""

    def test_an_imported_key_is_derived_not_declared(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore, keygen: DeployKeyRunner
    ) -> None:
        keygen.derive_as = lambda seed: ed25519_line(900 + seed, "derived")
        key = manager.import_key(DOMAIN, private_key_text(4))
        assert key.origin == "imported"
        assert key.fingerprint == fingerprint(ed25519_line(904))
        private = secrets.path(secret_name(key.name, "key"))
        assert stat.S_IMODE(private.stat().st_mode) == 0o600
        derive = [c for c in keygen.calls if "-y" in c]
        assert len(derive) == 1
        assert derive[0][derive[0].index("-P") + 1] == ""
        assert all("seed=4" not in part for call in keygen.calls for part in call)

    def test_a_key_without_a_final_newline_is_accepted(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore
    ) -> None:
        key = manager.import_key(DOMAIN, private_key_text(4).rstrip("\n"))
        assert secrets.read(secret_name(key.name, "key")).endswith("-----\n")  # type: ignore[union-attr]

    def test_an_rsa_key_is_a_deploy_key_too(
        self, manager: DeployKeyManager, app: App, keygen: DeployKeyRunner
    ) -> None:
        keygen.derive_as = lambda seed: rsa_line(seed)
        assert manager.import_key(DOMAIN, private_key_text(4)).key_type == "ssh-rsa"

    def test_a_key_with_a_passphrase_is_refused_with_the_fix(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore
    ) -> None:
        with pytest.raises(ValidationError, match="passphrase") as caught:
            manager.import_key(DOMAIN, private_key_text(4, marker="PASSPHRASE"))
        assert "ssh-keygen -p" in caught.value.details
        assert manager.view(DOMAIN).key is None
        assert not secrets.path(secret_name("shop-example-com", "key")).exists()

    @pytest.mark.parametrize(
        "text",
        ["", "hello", "-----BEGIN OPENSSH PRIVATE KEY-----\nno footer", "x" * 20000],
    )
    def test_text_that_is_not_a_key_is_refused_before_anything_is_written(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore, text: str
    ) -> None:
        with pytest.raises(ValidationError):
            manager.import_key(DOMAIN, text)
        assert not secrets.path(secret_name("shop-example-com", "key")).exists()

    def test_a_key_ssh_cannot_read_is_refused_and_removed(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore
    ) -> None:
        broken = "-----BEGIN OPENSSH PRIVATE KEY-----\nnothing\n-----END OPENSSH PRIVATE KEY-----"
        with pytest.raises(ValidationError, match="not a private key ssh can read"):
            manager.import_key(DOMAIN, broken)
        assert not secrets.path(secret_name("shop-example-com", "key")).exists()

    def test_it_is_audited_without_the_key(self, manager: DeployKeyManager, app: App) -> None:
        manager.import_key(DOMAIN, private_key_text(4))
        (entry,) = events("apps.deploy_key.import")
        assert "seed=4" not in str(entry)


class TestAssignAndRemove:
    """A key can serve another repository; removing it is deliberate."""

    def test_an_existing_key_is_assigned_to_another_repository(
        self, manager: DeployKeyManager, app: App, store: NoustStore
    ) -> None:
        store.create_app(
            App(domain="blog.example.com", app_type="nodejs", source="git@github.com:acme/blog.git")
        )
        key = manager.generate(DOMAIN)
        assigned = manager.assign("blog.example.com", key.name)
        assert assigned.repositories == ("github.com/acme/blog", "github.com/acme/shop")
        assert events("apps.deploy_key.assign")

    def test_removing_the_only_use_deletes_the_key_and_its_files(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore
    ) -> None:
        key = manager.generate(DOMAIN)
        assert manager.remove(DOMAIN) == key.name
        assert manager.keys() == []
        assert not secrets.path(f"deploy-keys/{key.name}").exists()
        (entry,) = events("apps.deploy_key.remove")
        assert entry["details"]["deleted"] is True

    def test_removing_a_shared_key_only_takes_the_repository_off_it(
        self, manager: DeployKeyManager, app: App, store: NoustStore, secrets: SecretStore
    ) -> None:
        store.create_app(
            App(domain="blog.example.com", app_type="nodejs", source="git@github.com:acme/blog.git")
        )
        key = manager.generate(DOMAIN)
        manager.assign("blog.example.com", key.name)
        manager.remove(DOMAIN)
        kept = manager.keystore.get(key.name)
        assert kept is not None and kept.repositories == ("github.com/acme/blog",)
        assert secrets.path(secret_name(key.name, "key")).exists()

    def test_removing_where_there_is_none_says_so(
        self, manager: DeployKeyManager, app: App
    ) -> None:
        assert manager.remove(DOMAIN) is None

    def test_a_key_nobody_builds_from_is_pruned(
        self, manager: DeployKeyManager, app: App, store: NoustStore, secrets: SecretStore
    ) -> None:
        key = manager.generate(DOMAIN)
        store.delete_app(DOMAIN)
        assert manager.prune() == [key.name]
        assert manager.keys() == []
        assert not secrets.path(secret_name(key.name, "key")).exists()

    def test_a_key_still_in_use_is_not_pruned(self, manager: DeployKeyManager, app: App) -> None:
        manager.generate(DOMAIN)
        assert manager.prune() == []


class TestRotation:
    """The old key keeps working until the code host has answered with the new one."""

    def test_the_new_key_waits_beside_the_old_one(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore
    ) -> None:
        old = manager.generate(DOMAIN)
        rotated = manager.rotate_start(DOMAIN)

        assert rotated.public_key == old.public_key
        assert rotated.pending is not None
        assert rotated.pending.public_key != old.public_key
        assert secrets.path(secret_name(old.name, "next_key")).exists()
        assert secrets.read(secret_name(old.name, "key")) == secrets.read(
            secret_name(old.name, "key")
        )
        assert events("apps.deploy_key.rotate")

    def test_asking_again_keeps_the_same_new_key(
        self, manager: DeployKeyManager, app: App, keygen: DeployKeyRunner
    ) -> None:
        manager.generate(DOMAIN)
        first = manager.rotate_start(DOMAIN)
        second = manager.rotate_start(DOMAIN)
        assert first.pending == second.pending
        assert keygen.generated == 2  # the key and one replacement, not three

    def test_cancelling_forgets_the_new_key(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore
    ) -> None:
        key = manager.generate(DOMAIN)
        manager.rotate_start(DOMAIN)
        cancelled = manager.rotate_cancel(DOMAIN)
        assert cancelled.pending is None
        assert not secrets.path(secret_name(key.name, "next_key")).exists()
        assert secrets.path(secret_name(key.name, "key")).exists()

    def test_git_tries_the_new_key_first_and_the_old_one_second(
        self, manager: DeployKeyManager, app: App
    ) -> None:
        manager.generate(DOMAIN)
        manager.rotate_start(DOMAIN)
        use = manager.identities_for(SSH_SOURCE)
        assert use is not None
        assert [identity.which for identity in use.identities] == ["pending", "current"]
        assert "next_key" in use.identities[0].command
        assert "next_key" not in use.identities[1].command

    def test_the_first_success_with_the_new_key_replaces_the_old_one(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore
    ) -> None:
        old = manager.generate(DOMAIN)
        old_private = secrets.read(secret_name(old.name, "key"))
        waiting = manager.rotate_start(DOMAIN)
        new_private = secrets.read(secret_name(old.name, "next_key"))
        use = manager.identities_for(SSH_SOURCE)
        assert use is not None

        use.succeeded(use.identities[0])

        done = manager.keystore.get(old.name)
        assert done is not None
        assert done.pending is None and done.rotated_at
        assert waiting.pending is not None
        assert done.public_key == waiting.pending.public_key
        assert secrets.read(secret_name(old.name, "key")) == new_private
        assert secrets.read(secret_name(old.name, "key")) != old_private
        assert not secrets.path(secret_name(old.name, "next_key")).exists()
        assert not secrets.path(secret_name(old.name, "next_key.pub")).exists()
        (entry,) = events("apps.deploy_key.promote")
        assert entry["details"]["replaced"] == old.fingerprint

    def test_success_with_the_old_key_leaves_the_rotation_waiting(
        self, manager: DeployKeyManager, app: App
    ) -> None:
        manager.generate(DOMAIN)
        manager.rotate_start(DOMAIN)
        use = manager.identities_for(SSH_SOURCE)
        assert use is not None
        use.succeeded(use.identities[1])
        kept = manager.keystore.get("shop-example-com")
        assert kept is not None and kept.pending is not None and kept.last_used_at

    def test_rotating_without_a_key_says_how_to_make_one(
        self, manager: DeployKeyManager, app: App
    ) -> None:
        with pytest.raises(ValidationError, match="no deploy key") as caught:
            manager.rotate_start(DOMAIN)
        assert f"deploy-key generate {DOMAIN}" in caught.value.details


class TestIdentities:
    """What git is given: a path and options, never the key."""

    def test_an_ssh_url_of_a_repository_with_a_key_gets_that_key_only(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore
    ) -> None:
        key = manager.generate(DOMAIN)
        use = manager.identities_for("ssh://git@github.com/acme/shop.git")
        assert use is not None
        (identity,) = use.identities
        path = secrets.path(secret_name(key.name, "key"))
        assert f"-i {path}" in identity.command
        for option in (
            "IdentitiesOnly=yes",
            "IdentityAgent=none",
            "BatchMode=yes",
            "StrictHostKeyChecking=accept-new",
            f"UserKnownHostsFile={manager.known_hosts_path()}",
            "GlobalKnownHostsFile=/dev/null",
        ):
            assert option in identity.command
        assert "-F /dev/null" in identity.command
        private = secrets.read(secret_name(key.name, "key"))
        assert private and private.splitlines()[1] not in identity.command

    def test_the_known_hosts_directory_is_private(
        self, manager: DeployKeyManager, app: App
    ) -> None:
        manager.generate(DOMAIN)
        assert manager.identities_for(SSH_SOURCE) is not None
        directory = manager.known_hosts_path().parent
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    @pytest.mark.parametrize(
        "url", ["https://github.com/acme/shop.git", "/srv/code/shop", "git://github.com/acme/shop"]
    )
    def test_a_url_that_is_not_ssh_is_left_alone(
        self, manager: DeployKeyManager, app: App, url: str
    ) -> None:
        manager.generate(DOMAIN)
        assert manager.identities_for(url) is None

    def test_a_repository_without_a_key_is_left_to_the_server_wide_one(
        self, manager: DeployKeyManager, app: App
    ) -> None:
        manager.generate(DOMAIN)
        assert manager.identities_for("git@github.com:acme/other.git") is None

    def test_a_key_whose_private_half_is_gone_is_an_error_that_says_what_to_do(
        self, manager: DeployKeyManager, app: App, secrets: SecretStore
    ) -> None:
        key = manager.generate(DOMAIN)
        os.remove(secrets.path(secret_name(key.name, "key")))
        with pytest.raises(SourceError, match="lost its private half"):
            manager.identities_for(SSH_SOURCE)

    def test_the_command_quotes_a_path_with_spaces(self, tmp_path: Path) -> None:
        command = ssh_command(tmp_path / "a b" / "key", tmp_path / "known hosts")
        assert "'" in command
        assert command.startswith("ssh -F /dev/null -i ")


class TestView:
    """What the console and the CLI show."""

    def test_a_view_without_a_key(self, manager: DeployKeyManager, app: App) -> None:
        view = manager.view(DOMAIN)
        assert view.key is None
        assert view.repository == "github.com/acme/shop"
        assert view.source_is_ssh is True

    def test_an_https_application_is_told_which_ssh_url_would_use_the_key(
        self, manager: DeployKeyManager, store: NoustStore
    ) -> None:
        store.create_app(
            App(domain="web.example.com", app_type="nodejs", source="https://github.com/acme/web")
        )
        manager.generate("web.example.com")
        view = manager.view("web.example.com")
        assert view.source_is_ssh is False
        assert view.ssh_url == "git@github.com:acme/web.git"
        assert any("git@github.com:acme/web.git" in warning for warning in view.warnings)

    def test_the_server_wide_key_is_shown_as_the_fallback(
        self, manager: DeployKeyManager, app: App, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        line = ed25519_line(77, "root@server")
        monkeypatch.setattr(
            "noust.managers.source_keys.ssh_key_exists", lambda: (True, tmp_path / "id_ed25519")
        )
        monkeypatch.setattr("noust.managers.source_keys.get_public_key", lambda path: line)
        view = manager.view(DOMAIN)
        assert view.server_key == line
        assert view.server_key_fingerprint == fingerprint(line)


class TestHostKeys:
    """Host keys are pinned on first use and a change is never accepted silently."""

    def test_pinned_hosts_are_listed_from_ssh_keygen(
        self, manager: DeployKeyManager, keygen: DeployKeyRunner
    ) -> None:
        path = manager.known_hosts_path()
        path.parent.mkdir(parents=True)
        path.write_text("github.com ssh-ed25519 AAAA\n")
        keygen.script(
            ["ssh-keygen", "-l"],
            stdout="256 SHA256:abc github.com (ED25519)\n3072 SHA256:def gitlab.com (RSA)\n",
        )
        hosts = manager.pinned_hosts()
        assert [(h.host, h.fingerprint, h.key_type) for h in hosts] == [
            ("github.com", "SHA256:abc", "ED25519"),
            ("gitlab.com", "SHA256:def", "RSA"),
        ]

    def test_nothing_is_listed_before_anything_is_pinned(self, manager: DeployKeyManager) -> None:
        assert manager.pinned_hosts() == []

    def test_forgetting_a_host_removes_only_its_key(
        self, manager: DeployKeyManager, keygen: DeployKeyRunner
    ) -> None:
        path = manager.known_hosts_path()
        path.parent.mkdir(parents=True)
        path.write_text("github.com ssh-ed25519 AAAA\ngitlab.com ssh-ed25519 BBBB\n")
        assert manager.forget_host("github.com") is True
        assert path.read_text() == "gitlab.com ssh-ed25519 BBBB\n"
        (call,) = [c for c in keygen.calls if "-R" in c]
        assert call[call.index("-R") + 1] == "github.com"
        assert events("apps.deploy_key.host_forget")

    def test_a_host_with_a_port_is_spelled_the_way_ssh_stores_it(
        self, manager: DeployKeyManager, keygen: DeployKeyRunner
    ) -> None:
        path = manager.known_hosts_path()
        path.parent.mkdir(parents=True)
        path.write_text("[git.example.com]:2222 ssh-ed25519 AAAA\n")
        assert manager.forget_host("git.example.com:2222") is True

    @pytest.mark.parametrize("host", ["", "-oProxyCommand=x", "a b", "host;rm", "*.com"])
    def test_a_host_is_a_name_never_an_option(self, manager: DeployKeyManager, host: str) -> None:
        with pytest.raises(ValidationError):
            manager.forget_host(host)

    def test_forgetting_a_host_nobody_pinned_is_not_an_error(
        self, manager: DeployKeyManager
    ) -> None:
        assert manager.forget_host("github.com") is False
