# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The gate to Docker (``noust.core.docker.gate``): what the console, the command line and the jobs ask.

Held here: every object comes back with its owner; a principal sees only what is its teams'; an action
goes through the one judgement and reaches Docker only with the id Docker itself listed (never the text
the caller typed, which is how an option, a path or another container's name would get into a
command); a volume is named before it is removed and says what it holds; and an image an application
keeps to go back to cannot be taken from it by a shortcut.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.docker.errors import (
    DockerNotFoundError,
    DockerOwnedError,
    DockerUnavailableError,
)
from noust.core.docker.gate import DockerGate
from noust.core.docker.models import OwnerKind
from noust.core.docker.owners import AppRecord, Registry
from noust.core.exceptions import ConfirmationRequired, DockerError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.teams import Scope

SHOP = "/var/www/apps/shop-example-com"
BLOG = "/var/www/apps/blog-example-com"
RELEASE = "20261112-101500-abc1234"


def row(name: str, **changes: Any) -> str:
    base: dict[str, Any] = {
        "id": (name.encode().hex() * 16)[:64],
        "name": name,
        "image": "nginx:1.27",
        "state": "running",
        "status": "Up 1 hour",
        "created": "2026-11-12 10:00:00 +0000 UTC",
        "ports": "",
        "mounts": "",
        "networks": "default",
        "project": "",
        "service": "",
        "working_dir": "",
        "config_files": "",
        "oneoff": "False",
        "instance": "",
        "helper": "",
    }
    base.update(changes)
    return json.dumps(base)


def full_id(name: str) -> str:
    return (name.encode().hex() * 16)[:64]


@pytest.fixture
def docker() -> Iterator[FakeRunner]:
    """A machine with two applications, an engine and two things nobody of Noust made."""
    runner = FakeRunner()
    containers = [
        row(
            "shop-web-1",
            project="shop-example-com",
            service="web",
            working_dir=SHOP,
            mounts="shop-example-com_data",
            image="noust-shop-example-com:" + RELEASE,
        ),
        row("blog-web-1", project="blog-example-com", working_dir=BLOG, image="nginx:1.27"),
        row(
            "noust-db-pg16",
            project="noust-db-pg16",
            image="postgres:16",
            mounts="noust-db-pg16-data",
            instance="pg16",
        ),
        row("portainer", image="portainer/portainer-ce", mounts="portainer_data"),
        row("redis-cache", image="redis:7", state="exited", status="Exited (0) 2 days ago"),
    ]
    runner.script(["docker", "ps"], stdout="\n".join(containers))
    images = [
        {
            "ID": "sha256:" + "1" * 64,
            "Repository": "noust-shop-example-com",
            "Tag": RELEASE,
            "Size": "300MB",
        },
        {"ID": "sha256:" + "2" * 64, "Repository": "nginx", "Tag": "1.27", "Size": "190MB"},
        {"ID": "sha256:" + "3" * 64, "Repository": "postgres", "Tag": "16", "Size": "430MB"},
        {
            "ID": "sha256:" + "4" * 64,
            "Repository": "portainer/portainer-ce",
            "Tag": "latest",
            "Size": "300MB",
        },
        {"ID": "sha256:" + "5" * 64, "Repository": "redis", "Tag": "7", "Size": "120MB"},
        {
            "ID": "sha256:" + "6" * 64,
            "Repository": "shop-example-com-web",
            "Tag": f"noust-{RELEASE}",
            "Size": "300MB",
        },
        {"ID": "sha256:" + "7" * 64, "Repository": "old", "Tag": "1", "Size": "10MB"},
    ]
    runner.script(["docker", "image", "ls"], stdout="\n".join(json.dumps(i) for i in images))
    volumes = [
        {"name": "shop-example-com_data", "project": "shop-example-com", "volume": "data"},
        {"name": "noust-db-pg16-data"},
        {"name": "portainer_data"},
        {"name": "orphan"},
    ]
    runner.script(["docker", "volume", "ls"], stdout="\n".join(json.dumps(v) for v in volumes))
    networks = [
        {
            "id": "n1",
            "name": "shop-example-com_default",
            "driver": "bridge",
            "project": "shop-example-com",
        },
        {"id": "n2", "name": "bridge", "driver": "bridge"},
        {"id": "n3", "name": "mine", "driver": "bridge"},
    ]
    runner.script(["docker", "network", "ls"], stdout="\n".join(json.dumps(n) for n in networks))
    runner.script(
        ["docker", "system", "df"],
        stdout=json.dumps(
            {
                "Volumes": [
                    {"Name": "orphan", "Size": "1.5GB"},
                    {"Name": "portainer_data", "Size": "40MB"},
                    {"Name": "shop-example-com_data", "Size": "2GB"},
                ]
            }
        ),
    )
    yield runner


