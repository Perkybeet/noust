"""
Deploying an application with ``git push`` (checklist A-23): turning it on and off, and who may push.

What is pinned: what turning it on makes (a bare repository of the push account's, a hook and
settings of root's, the application's source and pinned branch, the webhook secret the hook signs
with); what it refuses (a preview, a stack, a container, an application that builds in place, a
server that is not set up); that turning it off puts the source and the branch back and takes
the keys away; and that the webhook secret rotating reaches the hook.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit, push_store
from noust.core.exceptions import NoustError, SourceError, ValidationError
from noust.integrations import webhook
from noust.pushdeploy import hook
from noust.pushdeploy.host import PUSH_USER
from noust.pushdeploy.repos import ssh_config_snippet
from noust.validators.source import push_source
from tests.push_support import DOMAIN, OTHER, PushRig, build_rig, ecdsa, ed25519


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PushRig]:
    yield from build_rig(tmp_path, monkeypatch)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kwargs: recorded.append((event, kwargs)))
    return recorded


@pytest.fixture
def ready(rig: PushRig) -> PushRig:
    rig.set_up()
    rig.app()
    return rig


class TestTurningItOn:
    def test_it_makes_the_repository_the_hook_and_points_the_application_at_it(
        self, ready: PushRig, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        info = ready.repos.enable(DOMAIN, key=ed25519(1), label="laptop")

        repository = ready.world.home / f"{DOMAIN}.git"
        assert info.enabled and info.branch == "main"
        assert info.repository == str(repository)
        assert info.remote == f"{PUSH_USER}@{DOMAIN}:{DOMAIN}.git"
        assert [key.label for key in info.keys] == ["laptop"]
        assert info.hook_ok is True and info.host_ready is True and info.problems == []

        # The repository is made by the push account, with objects checked as they arrive.
        init = next(c for c in ready.runner.calls if "init" in c and "--bare" in c)
        assert init[:4] == ("runuser", "-u", PUSH_USER, "--")
        assert init[-1] == str(repository)
        configs = [c for c in ready.runner.calls if "config" in c and "--git-dir" in c]
        assert {c[-2] for c in configs} == {"receive.fsckObjects", "transfer.fsckObjects"}
        assert (repository / "HEAD").read_text() == "ref: refs/heads/main\n"

        # The hook and its settings are root's, and the account's group may read the settings.
        script = repository / "hooks" / hook.HOOK_NAME
        settings = repository / "hooks" / hook.CONFIG_NAME
        assert script.read_text() == hook.render_hook("/usr/bin/python3")
        assert (script.stat().st_mode & 0o777) == 0o755
        assert (settings.stat().st_mode & 0o777) == 0o640
        assert ("chown", f"root:{PUSH_USER}", str(settings)) in ready.runner.calls
        assert not (repository / "hooks" / "pre-commit.sample").exists()
        written = json.loads(settings.read_text())
        assert written["url"] == f"http://127.0.0.1:8080/hooks/deploy/{DOMAIN}"
        assert written["branch"] == "main" and written["domain"] == DOMAIN
        assert written["secret"] == ready.store.get_webhook_secret(DOMAIN)

        # The application deploys from it, on that branch, and it is on record.
        app = ready.store.get_app(DOMAIN)
        assert app is not None
        assert app.source == push_source(DOMAIN) and app.branch == "main"
        assert ready.store.get_branch_pin(DOMAIN) == "main"
        record = push_store.get(DOMAIN, store=ready.store)
        assert record is not None
        assert record.previous_source == "https://github.com/acme/shop.git"
        assert record.previous_branch is None and record.enabled_by == "alice"
        assert ("push.enable" in [name for name, _ in events]) is True

    def test_the_secret_the_application_already_has_is_the_one_the_hook_signs_with(
        self, ready: PushRig
    ) -> None:
        secret = webhook.mint_secret(DOMAIN)
        ready.repos.enable(DOMAIN)
        settings = ready.world.home / f"{DOMAIN}.git" / "hooks" / hook.CONFIG_NAME
        assert json.loads(settings.read_text())["secret"] == secret

    def test_a_pinned_branch_is_the_one_a_push_deploys(self, ready: PushRig) -> None:
        app = ready.store.get_app(DOMAIN)
        assert app is not None
        app.branch = "production"
        ready.store.update_app(app)

        assert ready.repos.enable(DOMAIN).branch == "production"

    def test_the_branch_can_be_chosen_and_changed(self, ready: PushRig) -> None:
        ready.repos.enable(DOMAIN, branch="release")
        repository = ready.world.home / f"{DOMAIN}.git"
        assert (repository / "HEAD").read_text() == "ref: refs/heads/release\n"

        info = ready.repos.enable(DOMAIN, branch="main")

        assert info.branch == "main"
        assert (repository / "HEAD").read_text() == "ref: refs/heads/main\n"
        record = push_store.get(DOMAIN, store=ready.store)
        # Turning it on again does not make push the thing to go back to.
        assert record is not None
        assert record.previous_source == "https://github.com/acme/shop.git"

    def test_an_unsafe_branch_name_is_refused(self, ready: PushRig) -> None:
        with pytest.raises(NoustError):
            ready.repos.enable(DOMAIN, branch="--upload-pack=evil")
        assert push_store.get(DOMAIN, store=ready.store) is None

    def test_a_key_needs_a_name(self, ready: PushRig) -> None:
        with pytest.raises(ValidationError) as caught:
            ready.repos.enable(DOMAIN, key=ed25519(1))
        assert caught.value.field == "label"
        assert push_store.get(DOMAIN, store=ready.store) is None

    def test_a_key_that_is_not_one_stops_it_before_anything_is_made(self, ready: PushRig) -> None:
        with pytest.raises(ValidationError):
            ready.repos.enable(DOMAIN, key="nonsense", label="x")
        assert not (ready.world.home / f"{DOMAIN}.git").exists()

    def test_other_key_types_work(self, ready: PushRig) -> None:
        info = ready.repos.enable(DOMAIN, key=ecdsa(), label="ana")
        assert info.keys[0].key_type == "ecdsa-sha2-nistp256"

    def test_a_remote_on_another_port_is_an_ssh_url(self, ready: PushRig) -> None:
        ready.runner.config = ready.runner.config.replace("port 22", "port 2222")
        assert ready.repos.enable(DOMAIN, host="git.example.org").remote == (
            f"ssh://{PUSH_USER}@git.example.org:2222/{DOMAIN}.git"
        )


class TestWhatItRefuses:
    def test_a_server_that_is_not_set_up(self, rig: PushRig) -> None:
        rig.app()
        with pytest.raises(NoustError) as caught:
            rig.repos.enable(DOMAIN)
        assert "not set up to take git pushes" in str(caught.value)
        assert "noust push setup" in caught.value.details
        assert push_store.get(DOMAIN, store=rig.store) is None

    def test_an_application_that_does_not_exist(self, ready: PushRig) -> None:
        with pytest.raises(NoustError, match="Application not found"):
            ready.repos.enable(OTHER)

    def test_an_application_that_builds_in_place(self, rig: PushRig) -> None:
        rig.set_up()
        rig.app(layout="inplace")
        with pytest.raises(NoustError) as caught:
            rig.repos.enable(DOMAIN)
        assert f"noust app migrate {DOMAIN}" in caught.value.details

    @pytest.mark.parametrize("app_type", ["docker-compose", "container"])
    def test_a_stack_and_a_container(self, rig: PushRig, app_type: str) -> None:
        rig.set_up()
        rig.app(app_type=app_type)
        with pytest.raises(NoustError, match="a push cannot deploy"):
            rig.repos.enable(DOMAIN)

    def test_a_preview(self, rig: PushRig) -> None:
        rig.set_up()
        rig.app()
        rig.app(OTHER, preview_parent=DOMAIN)
        with pytest.raises(NoustError, match="is a preview"):
            rig.repos.enable(OTHER)

    def test_a_repository_path_that_is_a_link(self, ready: PushRig, tmp_path: Path) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        ready.world.home.mkdir(parents=True, exist_ok=True)
        (ready.world.home / f"{DOMAIN}.git").symlink_to(elsewhere)
        with pytest.raises(SourceError, match="symlink"):
            ready.repos.enable(DOMAIN)
        assert ready.store.get_app(DOMAIN).source.startswith("https://")  # type: ignore[union-attr]


class TestDescribing:
    def test_an_application_that_does_not_deploy_from_push(self, ready: PushRig) -> None:
        info = ready.repos.describe(DOMAIN)
        assert info.enabled is False and info.host_ready is True
        assert info.to_dict()["remote"] is None

    def test_it_says_when_the_hook_is_not_what_noust_writes(self, ready: PushRig) -> None:
        ready.repos.enable(DOMAIN)
        script = ready.world.home / f"{DOMAIN}.git" / "hooks" / hook.HOOK_NAME
        script.write_text("#!/bin/sh\nexit 0\n")

        info = ready.repos.describe(DOMAIN)

        assert info.hook_ok is False
        assert any(f"noust push refresh {DOMAIN}" in problem for problem in info.problems)

    def test_it_says_when_the_repository_is_gone(self, ready: PushRig) -> None:
        ready.repos.enable(DOMAIN)
        import shutil

        shutil.rmtree(ready.world.home / f"{DOMAIN}.git")
        info = ready.repos.describe(DOMAIN)
        assert info.hook_ok is False and any("repository is missing" in p for p in info.problems)

    def test_it_lists_every_application_that_deploys_from_push(self, ready: PushRig) -> None:
        ready.app(OTHER)
        ready.repos.enable(DOMAIN)
        ready.repos.enable(OTHER)
        assert [info.domain for info in ready.repos.list()] == [OTHER, DOMAIN]

    def test_it_reads_when_the_console_last_received_a_push(self, ready: PushRig) -> None:
        from noust.core import webhook_deliveries

        ready.repos.enable(DOMAIN)
        app = ready.store.get_app(DOMAIN)
        assert app is not None and app.id is not None
        webhook_deliveries.record_delivery(
            app.id,
            webhook_deliveries.DEPLOY_STARTED,
            provider="github",
            event="push",
            branch="main",
            detail="queued",
            store=ready.store,
        )
        assert ready.repos.describe(DOMAIN).last_push_at is not None


class TestRefreshing:
    def test_it_writes_the_hook_and_its_settings_again(self, ready: PushRig) -> None:
        ready.repos.enable(DOMAIN)
        settings = ready.world.home / f"{DOMAIN}.git" / "hooks" / hook.CONFIG_NAME
        settings.write_text("{}")

        info = ready.repos.refresh(DOMAIN)

        assert info.hook_ok is True
        assert json.loads(settings.read_text())["domain"] == DOMAIN

    def test_a_rotated_webhook_secret_reaches_the_hook(
        self, ready: PushRig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.runner import set_runner

        ready.repos.enable(DOMAIN)
        settings = ready.world.home / f"{DOMAIN}.git" / "hooks" / hook.CONFIG_NAME
        before = json.loads(settings.read_text())["secret"]
        # The rotation builds a manager of its own: it runs on this rig's server.
        set_runner(ready.runner)
        monkeypatch.setattr(
            "noust.pushdeploy.repos.local_console_url", lambda: "http://127.0.0.1:8080"
        )

        rotated = webhook.mint_secret(DOMAIN)

        assert rotated != before
        assert json.loads(settings.read_text())["secret"] == rotated

    def test_a_rotation_that_cannot_reach_the_hook_says_so_and_still_rotates(
        self, ready: PushRig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from noust.core.runner import set_runner

        ready.repos.enable(DOMAIN)
        set_runner(ready.runner)
        monkeypatch.setattr(
            "noust.pushdeploy.repos.local_console_url", lambda: "http://127.0.0.1:8080"
        )
        monkeypatch.setattr(
            "noust.pushdeploy.repos.PushRepos.refresh_if_enabled",
            lambda self, domain: (_ for _ in ()).throw(SourceError("disk full")),
        )

        rotated = webhook.mint_secret(DOMAIN)

        assert ready.store.get_webhook_secret(DOMAIN) == rotated
        assert f"noust push refresh {DOMAIN}" in caplog.text

    def test_an_application_without_push_is_left_alone_by_a_rotation(self, ready: PushRig) -> None:
        # No runner is set: a rotation that tried to rewrite a hook would meet the real one.
        assert webhook.mint_secret(DOMAIN)

    def test_an_application_that_does_not_deploy_from_push_has_nothing_to_refresh(
        self, ready: PushRig
    ) -> None:
        with pytest.raises(NoustError, match="does not deploy from git push"):
            ready.repos.refresh(DOMAIN)
        assert ready.repos.refresh_if_enabled(DOMAIN) is False


class TestTurningItOff:
    def test_the_source_and_the_branch_go_back_the_keys_go_and_the_repository_stays(
        self, ready: PushRig, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        app = ready.store.get_app(DOMAIN)
        assert app is not None
        app.branch = "production"
        ready.store.update_app(app)
        ready.repos.enable(DOMAIN, branch="main", key=ed25519(1), label="laptop")

        info = ready.repos.disable(DOMAIN)

        assert info.enabled is False
        back = ready.store.get_app(DOMAIN)
        assert back is not None
        assert back.source == "https://github.com/acme/shop.git" and back.branch == "production"
        assert ready.repos.keys.list(DOMAIN) == []
        assert push_store.get(DOMAIN, store=ready.store) is None
        assert (ready.world.home / f"{DOMAIN}.git").is_dir()
        assert ("push.disable" in [name for name, _ in events]) is True

    def test_the_repository_can_go_with_it(self, ready: PushRig) -> None:
        ready.repos.enable(DOMAIN)
        ready.repos.disable(DOMAIN, delete_repository=True)
        assert not (ready.world.home / f"{DOMAIN}.git").exists()

    def test_turning_off_what_is_not_on_is_said(self, ready: PushRig) -> None:
        with pytest.raises(NoustError, match="does not deploy from git push"):
            ready.repos.disable(DOMAIN)

    def test_forgetting_removes_everything_for_a_deleted_application(self, ready: PushRig) -> None:
        ready.repos.enable(DOMAIN, key=ed25519(1), label="laptop")
        ready.repos.forget(DOMAIN)
        assert not (ready.world.home / f"{DOMAIN}.git").exists()
        assert ready.repos.keys.list() == []
        assert push_store.get(DOMAIN, store=ready.store) is None
        ready.repos.forget(DOMAIN)  # and again, for an application with nothing


class TestKeys:
    def test_adding_and_removing(
        self, ready: PushRig, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
        ready.repos.enable(DOMAIN)

        keys = ready.repos.add_key(DOMAIN, "ci", ed25519(2))
        assert [key.label for key in keys] == ["ci"]
        assert ready.repos.remove_key(DOMAIN, "ci") == []
        assert [name for name, _ in events].count("push.key") == 2

    def test_the_key_file_sshd_reads_names_this_repository(self, ready: PushRig) -> None:
        ready.repos.enable(DOMAIN, key=ed25519(1), label="laptop")
        line = ready.world.keys_file.read_text().strip()
        assert f"git-receive-pack '{ready.world.home / (DOMAIN + '.git')}'" in line

    def test_a_key_for_an_application_that_does_not_deploy_from_push(self, ready: PushRig) -> None:
        with pytest.raises(NoustError, match="does not deploy from git push"):
            ready.repos.add_key(DOMAIN, "ci", ed25519(2))

    def test_removing_a_key_that_is_not_there(self, ready: PushRig) -> None:
        ready.repos.enable(DOMAIN)
        with pytest.raises(ValidationError, match="has no key ghost"):
            ready.repos.remove_key(DOMAIN, "ghost")


def test_the_ssh_configuration_that_makes_one_key_push_to_one_application() -> None:
    text = ssh_config_snippet(DOMAIN, "git.example.org", 2222)
    assert f"Host noust-{DOMAIN}" in text
    assert "HostName git.example.org" in text and f"User {PUSH_USER}" in text
    assert "Port 2222" in text
    assert f"IdentityFile ~/.ssh/noust-{DOMAIN}" in text and "IdentitiesOnly yes" in text
    assert "Port" not in ssh_config_snippet(DOMAIN, "git.example.org")
