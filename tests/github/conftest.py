# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fixtures of the GitHub App tests: a fake GitHub, a store, a faked openssl."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.github.fakes import (
    APP_ID,
    FAKE_SIGNATURE_HEX,
    INSTALLATION_ID,
    FakeGitHub,
    start_server,
)
from wasm.core.runner import FakeRunner, set_runner
from wasm.core.secrets import SecretStore
from wasm.core.store import GitHubAppRecord, GitHubInstallationRecord, WASMStore
from wasm.integrations.github import app as github_app
from wasm.integrations.github import manifest as github_manifest
from wasm.integrations.github.client import GitHubClient, set_client


@pytest.fixture
def fake_github() -> Iterator[FakeGitHub]:
    """
    Run a fake GitHub on loopback and point WASM's client at it.

    Yields:
        The fake.
    """
    fake = FakeGitHub()
    server = start_server(fake)
    set_client(GitHubClient(fake.base_url, timeout=5))
    try:
        yield fake
    finally:
        set_client(None)
        server.shutdown()
        server.server_close()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WASMStore]:
    """
    Give the test a store of its own; secrets live beside it.

    Yields:
        The store.
    """
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "wasm.db")
    github_app.forget_tokens()
    github_manifest.states.clear()
    try:
        yield instance
    finally:
        github_app.forget_tokens()
        instance.close()
        WASMStore.reset_instance()


@pytest.fixture
def openssl() -> Iterator[FakeRunner]:
    """
    Install a runner whose openssl answers a fixed signature.

    Yields:
        The runner, to assert on the calls.
    """
    fake = FakeRunner()
    fake.script(["openssl", "dgst"], stdout=f"SHA2-256(stdin)= {FAKE_SIGNATURE_HEX}\n")
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def github_configured(store: WASMStore) -> WASMStore:
    """
    Record an App with one installation on the ``you`` account.

    Args:
        store: The store fixture.

    Returns:
        The store.
    """
    store.save_github_app(
        GitHubAppRecord(app_id=APP_ID, slug="wasm-test", name="wasm-test", owner="you")
    )
    store.save_github_installation(
        GitHubInstallationRecord(
            installation_id=INSTALLATION_ID,
            account="you",
            account_type="User",
            repository_selection="all",
        )
    )
    secrets = SecretStore()
    secrets.write(github_app.PRIVATE_KEY_SECRET, "-----BEGIN RSA PRIVATE KEY-----\nfake\n")
    secrets.write(github_app.WEBHOOK_SECRET, "hook-secret")
    return store
