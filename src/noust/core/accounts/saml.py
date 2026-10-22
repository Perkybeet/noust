# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Single sign-on through a SAML 2.0 identity provider: who may come in, and as whom.

:class:`SamlManager` is the one implementation, called by the console's ``/api/auth/saml`` routes and
by ``noust auth saml`` alike (rule 3). Noust is a service provider, and this is the profile it
speaks, narrow on purpose (the XML security is in :mod:`noust.core.accounts.saml_xml`):

- **Web Browser SSO, started here.** The AuthnRequest goes out over the HTTP-Redirect binding and
  the answer comes back to the assertion consumer service over HTTP-POST. A response nobody asked
  for (an identity-provider-initiated one) is refused: it has no request to answer, which is what
  makes a response replayed at another browser detectable at all.
- **Signed, by a key the operator configured.** The assertion must be signed, or the response
  that holds it, and the signature must refer to the element it is in. Encrypted assertions are
  refused: the connection is TLS, and a decryption key is one more secret to keep.
- **Every condition checked.** The issuer, the audience (this server's entity id), the destination
  and recipient (this server's consumer address), the request it answers, the time (with a stated
  tolerance) and the assertion's single use. A condition this server does not know is a refusal.
- **Then the admission every identity source shares**
  (:class:`~noust.core.accounts.federation.Admission`): who someone is is the identifier the
  provider guarantees, never their e-mail alone, and the groups they are in give roles on every
  sign-in. The provider is a first factor; the account's own second factor still follows.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import re
import secrets
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from xml.dom import minidom
from xml.sax.saxutils import escape, quoteattr

from noust.core.accounts import saml_xml as sx
from noust.core.accounts.federation import (
    ADMISSION_SETTINGS,
    Admission,
    apply_admission_setting,
    check_references,
    name_in_use,
    settle,
)
from noust.core.accounts.manager import AccountManager
from noust.core.accounts.oidc import Begun, Check
from noust.core.accounts.oidc_model import (
    IdentityClaims,
    OidcError,
    OidcRefused,
    check_endpoint_url,
    validate_name,
    validate_skew,
)
from noust.core.accounts.oidc_store import (
    FLOW_SECONDS,
    Identity,
    OidcStore,
    PendingFlow,
    digest,
)
from noust.core.accounts.saml_store import SamlProvider, SamlStore
from noust.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

NS_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
NS_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
NS_MD = "urn:oasis:names:tc:SAML:2.0:metadata"
STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BINDING_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
BINDING_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
CM_BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
NAMEID_EMAIL = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
NAMEID_TRANSIENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:transient"

#: The path of the assertion consumer service and of the metadata, under the console's address.
ACS_PATH = "/api/auth/saml/acs"
METADATA_PATH = "/api/auth/saml/metadata"

#: The settings :meth:`SamlManager.configure` accepts, beyond the admission ones.
SETTINGS: tuple[str, ...] = (
    "idp_metadata",
    "idp_entity_id",
    "sso_url",
    "certificates",
    "sp_entity_id",
    "subject_attribute",
    "email_attribute",
    "name_attribute",
    "username_attribute",
    "groups_attribute",
    "clock_skew_seconds",
    *ADMISSION_SETTINGS,
)

MAX_ATTRIBUTE_VALUES = 2000
MAX_CERTIFICATES = 6
_INSTANT = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|\+00:00)")
_KNOWN_CONDITIONS = frozenset({"AudienceRestriction", "OneTimeUse", "ProxyRestriction"})


class SamlRefused(sx.SamlXmlError):
    """A response is well-formed and signed, and says something this server does not accept."""


