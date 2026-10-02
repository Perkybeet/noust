"""The nginx tokenizer and concrete tree: lossless, faithful to nginx's lexer, never half a tree."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from noust.managers.siteconf import ParseError, parse
from noust.managers.siteconf.nginx import quote
from noust.managers.siteconf.tree import Block, Comment, Directive


def statements(nodes):
    return [node for node in nodes if not isinstance(node, Comment)]


class TestTree:
    def test_round_trip_of_a_small_site(self):
        text = "server {\n    listen 80;\n    location / {\n        return 204;\n    }\n}\n"
        tree = parse(text, "nginx")
        assert tree.render() == text
        (server,) = statements(tree.children)
        assert isinstance(server, Block)
        assert server.name.value == "server"
        listen, location = statements(server.children)
        assert isinstance(listen, Directive)
        assert [arg.word.value for arg in listen.args] == ["80"]
        assert [arg.word.value for arg in location.args] == ["/"]

    def test_values_are_decoded_and_raw_text_kept(self):
        text = 'set $a "hello \\"quoted\\" world";\nset $b \'it\\\'s\';\nset $c ${a}x;\n'
        tree = parse(text, "nginx")
        assert tree.render() == text
        a, b, c = statements(tree.children)
        assert a.args[1].word.value == 'hello "quoted" world'
        assert a.args[1].word.raw == '"hello \\"quoted\\" world"'
        assert a.args[1].word.quoted
        assert b.args[1].word.value == "it's"
        assert c.args[1].word.value == "${a}x"

    def test_braces_and_hashes_inside_words_follow_nginx(self):
        # ``}`` does not end a word and ``#`` only starts a comment at a token
        # start: both are how nginx's own lexer reads them.
        text = "set $a value#tail;\nset $b ${x}{ y; }\n"
        tree = parse(text, "nginx")
        a, b = statements(tree.children)
        assert a.args[1].word.value == "value#tail"
        assert b.args[1].word.value == "${x}"
        assert isinstance(b, Block)

    def test_end_of_line_comment_is_its_own_node_on_the_same_line(self):
        text = "server {\n    listen 80; # plain http\n}\n"
        tree = parse(text, "nginx")
        server = statements(tree.children)[0]
        listen, comment = server.children
        assert isinstance(comment, Comment)
        assert comment.prefix == " "
        assert comment.text == "# plain http"
        assert tree.render() == text

    def test_comment_between_arguments_is_kept(self):
        text = "gzip_types text/css # first\n    application/json;\n"
        tree = parse(text, "nginx")
        assert tree.render() == text
        (directive,) = statements(tree.children)
        assert [arg.word.value for arg in directive.args] == ["text/css", "application/json"]

    def test_quoted_string_followed_by_parenthesis(self):
        text = 'if ($request_uri ~* "^[^?]*(\\.\\.|%2e)") {\n    return 400;\n}\n'
        tree = parse(text, "nginx")
        assert tree.render() == text
        (block,) = statements(tree.children)
        assert [arg.word.value for arg in block.args] == [
            "($request_uri",
            "~*",
            "^[^?]*(\\.\\.|%2e)",
            ")",
        ]

    def test_lua_block_keeps_its_body_raw(self):
        text = (
            "location / {\n"
            "    content_by_lua_block {\n"
            '        ngx.say("}") -- } in a comment\n'
            "        local s = [==[ ]] } ]==]\n"
            "        if x then t = { a = '{' } end\n"
            "    }\n"
            "    return 200;\n"
            "}\n"
        )
        tree = parse(text, "nginx")
        assert tree.render() == text
        location = statements(tree.children)[0]
        lua, ret = statements(location.children)
        assert lua.raw_body is not None
        assert "ngx.say" in lua.raw_body
        assert ret.name.value == "return"

    def test_set_by_lua_block_takes_arguments(self):
        text = "set_by_lua_block $res { return '}' }\nlisten 80;\n"
        tree = parse(text, "nginx")
        assert tree.render() == text
        block, listen = statements(tree.children)
        assert [arg.word.value for arg in block.args] == ["$res"]
        assert block.raw_body == " return '}' "

    def test_map_entries_are_directives(self):
        text = 'map $host $b {\n    hostnames;\n    default 1;\n    "~^a" 2; # regex\n}\n'
        tree = parse(text, "nginx")
        assert tree.render() == text
        block = statements(tree.children)[0]
        names = [node.name.value for node in statements(block.children)]
        assert names == ["hostnames", "default", "~^a"]

    def test_crlf_and_no_final_newline(self):
        text = "server {\r\n\tlisten 80;\r\n}\r\n# end"
        assert parse(text, "nginx").render() == text

    def test_empty_text(self):
        assert parse("", "nginx").render() == ""
        assert parse("\n\n  # only a comment\n", "nginx").render() == "\n\n  # only a comment\n"

    def test_line_and_column_of_each_word(self):
        tree = parse("server {\n  listen  80;\n}\n", "nginx")
        listen = statements(statements(tree.children)[0].children)[0]
        assert (listen.name.line, listen.name.column) == (2, 3)
        assert (listen.args[0].word.line, listen.args[0].word.column) == (2, 11)


class TestParseErrors:
    @pytest.mark.parametrize(
        ("text", "line", "column", "fragment"),
        [
            ("server {\n    listen 80;\n", 3, 1, "end of file"),
            ("listen 80;\n}\n", 2, 1, '"}"'),
            ("server { listen 80 }", 1, 20, '"}"'),
            ('return 200 "x"y;', 1, 15, '"y"'),
            ('set $a "never closed;\n', 1, 8, "quoted"),
            (";\n", 1, 1, '";"'),
            ("  {\n}", 1, 3, '"{"'),
            ("listen 80", 1, 10, "end of file"),
            ("location / {\n  content_by_lua_block {\n ngx.say('}')\n", 2, 24, "Lua"),
            ("content_by_lua_block { s = [[ }", 1, 22, "Lua"),
        ],
    )
    def test_invalid_text_raises_with_line_and_column(self, text, line, column, fragment):
        with pytest.raises(ParseError) as caught:
            parse(text, "nginx")
        error = caught.value
        assert (error.line, error.column) == (line, column)
        assert fragment in error.message

    def test_error_is_a_noust_error_with_a_hint(self):
        from noust.core.exceptions import SiteError

        with pytest.raises(SiteError) as caught:
            parse("}", "nginx")
        assert "line 1" in caught.value.details.lower()

    def test_unknown_kind(self):
        with pytest.raises(ValueError):
            parse("", "caddy")  # type: ignore[arg-type]


# Grammar-driven configurations: every one of them is valid for nginx's lexer.
_word_chars = st.sampled_from([*list("abcxyz019_/.:$-=~*^@"), "\\;", "\\ ", "${v}"])
_bare = (
    st.lists(_word_chars, min_size=1, max_size=6)
    .map("".join)
    .filter(lambda w: not w.startswith(("#", '"', "'")) and not w.endswith("$"))
)
_dq = st.lists(st.sampled_from([*list("ab {};#'$"), '\\"', "\\\\"]), max_size=6).map(
    lambda s: '"' + "".join(s) + '"'
)
_sq = st.lists(st.sampled_from([*list('ab {};#"$'), "\\'"]), max_size=6).map(
    lambda s: "'" + "".join(s) + "'"
)
_word = st.one_of(_bare, _dq, _sq)
_space = st.sampled_from([" ", "  ", "\t", "\n", "\n    ", " # note\n  ", "\r\n"])
_gap = st.sampled_from(["", " ", "\n", "\n\n    ", "  # c\n", "\t"])


def _directive(depth: int) -> st.SearchStrategy[str]:
    simple = st.tuples(_bare, st.lists(st.tuples(_space, _word), max_size=4), _gap).map(
        lambda t: t[0] + "".join(s + w for s, w in t[1]) + t[2] + ";"
    )
    if depth == 0:
        return simple
    block = st.tuples(
        _bare,
        st.lists(st.tuples(_space, _word), max_size=2),
        _gap,
        st.lists(st.tuples(_gap, _directive(depth - 1)), max_size=4),
        _gap,
    ).map(
        lambda t: (
            t[0]
            + "".join(s + w for s, w in t[1])
            + t[2]
            + "{"
            + "".join(g + d for g, d in t[3])
            + t[4]
            + "}"
        )
    )
    return st.one_of(simple, block)


_config = st.lists(st.tuples(_gap, _directive(2)), max_size=6).map(
    lambda items: "".join(gap + d for gap, d in items)
)


@settings(max_examples=300, deadline=None)
@given(_config)
def test_generated_configurations_round_trip(text):
    assert parse(text, "nginx").render() == text


@settings(max_examples=500, deadline=None)
@given(
    st.lists(
        st.sampled_from([*" \t\n;{}#\"'\\$ab()", "_by_lua_block", "[[", "]]", "--"]), max_size=40
    ).map("".join)
)
def test_arbitrary_text_round_trips_or_raises(text):
    # Never a half tree: either the whole text comes back or a ParseError.
    try:
        tree = parse(text, "nginx")
    except ParseError as error:
        assert error.line >= 1 and error.column >= 1
    else:
        assert tree.render() == text


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=20))
def test_quote_produces_a_word_that_decodes_back(value):
    text = f"set $x {quote(value)};\n"
    (directive,) = statements(parse(text, "nginx").children)
    assert len(directive.args) == 2
    assert directive.args[1].word.value == value


def test_quote_leaves_plain_values_alone():
    assert quote("120s") == "120s"
    assert quote("$http_upgrade") == "$http_upgrade"
    assert quote("^/(\\.env|\\.git)") == "^/(\\.env|\\.git)"
    assert quote("max-age=1; preload") == '"max-age=1; preload"'
    assert quote("") == '""'
