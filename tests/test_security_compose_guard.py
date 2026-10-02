"""
Bypasses of the compose guard (privileged services, the Docker socket).

The guard refuses a new stack that asks for root on the host unless an
operator recorded an exception. Three ways around it existed: adopting any
directory (every adoption counted as "a stack that already runs"), updating
(every update was warned about, never refused, so a push adding
``privileged: true`` went through) and redeploying an existing application.
And adoption accepted any path, including ``/`` or another application's
directory, which an update then resets with git as root.
"""

# ruff: noqa: F811

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.exceptions import DeploymentError
from noust.core.runner import set_runner
from noust.core.store import App, NoustStore, get_store
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.managers.nginx_manager import NginxManager
from tests.test_compose_adopt import FIXTURES, RECREATES, Docker, web  # noqa: F401

DOMAIN = "proggest.es"

#: Proggest's backend already mounts the Docker socket (that finding is the
#: stack's own); what these tests add is a privileged backend.
PRIVILEGED = "    privileged: true\n"


@pytest.fixture
def store() -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    yield get_store()
    NoustStore.reset_instance()


def _checkout(path: Path, *, privileged: bool) -> Path:
    (path / ".git").mkdir(parents=True)
    text = (FIXTURES / "proggest.docker-compose.prod.yml").read_text()
    if privileged:
        text = text.replace(
            "    container_name: proggest-backend\n",
            "    container_name: proggest-backend\n" + PRIVILEGED,
        )
        assert PRIVILEGED in text
    (path / "docker-compose.prod.yml").write_text(text)
    return path


@pytest.fixture
def docker(tmp_path: Path) -> Iterator[Docker]:
    root = _checkout(tmp_path / "opt" / "proggest-src", privileged=True)
    fake = Docker(root / "docker-compose.prod.yml")
    set_runner(fake)
    yield fake
    set_runner(None)


def _plan(path: Path, **options: Any) -> Any:
    from noust.deployers.compose_adopt import plan_adoption

    return plan_adoption(DOMAIN, path, **options)


# -- Adoption -------------------------------------------------------------------


def test_adopting_a_privileged_stack_nothing_runs_from_is_refused(
    docker: Docker, store: NoustStore
) -> None:
    """Nothing proves the stack runs: it is a new stack, and the guard refuses it."""
    docker.containers = []
    with pytest.raises(DeploymentError, match="asks for root"):
        _plan(docker.compose_file.parent)


def test_adopting_a_privileged_stack_whose_up_would_recreate_it_is_refused(
    docker: Docker, store: NoustStore
) -> None:
    """Running, but not as the file says: what the file adds was never running."""
    docker.dry_run = RECREATES
    with pytest.raises(DeploymentError, match="asks for root"):
        _plan(docker.compose_file.parent, accept_recreate=True)


def test_adopting_a_privileged_stack_that_runs_as_its_file_says_is_warned(
    docker: Docker, store: NoustStore, web: NginxManager
) -> None:
    plan = _plan(docker.compose_file.parent, web=web)
    assert plan.project == "proggest"


@pytest.mark.parametrize("system", ["/", "/etc", "/usr", "/var", "/root", "/home"])
def test_a_system_directory_is_never_adopted(store: NoustStore, system: str) -> None:
    with pytest.raises(DeploymentError, match="system directory"):
        _plan(Path(system))


def test_the_apps_directory_and_what_is_in_it_are_never_adopted(
    tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    apps = tmp_path / "apps"
    monkeypatch.setattr(Config, "apps_directory", property(lambda self: apps))
    other = _checkout(apps / "shop-example-com", privileged=False)
    with pytest.raises(DeploymentError, match="apps directory"):
        _plan(apps)
    with pytest.raises(DeploymentError, match="apps directory"):
        _plan(other)


def test_another_applications_directory_is_never_adopted(tmp_path: Path, store: NoustStore) -> None:
    other = _checkout(tmp_path / "srv" / "shop", privileged=False)
    store.create_app(App(domain="shop.example.com", app_type="nodejs", app_path=str(other)))
    inside = other / "sub"
    inside.mkdir()
    for path in (other, inside, tmp_path / "srv"):
        with pytest.raises(DeploymentError, match=r"shop\.example\.com"):
            _plan(path)


# -- Update and redeploy ----------------------------------------------------------


def _deployer(app_path: Path, store: NoustStore) -> DockerComposeDeployer:
    compose = DockerComposeDeployer()
    compose.configure(DOMAIN, "src", app_path=app_path, compose_file="docker-compose.prod.yml")
    compose.store = store
    compose._is_new_deployment = False
    compose.previous_commit = "0" * 40
    compose._discover_compose_file()
    return compose


def test_an_update_that_makes_a_service_privileged_is_refused(
    tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compose file at the commit that served was not privileged: the push adds root."""
    root = _checkout(tmp_path / "stack", privileged=True)
    before = (FIXTURES / "proggest.docker-compose.prod.yml").read_text()
    compose = _deployer(root, store)
    monkeypatch.setattr(
        compose._source_manager().__class__, "file_at_commit", lambda *a, **k: before
    )
    with pytest.raises(DeploymentError, match="asks for root"):
        compose._check_update_privileges(compose._load_compose_document())


def test_an_update_keeps_what_the_serving_compose_file_already_had(
    tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _checkout(tmp_path / "stack", privileged=True)
    before = (root / "docker-compose.prod.yml").read_text()
    compose = _deployer(root, store)
    monkeypatch.setattr(
        compose._source_manager().__class__, "file_at_commit", lambda *a, **k: before
    )
    compose._check_update_privileges(compose._load_compose_document())


def test_an_update_with_nothing_to_compare_with_is_refused(
    tmp_path: Path, store: NoustStore
) -> None:
    """No commit that served: nothing proves the stack had the socket before."""
    root = _checkout(tmp_path / "stack", privileged=True)
    compose = _deployer(root, store)
    compose.previous_commit = None
    with pytest.raises(DeploymentError, match="asks for root"):
        compose._check_update_privileges(compose._load_compose_document())


def test_a_redeploy_that_adds_privileges_is_refused(tmp_path: Path, store: NoustStore) -> None:
    """The tree before the fetch is what served."""
    root = _checkout(tmp_path / "stack", privileged=False)
    compose = _deployer(root, store)
    compose._remember_serving_privileges()
    shutil.rmtree(root)
    _checkout(root, privileged=True)
    compose._discover_compose_file()
    with pytest.raises(DeploymentError, match="asks for root"):
        compose._parse_compose_services()


def test_file_at_commit_reads_the_blob_without_filters(tmp_path: Path) -> None:
    """cat-file blob: no textconv, no filter, nothing the tree's config could run."""
    from noust.core.runner import FakeRunner
    from noust.managers.source_manager import SourceManager

    (tmp_path / ".git").mkdir()
    runner = FakeRunner()
    runner.script(["git"], stdout="services: {}\n")
    text = SourceManager(runner=runner).file_at_commit(tmp_path, "a" * 40, "docker-compose.yml")
    assert text == "services: {}\n"
    assert runner.calls[-1][-3:] == ("cat-file", "blob", f"{'a' * 40}:docker-compose.yml")