def parse_instant(text: str) -> float:
    """
    Args:
        text: A SAML instant: ``2026-10-17T00:12:30Z``, UTC.

    Returns:
        UNIX seconds.

    Raises:
        SamlRefused: When it is not an instant in UTC.
    """
    matched = _INSTANT.fullmatch((text or "").strip())
    if matched is None:
        raise SamlRefused(f"Not an instant in UTC: {text[:40]!r}")
    year, month, day, hour, minute, second = (int(part) for part in matched.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()
    except ValueError as exc:
        raise SamlRefused(f"Not an instant: {text[:40]!r}") from exc


def format_instant(seconds: float) -> str:
    """
    Args:
        seconds: UNIX seconds.

    Returns:
        The instant as SAML writes it.
    """
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Assertion:
    """
    What a verified assertion says, and nothing it does not.

    Attributes:
        id: The assertion's ID.
        name_id: The ``NameID`` value.
        name_id_format: Its format.
        attributes: Each attribute's values, by name.
        classes: The authentication context classes it reports.
        expires_at: When it could no longer be valid, UNIX seconds.
    """

    id: str
    name_id: str
    name_id_format: str
    attributes: dict[str, list[str]]
    classes: tuple[str, ...]
    expires_at: float

    def values(self, name: str) -> list[str]:
        """
        Args:
            name: An attribute's name.

        Returns:
            Its values; none when the assertion has none.
        """
        return self.attributes.get(name, []) if name else []


def _instant_of(element: minidom.Element, name: str) -> float | None:
    """
    Args:
        element: An element.
        name: An attribute that holds an instant.

    Returns:
        It as UNIX seconds, or None when the attribute is absent.
    """
    return parse_instant(element.getAttribute(name)) if element.hasAttribute(name) else None


def read_attributes(assertion: minidom.Element) -> dict[str, list[str]]:
    """
    Args:
        assertion: A verified assertion.

    Returns:
        Its attributes' text values by name and by friendly name, bounded in number.

    Raises:
        SamlRefused: When it has too many values.
    """
    found: dict[str, list[str]] = {}
    total = 0
    for statement in sx.children(assertion, NS_SAML, "AttributeStatement"):
        for attribute in sx.children(statement, NS_SAML, "Attribute"):
            names = [attribute.getAttribute("Name"), attribute.getAttribute("FriendlyName")]
            values: list[str] = []
            for value in sx.children(attribute, NS_SAML, "AttributeValue"):
                try:
                    text = sx.text_of(value)
                except sx.SamlXmlError:
                    continue
                values.append(text)
                total += 1
                if total > MAX_ATTRIBUTE_VALUES:
                    raise SamlRefused("The assertion has too many attribute values")
            for name in dict.fromkeys(n for n in names if n):
                found.setdefault(name, []).extend(values)
    return found


def read_response(
    provider: SamlProvider,
    data: bytes,
    *,
    request_id: str,
    acs_url: str,
    audience: str,
    now: float,
) -> Assertion:
    """
    Verify a SAML Response and read the one assertion in it.

    Everything is checked here and nothing is stored: the XML security, the response's own
    conditions and the assertion's. What comes back is read from the element that was verified.

    Args:
        provider: The identity provider the sign-in was started with.
        data: The response's XML, already base64-decoded.
        request_id: The ID of the AuthnRequest this answers.
        acs_url: This server's assertion consumer address, as the request gave it.
        audience: This server's entity id, as the request gave it.
        now: The current time, UNIX seconds.

    Returns:
        The assertion's data.

    Raises:
        SamlXmlError: When the message is not one this server reads or its signature is not
            good; :class:`SamlRefused` when it is, and a condition fails.
    """
    root = sx.root_of(sx.parse(data))
    if root.namespaceURI != NS_SAMLP or root.localName != "Response":
        raise SamlRefused("The message is not a SAML Response")
    sx.check_unique_ids(root)
    everything = sx.descendants(root)
    if any(e.localName in ("EncryptedAssertion", "EncryptedID") for e in everything):
        raise SamlRefused(
            "The response carries encrypted data, which this server does not read",
            "Ask the identity provider to sign assertions and not to encrypt them: the connection "
            "is TLS already.",
        )
    assertions = [e for e in everything if e.namespaceURI == NS_SAML and e.localName == "Assertion"]
    if len(assertions) != 1 or assertions[0].parentNode is not root:
        raise SamlRefused("The response must hold exactly one assertion, directly in it")
    assertion = assertions[0]

    skew = float(provider.clock_skew_seconds)
    certificates = sx.load_certificates(provider.certificates)
    response_signed = bool(sx.children(root, sx.NS_DS, "Signature"))
    assertion_signed = bool(sx.children(assertion, sx.NS_DS, "Signature"))
    if not (response_signed or assertion_signed):
        raise SamlRefused("Neither the response nor the assertion is signed")
    if response_signed:
        sx.verify_signed(root, certificates)
    if assertion_signed:
        sx.verify_signed(assertion, certificates)

    if root.getAttribute("InResponseTo") != request_id:
        raise SamlRefused("The response answers another request, or none of ours")
    destination = root.getAttribute("Destination")
    if destination and destination != acs_url:
        raise SamlRefused("The response is addressed to another consumer")
    if response_signed and not destination:
        raise SamlRefused("A signed response must say where it is addressed")
    issuer_of_response = sx.only_child(root, NS_SAML, "Issuer")
    if issuer_of_response is not None and sx.text_of(issuer_of_response) != provider.idp_entity_id:
        raise SamlRefused("The response is from another issuer than the identity provider")
    instant = _instant_of(root, "IssueInstant")
    if instant is None or instant > now + skew:
        raise SamlRefused("The response was issued in the future, or says when it was not")

    status = sx.only_child(root, NS_SAMLP, "Status")
    status_code = sx.only_child(status, NS_SAMLP, "StatusCode") if status is not None else None
    if status_code is None or status_code.getAttribute("Value") != STATUS_SUCCESS:
        value = status_code.getAttribute("Value") if status_code is not None else "none"
        raise SamlRefused(
            f"The identity provider did not sign the person in: {value.rsplit(':', 1)[-1]}"
        )

    if assertion.getAttribute("Version") != "2.0":
        raise SamlRefused("The assertion is not SAML 2.0")
    issuer = sx.only_child(assertion, NS_SAML, "Issuer")
    if issuer is None or sx.text_of(issuer) != provider.idp_entity_id:
        raise SamlRefused("The assertion is from another issuer than the identity provider")
    assertion_id = assertion.getAttribute("ID")
    if not assertion_id:
        raise SamlRefused("The assertion has no ID")
    issued = _instant_of(assertion, "IssueInstant")
    if issued is None or issued > now + skew:
        raise SamlRefused("The assertion was issued in the future, or says when it was not")

    expires = _check_conditions(assertion, audience, now, skew)
    expires = max(expires, _check_subject(assertion, acs_url, request_id, now, skew))
    name_id_element = sx.only_child(
        sx.only_child(assertion, NS_SAML, "Subject") or assertion, NS_SAML, "NameID"
    )
    name_id = sx.text_of(name_id_element) if name_id_element is not None else ""
    classes = tuple(
        sx.text_of(c)
        for statement in sx.children(assertion, NS_SAML, "AuthnStatement")
        for context in sx.children(statement, NS_SAML, "AuthnContext")
        for c in sx.children(context, NS_SAML, "AuthnContextClassRef")
    )
    if not sx.children(assertion, NS_SAML, "AuthnStatement"):
        raise SamlRefused("The assertion says nothing about how the person authenticated")
    return Assertion(
        id=assertion_id,
        name_id=name_id,
        name_id_format=name_id_element.getAttribute("Format")
        if name_id_element is not None
        else "",
        attributes=read_attributes(assertion),
        classes=classes,
        expires_at=expires + skew,
    )


def _check_conditions(assertion: minidom.Element, audience: str, now: float, skew: float) -> float:
    """
    Args:
        assertion: The verified assertion.
        audience: This server's entity id.
        now: The current time.
        skew: The clock tolerance in seconds.

    Returns:
        When the conditions stop being valid (``now`` when they never say).

    Raises:
        SamlRefused: When they are not met, there are none, or one is unknown.
    """
    conditions = sx.only_child(assertion, NS_SAML, "Conditions")
    if conditions is None:
        raise SamlRefused("The assertion has no conditions, so it is not limited to this server")
    unknown = {e.localName for e in sx.elements(conditions) if e.localName not in _KNOWN_CONDITIONS}
    if unknown:
        raise SamlRefused(
            f"The assertion has a condition this server does not know: {sorted(unknown)[0]}"
        )
    start = _instant_of(conditions, "NotBefore")
    end = _instant_of(conditions, "NotOnOrAfter")
    if start is not None and start > now + skew:
        raise SamlRefused("The assertion is not valid yet")
    if end is None or end <= now - skew:
        raise SamlRefused("The assertion has expired, or says nothing of when it does")
    restrictions = sx.children(conditions, NS_SAML, "AudienceRestriction")
    if not restrictions:
        raise SamlRefused("The assertion is not restricted to an audience")
    for restriction in restrictions:
        names = [sx.text_of(a) for a in sx.children(restriction, NS_SAML, "Audience")]
        if audience not in names:
            raise SamlRefused("The assertion is for another audience than this server")
    return end


def _check_subject(
    assertion: minidom.Element, acs_url: str, request_id: str, now: float, skew: float
) -> float:
    """
    Args:
        assertion: The verified assertion.
        acs_url: This server's consumer address.
        request_id: The request it answers.
        now: The current time.
        skew: The clock tolerance in seconds.

    Returns:
        When the bearer confirmation stops being valid.

    Raises:
        SamlRefused: When no bearer confirmation is addressed to this server for this request.
    """
    subject = sx.only_child(assertion, NS_SAML, "Subject")
    if subject is None:
        raise SamlRefused("The assertion has no subject")
    name_id = sx.only_child(subject, NS_SAML, "NameID")
    if name_id is None or not sx.text_of(name_id):
        raise SamlRefused("The assertion does not name the person")
    for confirmation in sx.children(subject, NS_SAML, "SubjectConfirmation"):
        if confirmation.getAttribute("Method") != CM_BEARER:
            continue
        data = sx.only_child(confirmation, NS_SAML, "SubjectConfirmationData")
        if data is None:
            continue
        if (
            data.getAttribute("Recipient") != acs_url
            or data.getAttribute("InResponseTo") != request_id
        ):
            continue
        until = _instant_of(data, "NotOnOrAfter")
        start = _instant_of(data, "NotBefore")
        if until is None or until <= now - skew or (start is not None and start > now + skew):
            continue
        return until
    raise SamlRefused(
        "No bearer confirmation in the assertion is for this server, this request and now"
    )


# ------------------------------------------------------------------ requests


def authn_request(
    request_id: str, *, sp_entity_id: str, acs_url: str, sso_url: str, now: float
) -> str:
    """
    Args:
        request_id: The new request's ID.
        sp_entity_id: This server's entity id.
        acs_url: Where the answer is to be posted.
        sso_url: The identity provider's sign-in address.
        now: The current time.

    Returns:
        The AuthnRequest, unsigned.
    """
    return (
        f'<samlp:AuthnRequest xmlns:samlp="{NS_SAMLP}" xmlns:saml="{NS_SAML}" '
        f'ID={quoteattr(request_id)} Version="2.0" IssueInstant="{format_instant(now)}" '
        f'Destination={quoteattr(sso_url)} ProtocolBinding="{BINDING_POST}" '
        f"AssertionConsumerServiceURL={quoteattr(acs_url)}>"
        f"<saml:Issuer>{escape(sp_entity_id)}</saml:Issuer>"
        f'<samlp:NameIDPolicy AllowCreate="true"/>'
        f"</samlp:AuthnRequest>"
    )


def redirect_url(sso_url: str, request_xml: str, relay_state: str) -> str:
    """
    Args:
        sso_url: The identity provider's sign-in address.
        request_xml: The AuthnRequest.
        relay_state: What comes back with the answer.

    Returns:
        The address to send the browser to: the request deflated, base64 and URL-encoded.
    """
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = compressor.compress(request_xml.encode("utf-8")) + compressor.flush()
    encoded = quote(base64.b64encode(packed).decode("ascii"), safe="")
    joiner = "&" if urlsplit(sso_url).query else "?"
    return f"{sso_url}{joiner}SAMLRequest={encoded}&RelayState={quote(relay_state, safe='')}"


def decode_response(value: str) -> bytes:
    """
    Args:
        value: The ``SAMLResponse`` field the browser posted: base64.

    Returns:
        The XML bytes.

    Raises:
        SamlRefused: When it is not base64 or is longer than is accepted.
    """
    compact = re.sub(r"\s+", "", value or "")
    if not compact or len(compact) > (sx.MAX_XML_BYTES * 4) // 3 + 8:
        raise SamlRefused("The SAML response is empty or longer than is accepted")
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SamlRefused("The SAML response is not base64") from exc


# -------------------------------------------------------------------- metadata


@dataclass(frozen=True)
class IdpMetadata:
    """
    What an identity provider's metadata says that matters here.

    Attributes:
        entity_id: Its entity id.
        sso_url: Its HTTP-Redirect sign-in address.
        certificates: Its signing certificates, as base64.
    """

    entity_id: str
    sso_url: str
    certificates: tuple[str, ...]


def read_idp_metadata(data: bytes) -> IdpMetadata:
    """
    Read an identity provider's metadata document.

    Not verified: the document is what the operator pasted or fetched from the provider's own
    address, and what it says is shown for them to check. A signature on it is ignored.

    Args:
        data: The metadata XML.

    Returns:
        The entity id, the sign-in address and the signing certificates.

    Raises:
        ValidationError: When it is not an identity provider's metadata or lacks what a sign-in needs.
    """
    try:
        root = sx.root_of(sx.parse(data))
        if root.namespaceURI == NS_MD and root.localName == "EntitiesDescriptor":
            found = sx.children(root, NS_MD, "EntityDescriptor")
            if len(found) != 1:
                raise sx.SamlXmlError(
                    "The metadata describes several entities; give the identity provider's own"
                )
            root = found[0]
        if root.namespaceURI != NS_MD or root.localName != "EntityDescriptor":
            raise sx.SamlXmlError("The metadata is not an EntityDescriptor")
        descriptor = sx.only_child(root, NS_MD, "IDPSSODescriptor")
        if descriptor is None:
            raise sx.SamlXmlError("The metadata does not describe an identity provider")
        sso = next(
            (
                s.getAttribute("Location")
                for s in sx.children(descriptor, NS_MD, "SingleSignOnService")
                if s.getAttribute("Binding") == BINDING_REDIRECT
            ),
            "",
        )
        certificates: list[str] = []
        for key in sx.children(descriptor, NS_MD, "KeyDescriptor"):
            if key.getAttribute("use") not in ("", "signing"):
                continue
            for info in sx.children(key, sx.NS_DS, "KeyInfo"):
                for x509 in sx.children(info, sx.NS_DS, "X509Data"):
                    for cert in sx.children(x509, sx.NS_DS, "X509Certificate"):
                        certificates.append(re.sub(r"\s+", "", sx.text_of(cert)))
        entity_id = root.getAttribute("entityID")
    except sx.SamlXmlError as exc:
        raise ValidationError(
            f"The identity provider's metadata could not be read: {exc.message}",
            details="Give the XML the provider publishes, or set the entity id, the sign-in URL and the certificate yourself.",
            field="idp_metadata",
        ) from exc
    if not entity_id or not sso or not certificates:
        raise ValidationError(
            "The metadata lacks an entity id, a sign-in address for the HTTP-Redirect binding or a signing certificate",
            details="Set the missing settings yourself.",
            field="idp_metadata",
        )
    return IdpMetadata(entity_id, sso, tuple(dict.fromkeys(certificates)))


def sp_metadata(provider: SamlProvider, *, origin: str) -> str:
    """
    Args:
        provider: A provider.
        origin: The console's address, ``https://host[:port]``.

    Returns:
        This server's metadata for that provider: what to register with the identity provider.
    """
    entity = entity_id_of(provider, origin)
    acs = f"{origin}{ACS_PATH}"
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<md:EntityDescriptor xmlns:md="{NS_MD}" entityID={quoteattr(entity)}>'
        f'<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        f'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        f"<md:NameIDFormat>urn:oasis:names:tc:SAML:2.0:nameid-format:persistent</md:NameIDFormat>"
        f"<md:NameIDFormat>{NAMEID_EMAIL}</md:NameIDFormat>"
        f"<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified</md:NameIDFormat>"
        f'<md:AssertionConsumerService Binding="{BINDING_POST}" Location={quoteattr(acs)} '
        f'index="0" isDefault="true"/>'
        f"</md:SPSSODescriptor></md:EntityDescriptor>"
    )


