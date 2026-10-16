"""
Where deploying with git push meets the rest of the product (checklist A-23).

A push application is a git application to everything above the repository cache: a release is
staged from the cache that the push repository fills, with the commit that was pushed. Around it:
an export does not carry a repository that only exists on this server, deleting the application
takes its repository and keys away, and the hook is told about a webhook secret that changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import push_store
from noust.core.logger import Logger
from noust.deployers.app_export import _export_source, plan_import
from noust.deployers.helpers.release_build import stage_release
from noust.deployers.releases import ReleaseManager
from noust.managers.source_manager import SourceManager
from noust.validators.source import push_source
from tests.push_support import DOMAIN, PushRig, build_rig, ed25519


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PushRig]:
    yield from build_rig(tmp_path, monkeypatch)


@pytest.fixture
def pushed(rig: PushRig) -> PushRig:
    rig.set_up()
    rig.app()
    rig.repos.enable(DOMAIN, key=ed25519(1), label="laptop")
    rig.runner.branches[str(rig.world.home / f"{DOMAIN}.git")] = {"main": "d" * 40}
    rig.runner.cache_head = "d" * 40
    return rig


def test_a_release_is_staged_from_what_was_pushed(pushed: PushRig) -> None:
    app = pushed.store.get_app(DOMAIN)
    assert app is not None
    root = Path(app.app_path)
    root.mkdir(parents=True)
    # The pushed tree has no submodules.
    pushed.runner.script(
        ["git", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never", "cat-file"],
        exit_code=1,
    )

    staged = stage_release(
        app.source,
        app.branch,
        releases=ReleaseManager(root),
        source_manager=SourceManager(runner=pushed.runner),
        logger=Logger(),
    )

    assert staged.commit == "d" * 40
    assert staged.path.is_dir() and staged.path.parent == root / "releases"
    archive = next(call for call in pushed.runner.calls if "archive" in call)
    assert archive[-1] == "d" * 40
    assert ("tar", "-xf") == next(c for c in pushed.runner.calls if c[0] == "tar")[:2]


def test_an_export_does_not_carry_a_repository_that_only_exists_here() -> None:
    assert _export_source(push_source(DOMAIN), with_secrets=True) is None
    assert _export_source("https://github.com/acme/shop.git", with_secrets=True) is not None


def test_an_import_refuses_a_push_source_it_was_given(pushed: PushRig) -> None:
    from noust.core.exceptions import ValidationError

    document = {"format": "noust-app", "app": {"domain": DOMAIN, "source": push_source(DOMAIN)}}
    with pytest.raises(Exception) as caught:  # the plan refuses the document or the source
        plan_import(document, domain="other.example.com", source=push_source(DOMAIN))
    assert isinstance(caught.value, ValidationError)


def test_deleting_the_application_takes_its_repository_and_keys_away(
    pushed: PushRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.deployers.lifecycle import _forget_push

    monkeypatch.setattr("noust.pushdeploy.repos.PushRepos", lambda *args, **kwargs: pushed.repos)
    problems: list[str] = []

    _forget_push(DOMAIN, lambda what, error: problems.append(f"{what}: {error}"))

    assert problems == []
    assert not (pushed.world.home / f"{DOMAIN}.git").exists()
    assert pushed.repos.keys.list() == []
    assert push_store.get(DOMAIN, store=pushed.store) is None


def test_a_deletion_that_cannot_remove_the_repository_says_so_and_goes_on(
    pushed: PushRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.exceptions import SourceError
    from noust.deployers.lifecycle import _forget_push

    def broken(self: object, domain: str) -> None:
        raise SourceError("disk is read-only")

    monkeypatch.setattr("noust.pushdeploy.repos.PushRepos.forget", broken)
    monkeypatch.setattr("noust.pushdeploy.repos.PushRepos", lambda *a, **k: pushed.repos)
    problems: list[str] = []

    _forget_push(DOMAIN, lambda what, error: problems.append(str(error)))

    assert problems and "disk is read-only" in problems[0]


def test_an_application_without_push_has_nothing_to_forget(rig: PushRig) -> None:
    from noust.deployers.lifecycle import _forget_push

    rig.app()
    problems: list[str] = []
    # No PushRepos is built for it: a deletion on a server that never set push up must not touch ssh.
    _forget_push(DOMAIN, lambda what, error: problems.append(what))
    assert problems == []