REGISTRY = Registry(
    apps=(AppRecord("shop.example.com", SHOP), AppRecord("blog.example.com", BLOG)),
    engines=("pg16",),
)


def gate(
    runner: FakeRunner, *, scope: Scope | None = None, sees: set[str] | None = None
) -> DockerGate:
    return DockerGate(
        runner,
        registry=lambda: REGISTRY,
        scope=scope or Scope.everything(),
        sees_app=(lambda domain: domain in sees) if sees is not None else None,
    )


def actions(runner: FakeRunner) -> list[tuple[str, ...]]:
    """The calls that are not a look."""
    from noust.core.runner import is_read_only

    return [call for call in runner.calls if not is_read_only(call)]


class TestWhatIsThere:
    def test_every_container_comes_back_with_its_owner(self, docker: FakeRunner) -> None:
        page = gate(docker).containers()

        owners = {c.name: (c.owner.kind.value, c.owner.subject) for c in page.items}
        assert owners == {
            "blog-web-1": ("app", "blog.example.com"),
            "noust-db-pg16": ("database", "pg16"),
            "portainer": ("nobody", ""),
            "redis-cache": ("nobody", ""),
            "shop-web-1": ("app", "shop.example.com"),
        }
        assert page.total == 5
        assert [c.name for c in page.items] == sorted(owners)

    def test_a_listing_is_one_process_however_many_objects_there_are(
        self, docker: FakeRunner
    ) -> None:
        gate(docker).containers()

        assert [call[:2] for call in docker.calls] == [("docker", "ps")]

    def test_a_page_is_a_window_and_says_how_many_there_are_in_all(
        self, docker: FakeRunner
    ) -> None:
        page = gate(docker).containers(offset=1, limit=2)

        assert [c.name for c in page.items] == ["noust-db-pg16", "portainer"]
        assert (page.total, page.offset, page.limit) == (5, 1, 2)

    @pytest.mark.parametrize(("offset", "limit"), [(-1, None), (0, 0), (0, 5000)])
    def test_a_page_that_makes_no_sense_is_refused(
        self, docker: FakeRunner, offset: int, limit: int
    ) -> None:
        with pytest.raises(ValidationError):
            gate(docker).containers(offset=offset, limit=limit)

    def test_the_listing_can_be_cut_by_owner_state_project_and_words(
        self, docker: FakeRunner
    ) -> None:
        g = gate(docker)

        assert [c.name for c in g.containers(owner=OwnerKind.NOBODY).items] == [
            "portainer",
            "redis-cache",
        ]
        assert [c.name for c in g.containers(state="exited").items] == ["redis-cache"]
        assert [c.name for c in g.containers(project="shop-example-com").items] == ["shop-web-1"]
        assert [c.name for c in g.containers(search="PORT").items] == ["portainer"]
        assert [c.name for c in g.containers(app="blog.example.com").items] == ["blog-web-1"]

    def test_a_team_sees_only_the_containers_of_its_applications(self, docker: FakeRunner) -> None:
        mine = gate(docker, scope=Scope.nothing(), sees={"shop.example.com"})

        assert [c.name for c in mine.containers().items] == ["shop-web-1"]

    def test_the_instance_sees_what_nobody_made_and_a_team_never_does(
        self, docker: FakeRunner
    ) -> None:
        assert gate(docker, scope=Scope.nothing(), sees=set()).containers().items == ()
        assert gate(docker, scope=Scope.nothing(), sees=set()).volumes().total == 0

    def test_an_image_says_who_runs_it_and_whether_it_is_a_way_back(
        self, docker: FakeRunner
    ) -> None:
        images = {i.tags[0] if i.tags else i.id: i for i in gate(docker).images().items}

        release = images[f"noust-shop-example-com:{RELEASE}"]
        assert release.owner.subject == "shop.example.com"
        assert release.way_back is True and release.used_by == ("shop-web-1",)
        kept = images[f"shop-example-com-web:noust-{RELEASE}"]
        assert kept.way_back is True and kept.owner.subject == "shop.example.com"
        assert images["nginx:1.27"].owner.subject == "blog.example.com"
        assert images["postgres:16"].owner.kind is OwnerKind.DATABASE
        assert images["old:1"].owner.unowned and images["old:1"].used_by == ()
        assert images["old:1"].size_bytes == 10_000_000

    def test_a_volume_says_who_mounts_it_and_is_not_measured_by_a_listing(
        self, docker: FakeRunner
    ) -> None:
        volumes = {v.name: v for v in gate(docker).volumes().items}

        assert volumes["shop-example-com_data"].owner.subject == "shop.example.com"
        assert volumes["shop-example-com_data"].used_by == ("shop-web-1",)
        assert volumes["noust-db-pg16-data"].owner.kind is OwnerKind.DATABASE
        assert volumes["portainer_data"].owner.unowned and volumes["portainer_data"].used_by == (
            "portainer",
        )
        assert volumes["orphan"].used_by == () and volumes["orphan"].size_bytes is None
        assert not any(call[:3] == ("docker", "system", "df") for call in docker.calls)

    def test_the_sizes_are_a_call_of_their_own_and_only_of_what_is_seen(
        self, docker: FakeRunner
    ) -> None:
        mine = gate(docker, scope=Scope.nothing(), sees={"shop.example.com"})

        assert mine.volume_sizes() == {"shop-example-com_data": 2_000_000_000}
        assert gate(docker).volume_sizes()["orphan"] == 1_500_000_000

    def test_a_network_says_who_is_on_it(self, docker: FakeRunner) -> None:
        networks = {n.name: n for n in gate(docker).networks().items}

        assert networks["shop-example-com_default"].owner.subject == "shop.example.com"
        assert networks["bridge"].builtin and networks["bridge"].owner.unowned
        assert networks["mine"].owner.unowned

    def test_a_compose_project_is_its_containers_together(self, docker: FakeRunner) -> None:
        projects = {p.name: p for p in gate(docker).projects().items}

        shop = projects["shop-example-com"]
        assert (shop.owner.subject, shop.containers, shop.running) == (
            "shop.example.com",
            ("shop-web-1",),
            1,
        )
        assert shop.working_dir == SHOP

    def test_what_is_listed_can_be_turned_into_json(self, docker: FakeRunner) -> None:
        g = gate(docker)

        for page in (g.containers(), g.images(), g.volumes(), g.networks(), g.projects()):
            json.dumps([item.to_dict() for item in page.items])

    def test_without_docker_the_gate_says_so(self) -> None:
        runner = FakeRunner().only_knows("systemctl")

        with pytest.raises(DockerUnavailableError):
            gate(runner).containers()

    def test_the_engines_doctor_is_asked_through_the_same_runner(self, docker: FakeRunner) -> None:
        docker.script(["docker", "version"], stdout="{}")

        report = gate(docker).doctor()

        assert report.info.installed is True


