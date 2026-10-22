# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How the certificate of a deployment ended, kept on the deployment.

A name made of the server's address (``shop.203-0-113-10.sslip.io``) is the first address of a
person with no domain, and a certificate authority limits certificates per registered domain:
sslip.io is everybody's, so an order for it can be refused. That must never fail the deployment:
the application serves over HTTP and says so, with what the authority said, word for word. What
is pinned here:

- a certificate that is refused leaves a deployment that succeeded, served over HTTP, with the
  state ``failed`` and the output of the order on its row;
- one that is issued says ``issued``; an application that asked for none says nothing;
- the state is validated where it is written, and what is kept is bounded;
- the API shows both, and a deployment from before the columns is simply without them.
"""

# The pipeline's fixtures are imported rather than replicated, so there stays one fake machine.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import CertificateError, ValidationError
from noust.core.store import DeploymentStatus, NoustStore
from noust.deployers.nodejs import NodeJSDeployer
from noust.web.api import deployments as deployments_api
from noust.web.api.auth import get_current_session
from tests.test_release_pipeline import (  # noqa: F401 - fixtures used by name
    DOMAIN,
    GIT_URL,
    PORT,
    RELEASES,
    git,
    machine,
    node_tree,
    root,
    store,
    wire,
)

REFUSED = (
    "An unexpected error occurred:\n"
    "Some challenges have failed.\n"
    "Detail: too many certificates (5) already issued for this exact set of identifiers"
)


class Certbot:
    """A certificate manager that orders by ``order`` and remembers whether it ever succeeded."""

    def __init__(self, order: Any) -> None:
        self.order = order
        self.issued = False

    def obtain(self, *args: Any, **kwargs: Any) -> bool:
        self.order(*args, **kwargs)
        self.issued = True
        return True

    def cert_exists(self, domain: str) -> bool:
        return self.issued


def deploy(
    tmp_path: Path,
    root: Path,
    machine: SimpleNamespace,
    *,
    ssl: bool,
    obtain: Any,
) -> Any:
    """Deploy a new application whose certificate manager answers with ``obtain``."""
    machine.git.publish(node_tree(tmp_path / "v1"))
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    deployer.cert_manager = Certbot(obtain)
    deployer.configure(DOMAIN, GIT_URL, port=PORT, ssl=ssl, app_path=root, layout=RELEASES)
    assert deployer.deploy() is True
    return deployer


def refused(*args: Any, **kwargs: Any) -> None:
    raise CertificateError("Certificate request failed", details=REFUSED)


def test_a_refused_certificate_leaves_a_deployment_that_succeeded(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    deployer = deploy(tmp_path, root, machine, ssl=True, obtain=refused)

    record = store.get_deployment(deployer.last_deployment_id)

    assert record is not None
    assert record.status == DeploymentStatus.SUCCESS.value
    assert record.certificate_state == "failed"
    # The authority's own words, not a paraphrase of them.
    assert "Some challenges have failed." in (record.certificate_output or "")
    assert "too many certificates" in (record.certificate_output or "")
    app = store.get_app(DOMAIN)
    assert app is not None and not app.ssl_enabled


def test_the_site_of_a_refused_certificate_is_written_without_tls(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    deploy(tmp_path, root, machine, ssl=True, obtain=refused)

    assert machine.web.sites[DOMAIN]["context"]["ssl"] is False


def test_an_issued_certificate_is_recorded_as_issued(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    deployer = deploy(tmp_path, root, machine, ssl=True, obtain=lambda *a, **k: True)

    record = store.get_deployment(deployer.last_deployment_id)

    assert record is not None
    assert (record.certificate_state, record.certificate_output) == ("issued", None)
    assert machine.web.sites[DOMAIN]["context"]["ssl"] is True


def test_an_application_that_asked_for_no_certificate_records_none(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> None:
    def never(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no certificate was asked for")

    deployer = deploy(tmp_path, root, machine, ssl=False, obtain=never)

    record = store.get_deployment(deployer.last_deployment_id)

    assert record is not None
    assert (record.certificate_state, record.certificate_output) == (None, None)


def test_the_state_is_validated_where_it_is_written(store: NoustStore) -> None:
    deployment_id = store.record_deployment_start(DOMAIN, "cli")

    with pytest.raises(ValidationError):
        store.record_deployment_certificate(deployment_id, "pending-ish", "x")


def test_what_is_kept_of_an_output_is_bounded(store: NoustStore) -> None:
    deployment_id = store.record_deployment_start(DOMAIN, "cli")

    store.record_deployment_certificate(deployment_id, "failed", "x" * 50_000)

    kept = store.get_deployment(deployment_id)
    assert kept is not None and kept.certificate_output is not None
    assert len(kept.certificate_output) <= 4_000


def test_an_issued_certificate_keeps_no_output(store: NoustStore) -> None:
    deployment_id = store.record_deployment_start(DOMAIN, "cli")

    store.record_deployment_certificate(deployment_id, "issued", "certbot chatter")

    kept = store.get_deployment(deployment_id)
    assert kept is not None
    assert (kept.certificate_state, kept.certificate_output) == ("issued", None)


def test_a_deployment_that_is_not_there_records_nothing(store: NoustStore) -> None:
    assert store.record_deployment_certificate(424242, "failed", "x") is False


def test_a_deployment_from_before_has_neither(store: NoustStore) -> None:
    deployment_id = store.record_deployment_start(DOMAIN, "cli")

    kept = store.get_deployment(deployment_id)

    assert kept is not None
    assert (kept.certificate_state, kept.certificate_output) == (None, None)


@pytest.fixture
def client(store: NoustStore) -> TestClient:
    app = FastAPI()
    app.include_router(deployments_api.router, prefix="/api/deployments")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test"}
    return TestClient(app, raise_server_exceptions=False)


def test_the_api_says_how_the_certificate_ended(store: NoustStore, client: TestClient) -> None:
    deployment_id = store.record_deployment_start(DOMAIN, "cli")
    store.record_deployment_certificate(deployment_id, "failed", REFUSED)

    body = client.get(f"/api/deployments/{deployment_id}").json()

    assert body["certificate_state"] == "failed"
    assert "Some challenges have failed." in body["certificate_output"]


def test_the_api_of_a_deployment_without_one_says_null(
    store: NoustStore, client: TestClient
) -> None:
    deployment_id = store.record_deployment_start(DOMAIN, "cli")

    body = client.get(f"/api/deployments/{deployment_id}").json()

    assert body["certificate_state"] is None
    assert body["certificate_output"] is None
