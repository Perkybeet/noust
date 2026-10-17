# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A typed Python client of the Noust API.

    from noust_sdk import Client

    client = Client("https://noust.example.com", token=os.environ["NOUST_TOKEN"])
    for app in client.applications.list_apps()["apps"]:
        print(app["domain"], app["status"])
    queued = client.applications.deploy_app("shop.example.com", body={})
    client.wait_for_job(queued)

The methods are generated from the API's own schema (``scripts/generate_sdk.py``), grouped as the
console groups them; the shapes of requests and answers are in ``noust_sdk._models``.
"""

from __future__ import annotations

from noust_sdk._operations import API_VERSION, OPERATIONS
from noust_sdk.client import Client, Request, Response
from noust_sdk.errors import (
    APIError,
    ConnectionFailed,
    ElevationRequired,
    JobFailed,
    NoustError,
    RateLimited,
)

__all__ = [
    "API_VERSION",
    "OPERATIONS",
    "APIError",
    "Client",
    "ConnectionFailed",
    "ElevationRequired",
    "JobFailed",
    "NoustError",
    "RateLimited",
    "Request",
    "Response",
]
