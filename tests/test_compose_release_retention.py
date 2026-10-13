# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Compose stack keeps the images of its releases and goes back to one without building (checklist B-16).

Going back used to rebuild the old commit. A stack now names the image each service
ran after every release that passed the health gate, ``<project>-<service>:noust-<release>``,
records the release like a container application's, and activating one puts the tree
on its commit, names the images as the compose file looks them up and recreates the
containers with ``--no-build`` behind the same gate. Pinned here:

- what a release is, when it is kept and when it is not (a failed update, a tree that is
  not a checkout, an image that cannot be named);
- the retention, which removes the tags and the records of what ages out;
- activating one: the order of the operations, nothing built, the statuses, and what
  happens when the images were pruned or the containers do not answer;
- how the history offers it: ``rollback_availability``, ``rollback_methods`` and
  ``rollback_to_deployment`` take the swap when the images are there and rebuild when not.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core import compose_release_store
from noust.core.exceptions import DeploymentError, DockerError, RolledBackError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, NoustStore, ReleaseStatus
from noust.deployers import docker_compose, lifecycle
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import compose_retention
from noust.deployers.releases import release_order_key

DOMAIN = "stack.example.com"

COMPOSE_FILE = """\
services:
  web:
    build: .
    ports:
      - "8080:80"
  db:
    image: postgres:16
"""

#: What runs before an update: the old images.
OLD = [
    ("web", "stack-web:latest", "sha256:old0web", "stack"),
    ("db", "postgres:16", "sha256:old0pg16", "stack"),
]
#: What runs after the containers are recreated from the new build.
NEW = [
    ("web", "stack-web:latest", "sha256:new0web", "stack"),
    ("db", "postgres:16", "sha256:old0pg16", "stack"),
]


class Docker(FakeRunner):
    """
    Docker and git as a stack being updated and gone back sees them.

    Attributes:
        commit: The commit the tree is on, as ``git rev-parse --short HEAD`` says.
        running: What ``docker inspect`` says the containers run.
        after: What they run once ``up`` recreates them from a new build.
        kept: The tags ``docker images`` lists.
        tag_fails: Tags (or their start) that cannot be created.
        images_fail: ``docker images`` does not answer.
        removed: Tags ``docker image rm`` was asked to remove.
    """

    def __init__(self) -> None:
        super().__init__()
        self.commit = "1111111"
        self.running = list(OLD)
        self.after: list[tuple[str, str, str, str]] | None = None
        self.kept: set[str] = set()
        self.tag_fails: tuple[str, ...] = ()
        self.images_fail = False
        self.removed: list[str] = []

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv

        def answer(stdout: str = "", stderr: str = "", code: int = 0) -> CommandResult:
            return replace(result, stdout=stdout, stderr=stderr, exit_code=code)

        if args[:2] == ("docker", "inspect"):
            # docker inspect prints: image id, reference, service, project.
            return answer("".join(f"{row[2]}|{row[1]}|{row[0]}|{row[3]}\n" for row in self.running))
        if args[:3] == ("docker", "image", "tag"):
            destination = args[4]
            if self.tag_fails and destination.startswith(self.tag_fails):
                return answer(stderr=f"Error: cannot tag {args[3]}", code=1)
            if ":noust-" in destination:
                self.kept.add(destination)
            return answer()
        if args[:3] == ("docker", "image", "rm"):
            self.removed.append(args[3])
            self.kept.discard(args[3])
            return answer()
        if args[:2] == ("docker", "images"):
            if self.images_fail:
                return answer(stderr="Cannot connect to the Docker daemon", code=1)
            return answer("\n".join(sorted(self.kept)))
        if args[:2] == ("docker", "compose"):
            if "ps" in args and "-q" in args:
                return answer("c0ffee01c0ffee01\nc0ffee02c0ffee02\n")
            if "ps" in args:
                states = [
                    {"Service": row[0], "Name": f"stack-{row[0]}-1", "State": "running"}
                    for row in self.running
                ]
                return answer("\n".join(json.dumps(state) for state in states))
            if "up" in args and "--no-build" not in args and self.after is not None:
                self.running = list(self.after)
        if args[0] == "git":
            if "--short" in args:
                return answer(self.commit + "\n")
            if "--abbrev-ref" in args:
                return answer("main\n")
            if "--verify" in args:
                wanted = next(a for a in args if a.endswith("^{commit}")).removesuffix("^{commit}")
                return answer(wanted.ljust(40, "0") + "\n")
            if args[1:3] == ("checkout", "--force"):
                self.commit = args[-1][:7]
        if args[0] == "journalctl":
            return answer("up: done\n")
        return result

    def argv_with(self, *words: str) -> list[tuple[str, ...]]:
        """Every call containing all the given words."""
        return [call for call in self.calls if all(word in call for word in words)]

    def index_of(self, *words: str) -> int:
        """Position of the first call containing all the given words."""
        return next(i for i, call in enumerate(self.calls) if all(word in call for word in words))


