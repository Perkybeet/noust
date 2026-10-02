"""The Apache reader: line directives, continuations and sections, kept byte for byte."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from noust.managers.siteconf import ParseError, parse
from noust.managers.siteconf.apache import quote
from noust.managers.siteconf.tree import Block, Comment, Directive


def statements(nodes):
    return [node for node in nodes if not isinstance(node, Comment)]


class TestTree:
    def test_sections_and_directives(self):
        text = (
            "<IfModule mod_ssl.c>\n"
            "  <VirtualHost *:443>\n"
            "    ServerName a.example.com\n"
            '    Header always set X-A "b c"\n'
            "  </VirtualHost>\n"
            "</IfModule>\n"
        )
        tree = parse(text, "apache")
        assert tree.render() == text
        (ifmodule,) = statements(tree.children)
        assert isinstance(ifmodule, Block)
        assert ifmodule.name.value == "IfModule"
        (vhost,) = statements(ifmodule.children)
        assert [arg.word.value for arg in vhost.args] == ["*:443"]
        name, header = statements(vhost.children)
        assert isinstance(name, Directive)
        assert [arg.word.value for arg in header.args] == ["always", "set", "X-A", "b c"]

    def test_continuation_lines_are_one_directive(self):
        text = "ProxyPass /api/ http://127.0.0.1:9001/ \\\n          timeout=120 \\\n          keepalive=On\nProxyRequests Off\n"
        tree = parse(text, "apache")
        assert tree.render() == text
        proxy, requests = statements(tree.children)
        assert [arg.word.value for arg in proxy.args] == [
            "/api/",
            "http://127.0.0.1:9001/",
            "timeout=120",
            "keepalive=On",
        ]
        assert requests.name.value == "ProxyRequests"

    def test_comments_are_whole_lines(self):
        text = "# a comment\n  #indented\nServerName x # not a comment in Apache\n"
        tree = parse(text, "apache")
        assert tree.render() == text
        first, second, directive = tree.children
        assert isinstance(first, Comment) and isinstance(second, Comment)
        assert [arg.word.value for arg in directive.args] == [
            "x",
            "#",
            "not",
            "a",
            "comment",
            "in",
            "Apache",
        ]

    def test_close_tags_match_case_insensitively(self):
        text = "<virtualhost *:80>\nServerName a\n</VirtualHost >\n"
        assert parse(text, "apache").render() == text

    def test_escaped_quotes(self):
        text = 'Header set X-Note "a \\"quoted\\" value"\n'
        (directive,) = statements(parse(text, "apache").children)
        assert directive.args[2].word.value == 'a "quoted" value'

    def test_crlf_and_no_final_newline(self):
        text = "<VirtualHost *:80>\r\n  ServerName a\r\n</VirtualHost>"
        assert parse(text, "apache").render() == text


class TestParseErrors:
    @pytest.mark.parametrize(
        ("text", "line", "column", "fragment"),
        [
            ("<VirtualHost *:80>\nServerName a\n", 1, 1, "VirtualHost"),
            ("<VirtualHost *:80>\n</Location>\n", 2, 1, "</VirtualHost>"),
            ("ServerName a\n</VirtualHost>\n", 2, 1, "without"),
            ("  <VirtualHost *:80\nServerName a\n", 1, 3, ">"),
            ('Header set X "unterminated\n', 1, 14, "quoted"),
        ],
    )
    def test_invalid_text_raises_with_line_and_column(self, text, line, column, fragment):
        with pytest.raises(ParseError) as caught:
            parse(text, "apache")
        assert (caught.value.line, caught.value.column) == (line, column)
        assert fragment in caught.value.message


@settings(max_examples=500, deadline=None)
@given(
    st.lists(st.sampled_from([*' \t\n<>/#"\\ab', "VirtualHost", "\\\n"]), max_size=40).map("".join)
)
def test_arbitrary_text_round_trips_or_raises(text):
    try:
        tree = parse(text, "apache")
    except ParseError as error:
        assert error.line >= 1 and error.column >= 1
    else:
        assert tree.render() == text


@settings(max_examples=300, deadline=None)
@given(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\n\r"),
        max_size=20,
    )
)
def test_quote_produces_a_word_that_decodes_back(value):
    text = f"Header set X {quote(value)}\n"
    (directive,) = statements(parse(text, "apache").children)
    assert len(directive.args) == 3
    assert directive.args[2].word.value == value