@dataclass(frozen=True)
class CertificateSummary:
    """
    What an operator compares a certificate by.

    Attributes:
        subject: Its subject.
        fingerprint: Its SHA-256 fingerprint, hex.
        not_after: When it stops being valid, UNIX seconds.
    """

    subject: str
    fingerprint: str
    not_after: float


def describe_certificates(provider: SamlProvider) -> list[CertificateSummary]:
    """
    Args:
        provider: A provider.

    Returns:
        Each signing certificate, described. Empty when they cannot be read.
    """
    try:
        certificates = sx.load_certificates(provider.certificates)
    except sx.SamlXmlError:
        return []
    from cryptography.hazmat.primitives import hashes

    return [
        CertificateSummary(
            subject=certificate.subject.rfc4514_string(),
            fingerprint=certificate.fingerprint(hashes.SHA256()).hex(),
            not_after=sx.not_valid_after(certificate).timestamp(),
        )
        for certificate in certificates
    ]


def entity_id_of(provider: SamlProvider, origin: str) -> str:
    """
    Args:
        provider: A provider.
        origin: The console's address.

    Returns:
        The entity id Noust presents to it: the operator's, or the address of its metadata.
    """
    return provider.sp_entity_id or f"{origin}{METADATA_PATH}/{provider.name}"


