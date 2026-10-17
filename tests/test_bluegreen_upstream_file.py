"""
The upstream file of an application that runs as blue/green instances (checklist H-, harness).

The file is the web server's, not the store's and not a bundle of Noust's state. A site renders
as a proxy to the upstream only while the file is there, so a server brought back from a bundle
rendered it as a proxy to the first instance's port while the second one served, and answered 502.
"""

from __future__ import annotations

import pytest

from noust.core.store import App
from noust.deployers.bluegreen import ensure_upstream_file


class Upstreams:
    """The few calls of the web server manager the function makes, over a dictionary."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {})
        self.writes: list[tuple[str, int]] = []

    def read_upstream(self, domain: str) -> str | None:
        return self.files.get(domain)

    def write_upstream(self, domain: str, port: int) -> str | None:
        self.writes.append((domain, port))
        previous = self.files.get(domain)
        self.files[domain] = f"server 127.0.0.1:{port};"
        return previous


def application(**fields: object) -> App:
    row: dict[str, object] = {
        "domain": "shop.example.com",
        "app_type": "nodejs",
        "port": 3000,
        "zero_downtime": True,
        "active_color": "green",
        "layout": "releases",
    }
    row.update(fields)
    return App(**row)  # type: ignore[arg-type]


def test_the_file_names_the_instance_that_serves_when_it_is_missing() -> None:
    web = Upstreams()

    written = ensure_upstream_file(application(), web=web)  # type: ignore[arg-type]

    assert written is True
    assert web.writes == [("shop.example.com", 3001)], "green is the first port plus one"


def test_the_first_instance_is_the_application_port() -> None:
    web = Upstreams()

    ensure_upstream_file(application(active_color="blue"), web=web)  # type: ignore[arg-type]

    assert web.writes == [("shop.example.com", 3000)]


def test_a_file_that_is_there_is_left_alone() -> None:
    web = Upstreams({"shop.example.com": "server 127.0.0.1:3000;"})

    written = ensure_upstream_file(application(), web=web)  # type: ignore[arg-type]

    assert written is False
    assert web.writes == []


@pytest.mark.parametrize(
    "fields",
    [
        {"zero_downtime": False},
        {"active_color": None},
        {"app_type": "docker-compose"},
        {"port": None},
    ],
    ids=["not in the mode", "no serving instance recorded", "a stack (its relay)", "no port"],
)
def test_nothing_is_written_for_an_application_that_does_not_run_as_instances(
    fields: dict[str, object],
) -> None:
    web = Upstreams()

    assert ensure_upstream_file(application(**fields), web=web) is False  # type: ignore[arg-type]
    assert web.writes == []
