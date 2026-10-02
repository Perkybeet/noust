"""Edits touch only the bytes of their element and keep the surrounding indentation."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from noust.core.exceptions import ValidationError
from noust.managers.siteconf import ParseError, apply_ops, parse, structure

FIXTURES = Path(__file__).parent / "fixtures" / "siteconf"


def read(*parts):
    with FIXTURES.joinpath(*parts).open(encoding="utf-8", newline="") as handle:
        return handle.read()


PROGGEST = read("proggest", "proggest.es")


def loc(model, server_index, path, modifier=""):
    return next(
        entry
        for entry in model.servers[server_index].locations
        if entry.path == path and entry.modifier == modifier
    )


def diff_window(before: str, after: str) -> tuple[int, int, int]:
    """Where two texts differ: common prefix length and both suffix starts."""
    prefix = len(os.path.commonprefix([before, after]))
    suffix = len(os.path.commonprefix([before[prefix:][::-1], after[prefix:][::-1]]))
    return prefix, len(before) - suffix, len(after) - suffix


@pytest.fixture(scope="module")
def model():
    return structure(parse(PROGGEST, "nginx"))


class TestSetDirective:
    def test_by_id_changes_only_that_line(self, model):
        socket = loc(model, 1, "/socket.io")
        timeout = next(d for d in socket.directives if d.name == "proxy_read_timeout")
        result = apply_ops(
            PROGGEST, "nginx", [{"op": "set_directive", "target": timeout.id, "args": ["120s"]}]
        )
        assert result.changed_lines == 1
        assert result.config == PROGGEST.replace(
            "proxy_read_timeout 86400s;\n        proxy_send_timeout 86400s;",
            "proxy_read_timeout 120s;\n        proxy_send_timeout 86400s;",
        )
        assert loc(result.structure, 1, "/socket.io").settings.read_timeout == "120s"

    def test_by_name_adds_it_when_absent_with_the_sibling_indentation(self, model):
        login = loc(model, 1, "/api/v1/auth/login")
        result = apply_ops(
            PROGGEST,
            "nginx",
            [
                {
                    "op": "set_directive",
                    "parent": login.id,
                    "name": "proxy_read_timeout",
                    "args": ["30s"],
                }
            ],
        )
        assert result.changed_lines == 1
        assert (
            "        proxy_set_header X-Forwarded-Proto $scheme;\n"
            "        proxy_read_timeout 30s;\n"
            "    }\n\n    location /api/auth/ {"
        ) in result.config
        assert loc(result.structure, 1, "/api/v1/auth/login").settings.read_timeout == "30s"

    def test_by_name_changes_the_existing_one(self, model):
        files = loc(model, 1, "/api/files/")
        result = apply_ops(
            PROGGEST,
            "nginx",
            [
                {
                    "op": "set_directive",
                    "parent": files.id,
                    "name": "proxy_buffering",
                    "args": ["on"],
                }
            ],
        )
        assert result.changed_lines == 1
        assert loc(result.structure, 1, "/api/files/").settings.buffering is True

    def test_values_are_quoted_when_they_must_be(self, model):
        https = model.servers[1]
        result = apply_ops(
            PROGGEST,
            "nginx",
            [
                {
                    "op": "add_directive",
                    "parent": https.id,
                    "name": "add_header",
                    "args": ["Permissions-Policy", "camera=(), geolocation=()", "always"],
                }
            ],
        )
        assert (
            '    add_header Permissions-Policy "camera=(), geolocation=()" always;\n'
            in result.config
        )
        headers = [(h.name, h.value) for h in result.structure.servers[1].headers]
        assert ("Permissions-Policy", "camera=(), geolocation=()") in headers

    def test_unchanged_values_keep_their_original_quoting(self, model):
        socket = loc(model, 1, "/socket.io")
        connection = next(d for d in socket.directives if d.args[:1] == ["Connection"])
        result = apply_ops(
            PROGGEST,
            "nginx",
            [{"op": "set_directive", "target": connection.id, "args": ["Connection", "upgrade"]}],
        )
        assert result.config == PROGGEST
        assert result.changed_lines == 0


class TestAddAndRemoveDirectives:
    def test_remove_takes_its_end_of_line_comment(self):
        text = "server {\n    listen 80; # plain\n    listen 81;\n}\n"
        result = apply_ops(text, "nginx", [{"op": "remove_directive", "target": "s0/d0"}])
        assert result.config == "server {\n    listen 81;\n}\n"

    def test_remove_by_name(self):
        text = "server {\n    listen 80;\n    gzip on;\n}\n"
        result = apply_ops(
            text, "nginx", [{"op": "remove_directive", "parent": "s0", "name": "gzip"}]
        )
        assert result.config == "server {\n    listen 80;\n}\n"

    def test_add_into_an_empty_multiline_block(self):
        text = read("templates", "nginx-static.ssl.conf")
        model = structure(parse(text, "nginx"))
        well_known = loc(model, len(model.servers) - 1, "/.well-known/", "^~")
        result = apply_ops(
            text,
            "nginx",
            [{"op": "add_directive", "parent": well_known.id, "name": "allow", "args": ["all"]}],
        )
        assert "    location ^~ /.well-known/ {\n        allow all;\n    }\n" in result.config

    def test_add_into_a_one_line_empty_block_with_tabs(self):
        text = read("nginx", "edge-cases.conf")
        model = structure(parse(text, "nginx"))
        images = loc(model, 0, "\\.(gif|jpg|png)$", "~*")
        result = apply_ops(
            text,
            "nginx",
            [{"op": "add_directive", "parent": images.id, "name": "expires", "args": ["30d"]}],
        )
        assert "\tlocation ~* \\.(gif|jpg|png)$ {\n\t\texpires 30d;\n\t}\n" in result.config

    def test_add_at_a_position(self):
        text = "server {\n    listen 80;\n    gzip on;\n}\n"
        result = apply_ops(
            text,
            "nginx",
            [
                {
                    "op": "add_directive",
                    "parent": "s0",
                    "name": "server_name",
                    "args": ["a"],
                    "after": "s0/d0",
                }
            ],
        )
        assert result.config == "server {\n    listen 80;\n    server_name a;\n    gzip on;\n}\n"
        result = apply_ops(
            text,
            "nginx",
            [
                {
                    "op": "add_directive",
                    "parent": "s0",
                    "name": "server_name",
                    "args": ["a"],
                    "before": "s0/d0",
                }
            ],
        )
        assert result.config == "server {\n    server_name a;\n    listen 80;\n    gzip on;\n}\n"

    def test_add_at_the_top_level_of_an_empty_file(self):
        result = apply_ops(
            "",
            "nginx",
            [
                {
                    "op": "add_directive",
                    "name": "limit_req_zone",
                    "args": ["$binary_remote_addr", "zone=a:10m", "rate=1r/s"],
                }
            ],
        )
        assert result.config == "limit_req_zone $binary_remote_addr zone=a:10m rate=1r/s;\n"


class TestBlocks:
    def test_add_a_location_from_a_template(self, model):
        https = model.servers[1]
        result = apply_ops(
            PROGGEST,
            "nginx",
            [
                {
                    "op": "add_block",
                    "parent": https.id,
                    "name": "location",
                    "args": ["/reports/"],
                    "template": "proxy",
                    "to": "http://nestjs_upstream",
                }
            ],
        )
        new = loc(result.structure, 1, "/reports/")
        assert new.target.kind == "proxy"
        assert new.target.upstream == "nestjs_upstream"
        assert new.id == "s1/l25"
        assert (
            "        return 404;\n    }\n\n    location /reports/ {\n"
            "        proxy_pass http://nestjs_upstream;\n"
        ) in result.config
        assert result.config.endswith("    }\n}\n")

    def test_static_and_redirect_templates(self, model):
        result = apply_ops(
            PROGGEST,
            "nginx",
            [
                {
                    "op": "add_block",
                    "parent": "s1",
                    "name": "location",
                    "args": ["/files/"],
                    "template": "static",
                    "to": "/srv/files/",
                },
                {
                    "op": "add_block",
                    "parent": "s1",
                    "name": "location",
                    "args": ["=", "/old"],
                    "template": "redirect",
                    "to": "/new",
                    "code": 302,
                },
            ],
        )
        files = loc(result.structure, 1, "/files/")
        assert files.target.kind == "static" and files.target.alias == "/srv/files/"
        old = loc(result.structure, 1, "/old", "=")
        assert (old.target.kind, old.target.code, old.target.destination) == ("return", 302, "/new")

    def test_add_an_upstream_goes_next_to_the_others(self, model):
        result = apply_ops(
            PROGGEST,
            "nginx",
            [
                {
                    "op": "add_block",
                    "name": "upstream",
                    "args": ["worker_upstream"],
                    "body": [{"name": "server", "args": ["127.0.0.1:3002"]}],
                }
            ],
        )
        assert result.config.startswith(
            PROGGEST.split("# HTTP server")[0].rstrip("\n")
            + "\n\nupstream worker_upstream {\n    server 127.0.0.1:3002;\n}\n"
        )
        assert [u.name for u in result.structure.upstreams][-1] == "worker_upstream"

    def test_remove_a_location_with_its_comments(self, model):
        uaap = loc(model, 1, "/uaap/", "=")
        result = apply_ops(PROGGEST, "nginx", [{"op": "remove_block", "target": uaap.id}])
        assert "Cortacircuito" not in result.config
        assert "location = /uaap/" not in result.config
        assert "Descarga del informe agregado" in result.config
        start, end_before, end_after = diff_window(PROGGEST, result.config)
        assert end_after == start  # a pure deletion of one contiguous run
        assert len(result.structure.servers[1].locations) == 24

    def test_duplicate_with_new_arguments(self, model):
        user = loc(model, 1, "/api/user/")
        result = apply_ops(
            PROGGEST,
            "nginx",
            [{"op": "duplicate_block", "target": user.id, "args": ["/api/users/"]}],
        )
        copy = loc(result.structure, 1, "/api/users/")
        assert copy.id == "s1/l5"
        assert copy.target.upstream == "nextjs_upstream"
        start, end_before, _ = diff_window(PROGGEST, result.config)
        assert end_before == start  # a pure insertion

    def test_duplicate_does_not_copy_comments(self, model):
        uaap = loc(model, 1, "/uaap/", "=")
        result = apply_ops(
            PROGGEST,
            "nginx",
            [{"op": "duplicate_block", "target": uaap.id, "args": ["=", "/uaap2/"]}],
        )
        assert result.config.count("Cortacircuito") == 1

    def test_move_keeps_every_line(self, model):
        files = loc(model, 1, "/api/files/")
        settings_loc = loc(model, 1, "/api/settings/")
        result = apply_ops(
            PROGGEST, "nginx", [{"op": "move_block", "target": files.id, "before": settings_loc.id}]
        )
        assert sorted(result.config.splitlines()) == sorted(PROGGEST.splitlines())
        paths = [entry.path for entry in result.structure.servers[1].locations]
        assert paths.index("/api/files/") == paths.index("/api/settings/") - 1
        moved = loc(result.structure, 1, "/api/files/")
        assert any("gestor documental" in c for c in moved.comments)

    def test_move_after_the_last_one(self, model):
        first = loc(model, 1, "/health")
        last = model.servers[1].locations[-1]
        result = apply_ops(
            PROGGEST, "nginx", [{"op": "move_block", "target": first.id, "after": last.id}]
        )
        assert result.structure.servers[1].locations[-1].path == "/health"
        assert sorted(result.config.splitlines()) == sorted(PROGGEST.splitlines())

    def test_add_and_remove_a_server(self, model):
        result = apply_ops(
            PROGGEST,
            "nginx",
            [
                {
                    "op": "add_block",
                    "name": "server",
                    "args": [],
                    "body": [
                        {"name": "listen", "args": ["8080"]},
                        {
                            "name": "location",
                            "args": ["/"],
                            "body": [{"name": "return", "args": ["204"]}],
                        },
                    ],
                }
            ],
        )
        assert result.config.endswith(
            "\n\nserver {\n    listen 8080;\n    location / {\n        return 204;\n    }\n}\n"
        )
        assert len(result.structure.servers) == 3
        back = apply_ops(result.config, "nginx", [{"op": "remove_block", "target": "s2"}])
        assert back.config == PROGGEST


class TestErrors:
    @pytest.mark.parametrize(
        ("op", "field"),
        [
            ({"op": "explode"}, "ops[0].op"),
            ({"op": "set_directive", "target": "s9/d0", "args": ["x"]}, "ops[0].target"),
            ({"op": "set_directive", "args": ["x"]}, "ops[0].target"),
            ({"op": "remove_directive", "target": "s1/l0"}, "ops[0].target"),
            ("not an object", "ops[0]"),
            ({"op": "move_block", "target": "s1/l0", "before": "s1/l0"}, "ops[0].before"),
            ({"op": "remove_block", "target": "s1/d0"}, "ops[0].target"),
            (
                {"op": "add_directive", "parent": "s1", "name": "bad name;", "args": []},
                "ops[0].name",
            ),
            (
                {"op": "add_directive", "parent": "s1", "name": "x", "args": "notalist"},
                "ops[0].args",
            ),
            (
                {
                    "op": "add_block",
                    "parent": "s1",
                    "name": "location",
                    "args": ["/x"],
                    "template": "nope",
                },
                "ops[0].template",
            ),
            ({"op": "move_block", "target": "s1/l0"}, "ops[0].before"),
        ],
    )
    def test_bad_operations_name_the_field(self, op, field):
        with pytest.raises(ValidationError) as caught:
            apply_ops(PROGGEST, "nginx", [op])
        assert caught.value.field == field

    def test_invalid_text_is_a_parse_error(self):
        with pytest.raises(ParseError):
            apply_ops("server {", "nginx", [])


class TestApache:
    TEXT = read("templates", "apache-proxy.ssl-aliases-redirects.conf")

    def test_set_and_add(self):
        model = structure(parse(self.TEXT, "apache"))
        main = next(s for s in model.servers if s.tls and s.names[0] == "shop.example.com")
        timeout = next(d for d in main.directives if d.name == "ProxyTimeout")
        result = apply_ops(
            self.TEXT,
            "apache",
            [
                {"op": "set_directive", "target": timeout.id, "args": ["120"]},
                {
                    "op": "add_directive",
                    "parent": main.id,
                    "name": "Header",
                    "args": ["always", "set", "X-Test", "a b"],
                },
            ],
        )
        assert "    ProxyTimeout 120\n" in result.config
        # Next to the other Header lines, not at the end of the section.
        assert (
            '    Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains"\n'
            '    Header always set X-Test "a b"\n'
        ) in result.config
        assert result.changed_lines == 2

    def test_add_a_location_section(self):
        model = structure(parse(self.TEXT, "apache"))
        main = next(s for s in model.servers if s.tls and s.names[0] == "shop.example.com")
        result = apply_ops(
            self.TEXT,
            "apache",
            [
                {
                    "op": "add_block",
                    "parent": main.id,
                    "name": "Location",
                    "args": ["/admin"],
                    "body": [{"name": "Require", "args": ["ip", "10.0.0.0/8"]}],
                }
            ],
        )
        assert (
            "\n    <Location /admin>\n        Require ip 10.0.0.0/8\n    </Location>\n</VirtualHost>"
            in result.config
        )

    def test_remove_a_continued_directive(self):
        text = read("apache", "reverse-proxy.conf")
        model = structure(parse(text, "apache"))
        https = model.servers[1]
        v2 = next(entry for entry in https.locations if entry.path == "/api/v2/")
        result = apply_ops(text, "apache", [{"op": "remove_directive", "target": v2.id}])
        assert "keepalive=On" not in result.config
        assert "    ProxyPass /static !\n    ProxyPassReverse /api/v2/" in result.config


# Property tests over the whole corpus ---------------------------------------------------

CORPUS = [("nginx", p) for p in sorted((FIXTURES / "nginx").iterdir())]
CORPUS += [("nginx", p) for p in sorted((FIXTURES / "proggest").iterdir())]
CORPUS += [("apache", p) for p in sorted((FIXTURES / "apache").iterdir())]
CORPUS += [
    ("apache" if p.name.startswith("apache-") else "nginx", p)
    for p in sorted((FIXTURES / "templates").iterdir())
]


def _directive_ids(model):
    """Every raw directive id the structure exposes, blocks included."""
    ids = [d.id for d in model.directives]
    stack = list(model.servers) + list(model.upstreams)
    while stack:
        element = stack.pop()
        ids.extend(d.id for d in element.directives)
        stack.extend(getattr(element, "locations", []))
    return ids


def _load(kind, path):
    with path.open(encoding="utf-8", newline="") as handle:
        text = handle.read()
    tree = parse(text, kind)
    return text, tree, structure(tree)


_values = st.lists(
    st.text(alphabet=st.sampled_from(list("ab1 ;{}\"'\\$#")), min_size=0, max_size=6),
    min_size=1,
    max_size=3,
)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.sampled_from(CORPUS), st.data(), _values)
def test_set_directive_changes_only_its_own_bytes(case, data, values):
    kind, path = case
    text, tree, model = _load(kind, path)
    ids = [i for i in _directive_ids(model) if getattr(tree.find(i), "raw_body", None) is None]
    if not ids:
        return
    target = data.draw(st.sampled_from(ids))
    start, _ = tree.span_of(target)
    # A block's head is what set_directive changes; its body stays as it is.
    end = start + len(tree.find(target).render_head())
    result = apply_ops(text, kind, [{"op": "set_directive", "target": target, "args": values}])
    prefix, before_end, after_end = diff_window(text, result.config)
    assert prefix >= start
    assert before_end <= end
    changed = parse(result.config, kind).find(target)
    assert [arg.word.value for arg in changed.args] == values


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.sampled_from(CORPUS), st.data())
def test_remove_directive_deletes_one_contiguous_run(case, data):
    kind, path = case
    text, tree, model = _load(kind, path)
    ids = [i for i in _directive_ids(model) if type(tree.find(i)).__name__ == "Directive"]
    if not ids:
        return
    target = data.draw(st.sampled_from(ids))
    start, end = tree.span_of(target)
    result = apply_ops(text, kind, [{"op": "remove_directive", "target": target}])
    size = len(text) - len(result.config)
    # One contiguous run went, and it covers the directive.
    cut = next(
        (
            a
            for a in range(max(0, end - size), start + 1)
            if text[:a] + text[a + size :] == result.config
        ),
        None,
    )
    assert cut is not None
    removed = text[cut : cut + size]
    # Only the directive, its line break and indentation, and its own comments go.
    rest = removed.replace(text[start:end], "", 1)
    assert all(line.strip() == "" or line.strip().startswith("#") for line in rest.splitlines())
    parse(result.config, kind)


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.sampled_from(CORPUS), st.data())
def test_add_directive_inserts_one_run_with_the_sibling_indentation(case, data):
    kind, path = case
    text, tree, model = _load(kind, path)
    parents = [s.id for s in model.servers] + [u.id for u in model.upstreams if u.source is None]
    stack = list(model.servers)
    while stack:
        element = stack.pop()
        parents.extend(entry.id for entry in element.locations)
        stack.extend(element.locations)
    # Apache's ProxyPass rules are listed as locations but are directives.
    parents = [i for i in parents if type(tree.find(i)).__name__ == "Block"]
    if not parents:
        return
    parent = data.draw(st.sampled_from(parents))
    result = apply_ops(
        text,
        kind,
        [{"op": "add_directive", "parent": parent, "name": "Xnoust_test", "args": ["1"]}],
    )
    prefix, before_end, after_end = diff_window(text, result.config)
    assert before_end == prefix  # nothing was removed
    line = next(entry for entry in result.config.splitlines() if "Xnoust_test" in entry)
    indent = line[: len(line) - len(line.lstrip())]
    parent_node = parse(result.config, kind).find(parent)
    sibling_indents = {
        child.prefix.rsplit("\n", 1)[-1]
        for child in parent_node.children
        if "\n" in child.prefix and "Xnoust_test" not in child.render()
    }
    if sibling_indents:
        assert indent in sibling_indents
    else:
        assert indent.startswith(parent_node.prefix.rsplit("\n", 1)[-1])


def _block_ids(tree):
    """Ids of every block with a body the parser read (not Lua)."""
    return [
        ref.id
        for ref in tree.index().values()
        if type(ref.node).__name__ == "Block" and ref.node.raw_body is None
    ]


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.sampled_from(CORPUS), st.data())
def test_remove_block_deletes_one_contiguous_run_covering_it(case, data):
    kind, path = case
    text, tree, _ = _load(kind, path)
    ids = _block_ids(tree)
    if not ids:
        return
    target = data.draw(st.sampled_from(sorted(set(ids))))
    start, end = tree.span_of(target)
    result = apply_ops(text, kind, [{"op": "remove_block", "target": target}])
    size = len(text) - len(result.config)
    cut = next(
        (
            a
            for a in range(max(0, end - size), start + 1)
            if text[:a] + text[a + size :] == result.config
        ),
        None,
    )
    assert cut is not None
    outside = text[cut:start] + text[end : cut + size]
    assert all(line.strip() == "" or line.strip().startswith("#") for line in outside.splitlines())


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.sampled_from(CORPUS), st.data())
def test_duplicate_block_inserts_an_exact_copy_and_nothing_else(case, data):
    kind, path = case
    text, tree, _ = _load(kind, path)
    ids = _block_ids(tree)
    if not ids:
        return
    target = data.draw(st.sampled_from(sorted(set(ids))))
    start, end = tree.span_of(target)
    result = apply_ops(text, kind, [{"op": "duplicate_block", "target": target}])
    prefix, before_end, after_end = diff_window(text, result.config)
    assert before_end == prefix  # nothing was removed
    assert result.config.count(text[start:end]) == text.count(text[start:end]) + 1


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.sampled_from(CORPUS), st.data())
def test_move_block_among_siblings_keeps_every_line(case, data):
    kind, path = case
    text, tree, _ = _load(kind, path)
    index = tree.index()
    pairs = [
        (ref.id, other.id)
        for ref in index.values()
        for other in index.values()
        if type(ref.node).__name__ == "Block"
        and ref.container is other.container
        and ref.node is not other.node
        and "\n" in ref.node.prefix
        and "\n" in other.node.prefix
        and ref.node.prefix.rsplit("\n", 1)[1] == other.node.prefix.rsplit("\n", 1)[1]
    ]
    if not pairs:
        return
    target, anchor = data.draw(st.sampled_from(sorted(set(pairs))))
    where = data.draw(st.sampled_from(["before", "after"]))
    result = apply_ops(text, kind, [{"op": "move_block", "target": target, where: anchor}])

    # Same indentation, same lines: only their order (and blank lines) changed.
    # The one exception is the container's own closing brace when it shares a
    # line with its last element (``try_files ...;   }``): it stays last, so it
    # rides along with whatever is moved after that element. Closing braces at
    # the end of a line are set aside and counted instead.
    def non_blank(value):
        lines = (re.sub(r"(\s*\})+\s*$", "", line) for line in value.splitlines())
        return sorted(line for line in lines if line.strip())

    assert non_blank(result.config) == non_blank(text)
    assert result.config.count("}") == text.count("}")
    assert parse(result.config, kind).render() == result.config