def _printable(value: object, what: str, *, limit: int = 1024, required: bool = True) -> str:
    """
    Args:
        value: A setting as typed.
        what: Its name, for the message.
        limit: The longest it may be.
        required: Refuse an empty one.

    Returns:
        It, trimmed.

    Raises:
        ValidationError: When it is empty and required, too long, or has spaces or control characters.
    """
    text = str(value or "").strip()
    if (
        (required and not text)
        or len(text) > limit
        or any(ord(c) <= 32 or ord(c) == 127 for c in text)
    ):
        raise ValidationError(
            f"Invalid {what}",
            details=f"No spaces or control characters, at most {limit} characters.",
            field=what,
        )
    return text


def _normalised_certificates(value: object) -> tuple[str, ...]:
    """
    Args:
        value: A list of certificates (PEM or bare base64), or one text with several.

    Returns:
        The certificates in PEM, read and checked.

    Raises:
        ValidationError: When one cannot be read, is too weak, or there are too many.
    """
    if isinstance(value, str):
        pieces = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", value, re.S)
        items: list[str] = pieces or [value]
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        raise ValidationError("certificates must be a list", field="certificates")
    try:
        loaded = sx.load_certificates(items)
    except sx.SamlXmlError as exc:
        raise ValidationError(exc.message, field="certificates") from exc
    if len(loaded) > MAX_CERTIFICATES:
        raise ValidationError(f"At most {MAX_CERTIFICATES} certificates", field="certificates")
    from cryptography.hazmat.primitives import serialization

    return tuple(c.public_bytes(serialization.Encoding.PEM).decode("ascii") for c in loaded)


