# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A directory for tests: a real LDAP v3 server, small, speaking BER over real sockets.

It answers StartTLS, simple binds and searches (equality, presence, substrings, and, or, not),
over plain text, ``ldaps`` or StartTLS, with a certificate made on the spot. It records what it
was sent, so a test can say what never reached a directory (an empty password, an unescaped
filter), and can be told to misbehave: answer slowly, send a message that is far too long, or
speak nonsense.
"""

from __future__ import annotations

import datetime
import ipaddress
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core.accounts import ldap_client as ber

SEQUENCE = 0x30


def make_certificate(directory: Path, host: str = "localhost") -> tuple[Path, Path]:
    """
    Args:
        directory: Where to write them.
        host: The name the certificate is for.

    Returns:
        The paths of a self-signed certificate (valid for the name and for 127.0.0.1) and its key.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(host), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / f"{host}.pem"
    key_path = directory / f"{host}.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def entry(dn: str, **attributes: Any) -> dict[str, Any]:
    """
    Args:
        dn: The distinguished name.
        **attributes: Each attribute as a value or a list of them (bytes or text).

    Returns:
        An entry for :class:`FakeDirectory`.
    """
    values = {
        name.lower(): [
            v if isinstance(v, bytes) else str(v).encode()
            for v in (value if isinstance(value, list) else [value])
        ]
        for name, value in attributes.items()
    }
    return {"dn": dn, "attrs": values}


def _filter(reader: ber.Reader, tag: int, value: bytes) -> Any:
    """
    Args:
        reader: The reader the filter was read with.
        tag: The filter's tag.
        value: Its content.

    Returns:
        A predicate over an entry's attributes (lower-case name to raw values).
    """
    if tag in (0xA0, 0xA1):
        inner = reader.sub(value)
        parts = []
        while not inner.done:
            t, v = inner.read()
            parts.append(_filter(inner, t, v))
        if tag == 0xA0:
            return lambda attrs: all(p(attrs) for p in parts)
        return lambda attrs: any(p(attrs) for p in parts)
    if tag == 0xA2:
        inner = reader.sub(value)
        t, v = inner.read()
        negated = _filter(inner, t, v)
        return lambda attrs: not negated(attrs)
    if tag == 0x87:
        name = value.decode().lower()
        return lambda attrs: name in attrs
    if tag == 0xA3:
        inner = reader.sub(value)
        name = ber.decode_text(inner.expect(0x04)).lower()
        wanted = inner.expect(0x04).lower()
        return lambda attrs: any(v.lower() == wanted for v in attrs.get(name, []))
    if tag == 0xA4:
        inner = reader.sub(value)
        name = ber.decode_text(inner.expect(0x04)).lower()
        pieces = inner.sub(inner.expect(SEQUENCE))
        found: list[tuple[int, bytes]] = []
        while not pieces.done:
            found.append(pieces.read())

        def match(attrs: dict[str, list[bytes]]) -> bool:
            for candidate in attrs.get(name, []):
                text = candidate.lower()
                position = 0
                ok = True
                for kind, part in found:
                    part = part.lower()
                    if kind == 0x80:
                        ok = text.startswith(part)
                        position = len(part)
                    elif kind == 0x81:
                        index = text.find(part, position)
                        ok = index >= 0
                        position = index + len(part) if ok else position
                    else:
                        ok = text.endswith(part) and len(text) - len(part) >= position
                    if not ok:
                        break
                if ok:
                    return True
            return False

        return match
    raise ValueError(f"filter tag {tag:#04x} is not supported by the test directory")


@dataclass
class Seen:
    """
    What the directory was sent.

    Attributes:
        binds: ``(dn, password)`` of each bind.
        searches: ``(base, filter bytes as hex, attributes)`` of each search.
        starttls: How many StartTLS requests.
    """

    binds: list[tuple[str, str]] = field(default_factory=list)
    searches: list[tuple[str, str, list[str]]] = field(default_factory=list)
    starttls: int = 0


