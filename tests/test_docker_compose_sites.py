# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Docker Compose stack's site: the operator's is kept, aliases work, ``/`` is the front.

Spec 3.2 sections 1.5 to 1.7 and 2.7. Before 3.2 a stack replaced a site
the operator wrote on every deploy, deleted it to put TLS in and removed it
with the application; it refused aliases outright; it proxied ``/`` to
whatever service came first (the backend, in Proggest) and invented
``/<service>`` routes for the rest; and ``noust create --force`` emptied the
directory where its bind-mounted data lives.

Every test runs the real deployer, store, nginx manager (over a temporary
tree) and certificate manager; only processes are faked.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import docker_compose, domains
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.source_manager import SourceManager
from noust.managers.webserver import NGINX_BACKEND

DOMAIN = "proggest.example.com"
FIXTURES = Path(__file__).parent / "fixtures" / "compose"
OPERATOR_SITE = (
    "upstream nestjs_upstream { server 127.0.0.1:3000; }\n"
    "server {\n    server_name proggest.example.com;\n    access_log off;\n}\n"
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test's directory, where every module looks."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(docker_compose, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def web(tmp_path: Path, runner: FakeRunner, store: NoustStore) -> NginxManager:
    """An nginx manager writing into a temporary configuration tree."""
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


@pytest.fixture
def certs(tmp_path: Path, runner: FakeRunner, store: NoustStore) -> CertManager:
    """A certificate manager whose letsencrypt tree is temporary."""
    manager = CertManager()
    manager.LETSENCRYPT_DIR = tmp_path / "letsencrypt"
    manager.LIVE_DIR = manager.LETSENCRYPT_DIR / "live"
    manager.config = SimpleNamespace(ssl_email="")  # type: ignore[assignment]
    return manager


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Proggest's tree: its production compose file, as committed."""
    path = tmp_path / "apps" / "proggest"
    path.mkdir(parents=True)
    shutil.copy(FIXTURES / "proggest.docker-compose.prod.yml", path / "docker-compose.prod.yml")
    return path


@pytest.fixture
def stack(
    root: Path,
    runner: FakeRunner,
    web: NginxManager,
    certs: CertManager,
    store: NoustStore,
    monkeypatch: pytest.MonkeyPatch,
) -> DockerComposeDeployer:
    """The deployer over Proggest's tree, wired to this test's web server and certificates."""
    from noust.deployers.helpers.sandbox import set_compose_exception

    # Proggest's backend mounts the Docker socket, which a new stack needs
    # an operator's recorded reason for.
    set_compose_exception(
        DOMAIN, allowed=True, actor="test", reason="the backend reads its containers", store=store
    )
    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(DOMAIN, "https://example.com/proggest.git", app_path=root)
    wire(deployer, web, certs)
    # The tree is already in place, and nothing here is about systemd.
    monkeypatch.setattr(deployer, "_fetch_source", lambda: None)
    monkeypatch.setattr(deployer, "_create_systemd_service", lambda: None)
    monkeypatch.setattr(deployer, "_start_and_verify", lambda: None)
    deployer.deploy_target = SimpleNamespace(undo_fetch=lambda fs, log: None)  # type: ignore[assignment]
    return deployer


def wire(deployer: DockerComposeDeployer, web: NginxManager, certs: CertManager) -> None:
    """Point a compose deployer at the test's web server and certificates."""
    deployer.webserver_manager = lambda: web  # type: ignore[method-assign]
    deployer.cert_manager = certs


def put_operator_site(web: NginxManager) -> Path:
    """The site Proggest's operator wrote by hand, enabled."""
    path = web.config_path(DOMAIN)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OPERATOR_SITE)
    web.sites_enabled.mkdir(parents=True, exist_ok=True)
    (web.sites_enabled / path.name).symlink_to(path)
    return path


def put_certificate(certs: CertManager, domain: str = DOMAIN) -> None:
    """Create the files of a live lineage."""
    live = certs.LIVE_DIR / domain
    live.mkdir(parents=True, exist_ok=True)
    for name in ("fullchain.pem", "privkey.pem", "cert.pem", "chain.pem"):
        (live / name).write_text("-----BEGIN CERTIFICATE-----\n")


def deployed_row(store: NoustStore, root: Path, port: int | None = 3001) -> App:
    """The row of the stack as deployed."""
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type="docker-compose",
            app_path=str(root),
            port=port,
            webserver="nginx",
            status="running",
        )
    )


# -- Ports and the root route --------------------------------------------------


def test_proggest_proxies_root_to_its_front_and_says_what_is_not_routed(
    stack: DockerComposeDeployer, web: NginxManager, store: NoustStore
) -> None:
    stack.ssl = False
    logged: list[str] = []
    stack.logger.warning = logged.append  # type: ignore[method-assign]

    stack.deploy()

    site = web.get_site_config(DOMAIN) or ""
    assert "proxy_pass http://127.0.0.1:3001;" in site
    assert "/frontend" not in site and "/backend" not in site
    assert any("backend (3000)" in line and "noust.nginx.yaml" in line for line in logged)
    app = store.get_app(DOMAIN)
    assert app is not None and app.port == 3001


