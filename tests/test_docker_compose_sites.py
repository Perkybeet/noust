# Copyright (c) 2024-2026 Yago López Prado
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
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import docker_compose, domains
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.source_manager import SourceManager
from tests.support.docker_compose_sites import (  # noqa: F401
    DOMAIN,
    FIXTURES,
    certs,
    deployed_row,
    root,
    stack,
    store,
    web,
    wire,
)

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811

OPERATOR_SITE = (
    "upstream nestjs_upstream { server 127.0.0.1:3000; }\n"
    "server {\n    server_name proggest.example.com;\n    access_log off;\n}\n"
)


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

    root = tmp_path / "apps" / "notices"
    root.mkdir(parents=True)
    shutil.copy(FIXTURES / "notices.docker-compose.yml", root / "docker-compose.yml")
    store.create_app(
        App(domain="notices.example.com", app_type="docker-compose", app_path=str(root))
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
        domains.add_domain("notices.example.com", "avisos.example.com", "alias")

    assert [r.domain for r in store.list_domains("notices.example.com")] == ["notices.example.com"]


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
    (root / "data" / "notices.db").write_text("rows")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(SourceManager, "fetch", lambda self, **kwargs: calls.append(kwargs) or True)
    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(DOMAIN, "https://example.com/p.git", app_path=root, replace_existing=True)

    deployer._fetch_source()

    assert calls and calls[0]["clean"] is False and calls[0]["force"] is True
    assert (root / "data" / "notices.db").read_text() == "rows"


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
