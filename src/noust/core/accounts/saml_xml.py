# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading a signed SAML message safely: parsing, canonicalisation and signature verification.

SAML signs XML, and XML signatures are the part of single sign-on that has produced the most
authentication bypasses: a document that verifies yet means something else than what was verified
(signature wrapping), a parser that expands what it should not (entities), a verifier that trusts
the key the message carries, or one that accepts a weak algorithm. This module is a deliberately
*narrow* implementation of the profile SAML needs, using ``cryptography`` (already a dependency)
for the mathematics and the standard library for XML. Everything outside the profile is refused
with a sentence, not interpreted:

- **No DTD, no entity, no comment, no processing instruction, no encoding but UTF-8.** A signed
  document has none of them, and each is a way for two parsers to read one document two ways.
- **Exclusive canonicalisation only** (``xml-exc-c14n``, without comments), written here against
  the W3C definition and cross-checked against the standard library's own canonicaliser in the
  tests, because there is no library for it that every target distribution packages.
- **One reference, to the element signed, by that element's own ``ID``.** Not to whatever element
  an ``ID`` lookup finds: that lookup is what signature wrapping exploits. IDs must be unique in
  the document, and the signature must be a child of the element it signs.
- **SHA-256 or stronger, RSA of 2048 bits or more, or ECDSA P-256 and up.** SHA-1 is refused.
- **Only the keys the operator configured verify.** A ``KeyInfo`` in the message is never used.

What is signed is then the only thing read: callers take their data from the element this module
verified, which it returns, never from a search of the document.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast
from xml.dom import Node, minidom
from xml.parsers import expat

from noust.core.exceptions import NoustError

if TYPE_CHECKING:
    from collections.abc import Callable

    # The library is imported where it is used, as the passkey and OpenID Connect code do: a
    # server without it must still start, and refuses a SAML sign-in saying what is missing.
    from cryptography import x509

NS_DS = "http://www.w3.org/2000/09/xmldsig#"
NS_EC = "http://www.w3.org/2001/10/xml-exc-c14n#"
NS_XMLNS = "http://www.w3.org/2000/xmlns/"

ALG_EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ALG_ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"

#: The signature algorithms accepted and what each is. SHA-1 variants are not here.
SIGNATURE_METHODS: dict[str, tuple[str, str]] = {
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": ("rsa", "sha256"),
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha384": ("rsa", "sha384"),
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512": ("rsa", "sha512"),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256": ("ecdsa", "sha256"),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha384": ("ecdsa", "sha384"),
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha512": ("ecdsa", "sha512"),
}
DIGEST_METHODS: dict[str, str] = {
    "http://www.w3.org/2001/04/xmlenc#sha256": "sha256",
    "http://www.w3.org/2001/04/xmldsig-more#sha384": "sha384",
    "http://www.w3.org/2001/04/xmlenc#sha512": "sha512",
}
MIN_RSA_BITS = 2048
MAX_XML_BYTES = 1 << 20
_SPACE = re.compile(r"\s+")


class SamlXmlError(NoustError):
    """A message is not one this server reads, or its signature is not good."""


def _cryptography() -> None:
    """
    Raises:
        SamlXmlError: When the cryptography library is not installed.
    """
    try:
        import cryptography  # noqa: F401 - only to see that it is there
    except ImportError as exc:
        raise SamlXmlError(
            "Signed SAML needs the cryptography library, which is not installed",
            "Install python3-cryptography (or pip install cryptography) and restart Noust.",
        ) from exc


def not_valid_after(certificate: x509.Certificate) -> datetime:
    """
    Args:
        certificate: A certificate.

    Returns:
        When it stops being valid, in UTC. The library's older releases only have the naive form.
    """
    aware = getattr(certificate, "not_valid_after_utc", None)
    if isinstance(aware, datetime):
        return aware
    return certificate.not_valid_after.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------- parsing


