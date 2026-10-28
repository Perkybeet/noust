# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_console_exposure.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from noust.core.exceptions import CertificateError

DOMAIN = "console.example.com"


class FakeCerts:
    """A certificate manager that issues on paper."""

    def __init__(self, *, already: bool = False, fail: bool = False) -> None:
        self.installed = True
        self.fail = fail
        self.obtained: list[str] = []
        self.existing = {DOMAIN} if already else set()
        self.deleted: list[str] = []

    def is_installed(self) -> bool:
        return self.installed

    def cert_exists(self, domain: str) -> bool:
        return domain in self.existing or domain in self.obtained

    def test_cert(self, domain: str) -> dict[str, Any]:
        return {"valid": True}

    def cert_covers_domains(self, domain: str, names: list[str]) -> bool:
        return True

    def obtain(self, domain: str, **kwargs: Any) -> bool:
        if self.fail:
            raise CertificateError("Certificate issuance failed", details="DNS does not point here")
        self.obtained.append(domain)
        return True

    def get_cert_path(self, domain: str) -> dict[str, Path]:
        live = Path("/etc/letsencrypt/live") / domain
        return {"fullchain": live / "fullchain.pem", "privkey": live / "privkey.pem"}

    def delete(self, domain: str) -> bool:
        self.deleted.append(domain)
        self.obtained = [d for d in self.obtained if d != domain]
        self.existing.discard(domain)
        return True
