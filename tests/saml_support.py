# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An identity provider for tests: it signs SAML the way a real one does, and can be told to sign it
wrongly.

Signing here uses the same canonicalisation as the code under test, so a mistake the two share
would not be seen by a round trip; that is why ``tests/test_saml_xml.py`` also holds the
canonicaliser to the standard library's independent implementation and to hand-worked cases.
What these helpers are for is everything around it: the structure of a Response, and the many
ways a signed document can be made to mean something else.
"""

from __future__ import annotations

import base64
import datetime
import re
import zlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.dom import minidom

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

from noust.core.accounts import saml_xml as sx

NS_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
NS_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
ECDSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256"
SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
SHA1 = "http://www.w3.org/2000/09/xmldsig#sha1"
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
EXC_C14N_COMMENTS = "http://www.w3.org/2001/10/xml-exc-c14n#WithComments"
ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"

_keys: dict[str, tuple[Any, str]] = {}


def make_identity(kind: str = "rsa", name: str = "idp.acme.test") -> tuple[Any, str]:
    """
    Args:
        kind: ``rsa`` (2048 bits) or ``ec`` (P-256).
        name: The certificate's subject.

    Returns:
        A private key and its self-signed certificate in PEM. Made once per kind and name.
    """
    cache_key = f"{kind}:{name}"
    if cache_key in _keys:
        return _keys[cache_key]
    key: Any = (
        rsa.generate_private_key(65537, 2048)
        if kind == "rsa"
        else ec.generate_private_key(ec.SECP256R1())
    )
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    _keys[cache_key] = (key, pem)
    return key, pem


def iso(moment: datetime.datetime) -> str:
    """
    Args:
        moment: A time.

    Returns:
        It as SAML writes an instant.
    """
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Scenario:
    """
    What an identity provider says in a Response, with every part open to being wrong.

    Attributes:
        issuer: The identity provider's entity id.
        audience: Who the assertion is for.
        destination: Where the Response says it goes.
        recipient: Where the assertion says it may be presented.
        in_response_to: The request it answers.
        name_id: The person's identifier.
        name_id_format: The format it declares.
        attributes: Attribute name to its values.
        now: The IdP's clock.
        not_before: Start of validity relative to now, in seconds.
        not_on_or_after: End of validity relative to now, in seconds.
        confirmation_until: End of the bearer confirmation relative to now, in seconds.
        status: The status code.
        authn_context: The class of authentication.
        assertion_id: The assertion's ID.
        response_id: The response's ID.
        extra_conditions: XML to add inside Conditions.
    """

    issuer: str = "https://idp.acme.test/metadata"
    audience: str = "https://noust.acme.test/api/auth/saml/metadata/corp"
    destination: str = "https://noust.acme.test/api/auth/saml/acs"
    recipient: str = "https://noust.acme.test/api/auth/saml/acs"
    in_response_to: str = "ID_request_1"
    name_id: str = "uuid-maria"
    name_id_format: str = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"
    attributes: dict[str, list[str]] = field(
        default_factory=lambda: {
            "email": ["maria@acme.test"],
            "displayName": ["Maria Lopez"],
            "groups": ["ops", "devs"],
        }
    )
    now: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    not_before: int = -30
    not_on_or_after: int = 300
    confirmation_until: int = 300
    status: str = SUCCESS
    authn_context: str = "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
    assertion_id: str = "ID_assertion_1"
    response_id: str = "ID_response_1"
    extra_conditions: str = ""

    def assertion_xml(self, *, declare: bool = True) -> str:
        """
        Args:
            declare: Declare the namespace on the assertion (when it is a document of its own).

        Returns:
            The assertion, unsigned.
        """

        def moment(seconds: int) -> str:
            return iso(self.now + datetime.timedelta(seconds=seconds))

        attrs = "".join(
            f'<saml:Attribute Name="{name}">'
            + "".join(f"<saml:AttributeValue>{value}</saml:AttributeValue>" for value in values)
            + "</saml:Attribute>"
            for name, values in self.attributes.items()
        )
        namespace = f' xmlns:saml="{NS_SAML}"' if declare else ""
        return (
            f'<saml:Assertion{namespace} ID="{self.assertion_id}" Version="2.0" '
            f'IssueInstant="{moment(0)}">'
            f"<saml:Issuer>{self.issuer}</saml:Issuer>"
            f'<saml:Subject><saml:NameID Format="{self.name_id_format}">'
            f"{self.name_id}</saml:NameID>"
            f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
            f'<saml:SubjectConfirmationData InResponseTo="{self.in_response_to}" '
            f'NotOnOrAfter="{moment(self.confirmation_until)}" Recipient="{self.recipient}"/>'
            f"</saml:SubjectConfirmation></saml:Subject>"
            f'<saml:Conditions NotBefore="{moment(self.not_before)}" '
            f'NotOnOrAfter="{moment(self.not_on_or_after)}">'
            f"<saml:AudienceRestriction><saml:Audience>{self.audience}</saml:Audience>"
            f"</saml:AudienceRestriction>{self.extra_conditions}</saml:Conditions>"
            f'<saml:AuthnStatement AuthnInstant="{moment(0)}"><saml:AuthnContext>'
            f"<saml:AuthnContextClassRef>{self.authn_context}</saml:AuthnContextClassRef>"
            f"</saml:AuthnContext></saml:AuthnStatement>"
            f"<saml:AttributeStatement>{attrs}</saml:AttributeStatement>"
            f"</saml:Assertion>"
        )

    def response_xml(self, assertion: str | None = None, *, extra: str = "") -> str:
        """
        Args:
            assertion: The assertion's XML (without its own namespace declaration), or the
                scenario's own.
            extra: XML to add inside the Response before the assertion.

        Returns:
            The Response, unsigned.
        """
        body = assertion if assertion is not None else self.assertion_xml(declare=False)
        return (
            f'<samlp:Response xmlns:samlp="{NS_SAMLP}" xmlns:saml="{NS_SAML}" '
            f'ID="{self.response_id}" Version="2.0" IssueInstant="{iso(self.now)}" '
            f'Destination="{self.destination}" InResponseTo="{self.in_response_to}">'
            f"<saml:Issuer>{self.issuer}</saml:Issuer>"
            f'<samlp:Status><samlp:StatusCode Value="{self.status}"/></samlp:Status>'
            f"{extra}{body}</samlp:Response>"
        )


def _find(root: minidom.Element, identifier: str) -> minidom.Element:
    for element in sx.descendants(root):
        if element.getAttribute("ID") == identifier:
            return element
    raise KeyError(identifier)


def sign(
    xml: str,
    target_id: str,
    key: Any,
    *,
    signature_method: str = RSA_SHA256,
    digest_method: str = SHA256,
    transforms: tuple[str, ...] = (ENVELOPED, EXC_C14N),
    canonicalization: str = EXC_C14N,
    reference_uri: str | None = None,
    key_info: str = "",
    prefix_list: str = "",
) -> str:
    """
    Sign an element of a document the way an identity provider does.

    Args:
        xml: The document.
        target_id: The ``ID`` of the element to sign.
        key: The private key.
        signature_method: The signature algorithm URI.
        digest_method: The digest algorithm URI.
        transforms: The transforms to declare, in order.
        canonicalization: The canonicalisation of ``SignedInfo``.
        reference_uri: The reference to write; ``#<target_id>`` by default.
        key_info: XML to put in ``KeyInfo``, when it is to carry something.
        prefix_list: A ``PrefixList`` to put on the exclusive transform and the canonicalisation.

    Returns:
        The document with the signature inside the element, as its first child after the Issuer.
    """
    document = minidom.parseString(xml)  # noqa: S318 - a document of the tests' own
    target = _find(document.documentElement, target_id)
    digest_hash = hashes.SHA1() if digest_method == SHA1 else hashes.SHA256()  # noqa: S303 - weak on purpose, to see it refused
    prefixes = frozenset(prefix_list.split()) if prefix_list else frozenset()
    content = sx.canonicalize(target, prefixes)
    digest = hashes.Hash(digest_hash)
    digest.update(content)
    inclusive = (
        f'<ec:InclusiveNamespaces xmlns:ec="{sx.NS_EC}" PrefixList="{prefix_list}"/>'
        if prefix_list
        else ""
    )
    transform_xml = "".join(
        f'<ds:Transform Algorithm="{t}">{inclusive if t == EXC_C14N else ""}</ds:Transform>'
        for t in transforms
    )
    signature_xml = (
        f'<ds:Signature xmlns:ds="{sx.NS_DS}"><ds:SignedInfo>'
        f'<ds:CanonicalizationMethod Algorithm="{canonicalization}">{inclusive}</ds:CanonicalizationMethod>'
        f'<ds:SignatureMethod Algorithm="{signature_method}"/>'
        f'<ds:Reference URI="{reference_uri if reference_uri is not None else "#" + target_id}">'
        f"<ds:Transforms>{transform_xml}</ds:Transforms>"
        f'<ds:DigestMethod Algorithm="{digest_method}"/>'
        f"<ds:DigestValue>{base64.b64encode(digest.finalize()).decode()}</ds:DigestValue>"
        f"</ds:Reference></ds:SignedInfo><ds:SignatureValue>AAAA</ds:SignatureValue>"
        f"{key_info}</ds:Signature>"
    )
    signature = minidom.parseString(signature_xml).documentElement  # noqa: S318 - the tests' own
    imported = document.importNode(signature, True)
    issuer = next((e for e in sx.elements(target) if e.localName == "Issuer"), None)
    if issuer is not None and issuer.nextSibling is not None:
        target.insertBefore(imported, issuer.nextSibling)
    else:
        target.appendChild(imported)
    signed_info = sx.children(imported, sx.NS_DS, "SignedInfo")[0]
    to_sign = sx.canonicalize(signed_info, prefixes)
    if signature_method == RSA_SHA1:
        raw = key.sign(to_sign, padding.PKCS1v15(), hashes.SHA1())  # noqa: S303 - weak on purpose, to see it refused
    elif signature_method == ECDSA_SHA256:
        der = key.sign(to_sign, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    else:
        raw = key.sign(to_sign, padding.PKCS1v15(), hashes.SHA256())
    value = sx.children(imported, sx.NS_DS, "SignatureValue")[0]
    value.firstChild.data = base64.b64encode(raw).decode()
    return document.documentElement.toxml()


def encode_response(xml: str) -> str:
    """
    Args:
        xml: A Response.

    Returns:
        It as a browser posts it: base64.
    """
    return base64.b64encode(xml.encode("utf-8")).decode("ascii")


def read_redirect(url: str) -> tuple[minidom.Element, str]:
    """
    Take apart the address a sign-in sends the browser to, the way an identity provider does.

    Args:
        url: The HTTP-Redirect binding's address.

    Returns:
        The AuthnRequest and the relay state.
    """
    query = parse_qs(urlsplit(url).query)
    packed = base64.b64decode(query["SAMLRequest"][0])
    xml = zlib.decompress(packed, -15).decode("utf-8")
    return sx.root_of(minidom.parseString(xml)), query["RelayState"][0]  # noqa: S318 - the tests' own


def _strip_declaration(xml: str) -> str:
    """
    Args:
        xml: An XML document.

    Returns:
        It without an XML declaration, to be placed inside another.
    """
    return re.sub(r"\A<\?xml[^>]*\?>", "", xml)


class FakeIdp:
    """
    An identity provider that answers the request it was sent.

    Args:
        key: The private key it signs with.
        now: A callable giving the current time as a datetime: the clock the code under test uses.
        issuer: Its entity id.
    """

    def __init__(self, key: Any, now: Any, issuer: str = "https://idp.acme.test/metadata") -> None:
        self.key = key
        self.issuer = issuer
        self.now = now
        self._answers = 0

    def scenario(self, request: minidom.Element, **changes: Any) -> Scenario:
        """
        Args:
            request: The AuthnRequest.
            **changes: Fields of the scenario to say differently.

        Returns:
            What the provider would say to that request.
        """
        asker = sx.text_of(sx.children(request, NS_SAML, "Issuer")[0])
        acs = request.getAttribute("AssertionConsumerServiceURL")
        base: dict[str, Any] = {
            "issuer": self.issuer,
            "audience": asker,
            "destination": acs,
            "recipient": acs,
            "in_response_to": request.getAttribute("ID"),
            "now": self.now(),
        }
        return Scenario(**{**base, **changes})

    def answer(
        self,
        url: str,
        *,
        sign_what: str = "assertion",
        key: Any = None,
        **changes: Any,
    ) -> tuple[str, str]:
        """
        Answer a sign-in.

        Args:
            url: The address the browser was sent to.
            sign_what: ``assertion``, ``response``, ``both`` or ``nothing``.
            key: A key to sign with instead of the provider's.
            **changes: Fields of the scenario to say differently.

        Returns:
            The ``SAMLResponse`` field and the ``RelayState`` field a browser would post.
        """
        request, relay = read_redirect(url)
        self._answers += 1
        # A provider never issues the same assertion twice; one that did would be a replay.
        changes.setdefault("assertion_id", f"ID_assertion_{self._answers}")
        changes.setdefault("response_id", f"ID_response_{self._answers}")
        scenario = self.scenario(request, **changes)
        signing = key or self.key
        method = ECDSA_SHA256 if isinstance(signing, ec.EllipticCurvePrivateKey) else RSA_SHA256
        if sign_what in ("assertion", "both"):
            assertion = sign(
                scenario.assertion_xml(), scenario.assertion_id, signing, signature_method=method
            )
            document = scenario.response_xml(_strip_declaration(assertion))
        else:
            document = scenario.response_xml()
        if sign_what in ("response", "both"):
            document = sign(document, scenario.response_id, signing, signature_method=method)
        return encode_response(document), relay