def parse(data: bytes) -> minidom.Document:
    """
    Parse a SAML message, refusing everything a signed message does not need.

    Args:
        data: The XML as received.

    Returns:
        The document.

    Raises:
        SamlXmlError: When it is too long, is not UTF-8, declares a DTD or an entity, carries a
            comment or a processing instruction, or is not well-formed.
    """
    if len(data) > MAX_XML_BYTES:
        raise SamlXmlError("The SAML message is longer than is accepted")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SamlXmlError("The SAML message is not UTF-8") from exc
    if text.startswith("﻿") or "\x00" in text:
        raise SamlXmlError("The SAML message has a byte order mark or a NUL")
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered or "<!element" in lowered:
        raise SamlXmlError("The SAML message declares a DTD, which a signed message never needs")
    if "<!--" in text:
        raise SamlXmlError(
            "The SAML message has a comment, which is refused: two parsers read it two ways"
        )
    # The one processing instruction allowed is the XML declaration, first of all.
    body = re.sub(r"\A<\?xml\s[^?]*\?>", "", text, count=1)
    if "<?" in body:
        raise SamlXmlError("The SAML message has a processing instruction")
    declared = re.match(r'<\?xml\s[^?]*encoding\s*=\s*["\']([^"\']+)["\']', text)
    if declared is not None and declared.group(1).lower() != "utf-8":
        raise SamlXmlError("The SAML message declares an encoding other than UTF-8")
    try:
        document = minidom.parseString(text.encode("utf-8"))  # noqa: S318 - no DTD, entity, comment or PI, checked above
    except (expat.ExpatError, ValueError) as exc:
        raise SamlXmlError(f"The SAML message is not well-formed XML: {exc}") from exc
    return document


def root_of(document: minidom.Document) -> minidom.Element:
    """
    Args:
        document: A parsed message.

    Returns:
        Its root element.

    Raises:
        SamlXmlError: When it has none.
    """
    root = document.documentElement
    if root is None:
        raise SamlXmlError("The SAML message has no root element")
    return root


def elements(parent: minidom.Element) -> list[minidom.Element]:
    """
    Args:
        parent: An element.

    Returns:
        Its child elements, in order.
    """
    return [child for child in parent.childNodes if child.nodeType == Node.ELEMENT_NODE]


def children(parent: minidom.Element, namespace: str, local: str) -> list[minidom.Element]:
    """
    Args:
        parent: An element.
        namespace: A namespace URI.
        local: A local name.

    Returns:
        Its direct children with that name.
    """
    return [
        child
        for child in elements(parent)
        if child.namespaceURI == namespace and child.localName == local
    ]


def only_child(parent: minidom.Element, namespace: str, local: str) -> minidom.Element | None:
    """
    Args:
        parent: An element.
        namespace: A namespace URI.
        local: A local name.

    Returns:
        The one child with that name, or None when there is none.

    Raises:
        SamlXmlError: When there are several: an ambiguity is never resolved by choosing.
    """
    found = children(parent, namespace, local)
    if len(found) > 1:
        raise SamlXmlError(f"The SAML message has {len(found)} {local} elements where one is due")
    return found[0] if found else None


def text_of(element: minidom.Element) -> str:
    """
    Args:
        element: An element with only text inside.

    Returns:
        The text, with surrounding space removed.

    Raises:
        SamlXmlError: When it has an element inside.
    """
    parts: list[str] = []
    for child in element.childNodes:
        if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
            parts.append(cast(minidom.Text, child).data)
        elif child.nodeType == Node.ELEMENT_NODE:
            raise SamlXmlError(f"The SAML message has an element inside {element.localName}")
    return "".join(parts).strip()


def descendants(root: minidom.Element) -> list[minidom.Element]:
    """
    Args:
        root: An element.

    Returns:
        Every element under it, in document order, itself included.
    """
    found = [root]
    for child in elements(root):
        found.extend(descendants(child))
    return found


def check_unique_ids(root: minidom.Element) -> None:
    """
    Args:
        root: The document element.

    Raises:
        SamlXmlError: When two elements carry the same ``ID``. That is how a signed element and
            an unsigned one are made to answer to one name.
    """
    seen: set[str] = set()
    for element in descendants(root):
        for name in ("ID", "Id", "id"):
            if element.hasAttribute(name):
                value = element.getAttribute(name)
                if value in seen:
                    raise SamlXmlError(f"The SAML message uses the ID {value[:40]!r} twice")
                seen.add(value)


# ------------------------------------------------------------ canonicalisation