# -- The operator's site -------------------------------------------------------


def test_a_deploy_with_tls_keeps_the_operator_site(
    stack: DockerComposeDeployer,
    web: NginxManager,
    certs: CertManager,
    runner: FakeRunner,
) -> None:
    """The certificate step used to delete the site and write the template in its place."""
    put_operator_site(web)
    put_certificate(certs)

    stack.deploy()

    assert web.get_site_config(DOMAIN) == OPERATOR_SITE
    assert web.site_enabled(DOMAIN)


def test_a_failed_first_deploy_keeps_the_operator_site(
    stack: DockerComposeDeployer, web: NginxManager, runner: FakeRunner
) -> None:
    put_operator_site(web)
    stack.ssl = False
    runner.script(["docker", "compose"], stderr="failed to solve", exit_code=1)

    with pytest.raises(Exception, match="Failed to build"):
        stack.deploy()

    assert web.get_site_config(DOMAIN) == OPERATOR_SITE


def test_deleting_the_stack_keeps_the_operator_site(
    stack: DockerComposeDeployer, web: NginxManager, store: NoustStore, root: Path
) -> None:
    deployed_row(store, root)
    put_operator_site(web)

    stack.delete()

    assert web.get_site_config(DOMAIN) == OPERATOR_SITE


def test_deleting_the_stack_removes_the_site_noust_wrote(
    stack: DockerComposeDeployer, web: NginxManager, store: NoustStore
) -> None:
    stack.ssl = False
    stack.deploy()
    assert web.site_exists(DOMAIN)

    stack.delete()

    assert not web.site_exists(DOMAIN)


