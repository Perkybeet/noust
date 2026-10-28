# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_saml.py`` that other tests build on."""

from __future__ import annotations

from tests.support.saml_support import make_identity

ISSUER = "https://idp.acme.test/metadata"
SSO = "https://idp.acme.test/sso"

METADATA = """<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="https://idp.acme.test/metadata">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data>
      <ds:X509Certificate>{cert}</ds:X509Certificate>
    </ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
    <md:KeyDescriptor use="encryption"><ds:KeyInfo><ds:X509Data>
      <ds:X509Certificate>ZW5jcnlwdGlvbg==</ds:X509Certificate>
    </ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
        Location="https://idp.acme.test/post"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://idp.acme.test/sso"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>"""


def metadata_xml() -> str:
    _key, pem = make_identity()
    body = "".join(pem.strip().splitlines()[1:-1])
    return METADATA.format(cert=body)