def _escape_text(value: str) -> str:
    """
    Args:
        value: Character data.

    Returns:
        It as canonical XML writes text.
    """
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#xD;")
    )


def _escape_attribute(value: str) -> str:
    """
    Args:
        value: An attribute's value.

    Returns:
        It as canonical XML writes an attribute value.
    """
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace('"', "&quot;")
        .replace("\t", "&#x9;")
        .replace("\n", "&#xA;")
        .replace("\r", "&#xD;")
    )


def _declarations(element: minidom.Element) -> dict[str, str]:
    """
    Args:
        element: An element.

    Returns:
        The namespace declarations it makes, prefix to URI (the default namespace's prefix is
        the empty string).
    """
    found: dict[str, str] = {}
    attributes = element.attributes
    for index in range(attributes.length):
        attribute = cast("minidom.Attr | None", attributes.item(index))
        if attribute is None:
            continue
        # minidom reports an empty declaration (xmlns="") as None.
        if attribute.name == "xmlns":
            found[""] = attribute.value or ""
        elif attribute.name.startswith("xmlns:"):
            found[attribute.name[6:]] = attribute.value or ""
    return found


def _in_scope(element: minidom.Element) -> dict[str, str]:
    """
    Args:
        element: An element.

    Returns:
        The namespaces in scope at it, from the document element down to it.
    """
    chain: list[minidom.Element] = []
    node: Any = element
    while node is not None and node.nodeType == Node.ELEMENT_NODE:
        chain.append(cast(minidom.Element, node))
        node = node.parentNode
    scope: dict[str, str] = {}
    for ancestor in reversed(chain):
        scope.update(_declarations(ancestor))
    return scope


def canonicalize(
    element: minidom.Element,
    inclusive_prefixes: frozenset[str] = frozenset(),
    *,
    skip: minidom.Element | None = None,
) -> bytes:
    """
    Exclusive XML canonicalisation 1.0, without comments (W3C ``xml-exc-c14n``).

    Each element carries the namespace declarations it *visibly utilises* (its own prefix, and
    the prefix of each of its attributes) that its nearest output ancestor does not already
    carry, in order of prefix with the default namespace first; attributes are in order of
    namespace URI then local name; text and attribute values are escaped as the standard says;
    an empty element is written with a start tag and an end tag.

    Args:
        element: The element to write, with everything under it.
        inclusive_prefixes: Prefixes (``""`` for the default namespace) to write wherever they are
            in scope, as the ``InclusiveNamespaces`` element of the transform asks.
        skip: An element under it to leave out: the signature, for the enveloped transform.

    Returns:
        The canonical form, in UTF-8.

    Raises:
        SamlXmlError: When a prefix is used and not declared, or the document has a node this
            profile does not write (a comment, a processing instruction).
    """
    out: list[str] = []

    def emit(node: minidom.Element, scope: dict[str, str], written: dict[str, str]) -> None:
        here = {**scope, **_declarations(node)}
        used = {node.prefix or ""}
        attributes: list[minidom.Attr] = []
        for index in range(node.attributes.length):
            attribute = cast("minidom.Attr | None", node.attributes.item(index))
            if (
                attribute is None
                or attribute.name == "xmlns"
                or attribute.name.startswith("xmlns:")
            ):
                continue
            attributes.append(attribute)
            if attribute.prefix and attribute.prefix != "xml":
                used.add(attribute.prefix)
        used.update(prefix for prefix in inclusive_prefixes if prefix in here)
        declare: dict[str, str] = {}
        for prefix in used:
            uri = here.get(prefix, "")
            if prefix and not uri:
                raise SamlXmlError(f"The prefix {prefix!r} is used and never declared")
            if uri != written.get(prefix, ""):
                declare[prefix] = uri
        inside = {**written, **declare}
        name = node.tagName
        out.append("<" + name)
        for prefix in sorted(declare):
            out.append(
                f' xmlns:{prefix}="{_escape_attribute(declare[prefix])}"'
                if prefix
                else f' xmlns="{_escape_attribute(declare[prefix])}"'
            )
        for attribute in sorted(
            attributes, key=lambda a: (a.namespaceURI or "", a.localName or a.name)
        ):
            out.append(f' {attribute.name}="{_escape_attribute(attribute.value or "")}"')
        out.append(">")
        for child in node.childNodes:
            if child.nodeType == Node.ELEMENT_NODE:
                if child is skip:
                    continue
                emit(child, here, inside)
            elif child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
                out.append(_escape_text(child.data))
            else:
                raise SamlXmlError("The SAML message has a node this profile does not read")
        out.append("</" + name + ">")

    # The apex is written with what it needs; its ancestors' declarations are in scope only.
    parent = element.parentNode
    emit(
        element,
        _in_scope(parent) if parent is not None and parent.nodeType == Node.ELEMENT_NODE else {},
        {},
    )
    return "".join(out).encode("utf-8")


