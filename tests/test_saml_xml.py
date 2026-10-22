# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The XML security core of SAML (checklist I-20): parsing, canonicalisation and signatures.

Three kinds of evidence, because a canonicaliser that agrees with its own signer proves little:
cases worked out by hand from the W3C definition, a comparison with the standard library's own
(independent) canonicaliser, and signatures made by an identity provider that is then asked to
sign wrongly in every way a wrapped or forged document can.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from noust.core.accounts import saml_xml as sx
from noust.core.accounts.saml_xml import SamlXmlError
from tests import saml_support as idp


def root(xml: str):
    return sx.parse(xml.encode()).documentElement


def canon(xml: str, **kwargs) -> str:
    return sx.canonicalize(root(xml), **kwargs).decode()


class TestParsing:
    def test_a_plain_document_is_read(self) -> None:
        assert root('<?xml version="1.0" encoding="UTF-8"?><a><b/></a>').tagName == "a"

    @pytest.mark.parametrize(
        ("document", "message"),
        [
            ('<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>', "DTD"),
            ("<!doctype a><a/>", "DTD"),
            ('<!ENTITY lol "lol"><a/>', "DTD"),
            ("<a><!-- x --></a>", "comment"),
            ("<a><?pi target?></a>", "processing instruction"),
            ('<?xml version="1.0" encoding="UTF-16"?><a/>', "encoding"),
            ("﻿<a/>", "byte order mark"),
            ("<a>\x00</a>", "NUL"),
            ("<a><b></a>", "not well-formed"),
            ("", "not well-formed"),
        ],
    )
    def test_what_a_signed_message_never_needs_is_refused(
        self, document: str, message: str
    ) -> None:
        with pytest.raises(SamlXmlError, match=message):
            sx.parse(document.encode())

    def test_a_billion_laughs_never_gets_to_be_expanded(self) -> None:
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
            "<lolz>&lol3;</lolz>"
        )

        with pytest.raises(SamlXmlError, match="DTD"):
            sx.parse(bomb.encode())

    def test_an_external_entity_is_not_read(self) -> None:
        xxe = (
            '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>'
        )

        with pytest.raises(SamlXmlError):
            sx.parse(xxe.encode())

    def test_bytes_that_are_not_utf8_are_refused(self) -> None:
        with pytest.raises(SamlXmlError, match="UTF-8"):
            sx.parse(b"<a>\xff\xfe</a>")

    def test_a_message_longer_than_is_accepted_is_refused_unread(self) -> None:
        with pytest.raises(SamlXmlError, match="longer"):
            sx.parse(b"<a>" + b"x" * sx.MAX_XML_BYTES + b"</a>")

    def test_a_comment_cannot_split_a_name_in_two_readings(self) -> None:
        # user@evil.test<!---->.attacker.test: one parser reads the part before the comment.
        with pytest.raises(SamlXmlError, match="comment"):
            sx.parse(b"<NameID>user@evil.test<!---->.attacker.test</NameID>")

    def test_text_in_pieces_is_one_value(self) -> None:
        element = root("<a>one<![CDATA[two]]>three</a>")

        assert sx.text_of(element) == "onetwothree"

    def test_text_with_an_element_inside_is_refused(self) -> None:
        with pytest.raises(SamlXmlError, match="inside"):
            sx.text_of(root("<a>one<b/>two</a>"))

    def test_two_children_where_one_is_due_are_refused_not_chosen_between(self) -> None:
        with pytest.raises(SamlXmlError, match="2 b"):
            sx.only_child(root('<a xmlns="urn:x"><b/><b/></a>'), "urn:x", "b")

    def test_an_id_used_twice_is_refused(self) -> None:
        with pytest.raises(SamlXmlError, match="twice"):
            sx.check_unique_ids(root('<a ID="x"><b ID="y"/><c ID="x"/></a>'))
        sx.check_unique_ids(root('<a ID="x"><b ID="y"/></a>'))


