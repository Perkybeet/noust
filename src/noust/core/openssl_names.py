# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How ``openssl`` prints a certificate's names, fixed so Noust reads the same text on every release.

OpenSSL 3.0 (Ubuntu 24.04, Debian 12) prints a subject as ``CN = Corp CA`` when it is not asked
for a format; the OpenSSL of Ubuntu 26.04 prints ``CN=Corp CA``. Noust shows the subject and the
issuer of a certificate and reads them (which authority issued a certificate, what the internal
authority is called), so every ``openssl x509`` that prints a name asks for the one format every
release writes: ``oneline``, which is what OpenSSL 3.0 prints by default.
"""

from __future__ import annotations

#: The arguments that pin the format of the names ``openssl x509`` prints.
OPENSSL_NAME_FORMAT: tuple[str, ...] = ("-nameopt", "oneline")