# --------------------------------------------------------------------- signatures


@dataclass(frozen=True)
class Verified:
    """
    An element whose signature is good.

    Attributes:
        element: The element that was signed and verified: the only thing to read from.
        reference_id: Its ``ID``.
        algorithm: The signature algorithm that verified.
    """

    element: minidom.Element
    reference_id: str
    algorithm: str


def load_certificates(pems: Sequence[str]) -> list[x509.Certificate]:
    """
    Args:
        pems: Certificates in PEM, or bare base64 as metadata carries them.

    Returns:
        The certificates, those whose key this profile accepts.

    Raises:
        SamlXmlError: When one cannot be read or its key is too weak.
    """
    _cryptography()
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    found: list[x509.Certificate] = []
    for pem in pems:
        text = pem.strip()
        if not text:
            continue
        if "BEGIN CERTIFICATE" not in text:
            body = _SPACE.sub("", text)
            text = (
                "-----BEGIN CERTIFICATE-----\n"
                + "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
                + "\n-----END CERTIFICATE-----\n"
            )
        try:
            certificate = x509.load_pem_x509_certificate(text.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise SamlXmlError(f"Not a certificate: {exc}") from exc
        key = certificate.public_key()
        if isinstance(key, rsa.RSAPublicKey):
            if key.key_size < MIN_RSA_BITS:
                raise SamlXmlError(
                    f"The certificate's RSA key is {key.key_size} bits; at least {MIN_RSA_BITS} are required"
                )
        elif isinstance(key, ec.EllipticCurvePublicKey):
            if key.curve.name not in ("secp256r1", "secp384r1", "secp521r1"):
                raise SamlXmlError(f"The certificate's curve {key.curve.name} is not accepted")
        else:
            raise SamlXmlError("The certificate's key is not RSA or ECDSA")
        found.append(certificate)
    return found


def _decode(element: minidom.Element) -> bytes:
    """
    Args:
        element: An element whose text is base64.

    Returns:
        The bytes.

    Raises:
        SamlXmlError: When it is not base64.
    """
    try:
        return base64.b64decode(_SPACE.sub("", text_of(element)), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SamlXmlError("A value in the signature is not base64") from exc


def _prefix_list(parent: minidom.Element) -> frozenset[str]:
    """
    Args:
        parent: A ``CanonicalizationMethod`` or ``Transform``.

    Returns:
        The prefixes its ``InclusiveNamespaces`` asks to keep (``#default`` is the empty prefix).
    """
    found = children(parent, NS_EC, "InclusiveNamespaces")
    if not found:
        return frozenset()
    tokens = found[0].getAttribute("PrefixList").split()
    return frozenset("" if item == "#default" else item for item in tokens)


def verify_signed(signed: minidom.Element, certificates: Sequence[x509.Certificate]) -> Verified:
    """
    Verify the enveloped signature a SAML element carries.

    Args:
        signed: The element the signature is a child of: a ``Response`` or an ``Assertion``.
        certificates: The identity provider's signing certificates, as configured.

    Returns:
        What was verified.

    Raises:
        SamlXmlError: When the signature is missing or malformed, uses anything outside this
            profile, does not refer to ``signed`` by its own ``ID``, its digest differs, or
            no configured key verifies it.
    """
    if not certificates:
        raise SamlXmlError("No signing certificate is configured for this identity provider")
    _cryptography()
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    signatures = children(signed, NS_DS, "Signature")
    if len(signatures) != 1:
        raise SamlXmlError(
            f"The signed element carries {len(signatures)} signatures where one is due"
        )
    signature = signatures[0]
    signed_info = only_child(signature, NS_DS, "SignedInfo")
    value_element = only_child(signature, NS_DS, "SignatureValue")
    if signed_info is None or value_element is None:
        raise SamlXmlError("The signature has no SignedInfo or no SignatureValue")

    canonicalization = only_child(signed_info, NS_DS, "CanonicalizationMethod")
    if canonicalization is None or canonicalization.getAttribute("Algorithm") != ALG_EXC_C14N:
        raise SamlXmlError(
            "The signature's canonicalisation is not exclusive C14N without comments"
        )
    info_prefixes = _prefix_list(canonicalization)

    method = only_child(signed_info, NS_DS, "SignatureMethod")
    algorithm = method.getAttribute("Algorithm") if method is not None else ""
    if algorithm not in SIGNATURE_METHODS:
        raise SamlXmlError(
            "The signature algorithm is not accepted (RSA or ECDSA with SHA-256 or stronger)"
        )
    kind, digest_name = SIGNATURE_METHODS[algorithm]

    references = children(signed_info, NS_DS, "Reference")
    if len(references) != 1:
        raise SamlXmlError("The signature must have exactly one reference")
    reference = references[0]
    identifier = signed.getAttribute("ID")
    if not identifier or reference.getAttribute("URI") != "#" + identifier:
        raise SamlXmlError("The signature does not refer to the element it is in, by its ID")

    transforms_holder = only_child(reference, NS_DS, "Transforms")
    transforms = children(transforms_holder, NS_DS, "Transform") if transforms_holder else []
    names = [t.getAttribute("Algorithm") for t in transforms]
    if ALG_ENVELOPED not in names or ALG_EXC_C14N not in names or len(set(names)) != len(names):
        raise SamlXmlError(
            "The signature's transforms must be the enveloped signature and exclusive C14N"
        )
    if any(name not in (ALG_ENVELOPED, ALG_EXC_C14N) for name in names):
        raise SamlXmlError("The signature uses a transform this server does not accept")
    c14n_transform = next(t for t in transforms if t.getAttribute("Algorithm") == ALG_EXC_C14N)
    content_prefixes = _prefix_list(c14n_transform)

    digest_method = only_child(reference, NS_DS, "DigestMethod")
    reference_digest = DIGEST_METHODS.get(
        digest_method.getAttribute("Algorithm") if digest_method is not None else ""
    )
    if reference_digest is None:
        raise SamlXmlError("The signature's digest algorithm is not accepted (SHA-256 or stronger)")
    digest_value = only_child(reference, NS_DS, "DigestValue")
    if digest_value is None:
        raise SamlXmlError("The signature's reference has no digest")

    content = canonicalize(signed, content_prefixes, skip=signature)
    computed = hashlib.new(reference_digest, content).digest()
    if not hmac.compare_digest(computed, _decode(digest_value)):
        raise SamlXmlError("The signed content does not match its digest: it was changed")

    signed_bytes = canonicalize(signed_info, info_prefixes)
    raw = _decode(value_element)
    hash_classes: dict[str, Callable[[], hashes.HashAlgorithm]] = {
        "sha256": hashes.SHA256,
        "sha384": hashes.SHA384,
        "sha512": hashes.SHA512,
    }
    hash_class = hash_classes[digest_name]
    for certificate in certificates:
        key = certificate.public_key()
        try:
            if kind == "rsa" and isinstance(key, rsa.RSAPublicKey):
                key.verify(raw, signed_bytes, padding.PKCS1v15(), hash_class())
                return Verified(signed, identifier, algorithm)
            if kind == "ecdsa" and isinstance(key, ec.EllipticCurvePublicKey):
                half = (key.curve.key_size + 7) // 8
                if len(raw) != 2 * half:
                    continue
                der = encode_dss_signature(
                    int.from_bytes(raw[:half], "big"), int.from_bytes(raw[half:], "big")
                )
                key.verify(der, signed_bytes, ec.ECDSA(hash_class()))
                return Verified(signed, identifier, algorithm)
        except InvalidSignature:
            continue
    raise SamlXmlError("No configured signing certificate verifies the signature")