class TestCanonicalisation:
    def test_a_namespaced_document_by_the_definition(self) -> None:
        # a uses the default namespace; b:x uses b and, by its attribute, c. plain has no
        # namespace and sorts before c:attr (the empty URI first). CDATA becomes escaped text.
        document = (
            '<a xmlns="urn:a" xmlns:b="urn:b" xmlns:c="urn:c">'
            '<b:x c:attr="1" plain="2">t&amp;<![CDATA[<u>]]></b:x></a>'
        )

        assert canon(document) == (
            '<a xmlns="urn:a"><b:x xmlns:b="urn:b" xmlns:c="urn:c" plain="2" c:attr="1">'
            "t&amp;&lt;u&gt;</b:x></a>"
        )

    def test_a_declaration_is_written_where_it_is_first_used_and_not_again_below(self) -> None:
        document = '<r xmlns:p="urn:p"><p:a><p:b/></p:a></r>'

        assert canon(document) == '<r><p:a xmlns:p="urn:p"><p:b></p:b></p:a></r>'

    def test_a_default_namespace_undeclared_below_is_written_as_empty(self) -> None:
        assert canon('<r xmlns="urn:d"><i xmlns=""><j/></i><k/></r>') == (
            '<r xmlns="urn:d"><i xmlns=""><j></j></i><k></k></r>'
        )

    def test_the_apex_of_a_subtree_carries_what_it_needs_from_its_ancestors(self) -> None:
        document = root('<r xmlns:p="urn:p" xmlns:q="urn:q"><p:a q:x="1" xmlns:q="urn:q"/></r>')
        inner = document.firstChild

        assert (
            sx.canonicalize(inner).decode() == '<p:a xmlns:p="urn:p" xmlns:q="urn:q" q:x="1"></p:a>'
        )

    def test_the_apex_does_not_import_what_it_does_not_use(self) -> None:
        document = root('<r xmlns:unused="urn:u"><a/></r>')

        assert sx.canonicalize(document.firstChild).decode() == "<a></a>"

    def test_attributes_are_in_order_and_values_are_escaped(self) -> None:
        document = '<r b="2" a="x&#10;y&#9;z&quot;&lt;&amp;"/>'

        assert canon(document) == '<r a="x&#xA;y&#x9;z&quot;&lt;&amp;" b="2"></r>'

    def test_text_escapes_ampersand_less_than_greater_than_and_carriage_return(self) -> None:
        assert canon("<r>a&amp;b&lt;c&gt;d&#13;e</r>") == "<r>a&amp;b&lt;c&gt;d&#xD;e</r>"

    def test_an_empty_element_has_a_start_and_an_end_tag(self) -> None:
        assert canon("<r><a/><b></b></r>") == "<r><a></a><b></b></r>"

    def test_an_element_can_be_left_out(self) -> None:
        document = root("<r><keep/><drop/><keep2/></r>")
        drop = document.childNodes[1]

        assert (
            sx.canonicalize(document, skip=drop).decode() == "<r><keep></keep><keep2></keep2></r>"
        )

    def test_inclusive_prefixes_are_written_wherever_they_are_in_scope(self) -> None:
        document = '<r xmlns:keep="urn:k" xmlns:drop="urn:d"><a/></r>'

        assert canon(document, inclusive_prefixes=frozenset({"keep"})) == (
            '<r xmlns:keep="urn:k"><a></a></r>'
        )

    def test_a_prefix_that_is_used_and_never_declared_is_refused(self) -> None:
        # Not well-formed namespaced XML, but expat lets a prefix through without a declaration.
        document = sx.parse(b"<r><a/></r>").documentElement
        document.firstChild.prefix = "ghost"
        document.firstChild.tagName = "ghost:a"

        with pytest.raises(SamlXmlError, match="never declared"):
            sx.canonicalize(document)

    @pytest.mark.parametrize(
        "document",
        [
            '<a xmlns="urn:a" xmlns:b="urn:b" xmlns:c="urn:c"><b:x c:attr="1" plain="2">t&amp;<![CDATA[<u>]]></b:x></a>',
            '<root b="2" a="1"><child z="9" y="8">text &gt; more</child><empty/><e2></e2></root>',
            '<r xmlns:p="urn:p"><p:a p:x="1"/><p:b/></r>',
            '<r xmlns="urn:d"><i xmlns=""><j/></i><k/></r>',
            "<r>\r\n<a>x\ty</a>\n</r>",
            '<r a="x&#10;y&#9;z&quot;"/>',
            '<s:Env xmlns:s="urn:s" xmlns:t="urn:t"><s:Body t:id="1"><t:q>é ü ñ 日本</t:q></s:Body></s:Env>',
            '<r xml:lang="en"><a xml:space="preserve"> x </a></r>',
        ],
    )
    def test_it_agrees_with_the_standard_librarys_independent_canonicaliser(
        self, document: str
    ) -> None:
        assert canon(document) == ET.canonicalize(document)


