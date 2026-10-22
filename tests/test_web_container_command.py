# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The command and the entrypoint of a container application over the API.

The same two settings ``noust app container`` sets (``tests/test_container_command.py``): a list
of words each, an empty list clearing the image's own, ``clear`` giving it back.
"""

# The container API harness is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from noust.core.container_store import ContainerSettings, ContainerStore
from tests.test_web_app_container import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    app,
    client,
    deployed,
    queued,
    store,
)


def test_they_are_set_over_the_api_and_read_back(client, store):
    deployed(store)

    put = client.put(
        f"/api/apps/{DOMAIN}/container",
        json={"entrypoint": ["/app/run"], "command": ["serve", "--port", "8080"]},
    )
    got = client.get(f"/api/apps/{DOMAIN}/container")

    assert put.status_code == 200, put.text
    assert got.json()["entrypoint"] == ["/app/run"]
    assert got.json()["command"] == ["serve", "--port", "8080"]


def test_an_empty_entrypoint_clears_the_images_and_clear_gives_it_back(client, store):
    deployed(store, ContainerSettings(entrypoint=("/run",)))

    client.put(f"/api/apps/{DOMAIN}/container", json={"entrypoint": []})
    cleared = client.get(f"/api/apps/{DOMAIN}/container").json()["entrypoint"]
    client.put(f"/api/apps/{DOMAIN}/container", json={"clear": ["entrypoint"]})
    given_back = client.get(f"/api/apps/{DOMAIN}/container").json()["entrypoint"]

    assert cleared == [] and given_back is None


def test_an_empty_command_is_refused_because_docker_cannot_clear_a_cmd(client, store):
    deployed(store, ContainerSettings(command=("serve",)))

    response = client.put(f"/api/apps/{DOMAIN}/container", json={"command": []})
    kept = ContainerStore(store).get(DOMAIN)

    assert response.status_code == 400, response.text
    assert "cannot be cleared" in response.text
    assert kept is not None and kept.command == ("serve",)


def test_a_word_a_process_cannot_be_given_is_refused_and_nothing_changes(client, store):
    deployed(store, ContainerSettings(command=("serve",)))

    response = client.put(f"/api/apps/{DOMAIN}/container", json={"command": ["a\x00b"]})

    assert response.status_code == 400, response.text
    kept = ContainerStore(store).get(DOMAIN)
    assert kept is not None and kept.command == ("serve",)


def test_a_new_application_is_created_with_them(client, queued):
    response = client.post(
        "/api/apps",
        json={
            "domain": DOMAIN,
            "container": {
                "image": "ghcr.io/owner/app:1.4",
                "entrypoint": ["/app/run"],
                "command": ["serve"],
            },
        },
    )

    assert response.status_code == 202, response.text
    container = queued[0]["kwargs"]["container"]
    assert container["entrypoint"] == ("/app/run",) and container["command"] == ("serve",)
