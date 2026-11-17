# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stack that already runs goes on updating: what it already had is what its containers have.

The guard keeps three rules. What a stack's containers are given of the server is refused in a new stack;
what a stack that already ran **already had** is warned about and never stops anything; what an update adds is
refused. The second rule is only true if "already had" is read from something the repository and the
stack's ``.env`` cannot write: a stack whose mount is ``${DATA_DIR}:/data``, or comes in by an ``include`` or
an ``extends``, shows nothing in the file as written, and resolving the file with the ``.env`` of now would
let anybody who can change a variable (``DATA_DIR=/``) say that the stack always had it. The containers are
what runs, and a container is not changed without a deployment, which goes through the guard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.compose_yaml import load_compose
from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import sandbox as build_sandbox
from tests.support.docker_grants_samples import container_id, grants_line, ps_row

DOMAIN = "stack.example.com"
COMMIT = "a" * 40

#: What the repository holds: the mount is a variable, so the file says nothing of the server.
SERVED = "services:\n  web:\n    image: nginx\n    volumes: ['${DATA_DIR}:/data']\n"


def bind(source: str) -> dict[str, str]:
    """A bind mount as ``docker compose config --format json`` prints it."""
    return {"type": "bind", "source": source, "target": "/data"}


def resolved(**web: Any) -> dict[str, Any]:
    """What Compose resolves the stack to, with ``web`` as its one service."""
    return {"services": {"web": {"image": "nginx", **web}}}


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store of the test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def project_of(compose: Any) -> str:
    """The project the deployer's own commands address, read from one of them."""
    argv = compose._compose("ps")
    return str(argv[argv.index("-p") + 1])


def stack(
    tmp_path: Path,
    store: NoustStore,
    *,
    model: dict[str, Any] | None,
    boxes: list[dict[str, Any]] | None = None,
    text: str = SERVED,
    said: list[str] | None = None,
    docker_down: str | None = None,
) -> Any:
    """
    A compose deployer whose Compose resolves the stack to ``model`` and whose Docker has ``boxes``.

    Args:
        tmp_path: Where the application's directory is made.
        store: The store.
        model: What ``docker compose config`` prints; None for a Compose that cannot resolve it.
        boxes: The containers of the stack, each as the keywords of ``grants_line`` with ``service``.
        text: The compose file in the tree.
        said: Where the deployer's log goes.
        docker_down: What Docker says when its daemon does not answer, if it does not.
    """
    app = tmp_path / "stack"
    app.mkdir(exist_ok=True)
    (app / ".env").write_text("DATA_DIR=/srv/data\n")
    runner = FakeRunner()
    if model is None:
        runner.script(["docker", "compose"], stderr="invalid compose project", exit_code=1)
    else:
        runner.script(["docker", "compose"], stdout=json.dumps(model))
    compose = DockerComposeDeployer(runner=runner)
    compose.configure(DOMAIN, "src", app_path=app)
    compose.store = store
    compose.compose_path = app / "docker-compose.yml"
    compose.compose_path.write_text(text)
    compose.previous_commit = COMMIT
    project = project_of(compose)
    rows = [
        ps_row(
            container_id(box["service"]),
            f"{project}-{box['service']}-1",
            project=project,
            service=box["service"],
        )
        for box in boxes or []
    ]
    lines = [grants_line(**box) for box in boxes or []]
    if docker_down is not None:
        runner.script(["docker", "ps"], stderr=docker_down, exit_code=1)
    else:
        runner.script(["docker", "ps"], stdout="\n".join(rows))
    runner.script(["docker", "inspect"], stdout="\n".join(lines))
    if said is not None:
        compose.logger.attach_sink(said.append)
    return compose


def update(
    compose: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    served: str = SERVED,
    changed: Any = frozenset(),
) -> None:
    """Run the guard as an update does, against the file of the commit that served."""

    class Source:
        def file_at_commit(self, *_args: Any) -> str:
            return served

    monkeypatch.setattr(compose, "_source_manager", lambda: Source())
    monkeypatch.setattr(
        "noust.deployers.helpers.compose_privileges.changed_links", lambda *_a: changed
    )
    compose._check_update_privileges(load_compose(compose.compose_path.read_text()))