@pytest.fixture(scope="module")
def identity():
    """An identity provider's key and certificate, made once."""
    return idp.make_identity("rsa")


class TestSignatures:
    @pytest.fixture
    def certificates(self, identity):
        return sx.load_certificates([identity[1]])

    def signed_assertion(self, key, **kwargs) -> tuple[str, idp.Scenario]:
        scenario = idp.Scenario()
        return idp.sign(scenario.assertion_xml(), scenario.assertion_id, key, **kwargs), scenario

    def verify(self, xml: str, certificates, target: str = "ID_assertion_1"):
        document = sx.parse(xml.encode()).documentElement
        element = next(e for e in sx.descendants(document) if e.getAttribute("ID") == target)
        return sx.verify_signed(element, certificates)

    def test_a_signed_assertion_verifies_and_hands_back_what_was_signed(
        self, identity, certificates
    ) -> None:
        xml, _ = self.signed_assertion(identity[0])

        verified = self.verify(xml, certificates)

        assert verified.reference_id == "ID_assertion_1"
        assert verified.element.localName == "Assertion"
        assert verified.algorithm == idp.RSA_SHA256

    def test_a_signed_response_verifies(self, identity, certificates) -> None:
        scenario = idp.Scenario()
        xml = idp.sign(scenario.response_xml(), scenario.response_id, identity[0])

        assert self.verify(xml, certificates, "ID_response_1").element.localName == "Response"

    def test_a_response_with_a_signed_assertion_inside_verifies_both(
        self, identity, certificates
    ) -> None:
        scenario = idp.Scenario()
        inner = idp.sign(scenario.assertion_xml(), scenario.assertion_id, identity[0])
        outer = idp.sign(scenario.response_xml(inner), scenario.response_id, identity[0])

        assert self.verify(outer, certificates, "ID_response_1")
        assert self.verify(outer, certificates, "ID_assertion_1")

    def test_ecdsa_verifies(self) -> None:
        key, pem = idp.make_identity("ec", "ec.acme.test")
        scenario = idp.Scenario()
        xml = idp.sign(
            scenario.assertion_xml(), scenario.assertion_id, key, signature_method=idp.ECDSA_SHA256
        )

        verified = self.verify(xml, sx.load_certificates([pem]))

        assert verified.algorithm == idp.ECDSA_SHA256

    def test_an_inclusive_namespaces_prefix_list_is_honoured(self, identity, certificates) -> None:
        scenario = idp.Scenario()
        xml = idp.sign(
            scenario.response_xml(), scenario.response_id, identity[0], prefix_list="saml"
        )

        assert self.verify(xml, certificates, "ID_response_1")

    def test_either_of_two_certificates_may_have_signed_so_a_rollover_works(self, identity) -> None:
        _, other_pem = idp.make_identity("rsa", "old.acme.test")
        xml, _ = self.signed_assertion(identity[0])

        assert self.verify(xml, sx.load_certificates([other_pem, identity[1]]))

    def test_a_changed_value_no_longer_matches_its_digest(self, identity, certificates) -> None:
        xml, _ = self.signed_assertion(identity[0])
        forged = xml.replace("uuid-maria", "uuid-admin")

        with pytest.raises(SamlXmlError, match="digest"):
            self.verify(forged, certificates)

    def test_another_certificate_does_not_verify(self, identity) -> None:
        xml, _ = self.signed_assertion(identity[0])
        _, other = idp.make_identity("rsa", "other.acme.test")

        with pytest.raises(SamlXmlError, match="No configured signing certificate"):
            self.verify(xml, sx.load_certificates([other]))

    def test_the_key_the_message_carries_is_never_used(self, identity, certificates) -> None:
        attacker_key, attacker_pem = idp.make_identity("rsa", "attacker.test")
        body = "".join(attacker_pem.splitlines()[1:-1])
        info = f"<ds:KeyInfo><ds:X509Data><ds:X509Certificate>{body}</ds:X509Certificate></ds:X509Data></ds:KeyInfo>"
        xml, _ = self.signed_assertion(attacker_key, key_info=info)

        with pytest.raises(SamlXmlError, match="No configured signing certificate"):
            self.verify(xml, certificates)

    def test_no_certificate_configured_means_no_verification(self, identity) -> None:
        xml, _ = self.signed_assertion(identity[0])

        with pytest.raises(SamlXmlError, match="No signing certificate is configured"):
            self.verify(xml, [])

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"signature_method": idp.RSA_SHA1}, "signature algorithm"),
            ({"digest_method": idp.SHA1}, "digest algorithm"),
            ({"canonicalization": idp.EXC_C14N_COMMENTS}, "canonicalisation"),
            (
                {"canonicalization": "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"},
                "canonicalisation",
            ),
            ({"transforms": (idp.EXC_C14N,)}, "transforms"),
            ({"transforms": (idp.ENVELOPED,)}, "transforms"),
            (
                {
                    "transforms": (
                        idp.ENVELOPED,
                        idp.EXC_C14N,
                        "http://www.w3.org/TR/1999/REC-xpath-19991116",
                    )
                },
                "transform",
            ),
            ({"transforms": (idp.ENVELOPED, idp.EXC_C14N, idp.EXC_C14N)}, "transforms"),
            ({"reference_uri": "#ID_other"}, "refer to the element"),
            ({"reference_uri": ""}, "refer to the element"),
            ({"reference_uri": "https://evil.test/doc#ID_assertion_1"}, "refer to the element"),
        ],
    )
    def test_whatever_is_outside_the_profile_is_refused(
        self, identity, certificates, kwargs: dict, message: str
    ) -> None:
        xml, _ = self.signed_assertion(identity[0], **kwargs)

        with pytest.raises(SamlXmlError, match=message):
            self.verify(xml, certificates)

    def test_an_unsigned_element_is_not_verified(self, certificates) -> None:
        with pytest.raises(SamlXmlError, match="0 signatures"):
            self.verify(idp.Scenario().assertion_xml(), certificates)

    def test_two_signatures_where_one_is_due_are_refused(self, identity, certificates) -> None:
        xml, _ = self.signed_assertion(identity[0])
        document = sx.parse(xml.encode()).documentElement
        signature = sx.children(document, sx.NS_DS, "Signature")[0]
        document.appendChild(signature.cloneNode(True))

        with pytest.raises(SamlXmlError, match="2 signatures"):
            sx.verify_signed(document, certificates)

    def test_two_references_where_one_is_due_are_refused(self, identity, certificates) -> None:
        xml, _ = self.signed_assertion(identity[0])
        document = sx.parse(xml.encode()).documentElement
        signed_info = sx.children(
            sx.children(document, sx.NS_DS, "Signature")[0], sx.NS_DS, "SignedInfo"
        )[0]
        signed_info.appendChild(sx.children(signed_info, sx.NS_DS, "Reference")[0].cloneNode(True))

        with pytest.raises(SamlXmlError, match="exactly one reference"):
            sx.verify_signed(document, certificates)

    def test_a_signature_that_is_not_base64_is_refused(self, identity, certificates) -> None:
        xml, _ = self.signed_assertion(identity[0])
        broken = xml.replace("<ds:SignatureValue>", "<ds:SignatureValue>!!!not base64!!!")

        with pytest.raises(SamlXmlError, match="base64"):
            self.verify(broken, certificates)

    def test_a_weak_key_is_refused_when_the_certificate_is_loaded(self) -> None:
        import datetime

        from cryptography import x509 as x
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(65537, 1024)  # noqa: S505 - weak on purpose, to see it refused
        name = x.Name([x.NameAttribute(NameOID.COMMON_NAME, "weak")])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        pem = certificate.public_bytes(serialization.Encoding.PEM).decode()

        with pytest.raises(SamlXmlError, match="1024 bits"):
            sx.load_certificates([pem])

    def test_a_certificate_as_metadata_writes_it_is_read(self, identity) -> None:
        body = "".join(identity[1].splitlines()[1:-1])

        assert len(sx.load_certificates([body])) == 1
        assert len(sx.load_certificates([" \n" + body[:60] + "\n" + body[60:] + " "])) == 1

    def test_something_that_is_not_a_certificate_is_refused(self) -> None:
        with pytest.raises(SamlXmlError, match="Not a certificate"):
            sx.load_certificates(["this is not a certificate"])


