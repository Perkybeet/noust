# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_webserver_managers.py`` that other tests build on."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import DomainRecord
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import APACHE_BACKEND, NGINX_BACKEND, WebServerManager
from tests.support.network_tables import NetworkTables


class FakeStore(NetworkTables):
    """A store that keeps site records in memory."""

    def __init__(self) -> None:
        self.sites: dict[str, Any] = {}
        self.apps: dict[str, Any] = {}
        self.domains: dict[str, list[DomainRecord]] = {}
        self.owners: dict[str, tuple[str, str]] = {}

    def domain_owner(self, domain: str) -> tuple[str, str] | None:
        """
        Find the application a name belongs to.

        Args:
            domain: The name.

        Returns:
            ``(application domain, kind)``, or None.
        """
        return self.owners.get(domain)

    def domain_certificate(self, domain: str) -> str | None:
        """
        Say how a name is covered.

        Args:
            domain: The name.

        Returns:
            ``shared`` or ``own`` for a name of a recorded application, else None.
        """
        for records in self.domains.values():
            for record in records:
                if record.domain == domain:
                    return record.certificate
        return None

    def get_served_certificate(self, domain: str) -> tuple[str, str] | None:
        """
        Say which certificate a site serves when it is not its own lineage.

        Args:
            domain: The application's domain.

        Returns:
            Always None: no test here assigns one.
        """
        return None

    def get_site_policy_document(self, domain: str) -> str | None:
        """
        Read the policy of a site.

        Args:
            domain: The application's domain.

        Returns:
            Always None: no test here gives a site a policy.
        """
        return None

    def list_domains(self, app_domain: str, *, include_disabled: bool = True) -> list[DomainRecord]:
        """
        List the domains of an application.

        Args:
            app_domain: The application's primary domain.

        Returns:
            Its records, empty for a domain that is not an application.
        """
        return list(self.domains.get(app_domain, []))

    def get_site(self, domain: str) -> Any:
        """
        Look up a site record.

        Args:
            domain: Domain name.

        Returns:
            The record, or None.
        """
        return self.sites.get(domain)

    def create_site(self, site: Any) -> Any:
        """
        Store a new site record.

        Args:
            site: The record to store.

        Returns:
            The stored record.
        """
        self.sites[site.domain] = site
        return site

    def update_site(self, site: Any) -> Any:
        """
        Replace a site record.

        Args:
            site: The record to store.

        Returns:
            The stored record.
        """
        self.sites[site.domain] = site
        return site

    def delete_site(self, domain: str) -> bool:
        """
        Drop a site record.

        Args:
            domain: Domain name.

        Returns:
            True when a record was removed.
        """
        return self.sites.pop(domain, None) is not None

    def update_site_ssl(self, domain: str, ssl: bool, **_kwargs: Any) -> None:
        """
        Record the TLS state of a site.

        Args:
            domain: Domain name.
            ssl: Whether the site serves TLS.
            _kwargs: Certificate paths, ignored here.
        """
        site = self.sites.get(domain)
        if site is not None:
            site.ssl_enabled = ssl

    def get_app(self, domain: str) -> Any:
        """
        Look up an application record.

        Args:
            domain: Domain name.

        Returns:
            The record, or None.
        """
        return self.apps.get(domain)

    def update_app(self, app: Any) -> Any:
        """
        Replace an application record.

        Args:
            app: The record to store.

        Returns:
            The stored record.
        """
        self.apps[app.domain] = app
        return app


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """
    Replace the SQLite store used by the managers with an in-memory one.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The fake store.
    """
    fake = FakeStore()
    monkeypatch.setattr("noust.managers.webserver.get_store", lambda: fake)
    monkeypatch.setattr("noust.managers.cert_manager.get_store", lambda: fake)
    return fake


@pytest.fixture
def nginx(tmp_path: Path, runner: FakeRunner, store: FakeStore) -> NginxManager:
    """
    An nginx manager pointed at a temporary configuration tree.

    Args:
        tmp_path: Per-test temporary directory.
        runner: The fake command runner.
        store: The fake store.

    Returns:
        The manager.
    """
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


@pytest.fixture
def apache(tmp_path: Path, runner: FakeRunner, store: FakeStore) -> ApacheManager:
    """
    An apache manager pointed at a temporary configuration tree.

    Args:
        tmp_path: Per-test temporary directory.
        runner: The fake command runner.
        store: The fake store.

    Returns:
        The manager.
    """
    return ApacheManager(
        backend=replace(
            APACHE_BACKEND,
            sites_available=tmp_path / "apache/sites-available",
            sites_enabled=tmp_path / "apache/sites-enabled",
        )
    )


@pytest.fixture
def managers(nginx: NginxManager, apache: ApacheManager) -> dict[str, WebServerManager]:
    """
    Both backends, keyed by name.

    Args:
        nginx: The nginx manager.
        apache: The apache manager.

    Returns:
        A mapping from backend name to manager.
    """
    return {"nginx": nginx, "apache": apache}


@pytest.fixture
def validation_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Point the validation staging area into the test's temporary directory.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The directory validation stages its files in.
    """
    staging_root = tmp_path / "validation-tmp"
    staging_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(staging_root))
    return staging_root