class Probe:
    """The HTTP probe: answers from a script, failing the first ``fail_rounds`` asks of a URL."""

    def __init__(self) -> None:
        self.failing: set[str] = set()
        self.fail_rounds = 1
        self._seen: dict[str, int] = {}

    def fail_next(self, url: str, rounds: int = 1) -> None:
        """Make the next ``rounds`` asks of a URL fail, whatever was asked before."""
        self.failing.add(url)
        self.fail_rounds = rounds
        self._seen[url] = 0

    def __call__(
        self,
        url: str,
        *,
        retries: int = 5,
        delay: float = 2.0,
        on_attempt: Callable[[str], None] | None = None,
        accept: Callable[[int], bool] | None = None,
        within: float | None = None,
        **_: Any,
    ) -> bool:
        self._seen[url] = self._seen.get(url, 0) + 1
        return not (url in self.failing and self._seen[url] <= self.fail_rounds)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    monkeypatch.setattr(docker_compose, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_compose.time, "sleep", lambda seconds: None)


@pytest.fixture
def docker() -> Iterator[Docker]:
    fake = Docker()
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Probe:
    fake = Probe()
    monkeypatch.setattr(docker_compose, "wait_until_healthy", fake)
    return fake


def stack(store: NoustStore, root: Path, *, git: bool = True) -> App:
    root.mkdir(parents=True, exist_ok=True)
    if git:
        (root / ".git").mkdir(exist_ok=True)
    (root / "docker-compose.yml").write_text(COMPOSE_FILE)
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type="docker-compose",
            app_path=str(root),
            port=8080,
            status="running",
        )
    )


def deployer(root: Path, runner: FakeRunner) -> DockerComposeDeployer:
    made = DockerComposeDeployer(runner=runner)
    made.app_path = root
    made.app_name = root.name
    made.domain = DOMAIN
    made.previous_commit = "1111111"
    return made


def update(root: Path, runner: Docker, commit: str) -> DockerComposeDeployer:
    """One update that builds ``commit`` and passes the gate."""
    runner.commit = commit
    runner.after = [
        ("web", "stack-web:latest", f"sha256:{commit}web", "stack"),
        ("db", "postgres:16", "sha256:old0pg16", "stack"),
    ]
    made = deployer(root, runner)
    made.update()
    return made


@pytest.fixture
def root(tmp_path: Path, store: NoustStore) -> Path:
    path = tmp_path / "stack"
    stack(store, path)
    return path


def releases(store: NoustStore) -> list[Any]:
    app = store.get_app(DOMAIN)
    assert app is not None and app.id is not None
    return store.list_releases(app.id)


def images(store: NoustStore, release_id: str) -> tuple[Any, ...]:
    app = store.get_app(DOMAIN)
    assert app is not None and app.id is not None
    return compose_release_store.images_by_release(app.id, store=store).get(release_id, ())


# ---------------------------------------------------------------------------
# Keeping a release
# ---------------------------------------------------------------------------


def test_a_tag_names_the_service_and_the_release() -> None:
    assert (
        compose_retention.release_tag("Stack", "Web_1", "20261013-101500-abc1234")
        == "stack-web_1:noust-20261013-101500-abc1234"
    )
    assert compose_retention.release_tag("a b", "c", "r").startswith("a-b-c:")