class TestWrapping:
    """Documents that are signed and mean something else (signature wrapping)."""

    def test_a_signed_assertion_moved_aside_leaves_the_one_that_replaced_it_unsigned(
        self, identity
    ) -> None:
        good = idp.Scenario()
        evil = idp.Scenario(name_id="uuid-admin", assertion_id="ID_evil")
        signed = idp.sign(good.assertion_xml(), good.assertion_id, identity[0])
        wrapped = good.response_xml(
            evil.assertion_xml(declare=False),
            extra=f"<samlp:Extensions>{signed}</samlp:Extensions>",
        )
        document = sx.parse(wrapped.encode()).documentElement
        certificates = sx.load_certificates([identity[1]])
        by_id = {e.getAttribute("ID"): e for e in sx.descendants(document) if e.hasAttribute("ID")}

        # The one that comes first and would be read by a naive consumer carries no signature...
        with pytest.raises(SamlXmlError, match="0 signatures"):
            sx.verify_signed(by_id["ID_evil"], certificates)
        # ...and the signed one verifies only as what it is, where it is.
        assert (
            sx.verify_signed(by_id["ID_assertion_1"], certificates).element
            is by_id["ID_assertion_1"]
        )

    def test_a_signature_copied_onto_another_element_does_not_refer_to_it(self, identity) -> None:
        good = idp.Scenario()
        evil = idp.Scenario(name_id="uuid-admin", assertion_id="ID_evil")
        signed = idp.sign(good.assertion_xml(), good.assertion_id, identity[0])
        signed_doc = sx.parse(signed.encode()).documentElement
        signature = sx.children(signed_doc, sx.NS_DS, "Signature")[0]
        evil_doc = sx.parse(evil.assertion_xml().encode())
        evil_root = evil_doc.documentElement
        evil_root.appendChild(evil_doc.importNode(signature, True))

        with pytest.raises(SamlXmlError, match="refer to the element"):
            sx.verify_signed(evil_root, sx.load_certificates([identity[1]]))

    def test_the_same_id_on_two_elements_is_refused_before_any_of_it_is_trusted(
        self, identity
    ) -> None:
        good = idp.Scenario()
        signed = idp.sign(good.assertion_xml(), good.assertion_id, identity[0])
        twin = good.assertion_xml(declare=False).replace("uuid-maria", "uuid-admin")
        wrapped = good.response_xml(signed + twin)
        document = sx.parse(wrapped.encode()).documentElement

        with pytest.raises(SamlXmlError, match="twice"):
            sx.check_unique_ids(document)

    def test_content_added_inside_the_signed_element_breaks_the_digest(self, identity) -> None:
        scenario = idp.Scenario()
        signed = idp.sign(scenario.assertion_xml(), scenario.assertion_id, identity[0])
        extended = signed.replace(
            "</saml:AttributeStatement>", "<saml:Attribute Name='x'/></saml:AttributeStatement>"
        )
        document = sx.parse(extended.encode()).documentElement

        with pytest.raises(SamlXmlError, match="digest"):
            sx.verify_signed(document, sx.load_certificates([identity[1]]))

    def test_content_added_outside_the_signed_element_is_not_covered_and_must_not_be_read(
        self, identity
    ) -> None:
        scenario = idp.Scenario()
        signed = idp.sign(scenario.assertion_xml(), scenario.assertion_id, identity[0])
        response = scenario.response_xml(
            signed, extra="<saml:Issuer>https://evil.test</saml:Issuer>"
        )
        document = sx.parse(response.encode()).documentElement
        by_id = {e.getAttribute("ID"): e for e in sx.descendants(document) if e.hasAttribute("ID")}

        # The assertion verifies; what verifies is the assertion alone, and a consumer reads
        # nothing from the Response around it.
        verified = sx.verify_signed(by_id["ID_assertion_1"], sx.load_certificates([identity[1]]))
        assert verified.element.localName == "Assertion"