def test_an_update_never_touches_the_operator_site(
    stack: DockerComposeDeployer,
    web: NginxManager,
    store: NoustStore,
    root: Path,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployed_row(store, root)
    put_operator_site(web)
    monkeypatch.setattr(docker_compose, "wait_until_healthy", lambda url, **kw: True)
    monkeypatch.setattr(docker_compose.time, "sleep", lambda seconds: None)

    stack.update()

    assert web.get_site_config(DOMAIN) == OPERATOR_SITE


# -- Aliases, redirects and the certificate ------------------------------------


def test_a_stack_answers_on_an_alias_and_the_certificate_covers_it(
    stack: DockerComposeDeployer,
    web: NginxManager,
    certs: CertManager,
    store: NoustStore,
    root: Path,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """domains.py refused anything that was not a BaseDeployer."""
    stack.ssl = False
    stack.deploy()
    store.update_app_status(DOMAIN, "running")
    put_certificate(certs)
    site = store.get_site(DOMAIN)
    assert site is not None
    site.ssl_enabled = True
    store.update_site(site)

    def deployer_for(app_type: str, verbose: bool = False) -> Any:
        fresh = DockerComposeDeployer(runner=runner)
        wire(fresh, web, certs)
        return fresh

    monkeypatch.setattr(domains, "get_deployer", deployer_for)
    monkeypatch.setattr(
        docker_compose.ServiceManager, "get_service_config", lambda self, name: None
    )

    change = domains.add_domain(DOMAIN, "erp.example.com", "alias")
    domains.add_domain(DOMAIN, "www.proggest.example.com", "redirect")

    text = web.get_site_config(DOMAIN) or ""
    assert f"server_name {DOMAIN} erp.example.com;" in text
    assert "server_name www.proggest.example.com;" in text
    assert "return 301 https://proggest.example.com$request_uri;" in text
    assert change.tls is True
    certbot = [call for call in runner.calls if call[0] == "certbot"]
    assert certbot, "the certificate was not expanded"
    names = [certbot[-1][i + 1] for i, arg in enumerate(certbot[-1]) if arg == "-d"]
    assert names[:1] == [DOMAIN]
    assert {"erp.example.com", "www.proggest.example.com"} <= set(names)


def test_an_alias_on_the_operator_site_is_recorded_and_reported(
    stack: DockerComposeDeployer,
    web: NginxManager,
    certs: CertManager,
    store: NoustStore,
    root: Path,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployed_row(store, root)
    put_operator_site(web)
    logged: list[str] = []

    def deployer_for(app_type: str, verbose: bool = False) -> Any:
        fresh = DockerComposeDeployer(runner=runner)
        wire(fresh, web, certs)
        fresh.logger.warning = logged.append  # type: ignore[method-assign]
        return fresh

    monkeypatch.setattr(domains, "get_deployer", deployer_for)
    monkeypatch.setattr(
        docker_compose.ServiceManager, "get_service_config", lambda self, name: None
    )

    domains.add_domain(DOMAIN, "erp.example.com", "alias")

    assert web.get_site_config(DOMAIN) == OPERATOR_SITE
    assert any("erp.example.com" in line and "server_name" in line for line in logged)


def test_a_headless_stack_refuses_an_alias_saying_why(
    tmp_path: Path,
    web: NginxManager,
    certs: CertManager,
    store: NoustStore,
    runner: FakeRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.core.exceptions import ValidationError

    root = tmp_path / "apps" / "licitaciones"
    root.mkdir(parents=True)
    shutil.copy(FIXTURES / "licitaciones.docker-compose.yml", root / "docker-compose.yml")
    store.create_app(
        App(domain="licitaciones.example.com", app_type="docker-compose", app_path=str(root))
    )

    def deployer_for(app_type: str, verbose: bool = False) -> Any:
        fresh = DockerComposeDeployer(runner=runner)
        wire(fresh, web, certs)
        return fresh

    monkeypatch.setattr(domains, "get_deployer", deployer_for)
    monkeypatch.setattr(
        docker_compose.ServiceManager, "get_service_config", lambda self, name: None
    )

    with pytest.raises(ValidationError, match="publishes no port"):
        domains.add_domain("licitaciones.example.com", "avisos.example.com", "alias")

    assert [r.domain for r in store.list_domains("licitaciones.example.com")] == [
        "licitaciones.example.com"
    ]


# -- The Compose project the store pins -----------------------------------------


def test_the_project_the_store_records_is_passed_as_p(
    stack: DockerComposeDeployer, store: NoustStore, root: Path
) -> None:
    deployed_row(store, root)
    store.set_app_compose_project(DOMAIN, "proggest")
    stack._discover_compose_file()

    argv = stack._compose("ps")

    assert argv[:4] == ["docker", "compose", "-p", "proggest"]


def test_without_a_recorded_project_the_derived_one_is_kept(
    stack: DockerComposeDeployer, store: NoustStore, root: Path
) -> None:
    deployed_row(store, root)
    stack._discover_compose_file()

    assert stack._compose("ps")[:4] == ["docker", "compose", "-p", "proggest"]
    assert root.name == "proggest"


# -- noust create --force ---------------------------------------------------------


def test_force_over_a_checkout_updates_it_in_place_and_keeps_the_data(
    root: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean fetch removed the directory, the bind-mounted data in it included."""
    (root / ".git").mkdir()
    (root / "data").mkdir()
    (root / "data" / "licitaciones.db").write_text("rows")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(SourceManager, "fetch", lambda self, **kwargs: calls.append(kwargs) or True)
    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(DOMAIN, "https://example.com/p.git", app_path=root, replace_existing=True)

    deployer._fetch_source()

    assert calls and calls[0]["clean"] is False and calls[0]["force"] is True
    assert (root / "data" / "licitaciones.db").read_text() == "rows"


def test_force_over_files_that_are_not_a_checkout_is_refused(
    root: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.exceptions import DeploymentError

    (root / "data.db").write_text("rows")
    monkeypatch.setattr(
        SourceManager, "fetch", lambda self, **kwargs: pytest.fail("nothing may be fetched")
    )
    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(DOMAIN, "https://example.com/p.git", app_path=root, replace_existing=True)

    with pytest.raises(DeploymentError, match="not a git checkout"):
        deployer._fetch_source()

    assert (root / "data.db").read_text() == "rows"


def test_a_noust_nginx_yaml_routes_the_stack(
    stack: DockerComposeDeployer, web: NginxManager, root: Path
) -> None:
    (root / "noust.nginx.yaml").write_text(
        "max_body_size: 50m\n"
        "routes:\n"
        "  - path: /api/\n    port: 3000\n    name: proggest_backend\n    buffering: false\n"
        "  - path: /\n    port: 3001\n    name: proggest_frontend\n"
    )
    stack.ssl = False

    stack.deploy()

    site = web.get_site_config(DOMAIN) or ""
    assert "location /api/ {" in site and "proxy_pass http://proggest_backend;" in site
    assert "proxy_buffering off;" in site
    assert "client_max_body_size 50m;" in site


def test_a_noust_nginx_yaml_that_does_not_validate_is_not_half_applied(
    stack: DockerComposeDeployer, web: NginxManager, root: Path
) -> None:
    (root / "noust.nginx.yaml").write_text("routes:\n  - path: /\n    port: 3001\n    gzip: on\n")
    stack.ssl = False
    logged: list[str] = []
    stack.logger.warning = logged.append  # type: ignore[method-assign]

    stack.deploy()

    assert "proxy_pass http://127.0.0.1:3001;" in (web.get_site_config(DOMAIN) or "")
    assert any("gzip" in line and "was not used" in line for line in logged)
