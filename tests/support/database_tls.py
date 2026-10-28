# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_tls.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noust.core.fs import RealFileSystem
from noust.core.runner import CommandResult
from tests.support.database_instance_support import DockerHost


class UnprivilegedFs(RealFileSystem):
    """The real filesystem without the chown a test is not root to make."""

    def write_text(self, path, content, *, mode=0o644, owner=None):  # type: ignore[no-untyped-def]
        super().write_text(path, content, mode=mode)


class TlsHost(DockerHost):
    """A Docker host that also answers openssl, with certificates that say what they are."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.issued = 0
        self.not_after = datetime.now(timezone.utc) + timedelta(days=825)

    def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:  # type: ignore[override]
        command = tuple(argv)
        if command[:1] == ("openssl",):
            super().run(argv, **kwargs)  # recorded like every other call
            return self._openssl(command, str(kwargs.get("input") or ""))
        return super().run(argv, **kwargs)

    def _openssl(self, command: tuple[str, ...], stdin: str) -> CommandResult:
        def ok(stdout: str) -> CommandResult:
            return CommandResult(argv=command, exit_code=0, stdout=stdout)

        self.issued += 1
        key = f"-----BEGIN PRIVATE KEY-----\nKEY{self.issued}\n-----END PRIVATE KEY-----\n"
        if command[1:3] == ("req", "-x509"):
            cert = f"-----BEGIN CERTIFICATE-----\nCA{self.issued}\n-----END CERTIFICATE-----\n"
            return ok(key + cert)
        if command[1:3] == ("req", "-new"):
            csr = (
                "-----BEGIN CERTIFICATE REQUEST-----\n"
                f"CSR{self.issued}\n-----END CERTIFICATE REQUEST-----\n"
            )
            return ok(key + csr)
        if command[1:3] == ("x509", "-req"):
            assert "CERTIFICATE REQUEST" in stdin, "the request goes on stdin"
            cert = f"-----BEGIN CERTIFICATE-----\nCERT{self.issued}\n-----END CERTIFICATE-----\n"
            return ok(cert)
        if command[1:3] == ("x509", "-in"):
            return ok(
                "subject=CN = pg16\n"
                "issuer=CN = Noust database CA on testhost\n"
                "notBefore=Oct 13 10:00:00 2026 GMT\n"
                f"notAfter={self.not_after.strftime('%b %d %H:%M:%S %Y')} GMT\n"
                "sha256 Fingerprint=AB:CD:EF\n"
                "X509v3 Subject Alternative Name: \n"
                "    DNS:localhost, IP Address:127.0.0.1, DNS:pg16\n"
            )
        return ok("")
