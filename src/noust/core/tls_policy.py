# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The TLS the console serves: one definition of the floor.

uvicorn hands the context it builds to OpenSSL's defaults and its own
``ssl_ciphers="TLSv1"``, which is whatever the installed OpenSSL and the
distribution decided. A console that guards a root shell states instead what it
accepts: TLS 1.2 at the least, and of the TLS 1.2 suites only ECDHE key
exchange with an AEAD cipher (AES-GCM or ChaCha20-Poly1305). TLS 1.3 has no
other kind of suite, so it needs no list.

Nothing here reads the network or the configuration: it turns a context into
the stricter one, and :mod:`noust.web.server` is what applies it to the context
uvicorn builds.
"""

from __future__ import annotations

import ssl

#: The oldest protocol version the console negotiates. TLS 1.0 and 1.1 are
#: deprecated (RFC 8996) and have no AEAD suite to offer anyway.
MINIMUM_VERSION = ssl.TLSVersion.TLSv1_2

#: OpenSSL cipher list for TLS 1.2: forward secrecy (ECDHE) and authenticated
#: encryption (AES-GCM, ChaCha20-Poly1305) only. Never a CBC, RSA key exchange
#: or 3DES suite, which is what ``!aNULL:!eNULL`` and the two families exclude
#: by construction.
TLS12_CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20:!aNULL:!eNULL"


def harden_server_context(context: ssl.SSLContext) -> ssl.SSLContext:
    """
    Apply the floor to a server context, in place.

    Args:
        context: The server-side context uvicorn built from the certificate
            and key; anything already set on it (the certificate chain, the
            verify mode) is kept.

    Returns:
        The same context, for chaining.
    """
    context.minimum_version = MINIMUM_VERSION
    context.set_ciphers(TLS12_CIPHERS)
    # Compression is what CRIME abuses; renegotiation is what its own denial of
    # service abuses; and the server's order is the one that puts the strongest
    # suite first, not whatever order the client happens to list.
    context.options |= ssl.OP_NO_COMPRESSION | ssl.OP_CIPHER_SERVER_PREFERENCE
    context.options |= getattr(ssl, "OP_NO_RENEGOTIATION", 0)
    return context