class FakeDirectory:
    """
    A directory listening on 127.0.0.1.

    Args:
        entries: The entries, from :func:`entry`. A bind succeeds as an entry that has a
            ``userpassword`` equal to the password.
        mode: ``plain``, ``ldaps`` (TLS from the first byte) or ``starttls``.
        certificate: The certificate and key paths, for the TLS modes.
        refuse_starttls: Answer StartTLS with a failure.
        delay: Seconds to wait before answering anything.
        misbehave: ``"huge"`` answers every request with a message whose length is a gigabyte,
            ``"garbage"`` with bytes that are not BER.
    """

    def __init__(
        self,
        entries: list[dict[str, Any]],
        *,
        mode: str = "plain",
        certificate: tuple[Path, Path] | None = None,
        refuse_starttls: bool = False,
        delay: float = 0.0,
        misbehave: str | None = None,
    ) -> None:
        self.entries = entries
        self.mode = mode
        self.certificate = certificate
        self.refuse_starttls = refuse_starttls
        self.delay = delay
        self.misbehave = misbehave
        self.seen = Seen()
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self.port: int = self._listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _context(self) -> ssl.SSLContext:
        assert self.certificate is not None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.certificate[0]), str(self.certificate[1]))
        return context

    def start(self) -> FakeDirectory:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
        except OSError:
            pass
        self._listener.close()

    def __enter__(self) -> FakeDirectory:
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            if self._stop.is_set():
                client.close()
                return
            threading.Thread(target=self._client, args=(client,), daemon=True).start()

    # -- one connection

    def _client(self, raw: socket.socket) -> None:
        sock: socket.socket | ssl.SSLSocket = raw
        raw.settimeout(5)
        try:
            if self.mode == "ldaps":
                sock = self._context().wrap_socket(raw, server_side=True)
            while True:
                message = self._read(sock)
                if message is None:
                    return
                message_id, tag, value, reader = message
                if self.delay:
                    time.sleep(self.delay)
                if self.misbehave == "huge":
                    sock.sendall(b"\x30\x84\x40\x00\x00\x00")
                    return
                if self.misbehave == "garbage":
                    sock.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                    return
                if tag == 0x42:
                    return
                if tag == 0x77:
                    self.seen.starttls += 1
                    if self.refuse_starttls:
                        sock.sendall(self._result(message_id, 0x78, 2, "no TLS here"))
                        continue
                    sock.sendall(self._result(message_id, 0x78, 0, ""))
                    sock = self._context().wrap_socket(sock, server_side=True)
                elif tag == 0x60:
                    sock.sendall(self._bind(message_id, value, reader))
                elif tag == 0x63:
                    for part in self._search(message_id, value, reader):
                        sock.sendall(part)
        except (OSError, ssl.SSLError, ValueError, ber.LdapError):
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _read(sock: socket.socket) -> tuple[int, int, bytes, ber.Reader] | None:
        head = sock.recv(2)
        if len(head) < 2:
            return None
        size = head[1]
        if size >= 0x80:
            count = size & 0x7F
            size = int.from_bytes(_exactly(sock, count), "big")
        body = _exactly(sock, size)
        reader = ber.Reader(body)
        message_id = ber.decode_integer(reader.expect(0x02))
        tag, value = reader.read()
        return message_id, tag, value, reader

    @staticmethod
    def _result(message_id: int, tag: int, code: int, text: str) -> bytes:
        body = ber.encode_integer(code, 0x0A) + ber.encode_string("") + ber.encode_string(text)
        return ber.tlv(SEQUENCE, ber.encode_integer(message_id) + ber.tlv(tag, body))

    def _bind(self, message_id: int, value: bytes, reader: ber.Reader) -> bytes:
        inner = reader.sub(value)
        inner.expect(0x02)
        dn = ber.decode_text(inner.expect(0x04))
        password = ber.decode_text(inner.expect(0x80))
        self.seen.binds.append((dn, password))
        if dn == "" and password == "":
            return self._result(message_id, 0x61, 0, "")
        for candidate in self.entries:
            if candidate["dn"].lower() == dn.lower():
                stored = candidate["attrs"].get("userpassword", [])
                if password and any(v.decode() == password for v in stored):
                    return self._result(message_id, 0x61, 0, "")
        return self._result(message_id, 0x61, 49, "invalid credentials")

    def _search(self, message_id: int, value: bytes, reader: ber.Reader) -> list[bytes]:
        inner = reader.sub(value)
        base = ber.decode_text(inner.expect(0x04))
        inner.expect(0x0A)
        inner.expect(0x0A)
        size_limit = ber.decode_integer(inner.expect(0x02))
        inner.expect(0x02)
        inner.expect(0x01)
        tag, filter_value = inner.read()
        predicate = _filter(inner, tag, filter_value)
        names = inner.sub(inner.expect(SEQUENCE))
        wanted: list[str] = []
        while not names.done:
            wanted.append(ber.decode_text(names.expect(0x04)))
        self.seen.searches.append((base, ber.tlv(tag, filter_value).hex(), wanted))
        parts: list[bytes] = []
        sent = 0
        code = 0
        for candidate in self.entries:
            if not candidate["dn"].lower().endswith(base.lower()):
                continue
            attrs = candidate["attrs"]
            if not predicate(attrs):
                continue
            if size_limit and sent >= size_limit:
                code = 4
                break
            sent += 1
            listing = b""
            for name in wanted:
                values = attrs.get(name.lower())
                if values is None or name.lower() == "userpassword":
                    continue
                listing += ber.tlv(
                    SEQUENCE,
                    ber.encode_string(name)
                    + ber.tlv(0x31, b"".join(ber.encode_string(v) for v in values)),
                )
            body = ber.encode_string(candidate["dn"]) + ber.tlv(SEQUENCE, listing)
            parts.append(ber.tlv(SEQUENCE, ber.encode_integer(message_id) + ber.tlv(0x64, body)))
        parts.append(self._result(message_id, 0x65, code, ""))
        return parts


def _exactly(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise OSError("closed")
        data += part
    return data