class TestFindingOne:
    def test_a_container_is_found_by_name_or_by_its_id(self, docker: FakeRunner) -> None:
        g = gate(docker)

        assert g.container("shop-web-1").name == "shop-web-1"
        assert g.container(full_id("shop-web-1")).name == "shop-web-1"
        assert g.container(full_id("shop-web-1")[:12]).name == "shop-web-1"

    @pytest.mark.parametrize(
        "ref", ["", "nope", "--all", "-f", "shop-web", "shop-web-1; id", "../x", "a" * 400, "shop*"]
    )
    def test_anything_that_is_not_the_name_or_the_id_of_one_is_not_found(
        self, docker: FakeRunner, ref: str
    ) -> None:
        with pytest.raises(DockerNotFoundError):
            gate(docker).container(ref)

    def test_a_prefix_of_an_id_is_not_enough_to_be_one(self, docker: FakeRunner) -> None:
        """Six digits could be many containers: an id is the twelve Docker shows."""
        with pytest.raises(DockerNotFoundError):
            gate(docker).container(full_id("shop-web-1")[:6])

    def test_a_container_of_another_team_is_not_found_in_the_same_words_as_a_missing_one(
        self, docker: FakeRunner
    ) -> None:
        mine = gate(docker, scope=Scope.nothing(), sees={"shop.example.com"})

        with pytest.raises(DockerNotFoundError) as hidden:
            mine.container("blog-web-1")
        with pytest.raises(DockerNotFoundError) as missing:
            mine.container("nothing-called-this")

        assert hidden.value.message.replace("blog-web-1", "X") == missing.value.message.replace(
            "nothing-called-this", "X"
        )
        assert hidden.value.details == missing.value.details

    def test_an_image_is_found_by_its_name_or_its_id_with_or_without_the_algorithm(
        self, docker: FakeRunner
    ) -> None:
        g = gate(docker)

        assert g.image("nginx:1.27").id == "sha256:" + "2" * 64
        assert g.image("sha256:" + "2" * 64).tags == ("nginx:1.27",)
        assert g.image("2" * 12).tags == ("nginx:1.27",)

    def test_a_volume_and_a_network_are_found_by_name(self, docker: FakeRunner) -> None:
        g = gate(docker)

        assert g.volume("orphan").name == "orphan"
        assert g.network("mine").name == "mine"
        with pytest.raises(DockerNotFoundError):
            g.volume("--force")