def test_an_update_that_passes_keeps_the_images_it_serves_as_a_release(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    update(root, docker, "2222222")

    (release,) = releases(store)
    assert release.git_commit == "2222222" and release.status == "active"
    kept = {image.service: image for image in images(store, release.id)}
    assert kept["web"].tag == f"stack-web:noust-{release.id}"
    assert kept["web"].reference == "stack-web:latest"
    assert kept["web"].image_id == "sha256:2222222web", "the image the new containers run"
    assert kept["db"].reference == "postgres:16"
    assert docker.argv_with("docker", "image", "tag", "sha256:2222222web", kept["web"].tag)
    assert store.list_deployments(DOMAIN)[0].release_id == release.id


def test_a_failed_update_keeps_no_release(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    probe.failing.add("http://127.0.0.1:8080/")
    docker.after = list(NEW)

    with pytest.raises(DeploymentError):
        deployer(root, docker).update()

    assert releases(store) == [] and docker.kept == set()


def test_a_tree_that_is_not_a_checkout_keeps_nothing(
    tmp_path: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    path = tmp_path / "plain"
    stack(store, path, git=False)
    docker.after = list(NEW)

    deployer(path, docker).update()

    assert releases(store) == []


def test_images_that_cannot_all_be_named_make_no_release_and_do_not_fail_the_update(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    docker.tag_fails = ("stack-db:noust-",)
    docker.after = list(NEW)

    made = deployer(root, docker)
    made.update()

    assert releases(store) == []
    assert docker.removed and docker.removed[0].startswith("stack-web:noust-"), (
        "the one that was named is not left behind"
    )
    assert made.release_id is None


def test_a_release_is_kept_without_rehearsing(
    root: Path, store: NoustStore, docker: Docker, probe: Probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = deployer(root, docker)
    monkeypatch.setattr(made, "_rehearsing", lambda: True)

    made._retain_release()

    assert releases(store) == []


def test_a_container_application_keeps_its_own_images_not_these() -> None:
    from noust.deployers.container import ContainerDeployer

    assert DockerComposeDeployer.RETAINS_RELEASES is True
    assert ContainerDeployer.RETAINS_RELEASES is False


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_releases_past_the_retention_lose_their_images_and_their_record(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    store.set_keep_releases(DOMAIN, 2)
    update(root, docker, "1111111")
    (oldest,) = releases(store)
    oldest_tags = {image.tag for image in images(store, oldest.id)}

    update(root, docker, "2222222")
    update(root, docker, "3333333")

    assert [r.git_commit for r in releases(store)] == ["3333333", "2222222"]
    assert set(docker.removed) == oldest_tags
    assert images(store, oldest.id) == ()
    assert len(docker.kept) == 4, "two releases, two images each"


def test_the_retention_never_goes_below_what_serves_and_the_way_back(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    store.set_keep_releases(DOMAIN, 1)

    for commit in ("1111111", "2222222", "3333333"):
        update(root, docker, commit)

    assert [r.git_commit for r in releases(store)] == ["3333333", "2222222"]


def test_the_release_that_serves_is_kept_wherever_it_is(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    first, _, _ = three_releases(root, docker, store)
    deployer(root, docker).activate_release(first, went_back=True)
    store.set_keep_releases(DOMAIN, 1)
    app = store.get_app(DOMAIN)
    assert app is not None

    forgotten = deployer(root, docker)._prune_releases(app)

    assert forgotten == []
    assert len(releases(store)) == 3


def test_removing_a_stack_removes_the_images_its_releases_kept(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    update(root, docker, "1111111")
    (release,) = releases(store)
    tags = {image.tag for image in images(store, release.id)}

    removed = deployer(root, docker).remove_kept_images()

    assert tags <= set(removed) and tags <= set(docker.removed)
    assert images(store, release.id) == ()


# ---------------------------------------------------------------------------
# What can be gone back to
# ---------------------------------------------------------------------------


def test_a_release_is_present_only_when_every_one_of_its_images_is(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    update(root, docker, "1111111")
    (release,) = releases(store)
    made = deployer(root, docker)

    assert [r.present for r in made.release_images()] == [True]
    docker.kept.discard(f"stack-db:noust-{release.id}")
    (listed,) = made.release_images()

    assert listed.present is False and listed.active is True


def test_listing_the_releases_when_docker_cannot_say_is_an_error_not_an_empty_list(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    update(root, docker, "1111111")
    docker.images_fail = True

    with pytest.raises(DockerError):
        deployer(root, docker).release_images()


# ---------------------------------------------------------------------------
# Going back to a release
# ---------------------------------------------------------------------------


def in_order(store: NoustStore) -> list[str]:
    """The release ids, oldest first."""
    return [r.id for r in sorted(releases(store), key=lambda r: release_order_key(r.id))]


def two_releases(root: Path, docker: Docker, store: NoustStore) -> tuple[str, str]:
    update(root, docker, "1111111")
    update(root, docker, "2222222")
    first, second = in_order(store)
    return first, second


def three_releases(root: Path, docker: Docker, store: NoustStore) -> tuple[str, str, str]:
    update(root, docker, "1111111")
    update(root, docker, "2222222")
    update(root, docker, "3333333")
    first, second, third = in_order(store)
    return first, second, third


def test_activating_a_release_recreates_the_containers_from_its_images_without_building(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    first, second = two_releases(root, docker, store)
    docker.calls.clear()

    deployer(root, docker).activate_release(first, went_back=True)

    checkout = docker.index_of("git", "checkout", "--force", "--detach")
    named = docker.index_of(
        "docker", "image", "tag", f"stack-web:noust-{first}", "stack-web:latest"
    )
    up = docker.index_of("up", "-d", "--no-build", "--remove-orphans")
    assert checkout < named < up
    assert docker.calls[checkout][-1].startswith("1111111")
    assert not docker.argv_with("compose", "build")
    assert not [c for c in docker.calls if "up" in c and "--no-build" not in c]
    status = {r.id: r.status for r in releases(store)}
    assert status[first] == ReleaseStatus.ACTIVE.value
    assert status[second] == ReleaseStatus.ROLLED_BACK.value


def test_going_forward_to_a_kept_release_does_not_mark_the_one_it_replaces(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    first, second = two_releases(root, docker, store)
    deployer(root, docker).activate_release(first, went_back=True)

    deployer(root, docker).activate_release(second, went_back=False)

    status = {r.id: r.status for r in releases(store)}
    assert status[second] == "active" and status[first] == "superseded"


def test_a_release_whose_images_were_pruned_cannot_be_activated(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    first, _ = two_releases(root, docker, store)
    docker.kept.discard(f"stack-web:noust-{first}")
    docker.calls.clear()

    with pytest.raises(DeploymentError) as caught:
        deployer(root, docker).activate_release(first, went_back=True)

    assert "no longer on this server" in caught.value.message
    assert not docker.argv_with("checkout"), "the tree was not touched"


def test_a_release_that_was_never_kept_says_so(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    update(root, docker, "1111111")

    with pytest.raises(DeploymentError) as caught:
        deployer(root, docker).activate_release("20200101-000000-aaaaaaa", went_back=True)

    assert "has no kept images" in caught.value.message
    assert "noust update" in (caught.value.details or "")


def test_a_release_that_does_not_answer_goes_back_to_what_served_and_is_marked_failed(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    first, second = two_releases(root, docker, store)
    probe.fail_next("http://127.0.0.1:8080/")

    with pytest.raises(RolledBackError) as caught:
        deployer(root, docker).activate_release(first, went_back=True)

    assert "running again" in caught.value.message
    status = {r.id: r.status for r in releases(store)}
    assert status[first] == ReleaseStatus.FAILED.value
    assert status[second] == ReleaseStatus.ACTIVE.value, "what served is still the active one"
    restored = [c for c in docker.calls if c[-1].startswith("2222222") and "checkout" in c]
    assert restored, "the tree went back to the commit that served"


def test_a_stack_that_cannot_be_named_again_is_put_back_before_anything_is_recreated(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    first, _ = two_releases(root, docker, store)
    docker.tag_fails = ("stack-web:latest",)
    docker.calls.clear()

    with pytest.raises(DockerError) as caught:
        deployer(root, docker).activate_release(first, went_back=True)

    assert "could not be named" in caught.value.message
    assert not [c for c in docker.calls if "up" in c]


# ---------------------------------------------------------------------------
# What the history offers
# ---------------------------------------------------------------------------


def history(store: NoustStore) -> list[Any]:
    return sorted(store.list_deployments(DOMAIN), key=lambda row: row.id or 0)


def test_the_history_offers_a_kept_release_as_an_instant_way_back(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    two_releases(root, docker, store)
    first, second = history(store)[:2]

    availability = lifecycle.rollback_availability([first, second])
    methods = lifecycle.rollback_methods([first, second], availability)

    assert availability[first.id] is None and methods == {first.id: "instant"}
    assert "already live" in (availability[second.id] or "")


def test_without_the_images_the_history_offers_to_build_the_commit_again(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    two_releases(root, docker, store)
    first = history(store)[0]
    docker.kept.clear()

    availability = lifecycle.rollback_availability([first])
    methods = lifecycle.rollback_methods([first], availability)

    assert availability[first.id] is None
    assert methods == {first.id: "rebuild"}


def test_a_deployment_that_cannot_be_gone_back_to_has_no_method(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    two_releases(root, docker, store)
    last = history(store)[-1]

    availability = lifecycle.rollback_availability([last])

    assert availability[last.id] is not None
    assert lifecycle.rollback_methods([last], availability) == {}


def test_a_rollback_to_a_deployment_swaps_the_images_when_they_are_kept(
    root: Path, store: NoustStore, docker: Docker, probe: Probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = two_releases(root, docker, store)
    target = history(store)[0]

    def never_built(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a rollback with its images must not build")

    monkeypatch.setattr(lifecycle, "update_app", never_built)
    docker.calls.clear()

    outcome = lifecycle.rollback_to_deployment(DOMAIN, target.id)

    assert outcome.release_id == first and outcome.history_id is not None
    assert docker.argv_with("up", "-d", "--no-build")
    assert not docker.argv_with("compose", "build")
    row = store.get_deployment(outcome.history_id)
    assert row is not None and row.status == "success" and row.release_id == first


def test_a_rollback_without_the_images_builds_the_commit_again(
    root: Path, store: NoustStore, docker: Docker, probe: Probe, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_releases(root, docker, store)
    target = history(store)[0]
    docker.kept.clear()
    built: list[str | None] = []

    def rebuilt(domain: str, **kwargs: Any) -> Any:
        built.append(kwargs.get("commit"))
        return type("Outcome", (), {"deployment_id": 99})()

    monkeypatch.setattr(lifecycle, "update_app", rebuilt)

    outcome = lifecycle.rollback_to_deployment(DOMAIN, target.id)

    assert built == ["1111111"] and outcome.history_id == 99


def test_the_releases_commands_list_and_activate_a_stack_like_a_container(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    first, second = two_releases(root, docker, store)

    listed = lifecycle.list_releases(DOMAIN)
    assert [(r.id, r.active, r.on_disk) for r in listed] == [
        (second, True, True),
        (first, False, True),
    ]

    outcome = lifecycle.activate_release(DOMAIN, None)

    assert outcome.release.id == first and outcome.went_back is True
    assert [r.id for r in lifecycle.list_releases(DOMAIN) if r.active] == [first]


def test_changing_the_retention_prunes_a_stacks_images_too(
    root: Path, store: NoustStore, docker: Docker, probe: Probe
) -> None:
    for commit in ("1111111", "2222222", "3333333", "4444444"):
        update(root, docker, commit)
    assert len(releases(store)) == 4

    change = lifecycle.set_release_retention(DOMAIN, 2)

    assert change.keep_releases == 2 and len(change.pruned) == 2
    assert [r.git_commit for r in releases(store)] == ["4444444", "3333333"]
    assert len(docker.removed) == 4