class SamlManager:
    """
    Configure SAML identity providers, start their sign-ins and decide who they let in.

    Args:
        store: The store; the process-wide one by default.
        accounts: The account manager; one over the same store by default.
        saml_store: The persistence; one over the same store by default.
        clock: The time source, replaceable in tests.
    """

    def __init__(
        self,
        store: object | None = None,
        *,
        accounts: AccountManager | None = None,
        saml_store: SamlStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.time
        self.accounts = accounts or AccountManager(store, clock=clock)  # type: ignore[arg-type]
        self.data = saml_store or SamlStore(store, clock=clock)  # type: ignore[arg-type]
        self.sso = OidcStore(store, clock=clock)  # type: ignore[arg-type]
        self.admission = Admission(self.accounts, self.sso, self._clock)

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    # -------------------------------------------------------------- configuration

    def providers(self) -> list[SamlProvider]:
        """
        Returns:
            Every configured provider.
        """
        return self.data.list_providers()

    def provider(self, name: str) -> SamlProvider | None:
        """
        Args:
            name: A provider's name.

        Returns:
            The provider, or None.
        """
        return self.data.get_provider(name)

    def require_provider(self, name: str) -> SamlProvider:
        """
        Args:
            name: A provider's name.

        Returns:
            The provider.

        Raises:
            ValidationError: When there is none.
        """
        found = self.data.get_provider(name)
        if found is None:
            raise ValidationError(
                f"No SAML provider named {name!r}",
                details="List them with 'noust auth saml show'.",
                field="name",
            )
        return found

    def public_providers(self) -> list[dict[str, str]]:
        """
        Returns:
            The name and label of each enabled provider: what the sign-in page offers.
        """
        return [
            {"name": found.name, "label": found.label}
            for found in self.data.list_providers()
            if found.enabled
        ]

    def configure(
        self, name: str, changes: Mapping[str, object], *, actor: str | None = None
    ) -> SamlProvider:
        """
        Add a provider, or change one: only the settings named are touched.

        Args:
            name: The provider's name.
            changes: Setting to value, from :data:`SETTINGS`. ``idp_metadata`` (the provider's
                metadata XML) fills the entity id, the sign-in address and the certificates,
                which the settings given beside it override.
            actor: Who is making the change, for the record.

        Returns:
            The provider as it is now.

        Raises:
            ValidationError: When a setting is refused or names something that does not exist;
                nothing is written then.
        """
        key = validate_name(name)
        unknown = sorted(set(changes) - set(SETTINGS))
        if unknown:
            raise ValidationError(
                f"Unknown setting: {', '.join(unknown)}",
                details=f"The settings are: {', '.join(SETTINGS)}.",
            )
        given = dict(changes)
        metadata = given.pop("idp_metadata", None)
        if metadata:
            read = read_idp_metadata(str(metadata).encode("utf-8"))
            given.setdefault("idp_entity_id", read.entity_id)
            given.setdefault("sso_url", read.sso_url)
            given.setdefault("certificates", list(read.certificates))
        existing = self.data.get_provider(key)
        if existing is None:
            missing = [s for s in ("idp_entity_id", "sso_url", "certificates") if not given.get(s)]
            if missing:
                raise ValidationError(
                    "A new SAML provider needs the identity provider's entity id, sign-in URL and certificate",
                    details="Pass its metadata (--metadata-file), or --entity-id, --sso-url and --certificate.",
                    field=missing[0],
                )
            if name_in_use(self.accounts.store, key, except_protocol="saml"):
                raise ValidationError(
                    f"The name {key!r} is already a sign-in provider's",
                    details="Names are shared by every way of signing in; pick another.",
                    field="name",
                )
        now = self.now()
        base = existing or SamlProvider(
            name=key, label=key, idp_entity_id="", sso_url="", created_at=now, created_by=actor
        )
        provider = self._apply(base, given)
        if not provider.certificates:
            raise ValidationError(
                "A SAML provider needs at least one signing certificate", field="certificates"
            )
        provider = replace(provider, updated_at=now, updated_by=actor)
        check_references(provider, self.accounts)
        if existing is not None and existing.enabled and not provider.enabled:
            from noust.core.accounts.local_login import refuse_to_lose_a_way_in

            refuse_to_lose_a_way_in(
                self.accounts.store,
                without_provider=existing.name,
                action=f"Disabling the provider {existing.name!r}",
            )
        self.data.save_provider(provider)
        return provider

    def _apply(self, provider: SamlProvider, changes: Mapping[str, object]) -> SamlProvider:
        """
        Args:
            provider: The provider before.
            changes: The settings to change.

        Returns:
            The provider after, every changed setting validated.

        Raises:
            ValidationError: When a setting, or a combination, is refused.
        """
        updated = provider
        for setting, value in changes.items():
            if setting in ADMISSION_SETTINGS:
                updated = apply_admission_setting(updated, setting, value)
            elif setting == "idp_entity_id":
                updated = replace(updated, idp_entity_id=_printable(value, "idp_entity_id"))
            elif setting == "sso_url":
                url = check_endpoint_url(str(value or ""), "sign-in URL", field_name="sso_url")
                updated = replace(updated, sso_url=url)
            elif setting == "certificates":
                updated = replace(updated, certificates=_normalised_certificates(value))
            elif setting == "sp_entity_id":
                updated = replace(
                    updated, sp_entity_id=_printable(value, "sp_entity_id", required=False)
                )
            elif setting == "subject_attribute":
                updated = replace(
                    updated, subject_attribute=_printable(value, setting, limit=256, required=False)
                )
            elif setting == "email_attribute":
                updated = replace(
                    updated, email_attribute=_printable(value, setting, limit=256, required=False)
                )
            elif setting == "name_attribute":
                updated = replace(
                    updated, name_attribute=_printable(value, setting, limit=256, required=False)
                )
            elif setting == "username_attribute":
                updated = replace(
                    updated,
                    username_attribute=_printable(value, setting, limit=256, required=False),
                )
            elif setting == "groups_attribute":
                updated = replace(
                    updated, groups_attribute=_printable(value, setting, limit=256, required=False)
                )
            elif setting == "clock_skew_seconds":
                updated = replace(updated, clock_skew_seconds=validate_skew(value))
        return settle(updated, self.accounts)

    def remove(self, name: str) -> SamlProvider:
        """
        Remove a provider. People linked to it keep their links but can no longer sign in through it.

        Args:
            name: The provider's name.

        Returns:
            The provider as it was.

        Raises:
            ValidationError: When there is none.
            LocalLoginRefused: While local sign-in is off and this is the last way in.
        """
        from noust.core.accounts.local_login import refuse_to_lose_a_way_in

        found = self.require_provider(name)
        refuse_to_lose_a_way_in(
            self.accounts.store,
            without_provider=found.name,
            action=f"Removing the provider {found.name!r}",
        )
        self.data.delete_provider(found.name)
        return found

    def test(self, name: str, *, origin: str | None = None) -> list[Check]:
        """
        Look at a provider's settings the way a sign-in would use them, without signing anybody in.

        Args:
            name: The provider's name.
            origin: The console's address, to look at what the provider is told about this server.

        Returns:
            Each thing looked at: its certificates (and how long they are good for), its
            sign-in address, and the addresses to register with it.

        Raises:
            ValidationError: When there is no such provider.
        """
        provider = self.require_provider(name)
        checks: list[Check] = []
        try:
            certificates = sx.load_certificates(provider.certificates)
            now = datetime.fromtimestamp(self.now(), tz=timezone.utc)
            for certificate in certificates:
                left = (sx.not_valid_after(certificate) - now).days
                subject = certificate.subject.rfc4514_string() or "a certificate"
                checks.append(
                    Check(
                        "certificate",
                        left >= 0,
                        f"{subject}: "
                        + (
                            f"good for {left} more days"
                            if left >= 0
                            else f"expired {-left} days ago"
                        ),
                    )
                )
        except sx.SamlXmlError as exc:
            checks.append(Check("certificate", False, exc.message))
        try:
            check_endpoint_url(provider.sso_url, "sign-in URL")
            checks.append(Check("sign-in address", True, provider.sso_url))
        except ValidationError as exc:
            checks.append(Check("sign-in address", False, exc.message))
        if origin is not None:
            checks.append(Check("entity id", True, entity_id_of(provider, origin)))
            secure = origin.startswith("https://")
            checks.append(
                Check(
                    "assertion consumer address",
                    secure,
                    f"{origin}{ACS_PATH}"
                    if secure
                    else (
                        f"{origin}{ACS_PATH}: over plain HTTP a browser does not send the sign-in "
                        "cookie with the identity provider's POST, so no sign-in can finish; "
                        "serve the console over TLS"
                    ),
                )
            )
        return checks

    # -------------------------------------------------------------------- sign-in

    def begin(
        self, name: str, *, origin: str, client_ip: str, next_path: str | None = None
    ) -> Begun:
        """
        Start a sign-in: remember the request, and say where to send the browser.

        Args:
            name: The provider's name.
            origin: The console's address, ``https://host[:port]``.
            client_ip: Where the person is.
            next_path: Where they were going; reduced to a path of this console.

        Returns:
            The address to send the browser to, and the secret for its cookie.

        Raises:
            OidcError: ``unknown_provider`` or ``provider_disabled`` (shown as unavailable).
            OidcRefused: ``no_accounts`` while the server has none.
        """
        from noust.core.accounts.oidc import safe_next

        provider = self.data.get_provider(name)
        if provider is None or not provider.enabled:
            raise OidcError(
                "unknown_provider" if provider is None else "provider_disabled",
                "That sign-in is not available",
                public="unavailable",
            )
        if not self.accounts.any_exist():
            raise Admission.no_accounts()
        request_id = "_" + secrets.token_hex(20)
        relay = secrets.token_urlsafe(32)
        binding = secrets.token_urlsafe(32)
        entity = entity_id_of(provider, origin)
        acs = f"{origin}{ACS_PATH}"
        self.sso.begin_flow(
            PendingFlow(
                state_hash=digest(relay),
                provider=provider.name,
                nonce=request_id,
                # The column holds an OpenID Connect sign-in's PKCE verifier; a SAML one keeps the
                # entity id the assertion must be for in it.
                code_verifier=entity,
                binding_hash=digest(binding),
                redirect_uri=acs,
                next_path=safe_next(next_path),
                client_ip=client_ip,
                expires_at=self.now() + FLOW_SECONDS,
            )
        )
        request = authn_request(
            request_id, sp_entity_id=entity, acs_url=acs, sso_url=provider.sso_url, now=self.now()
        )
        return Begun(
            url=redirect_url(provider.sso_url, request, relay),
            binding=binding,
            expires_in=FLOW_SECONDS,
        )

    def finish(
        self, *, response: str, relay_state: str, binding: str | None, client_ip: str
    ) -> SamlAnswer:
        """
        Take the identity provider's answer: verify it, admit the person, and issue the ticket.

        Args:
            response: The ``SAMLResponse`` field: base64.
            relay_state: The ``RelayState`` field.
            binding: The secret of the browser's cookie.
            client_ip: Where the answer came from.

        Returns:
            The ticket for the console to collect, and what happened.

        Raises:
            OidcError: The sign-in is unknown, expired, spent or from another browser, or the
                response is not good (``saml_invalid``) or says the person did not sign in
                (``idp_status``).
            OidcRefused: The provider vouched for the person and the policy says no.
        """
        key = digest(relay_state or "")
        flow = self.sso.get_flow(key)
        if flow is None:
            raise OidcError(
                "unknown_state",
                "This sign-in is unknown, expired or already answered",
                public="expired",
            )
        if not hmac.compare_digest(flow.binding_hash, digest(binding or "")):
            raise OidcError(
                "binding",
                "This answer came back to a different browser than the one that began",
                public="failed",
            )
        if self.sso.spend_flow(key) is None:
            raise OidcError(
                "unknown_state",
                "This sign-in is unknown, expired or already answered",
                public="expired",
            )
        provider = self.data.get_provider(flow.provider)
        if provider is None or not provider.enabled:
            raise OidcError(
                "provider_disabled", "That sign-in is not available", public="unavailable"
            )
        try:
            assertion = read_response(
                provider,
                decode_response(response),
                request_id=flow.nonce,
                acs_url=flow.redirect_uri,
                audience=flow.code_verifier,
                now=self.now(),
            )
        except SamlRefused as exc:
            denied = exc.message.startswith("The identity provider did not sign the person in")
            raise OidcError(
                "idp_status" if denied else "saml_invalid",
                exc.message,
                public="denied" if denied else "failed",
            ) from exc
        except sx.SamlXmlError as exc:
            raise OidcError("saml_invalid", exc.message, public="failed") from exc
        fingerprint = hashlib.sha256(f"{provider.name}:{assertion.id}".encode()).hexdigest()
        if not self.data.remember_assertion(fingerprint, assertion.expires_at):
            raise OidcError("replay", "This assertion was already accepted once", public="failed")
        person = self._person(provider, assertion)
        admitted = self.admission.admit(provider, person, flow, client_ip)
        return SamlAnswer(
            ticket=admitted.ticket,
            next_path=flow.next_path,
            account=admitted.account,
            provider=provider,
            notes=admitted.notes,
        )

    @staticmethod
    def _person(provider: SamlProvider, assertion: Assertion) -> IdentityClaims:
        """
        Args:
            provider: The provider.
            assertion: What it asserted.

        Returns:
            Who the assertion says the person is.

        Raises:
            OidcRefused: ``no_subject`` when the attribute that identifies them is empty.
        """
        if provider.subject_attribute:
            values = assertion.values(provider.subject_attribute)
            subject = values[0].strip() if values else ""
        elif assertion.name_id_format == NAMEID_TRANSIENT:
            # A transient NameID is new at every sign-in: taken for who somebody is, it would make
            # a new person (and, with provisioning on, a new account) each time.
            raise OidcRefused(
                "transient_subject",
                "The identity provider sends a transient NameID, which changes at every sign-in",
                details=(
                    "Have it send a persistent NameID, or name an attribute that never changes "
                    "with --subject-attribute."
                ),
            )
        else:
            subject = assertion.name_id.strip()
        if not subject or len(subject) > 255 or any(ord(c) < 32 for c in subject):
            raise OidcRefused(
                "no_subject",
                "The identity provider did not say who the person is in a way this server can keep",
                details=(
                    "Set the subject attribute, or have the provider send a persistent NameID."
                ),
            )
        mail = assertion.values(provider.email_attribute)
        email = (mail[0] if mail else "").strip().lower()
        if not email and assertion.name_id_format == NAMEID_EMAIL:
            email = assertion.name_id.strip().lower()
        name = assertion.values(provider.name_attribute)
        login = assertion.values(provider.username_attribute)
        return IdentityClaims(
            issuer=provider.idp_entity_id,
            subject=subject,
            email=email or None,
            email_verified=None,
            name=name[0].strip() if name else None,
            username=login[0].strip() if login else None,
            groups=tuple(
                dict.fromkeys(
                    g.strip() for g in assertion.values(provider.groups_attribute) if g.strip()
                )
            ),
            amr=assertion.classes,
        )

    def link(self, name: str, username: str, subject: str, *, actor: str | None = None) -> Identity:
        """
        Link an account to a person at a provider by hand.

        Args:
            name: The provider.
            username: The account.
            subject: The identifier the provider guarantees for the person (their NameID).
            actor: Who links it, for the record.

        Returns:
            The identity.

        Raises:
            ValidationError: When the subject is empty or too long.
            OidcError: ``already_linked`` when that person is linked already.
        """
        provider = self.require_provider(name)
        account = self.accounts.require(username)
        text = (subject or "").strip()
        if not text or len(text) > 255 or any(ord(char) < 32 for char in text):
            raise ValidationError(
                "Invalid subject",
                details="Copy the provider's identifier for the person (their NameID, up to 255 characters).",
                field="subject",
            )
        return self.sso.link_identity(
            account.id,
            provider=provider.name,
            issuer=provider.idp_entity_id,
            subject=text,
            email=None,
            created_by=actor,
            protocol=provider.protocol,
        )


@dataclass(frozen=True)
class SamlAnswer:
    """
    A sign-in the provider vouched for and Noust admitted.

    Attributes:
        ticket: The single-use handle for the console to collect.
        next_path: Where the console goes once signed in.
        account: The account.
        provider: The provider.
        notes: What happened on the way.
    """

    ticket: str
    next_path: str
    account: object
    provider: SamlProvider
    notes: tuple[str, ...]


__all__ = ["SamlManager", "SamlRefused", "read_response"]