class TestActingOnContainers:
    def test_what_nobody_made_is_stopped_started_restarted_and_removed_by_the_instance(
        self, docker: FakeRunner
    ) -> None:
        g = gate(docker)
        target = full_id("portainer")

        g.stop_container("portainer")
        g.start_container("portainer")
        g.restart_container("portainer")
        g.remove_container("portainer")
        g.remove_container(full_id("portainer")[:12], force=True)

        assert actions(docker) == [
            ("docker", "stop", "--time", "30", target),
            ("docker", "start", target),
            ("docker", "restart", "--time", "30", target),
            ("docker", "rm", target),
            ("docker", "rm", "--force", target),
        ]

    def test_docker_is_given_the_id_it_listed_and_never_the_text_that_was_typed(
        self, docker: FakeRunner
    ) -> None:
        g = gate(docker)
        by_prefix = full_id("portainer")[:12]

        g.stop_container(by_prefix)

        (call,) = actions(docker)
        assert call[-1] == full_id("portainer") != by_prefix

    @pytest.mark.parametrize(
        "ref", ["--privileged", "-f", "x y", "portainer;id", "$(id)", "portainer\n"]
    )
    def test_what_is_not_the_name_or_id_of_a_container_reaches_nothing(
        self, docker: FakeRunner, ref: str
    ) -> None:
        with pytest.raises(DockerNotFoundError):
            gate(docker).stop_container(ref)

        assert actions(docker) == []

    @pytest.mark.parametrize("name", ["shop-web-1", "noust-db-pg16"])
    def test_what_belongs_to_an_application_or_an_engine_is_refused_and_reaches_nothing(
        self, docker: FakeRunner, name: str
    ) -> None:
        for act in ("stop_container", "start_container", "restart_container", "remove_container"):
            with pytest.raises(DockerOwnedError):
                getattr(gate(docker), act)(name)

        assert actions(docker) == []

    def test_the_id_of_an_applications_container_is_the_same_container_as_its_name(
        self, docker: FakeRunner
    ) -> None:
        """The hole of 2026: ask by id what you could not ask by name. There is no such hole."""
        with pytest.raises(DockerOwnedError) as by_id:
            gate(docker).stop_container(full_id("shop-web-1"))
        with pytest.raises(DockerOwnedError) as by_name:
            gate(docker).stop_container("shop-web-1")

        assert by_id.value.subject == by_name.value.subject == "shop.example.com"
        assert actions(docker) == []

    def test_a_team_member_who_may_not_see_it_gets_nothing_to_hold_on_to(
        self, docker: FakeRunner
    ) -> None:
        mine = gate(docker, scope=Scope.nothing(), sees={"shop.example.com"})

        with pytest.raises(DockerNotFoundError):
            mine.stop_container("portainer")
        with pytest.raises(DockerNotFoundError):
            mine.stop_container("blog-web-1")
        with pytest.raises(DockerOwnedError):
            mine.stop_container("shop-web-1")
        assert actions(docker) == []

    def test_a_container_that_does_not_stop_says_dockers_own_words(
        self, docker: FakeRunner
    ) -> None:
        docker.script(["docker", "stop"], stderr="Error response from daemon: boom", exit_code=1)

        with pytest.raises(DockerError) as failed:
            gate(docker).stop_container("portainer")

        assert "boom" in (failed.value.output or "")
        assert "portainer" in failed.value.message

    def test_the_container_noust_runs_in_is_never_stopped(self, docker: FakeRunner) -> None:
        own = DockerGate(
            docker,
            registry=lambda: Registry(own_ids=frozenset({full_id("portainer")[:12]})),
            scope=Scope.everything(),
        )

        with pytest.raises(DockerOwnedError) as refused:
            own.stop_container("portainer")

        assert refused.value.owner_kind == "noust"
        assert actions(docker) == []


