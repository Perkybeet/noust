# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/docker`` (:mod:`noust.web.api.docker`).

Looking at Docker is what looking at the server is (``docker.read``, which every role holds), and
changing it is what changing the server is (``docker.manage``, the administrator's): start, stop,
restart and remove a container, remove an image, a network or a volume, open a console in a container
and apply a cleanup. Every route that changes something also asks for sudo mode (the routes declare
it), and the owner of what is changed is judged by the gate, not here: what an application or a
database engine owns is refused whoever asks.

The zone is the machine's: ``scopes.py`` holds all of it to the instance, so a person of a team is
refused every route whatever role they hold.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/docker/summary"): Permission.DOCKER_READ,
    ("GET", "/api/docker/engine"): Permission.DOCKER_READ,
    ("GET", "/api/docker/restart-loss"): Permission.DOCKER_READ,
    ("GET", "/api/docker/stacks"): Permission.DOCKER_READ,
    ("GET", "/api/docker/stacks/{name}"): Permission.DOCKER_READ,
    ("GET", "/api/docker/containers"): Permission.DOCKER_READ,
    ("GET", "/api/docker/containers/{ref}"): Permission.DOCKER_READ,
    ("GET", "/api/docker/containers/{ref}/logs"): Permission.DOCKER_READ,
    ("GET", "/api/docker/images"): Permission.DOCKER_READ,
    ("GET", "/api/docker/volumes"): Permission.DOCKER_READ,
    ("GET", "/api/docker/volumes/sizes"): Permission.DOCKER_READ,
    ("GET", "/api/docker/networks"): Permission.DOCKER_READ,
    ("GET", "/api/docker/cleanup/plan"): Permission.DOCKER_READ,
    ("POST", "/api/docker/containers/{ref}/start"): Permission.DOCKER_MANAGE,
    ("POST", "/api/docker/containers/{ref}/stop"): Permission.DOCKER_MANAGE,
    ("POST", "/api/docker/containers/{ref}/restart"): Permission.DOCKER_MANAGE,
    ("POST", "/api/docker/containers/{ref}/terminal"): Permission.DOCKER_MANAGE,
    ("DELETE", "/api/docker/containers/{ref}"): Permission.DOCKER_MANAGE,
    ("DELETE", "/api/docker/images/{image_id}"): Permission.DOCKER_MANAGE,
    ("DELETE", "/api/docker/volumes/{name}"): Permission.DOCKER_MANAGE,
    ("DELETE", "/api/docker/networks/{ref}"): Permission.DOCKER_MANAGE,
    ("POST", "/api/docker/cleanup"): Permission.DOCKER_MANAGE,
    # The zone of every server of the fleet at once: a view asks each server what its own page asks.
    ("GET", "/api/views/docker/summary"): Permission.DOCKER_READ,
    ("GET", "/api/views/docker/attention"): Permission.DOCKER_READ,
    ("GET", "/api/views/docker/engine"): Permission.DOCKER_READ,
    ("GET", "/api/views/docker/stacks"): Permission.DOCKER_READ,
    ("GET", "/api/views/docker/containers"): Permission.DOCKER_READ,
    ("GET", "/api/views/docker/images"): Permission.DOCKER_READ,
    ("GET", "/api/views/docker/volumes"): Permission.DOCKER_READ,
    ("GET", "/api/views/docker/networks"): Permission.DOCKER_READ,
}
