"""A server without httpx still starts its console; only the fleet says what is missing."""

from __future__ import annotations

import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import FleetUnavailableError
from noust.fleet.client import load_httpx
from noust.web.api.deps import install_error_handlers
from noust.web.api.router import node_proxy_router


@pytest.fixture
def without_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import httpx`` fail, and forget the proxy module so it imports again."""
    monkeypatch.setitem(sys.modules, "httpx", None)
    monkeypatch.delitem(sys.modules, "noust.web.api.node_proxy", raising=False)


@pytest.mark.usefixtures("without_httpx")
class TestWithoutHttpx:
    def test_the_fleet_names_the_package_that_is_missing(self):
        with pytest.raises(FleetUnavailableError) as caught:
            load_httpx()
        assert "httpx" in str(caught.value)
        assert "python3-httpx" in (caught.value.details or "")

    def test_a_call_to_a_node_answers_503_instead_of_the_api_failing_to_import(self):
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(node_proxy_router(), prefix="/api/nodes")

        response = TestClient(app).get("/api/nodes/web-2/api/apps")

        assert response.status_code == 503
        assert "httpx" in response.text


def test_with_httpx_the_real_proxy_is_mounted():
    router = node_proxy_router()
    assert any("{path:path}" in getattr(route, "path", "") for route in router.routes)
    assert load_httpx().__name__ == "httpx"
