# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_custom_certificates.py`` that other tests build on."""

from __future__ import annotations

RSA_TEXT = """\
Certificate:
    Data:
        Version: 3 (0x2)
        Signature Algorithm: sha256WithRSAEncryption
        Issuer: CN = Cloudflare Origin SSL Certificate Authority, O = CloudFlare, Inc.
        Validity
            Not Before: Oct 13 13:18:54 2026 GMT
            Not After : Nov 17 13:18:54 2027 GMT
        Subject: CN = origin.example.com, O = Test Org
        Subject Public Key Info:
            Public Key Algorithm: rsaEncryption
                Public-Key: (2048 bit)
        X509v3 extensions:
            X509v3 Subject Alternative Name:
                DNS:origin.example.com, DNS:*.example.com
sha256 Fingerprint=05:0C:ED:6E:CB:83:49:FF:BD:0A:F6:5D:EC:6B:54:7A:E2:8B:4A:06:77:A9:E0:8E:8A:14:62:18:B2:24:C1:20
"""

LEAF = "-----BEGIN CERTIFICATE-----\nLEAFLEAFLEAF\n-----END CERTIFICATE-----\n"

KEY = "-----BEGIN PRIVATE KEY-----\nKEYKEYKEY\n-----END PRIVATE KEY-----\n"