def docker_calls(compose: Any, *shape: str) -> list[tuple[str, ...]]:
    """The calls the deployer made to Docker that start like ``shape``."""
    return [call for call in compose.runner.calls if call[: len(shape)] == shape]


class TestWhatOnlyResolvingShows:
    """A mount made of a variable, or brought by another file, is what the containers have."""

    def test_a_mount_made_of_a_variable_that_a_container_already_has_updates_with_a_warning(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        said: list[str] = []
        compose = stack(
            tmp_path,
            store,
            model=resolved(volumes=[bind("/srv/data")]),
            boxes=[{"service": "web", "binds": ("/srv/data",)}],
            said=said,
        )

        update(compose, monkeypatch)

        assert any("/srv/data" in line and "outside the application" in line for line in said)

    def test_the_same_stack_is_judged_again_when_its_env_is_written_and_docker_is_asked_once(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compose = stack(
            tmp_path,
            store,
            model=resolved(volumes=[bind("/srv/data")]),
            boxes=[{"service": "web", "binds": ("/srv/data",)}],
        )

        update(compose, monkeypatch)
        compose._check_final_privileges()

        assert len(docker_calls(compose, "docker", "inspect")) == 1

    def test_a_mount_brought_by_an_include_is_what_the_container_has(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = "include:\n  - common.yml\nservices:\n  web:\n    image: nginx\n"
        said: list[str] = []
        compose = stack(
            tmp_path,
            store,
            text=text,
            model=resolved(volumes=[bind("/srv/data")]),
            boxes=[{"service": "web", "binds": ("/srv/data",)}],
            said=said,
        )

        update(compose, monkeypatch, served=text)

        assert any("/srv/data" in line for line in said)

    def test_a_mount_brought_by_an_extends_is_what_the_container_has(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = "services:\n  web:\n    extends:\n      file: common.yml\n      service: base\n"
        said: list[str] = []
        compose = stack(
            tmp_path,
            store,
            text=text,
            model=resolved(volumes=[bind("/srv/data")]),
            boxes=[{"service": "web", "binds": ("/srv/data",)}],
            said=said,
        )

        update(compose, monkeypatch, served=text)

        assert any("/srv/data" in line for line in said)

    @pytest.mark.parametrize("source", ["/", "/srv/other", "/srv/data/../other"])
    def test_a_variable_changed_to_somewhere_else_is_refused_saying_the_new_line(
        self, source: str, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``DATA_DIR`` was ``/srv/data`` when the containers were made and somebody who can change a
        # variable changed it: the containers did not move, so the stack never had this.
        compose = stack(
            tmp_path,
            store,
            model=resolved(volumes=[bind(source)]),
            boxes=[{"service": "web", "binds": ("/srv/data",)}],
        )

        with pytest.raises(DeploymentError, match="asks for root") as raised:
            update(compose, monkeypatch)

        assert "service web mounts" in (raised.value.details or "")

    def test_a_redeploy_does_not_take_what_the_env_says_now_for_what_the_stack_had(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        # What a redeploy used to remember was the file on disk resolved with the ``.env`` on disk, so
        # a variable changed since the containers were made became what the stack always had.
        compose = stack(
            tmp_path,
            store,
            model=resolved(volumes=[bind("/srv/other")]),
            boxes=[{"service": "web", "binds": ("/srv/data",)}],
        )

        compose._remember_serving_privileges()
        compose._discover_compose_file()

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_host_privileges(load_compose(SERVED))

    def test_a_redeploy_of_a_stack_whose_container_has_the_mount_goes_on_with_a_warning(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        said: list[str] = []
        compose = stack(
            tmp_path,
            store,
            model=resolved(volumes=[bind("/srv/data")]),
            boxes=[{"service": "web", "binds": ("/srv/data",)}],
            said=said,
        )

        compose._remember_serving_privileges()
        compose._discover_compose_file()
        compose._check_host_privileges(load_compose(SERVED))

        assert any("/srv/data" in line for line in said)


class TestWhatAnUpdateAdds:
    ADDITIONS = {
        "a directory of the server": (
            {"volumes": [bind("/srv/data"), bind("/srv/more")]},
            "/srv/more",
        ),
        "a device": (
            {
                "volumes": [bind("/srv/data")],
                "devices": [{"source": "/dev/kvm", "target": "/dev/kvm", "permissions": "rwm"}],
            },
            "/dev/kvm",
        ),
        "a capability": ({"volumes": [bind("/srv/data")], "cap_add": ["SYS_ADMIN"]}, "SYS_ADMIN"),
        "the network of the host": (
            {"volumes": [bind("/srv/data")], "network_mode": "host"},
            "network",
        ),
        "the processes of the host": ({"volumes": [bind("/srv/data")], "pid": "host"}, "pid"),
    }

    @pytest.mark.parametrize("name", list(ADDITIONS))
    def test_what_no_container_has_is_refused(
        self, name: str, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        added, said = self.ADDITIONS[name]
        compose = stack(
            tmp_path,
            store,
            model=resolved(**added),
            boxes=[{"service": "web", "binds": ("/srv/data",)}],
        )

        with pytest.raises(DeploymentError, match="asks for root") as raised:
            update(compose, monkeypatch)

        assert said in (raised.value.details or "")
        assert "/srv/data from" not in (raised.value.details or ""), (
            "what it had is not listed as new"
        )

    def test_what_another_service_has_is_not_what_this_one_had(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model = {
            "services": {
                "web": {"image": "nginx", "volumes": [bind("/srv/data")]},
                "worker": {"image": "nginx", "volumes": [bind("/srv/data")]},
            }
        }
        compose = stack(
            tmp_path,
            store,
            model=model,
            boxes=[{"service": "web", "binds": ("/srv/data",)}, {"service": "worker"}],
        )

        with pytest.raises(DeploymentError, match="asks for root") as raised:
            update(compose, monkeypatch)

        assert "service worker mounts /srv/data" in (raised.value.details or "")
        assert "service web mounts" not in (raised.value.details or "")

    def test_a_service_that_has_no_container_yet_has_nothing(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compose = stack(tmp_path, store, model=resolved(volumes=[bind("/srv/data")]), boxes=[])

        with pytest.raises(DeploymentError, match="asks for root"):
            update(compose, monkeypatch)

    def test_a_new_stack_has_nothing_and_docker_is_not_asked(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = stack(
            tmp_path,
            store,
            model=resolved(volumes=[bind("/srv/data")]),
            boxes=[{"service": "web", "binds": ("/srv/data",)}],
        )

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_host_privileges(load_compose(SERVED), final=True)

        assert docker_calls(compose, "docker", "ps") == []


class TestWhatAContainerHasIsTheSameFactWhateverTheSpelling:
    SAME = {
        "a capability": ({"cap_add": ["sys_admin"]}, {"cap_add": ("CAP_SYS_ADMIN",)}),
        "confinement turned off": (
            {"security_opt": ["seccomp:unconfined"]},
            {"security_opt": ("seccomp=unconfined",)},
        ),
        "a device": (
            {"devices": [{"source": "/dev/kvm", "target": "/dev/kvm", "permissions": "rwm"}]},
            {"devices": ("/dev/kvm",)},
        ),
        "the processes of the host": ({"pid": "host"}, {"pid": "host"}),
        "the host's network": ({"network_mode": "host"}, {"network": "host"}),
        "the host's ipc": ({"ipc": "host"}, {"ipc": "host"}),
        "another runtime": ({"runtime": "kata"}, {"runtime": "kata"}),
        "another cgroup": ({"cgroup_parent": "/system.slice"}, {"cgroup_parent": "/system.slice"}),
        "the volumes of a container that is not the stack's": (
            {"volumes_from": ["container:shared-data:ro"]},
            {"volumes_from": ("shared-data:ro",)},
        ),
        "a directory spelled with a trailing slash": (
            {"volumes": [bind("/srv/data/")]},
            {"binds": ("/srv/data",)},
        ),
    }

    @pytest.mark.parametrize("name", list(SAME))
    def test_what_it_has_is_not_new(
        self, name: str, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spelled, given = self.SAME[name]
        said: list[str] = []
        compose = stack(
            tmp_path,
            store,
            model=resolved(**spelled),
            boxes=[{"service": "web", **given}],
            said=said,
        )

        update(compose, monkeypatch)

        assert any(
            "Compose: " in line and "A new stack is refused this" in line for line in said
        ), f"it is said, not hidden: {said}"

    def test_a_device_written_the_short_way_in_the_file_is_the_device_the_container_has(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = "services:\n  web:\n    image: nginx\n    devices: ['/dev/kvm:/dev/kvm:rwm']\n"
        said: list[str] = []
        compose = stack(
            tmp_path,
            store,
            text=text,
            model=None,
            boxes=[{"service": "web", "devices": ("/dev/kvm",)}],
            said=said,
        )

        update(compose, monkeypatch, served="services:\n  web:\n    image: nginx\n")

        assert any("/dev/kvm" in line for line in said)

    def test_a_container_on_the_network_of_a_service_of_its_own_stack_is_not_a_finding(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Compose turns ``network_mode: service:vpn`` into ``container:<id of vpn>``.
        model = {
            "services": {
                "vpn": {"image": "x"},
                "web": {"image": "nginx", "network_mode": "service:vpn"},
            }
        }
        said: list[str] = []
        compose = stack(
            tmp_path,
            store,
            model=model,
            boxes=[
                {"service": "vpn"},
                {"service": "web", "network": f"container:{container_id('vpn')}"},
            ],
            said=said,
        )

        update(compose, monkeypatch)

        assert not [line for line in said if "joins" in line]


class TestWhatIsNotInheritedByRunning:
    """What 4.2 refused without an exception is only ever inherited from the file that served."""

    @pytest.mark.parametrize(
        ("box", "added"),
        [
            ({"privileged": True}, {"privileged": True}),
            ({"binds": ("/var/run/docker.sock",)}, {"volumes": [bind("/var/run/docker.sock")]}),
            ({"binds": ("/etc/noust",)}, {"volumes": [bind("/etc/noust")]}),
            ({"binds": ("/",)}, {"volumes": [bind("/")]}),
        ],
        ids=["privileged", "the Docker socket", "Noust's state", "the whole server"],
    )
    def test_a_container_that_has_it_does_not_make_it_the_stacks_to_keep(
        self,
        box: dict[str, Any],
        added: dict[str, Any],
        tmp_path: Path,
        store: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        compose = stack(tmp_path, store, model=resolved(**added), boxes=[{"service": "web", **box}])

        with pytest.raises(DeploymentError, match="asks for root"):
            update(compose, monkeypatch)

    def test_a_file_that_served_with_it_written_plainly_goes_on_as_it_always_did(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = "services:\n  web:\n    image: nginx\n    privileged: true\n"
        said: list[str] = []
        compose = stack(
            tmp_path,
            store,
            text=text,
            model=resolved(privileged=True),
            boxes=[{"service": "web", "privileged": True}],
            said=said,
        )

        update(compose, monkeypatch, served=text)

        assert any("is privileged" in line for line in said)

    def test_an_exception_recorded_for_the_stack_lets_it_through_without_asking_docker(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        build_sandbox.set_compose_exception(
            DOMAIN, allowed=True, actor="ana", reason="needs it", store=store
        )
        compose = stack(tmp_path, store, model=resolved(volumes=[bind("/srv/other")]), boxes=[])

        update(compose, monkeypatch)

        assert docker_calls(compose, "docker", "ps") == []


class TestWhenDockerDoesNotAnswer:
    def test_it_is_a_refusal_that_says_why_and_how_to_mend_it_and_never_a_pass(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compose = stack(
            tmp_path,
            store,
            model=resolved(volumes=[bind("/srv/data")]),
            docker_down="Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
        )

        with pytest.raises(DeploymentError) as raised:
            update(compose, monkeypatch)

        error = raised.value
        assert (
            error.coded is not None and error.coded.code == "compose_guard.cannot_tell_what_it_has"
        )
        assert "service web mounts /srv/data" in (error.details or "")
        assert "systemctl status docker" in (error.details or "")
        assert "Cannot connect to the Docker daemon" in (error.output or "")

    def test_a_stack_with_nothing_in_the_way_does_not_need_docker_to_update(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compose = stack(
            tmp_path,
            store,
            text="services:\n  web:\n    image: nginx\n",
            model=resolved(),
            docker_down="Cannot connect to the Docker daemon",
        )

        update(compose, monkeypatch, served="services:\n  web:\n    image: nginx\n")

        assert docker_calls(compose, "docker", "ps") == []


class TestAMountThroughALink:
    """A line that reaches somewhere else because of a link the update put there is not what ran."""

    @pytest.fixture
    def linked(self, tmp_path: Path, store: NoustStore) -> Any:
        app = tmp_path / "stack"
        app.mkdir()
        (app / "data").symlink_to("/srv/elsewhere")
        return app

    def check(
        self, linked: Path, tmp_path: Path, store: NoustStore, monkeypatch: Any, changed: Any
    ) -> None:
        compose = stack(
            tmp_path,
            store,
            model=resolved(volumes=[bind(str(linked / "data"))]),
            boxes=[{"service": "web", "binds": (str(linked / "data"),)}],
        )
        update(compose, monkeypatch, changed=changed)

    def test_a_link_nobody_committed_is_what_the_container_already_had(
        self, linked: Path, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.check(linked, tmp_path, store, monkeypatch, frozenset())

    def test_a_link_the_update_changed_is_refused(
        self, linked: Path, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(DeploymentError, match="asks for root"):
            self.check(linked, tmp_path, store, monkeypatch, frozenset({linked / "data"}))

    def test_when_git_cannot_say_it_is_refused_rather_than_guessed(
        self, linked: Path, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(DeploymentError, match="asks for root"):
            self.check(linked, tmp_path, store, monkeypatch, None)


class TestWhatHealthLists:
    """``noust health`` and the ENS check read the same thing the update does."""

    def app(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner, boxes: list[dict[str, Any]]
    ) -> App:
        path = tmp_path / "stack"
        path.mkdir()
        (path / "docker-compose.yml").write_text(SERVED)
        app = App(
            domain=DOMAIN,
            app_type="docker-compose",
            app_path=str(path),
            compose_project="stackproject",
        )
        store.create_app(app)
        rows = [
            ps_row(
                container_id(b["service"]),
                f"stackproject-{b['service']}-1",
                project="stackproject",
                service=b["service"],
            )
            for b in boxes
        ]
        runner.script(["docker", "ps"], stdout="\n".join(rows))
        runner.script(["docker", "inspect"], stdout="\n".join(grants_line(**b) for b in boxes))
        return app

    def test_a_stack_whose_file_says_nothing_and_whose_container_mounts_the_server_is_listed(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner
    ) -> None:
        app = self.app(tmp_path, store, runner, [{"service": "web", "binds": ("/srv/data",)}])

        warning = build_sandbox.sandbox_warning(app)

        assert warning is not None
        assert "service web mounts /srv/data" in warning
        assert f"noust app sandbox compose-exception {DOMAIN}" in warning

    def test_a_stack_with_an_exception_is_not_listed(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner
    ) -> None:
        app = self.app(tmp_path, store, runner, [{"service": "web", "binds": ("/srv/data",)}])
        build_sandbox.set_compose_exception(
            DOMAIN, allowed=True, actor="ana", reason="needs it", store=store
        )

        assert build_sandbox.sandbox_warning(app) is None

    def test_a_stack_that_has_nothing_of_the_server_is_not_listed(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner
    ) -> None:
        app = self.app(tmp_path, store, runner, [{"service": "web"}])

        assert build_sandbox.sandbox_warning(app) is None

    def test_a_privileged_container_is_listed_whatever_its_file_says(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner
    ) -> None:
        app = self.app(tmp_path, store, runner, [{"service": "web", "privileged": True}])

        warning = build_sandbox.sandbox_warning(app)

        assert warning is not None and "is privileged" in warning

    def test_a_docker_that_does_not_answer_is_said_and_the_file_is_still_judged(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner
    ) -> None:
        app = self.app(tmp_path, store, runner, [])
        runner.script(["docker", "ps"], stderr="Cannot connect to the Docker daemon", exit_code=1)
        (Path(app.app_path) / "docker-compose.yml").write_text(
            "services:\n  web:\n    image: nginx\n    devices: ['/dev/kvm']\n"
        )

        warning = build_sandbox.sandbox_warning(app)

        assert warning is not None
        assert "/dev/kvm" in warning
        assert "could not be read" in warning and "Cannot connect to the Docker daemon" in warning
