# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A central with a GitHub App, talking to a GitHub that behaves as GitHub documents.

``docs_github`` is the fixture: the fake of :mod:`tests.support.github_api_fake` served on loopback,
the process-wide GitHub client pointed at it, an App recorded in the store with its key and its
webhook secret, and ``openssl`` answering a fixed signature (the fake checks everything about the
App's token but the signature). A test module imports it (``# noqa: F401``) to use it; a test that
builds its own store (a central with fake nodes) uses :func:`github_world` on it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from noust.core.runner import FakeRunner, set_runner
from noust.core.secrets import SecretStore
from noust.core.store import GitHubAppRecord, GitHubInstallationRecord, NoustStore
from noust.integrations.github import app as github_app
from noust.integrations.github.client import GitHubClient, set_client
from tests.support.github_api_fake import FakeGitHubApi, serve

APP_ID = 4242
WEBHOOK_SECRET = "hook-secret"

#: What the faked ``openssl`` answers as the signature.
SIGNATURE_HEX = "0a0b0c"


@contextmanager
def github_world(store: NoustStore) -> Iterator[FakeGitHubApi]:
    """
    Give a store an App, and a GitHub for it to talk to.

    Args:
        store: The central's store.

    Yields:
        GitHub, with this server's App recorded and no account installed yet; a test installs it
        with ``fake.install(...)`` and records the installation with :func:`record_installation`.
    """
    fake = FakeGitHubApi(app_id=APP_ID)
    server, url = serve(fake)
    set_client(GitHubClient(url, timeout=5))
    runner = FakeRunner()
    runner.script(["openssl", "dgst"], stdout=f"SHA2-256(stdin)= {SIGNATURE_HEX}\n")
    set_runner(runner)
    github_app.forget_tokens()
    store.save_github_app(
        GitHubAppRecord(app_id=APP_ID, slug="noust-test", name="noust-test", owner="you")
    )
    secrets = SecretStore(root=store.db_path.parent / "secrets")
    secrets.write(github_app.PRIVATE_KEY_SECRET, "-----BEGIN RSA PRIVATE KEY-----\nfake\n")
    secrets.write(github_app.WEBHOOK_SECRET, WEBHOOK_SECRET)
    try:
        yield fake
    finally:
        github_app.forget_tokens()
        set_client(None)
        set_runner(None)
        server.shutdown()
        server.server_close()


@pytest.fixture
def docs_github(store: NoustStore) -> Iterator[FakeGitHubApi]:
    """
    Yields:
        GitHub, as :func:`github_world` gives it, on the test's store.
    """
    with github_world(store) as fake:
        yield fake


def record_installation(
    store: NoustStore, fake: FakeGitHubApi, installation_id: int, selection: str = "selected"
) -> None:
    """
    Record in the store an installation the fake has, as the console does after GitHub says so.

    Args:
        store: The central's store.
        fake: GitHub.
        installation_id: An installation of the fake.
        selection: ``all`` or ``selected``.
    """
    installation = fake.installations[installation_id]
    store.save_github_installation(
        GitHubInstallationRecord(
            installation_id=installation_id,
            account=installation.account,
            account_type="User",
            repository_selection=selection,
        )
    )
