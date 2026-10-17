# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The servers an application runs on, written for the load balancer in front of them (checklist F-18).

Noust routes no traffic between servers: the balancer is the operator's. What it gives them is the
list of servers to put behind it, as it knows them now, with the ones that do not serve marked so
that traffic is not sent there.
"""

from __future__ import annotations

import json

import pytest

from noust.core.exceptions import ValidationError
from noust.fleet.placement_upstreams import FORMATS, render_upstreams, upstream_servers

DOMAIN = "shop.example.com"


def status(*rows: dict) -> dict:
    return {
        "domain": DOMAIN,
        "members": [row["node"] for row in rows],
        "reference_commit": "abc1234",
        "in_sync": True,
        "servers": list(rows),
    }


def row(node: str, *, sync: str = "current", app: str = "running", commit: str | None = "abc1234"):
    return {
        "node": node,
        "reachable": sync != "unreachable",
        "present": sync not in ("missing", "unreachable"),
        "status": app if sync not in ("missing", "unreachable") else None,
        "commit": commit,
        "sync": sync,
        "message": None,
    }


ADDRESSES = {"web-1": "203.0.113.10", "web-2": "web2.example.com", "web-3": "2001:db8::3"}


def servers(*rows: dict) -> list[dict]:
    return upstream_servers(status(*rows), ADDRESSES, port=443)


def test_a_server_that_serves_is_in_rotation_and_one_that_does_not_is_marked_down() -> None:
    found = servers(
        row("web-1"),
        row("web-2", app="failed"),
        row("web-3", sync="missing"),
    )

    assert [(s["node"], s["serving"]) for s in found] == [
        ("web-1", True),
        ("web-2", False),
        ("web-3", False),
    ]
    assert found[0]["address"] == "203.0.113.10:443"
    assert found[2]["address"] == "[2001:db8::3]:443", "an IPv6 address is bracketed"


def test_a_server_on_another_commit_still_serves_and_is_said_to_differ() -> None:
    found = servers(row("web-1"), row("web-2", sync="differs", commit="def5678"))

    assert found[1]["serving"] is True
    assert found[1]["note"] == "on def5678, not abc1234"


def test_a_server_with_no_address_is_left_out_and_said() -> None:
    found = upstream_servers(
        status(row("web-1"), row("web-9")), {"web-1": "203.0.113.10"}, port=443
    )

    assert [s["node"] for s in found] == ["web-1", "web-9"]
    assert found[1]["address"] is None
    assert found[1]["serving"] is False
    assert "no address" in found[1]["note"]


def test_nginx_gets_an_upstream_block_and_how_to_use_it() -> None:
    text = render_upstreams(
        DOMAIN, servers(row("web-1"), row("web-2"), row("web-3", sync="unreachable")), "nginx"
    )

    assert "upstream shop_example_com {" in text
    assert "server 203.0.113.10:443;" in text
    assert "server web2.example.com:443;" in text
    assert "server [2001:db8::3]:443 down;" in text
    assert "proxy_pass https://shop_example_com;" in text
    assert "proxy_set_header Host shop.example.com;" in text
    assert "proxy_ssl_server_name on;" in text
    assert "proxy_ssl_name shop.example.com;" in text
    assert text.count("\n}\n") >= 1


def test_the_nginx_name_is_made_of_letters_digits_and_underscores() -> None:
    text = render_upstreams("my-shop.example.co.uk", servers(row("web-1")), "nginx")

    assert "upstream my_shop_example_co_uk {" in text


def test_a_plain_list_has_the_servers_that_serve_one_per_line() -> None:
    text = render_upstreams(DOMAIN, servers(row("web-1"), row("web-2", app="failed")), "list")

    assert text.strip().splitlines() == ["203.0.113.10:443"]


def test_json_is_every_server_with_what_the_central_knows() -> None:
    parsed = json.loads(
        render_upstreams(DOMAIN, servers(row("web-1"), row("web-3", sync="missing")), "json")
    )

    assert parsed["domain"] == DOMAIN
    assert [s["node"] for s in parsed["servers"]] == ["web-1", "web-3"]
    assert parsed["servers"][1]["serving"] is False


def test_an_unknown_format_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        render_upstreams(DOMAIN, servers(row("web-1")), "haproxy")

    assert "nginx" in caught.value.details


def test_the_formats_are_the_ones_the_documentation_names() -> None:
    assert FORMATS == ("nginx", "list", "json")


def test_nothing_serving_is_said_not_written_as_an_empty_block() -> None:
    text = render_upstreams(DOMAIN, servers(row("web-1", app="failed")), "nginx")

    assert "server 203.0.113.10:443 down;" in text
    assert "no server serves" in text.lower()
