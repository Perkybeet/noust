# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The notifications the network and monitoring lane added, for the shared gallery.

Kept in a file of their own and appended after every other event of
``tests/notifications_support.py``, so the fixed ids of the earlier events do not move.
"""

from __future__ import annotations

from noust.core.notifications.composers_netobs import (
    compose_container_loop,
    compose_recipes_outdated,
    compose_security_release,
    compose_webserver_down,
)
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification

WORKER_LOG = (
    "Error: connect ECONNREFUSED 10.0.0.4:5432\n"
    "    at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1555:16)\n"
    "worker exiting with status 1"
)

NGINX_TEST = (
    'nginx: [emerg] unknown directive "servre_name" in /etc/nginx/sites-enabled/shop.example.com:4\n'
    "nginx: configuration file /etc/nginx/nginx.conf test failed"
)


def netobs_events(ctx: NotificationContext) -> dict[str, Notification]:
    """
    Build one notification per code this lane added.

    Args:
        ctx: The context.

    Returns:
        Code to notification.
    """
    out: dict[str, Notification] = {}
    kwargs = {"unit": "nginx", "test": "nginx -t"}
    out["webserver.restarted"] = compose_webserver_down("restarted", "nginx", ctx, **kwargs)
    out["webserver.down"] = compose_webserver_down("down", "nginx", ctx, **kwargs)
    out["webserver.config_invalid"] = compose_webserver_down(
        "config_invalid", "nginx", ctx, output=NGINX_TEST, **kwargs
    )
    out["webserver.restart_failed"] = compose_webserver_down(
        "restart_failed",
        "nginx",
        ctx,
        output="Job for nginx.service failed because the control process exited with error code.",
        **kwargs,
    )
    out["webserver.limit"] = compose_webserver_down("limit", "nginx", ctx, restarts=3, **kwargs)
    out["webserver.stopped"] = compose_webserver_down("stopped", "nginx", ctx, **kwargs)
    out["security.release"] = compose_security_release(
        "3.3.2",
        "3.3.1",
        ctx,
        advisory="https://github.com/Perkybeet/noust/security/advisories/GHSA-aaaa-bbbb-cccc",
        update="pipx upgrade noust",
    )
    out["container.restart_loop"] = compose_container_loop(
        "restart_loop",
        "shop-worker-1",
        ctx,
        domain="shop.example.com",
        restarts=6,
        limit=5,
        exit_code=1,
        output=WORKER_LOG,
    )
    out["container.recovered"] = compose_container_loop(
        "recovered", "shop-worker-1", ctx, domain="shop.example.com", stable_minutes=7
    )
    out["recipe.outdated"] = compose_recipes_outdated(
        [
            ("vault.example.com", "vaultwarden", "1.37.4", "1.38.0"),
            ("blog.example.com", "ghost", "5.0.1", "5.1.0"),
        ],
        ctx,
    )
    return out