class TestRemovingWhatIsKept:
    def test_a_volume_is_removed_by_naming_it_and_says_what_it_held(
        self, docker: FakeRunner
    ) -> None:
        g = gate(docker)

        with pytest.raises(ConfirmationRequired) as asked:
            g.remove_volume("orphan")

        assert "orphan" in asked.value.warnings[0]
        assert "1.5 GB" in asked.value.warnings[0] or "1500000000" in asked.value.warnings[0]
        assert actions(docker) == []

    def test_naming_it_wrongly_removes_nothing(self, docker: FakeRunner) -> None:
        with pytest.raises(ConfirmationRequired):
            gate(docker).remove_volume("orphan", confirm_name="orphans")

        assert actions(docker) == []

    def test_naming_it_removes_it_and_reports_the_size(self, docker: FakeRunner) -> None:
        removed = gate(docker).remove_volume("orphan", confirm_name="orphan")

        assert actions(docker) == [("docker", "volume", "rm", "orphan")]
        assert (removed.kind.value, removed.name, removed.size_bytes) == (
            "volume",
            "orphan",
            1_500_000_000,
        )

    def test_a_volume_a_container_mounts_is_not_removed_and_says_whose(
        self, docker: FakeRunner
    ) -> None:
        with pytest.raises(ValidationError) as busy:
            gate(docker).remove_volume("portainer_data", confirm_name="portainer_data")

        assert "portainer" in busy.value.message + (busy.value.details or "")
        assert actions(docker) == []

    def test_the_volume_of_an_application_is_removed_with_the_application(
        self, docker: FakeRunner
    ) -> None:
        with pytest.raises(DockerOwnedError) as refused:
            gate(docker).remove_volume(
                "shop-example-com_data", confirm_name="shop-example-com_data"
            )

        assert "noust delete shop.example.com --remove-volumes" in refused.value.details
        assert actions(docker) == []

    def test_the_volume_of_a_database_instance_is_never_a_shortcut(
        self, docker: FakeRunner
    ) -> None:
        with pytest.raises(DockerOwnedError):
            gate(docker).remove_volume("noust-db-pg16-data", confirm_name="noust-db-pg16-data")

        assert actions(docker) == []

    def test_an_image_nothing_runs_and_nobody_keeps_is_removed_by_its_id(
        self, docker: FakeRunner
    ) -> None:
        gate(docker).remove_image("old:1")

        assert actions(docker) == [("docker", "image", "rm", "sha256:" + "7" * 64)]

    def test_an_image_a_container_uses_is_not_removed_and_says_which(
        self, docker: FakeRunner
    ) -> None:
        with pytest.raises(ValidationError) as busy:
            gate(docker).remove_image("redis:7")

        assert "redis-cache" in busy.value.message + (busy.value.details or "")
        assert actions(docker) == []

    def test_the_image_an_application_keeps_to_go_back_to_is_not_taken_by_a_shortcut(
        self, docker: FakeRunner
    ) -> None:
        for ref in (f"shop-example-com-web:noust-{RELEASE}", f"noust-shop-example-com:{RELEASE}"):
            with pytest.raises(DockerOwnedError) as refused:
                gate(docker).remove_image(ref)
            assert "noust releases list shop.example.com" in refused.value.details

        assert actions(docker) == []

    def test_a_network_is_removed_by_its_id_unless_it_is_dockers_or_an_applications(
        self, docker: FakeRunner
    ) -> None:
        g = gate(docker)

        g.remove_network("mine")
        with pytest.raises(DockerOwnedError):
            g.remove_network("bridge")
        with pytest.raises(DockerOwnedError):
            g.remove_network("shop-example-com_default")

        assert actions(docker) == [("docker", "network", "rm", "n3")]


def test_every_action_has_a_deadline(docker: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    original = docker.run

    def run(argv: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("timeout", 0))
        return original(argv, **kwargs)

    monkeypatch.setattr(docker, "run", run)

    g = gate(docker)
    g.stop_container("portainer")
    g.remove_network("mine")

    assert seen and all(isinstance(t, int) and t > 0 for t in seen)


def test_the_gate_reads_what_noust_registered_from_the_store_by_default(tmp_path: Path) -> None:
    from noust.core.store import App, NoustStore

    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    try:
        store.create_app(App(domain="shop.example.com", app_type="docker-compose", app_path=SHOP))
        runner = FakeRunner()
        runner.script(
            ["docker", "ps"],
            stdout=row("shop-web-1", project="shop-example-com", working_dir=SHOP),
        )

        page = DockerGate(runner, store=store, scope=Scope.everything()).containers()

        assert page.items[0].owner.subject == "shop.example.com"
    finally:
        NoustStore.reset_instance()
