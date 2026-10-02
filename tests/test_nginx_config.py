# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the NginxConfigBuilder helper."""

import pytest

from noust.deployers.helpers.nginx_config import (
    NginxAdvancedConfig,
    NginxConfigBuilder,
    NginxRoute,
)


@pytest.fixture
def builder():
    """Create a NginxConfigBuilder instance."""
    return NginxConfigBuilder(verbose=False)


@pytest.fixture
def sample_config():
    """Create a sample advanced config."""
    return NginxAdvancedConfig(
        routes=[
            NginxRoute(
                path="/api",
                upstream_port=3001,
                upstream_name="backend",
            ),
            NginxRoute(
                path="/socket.io",
                upstream_port=3001,
                upstream_name="backend-ws",
                websocket=True,
            ),
            NginxRoute(
                path="/",
                upstream_port=3000,
                upstream_name="frontend",
            ),
        ],
        global_rate_limit="100r/s",
        security_headers={
            "Content-Security-Policy": "default-src 'self'",
        },
    )


class TestDetection:
    """Tests for config file detection."""

    def test_detect_wasm_nginx_yaml(self, builder, tmp_path):
        (tmp_path / "wasm.nginx.yaml").write_text("routes: []")
        result = builder.detect(tmp_path)
        assert result is not None
        assert result.name == "wasm.nginx.yaml"

    def test_detect_nginx_yaml(self, builder, tmp_path):
        (tmp_path / "nginx.yaml").write_text("routes: []")
        result = builder.detect(tmp_path)
        assert result is not None
        assert result.name == "nginx.yaml"

    def test_detect_none(self, builder, tmp_path):
        result = builder.detect(tmp_path)
        assert result is None

    def test_detect_priority(self, builder, tmp_path):
        """wasm.nginx.yaml takes priority over nginx.yaml."""
        (tmp_path / "wasm.nginx.yaml").write_text("routes: []")
        (tmp_path / "nginx.yaml").write_text("routes: []")
        result = builder.detect(tmp_path)
        assert result.name == "wasm.nginx.yaml"


class TestParsing:
    """Tests for YAML config parsing."""

    def test_parse_basic(self, builder, tmp_path):
        config_file = tmp_path / "wasm.nginx.yaml"
        config_file.write_text(
            "routes:\n"
            "  - path: /api\n"
            "    port: 3001\n"
            "    name: backend\n"
            "  - path: /\n"
            "    port: 3000\n"
            "    name: frontend\n"
        )
        config = builder.parse(config_file)

        assert len(config.routes) == 2
        assert config.routes[0].path == "/api"
        assert config.routes[0].upstream_port == 3001
        assert config.routes[0].upstream_name == "backend"
        assert config.routes[1].path == "/"
        assert config.routes[1].upstream_port == 3000

    def test_parse_websocket(self, builder, tmp_path):
        config_file = tmp_path / "wasm.nginx.yaml"
        config_file.write_text("routes:\n  - path: /ws\n    port: 3001\n    websocket: true\n")
        config = builder.parse(config_file)
        assert config.routes[0].websocket is True

    def test_parse_rate_limit(self, builder, tmp_path):
        config_file = tmp_path / "wasm.nginx.yaml"
        config_file.write_text(
            "rate_limit: '50r/s'\nroutes:\n  - path: /\n    port: 3000\n    rate_limit: '10r/s'\n"
        )
        config = builder.parse(config_file)
        assert config.global_rate_limit == "50r/s"
        assert config.routes[0].rate_limit == "10r/s"

    def test_parse_security_headers(self, builder, tmp_path):
        config_file = tmp_path / "wasm.nginx.yaml"
        config_file.write_text(
            "routes:\n"
            "  - path: /\n"
            "    port: 3000\n"
            "security_headers:\n"
            "  X-Custom-Header: 'test-value'\n"
        )
        config = builder.parse(config_file)
        assert config.security_headers["X-Custom-Header"] == "test-value"

    def test_parse_strip_prefix(self, builder, tmp_path):
        config_file = tmp_path / "wasm.nginx.yaml"
        config_file.write_text("routes:\n  - path: /api\n    port: 3001\n    strip_prefix: true\n")
        config = builder.parse(config_file)
        assert config.routes[0].strip_prefix is True

    def test_parse_invalid_yaml(self, builder, tmp_path):
        config_file = tmp_path / "wasm.nginx.yaml"
        config_file.write_text("routes:\n  - path: /\n    port: {\nbad")
        with pytest.raises(ValueError, match="Invalid YAML"):
            builder.parse(config_file)

    def test_parse_empty_file(self, builder, tmp_path):
        config_file = tmp_path / "wasm.nginx.yaml"
        config_file.write_text("")
        with pytest.raises(ValueError, match="Empty or invalid"):
            builder.parse(config_file)

    def test_auto_name_generation(self, builder, tmp_path):
        config_file = tmp_path / "wasm.nginx.yaml"
        config_file.write_text("routes:\n  - path: /api/v1\n    port: 3001\n")
        config = builder.parse(config_file)
        assert config.routes[0].upstream_name == "api-v1"


class TestContextBuilding:
    """Tests for Jinja2 context building."""

    def test_build_basic_context(self, builder, sample_config):
        ctx = builder.build_context(sample_config, "example.com", ssl=True)

        assert ctx["domain"] == "example.com"
        assert ctx["ssl"] is True
        assert len(ctx["routes"]) == 3
        assert len(ctx["upstreams"]) > 0
        assert "X-Frame-Options" in ctx["security_headers"]
        assert ctx["security_headers"]["Content-Security-Policy"] == "default-src 'self'"

    def test_context_upstreams(self, builder, sample_config):
        ctx = builder.build_context(sample_config, "example.com")

        # Should have unique upstreams
        upstream_names = [u["name"] for u in ctx["upstreams"].values()]
        assert "frontend" in upstream_names
        assert "backend" in upstream_names

    def test_context_rate_limit_zones(self, builder, sample_config):
        ctx = builder.build_context(sample_config, "example.com")
        # Named after the site: limit_req_zone is http-wide, and two sites
        # declaring the same zone name stop nginx from loading either.
        assert ctx["rate_limit_zones"]["example_com_global"] == "100r/s"

    def test_context_websocket_route(self, builder, sample_config):
        ctx = builder.build_context(sample_config, "example.com")
        ws_routes = [r for r in ctx["routes"] if r["websocket"]]
        assert len(ws_routes) == 1
        assert ws_routes[0]["path"] == "/socket.io"


class TestValidation:
    """Tests for configuration validation."""

    def test_valid_config(self, builder, sample_config):
        errors = builder.validate(sample_config)
        assert len(errors) == 0

    def test_no_routes(self, builder):
        config = NginxAdvancedConfig()
        errors = builder.validate(config)
        assert any("No routes" in e for e in errors)

    def test_duplicate_paths(self, builder):
        config = NginxAdvancedConfig(
            routes=[
                NginxRoute(path="/api", upstream_port=3000),
                NginxRoute(path="/api", upstream_port=3001),
            ]
        )
        errors = builder.validate(config)
        assert any("Duplicate" in e for e in errors)

    def test_invalid_port(self, builder):
        config = NginxAdvancedConfig(
            routes=[
                NginxRoute(path="/", upstream_port=0),
            ]
        )
        errors = builder.validate(config)
        assert any("Invalid port" in e for e in errors)

    def test_invalid_port_too_high(self, builder):
        config = NginxAdvancedConfig(
            routes=[
                NginxRoute(path="/", upstream_port=99999),
            ]
        )
        errors = builder.validate(config)
        assert any("Invalid port" in e for e in errors)

    def test_invalid_rate_limit(self, builder):
        config = NginxAdvancedConfig(
            routes=[
                NginxRoute(path="/", upstream_port=3000),
            ],
            global_rate_limit="invalid",
        )
        errors = builder.validate(config)
        assert any("rate limit" in e.lower() for e in errors)

    def test_valid_rate_limit_formats(self, builder):
        config = NginxAdvancedConfig(
            routes=[
                NginxRoute(path="/", upstream_port=3000, rate_limit="10r/s"),
            ],
            global_rate_limit="100r/m",
        )
        errors = builder.validate(config)
        assert len(errors) == 0


class TestDomains:
    """An app with a wasm.nginx.yaml answers on its aliases and redirects like any other."""

    def test_the_advanced_template_serves_aliases_and_redirects(self, builder, sample_config):
        from noust.managers.nginx_manager import NginxManager

        context = builder.build_context(sample_config, "example.com", ssl=True)
        context["server_names"] = "example.com shop.example.com"
        context["redirect_domains"] = ["www.example.com"]

        rendered = NginxManager().render_config("example.com", "advanced", context)

        assert rendered.count("server_name example.com shop.example.com;") == 2
        assert rendered.count("server_name www.example.com;") == 2
        assert "return 301 https://example.com$request_uri;" in rendered
        assert "upstream frontend {" in rendered


# -- 3.2: a closed schema and the options routes were missing ----------------------


def _load(builder: NginxConfigBuilder, tmp_path, text: str) -> NginxAdvancedConfig:
    path = tmp_path / "noust.nginx.yaml"
    path.write_text(text)
    return builder.parse(path)


def _render(builder: NginxConfigBuilder, config: NginxAdvancedConfig, *, ssl: bool = False) -> str:
    from noust.managers.nginx_manager import NginxManager

    context = builder.build_context(config, "example.com", ssl=ssl, app_path="/var/www/apps/x")
    return NginxManager().render_config("example.com", "advanced", context)


class TestClosedSchema:
    """A key nobody reads is a typo the operator should hear about."""

    def test_an_unknown_top_level_key_is_named(self, builder, tmp_path):
        config = _load(
            builder, tmp_path, "routes:\n  - path: /\n    port: 3000\nratelimit: 10r/s\n"
        )

        errors = builder.validate(config)

        assert any("ratelimit" in e and "unknown" in e.lower() for e in errors)

    def test_an_unknown_route_key_is_named_with_its_route(self, builder, tmp_path):
        config = _load(
            builder, tmp_path, "routes:\n  - path: /api\n    port: 3000\n    websockets: true\n"
        )

        errors = builder.validate(config)

        assert any("routes[0]" in e and "websockets" in e for e in errors)

    @pytest.mark.parametrize("static", ["/etc", "../shared", "public/../../etc"])
    def test_a_static_path_must_stay_inside_the_app(self, builder, tmp_path, static):
        config = _load(builder, tmp_path, f"routes:\n  - path: /assets\n    static: '{static}'\n")

        assert any("static" in e for e in builder.validate(config))

    def test_a_route_is_one_thing(self, builder, tmp_path):
        config = _load(
            builder,
            tmp_path,
            "routes:\n  - path: /\n    port: 3000\n    return: {code: 301, to: /x}\n",
        )

        assert any("routes[0]" in e and "one of" in e for e in builder.validate(config))

    @pytest.mark.parametrize("value", ["10mb", "10x", "-1m"])
    def test_a_body_size_nginx_would_refuse_is_refused(self, builder, tmp_path, value):
        config = _load(
            builder, tmp_path, f"max_body_size: '{value}'\nroutes:\n  - path: /\n    port: 3000\n"
        )

        assert any("max_body_size" in e for e in builder.validate(config))

    @pytest.mark.parametrize("code", [200, 404, 999])
    def test_a_return_is_a_redirect(self, builder, tmp_path, code):
        config = _load(
            builder, tmp_path, f"routes:\n  - path: /old\n    return: {{code: {code}, to: /new}}\n"
        )

        assert any("return" in e for e in builder.validate(config))

    def test_a_value_that_would_end_the_directive_is_refused(self, builder, tmp_path):
        config = _load(
            builder,
            tmp_path,
            "routes:\n  - path: /old\n    return: {code: 301, to: '/x; include /etc/passwd'}\n",
        )

        assert any("return" in e for e in builder.validate(config))


class TestRouteOptions:
    """Each option, rendered."""

    def test_max_body_size_per_route_and_for_the_server(self, builder, tmp_path):
        config = _load(
            builder,
            tmp_path,
            "max_body_size: 20m\nroutes:\n  - path: /upload\n    port: 3000\n    max_body_size: 1g\n"
            "  - path: /\n    port: 3001\n",
        )
        assert builder.validate(config) == []

        text = _render(builder, config)

        assert "    client_max_body_size 20m;" in text
        assert "        client_max_body_size 1g;" in text

    def test_buffering_off(self, builder, tmp_path):
        config = _load(
            builder, tmp_path, "routes:\n  - path: /events\n    port: 3000\n    buffering: false\n"
        )

        text = _render(builder, config)

        assert "proxy_buffering off;" in text
        assert "proxy_buffer_size 128k;" not in text

    def test_read_and_send_timeouts_apart_from_timeout(self, builder, tmp_path):
        config = _load(
            builder,
            tmp_path,
            "routes:\n  - path: /\n    port: 3000\n    timeout: 30\n    read_timeout: 3600\n"
            "    send_timeout: 120\n",
        )

        text = _render(builder, config)

        assert "proxy_connect_timeout 30s;" in text
        assert "proxy_read_timeout 3600s;" in text
        assert "proxy_send_timeout 120s;" in text

    def test_static_is_an_alias_with_an_optional_cache(self, builder, tmp_path):
        config = _load(
            builder,
            tmp_path,
            "routes:\n  - path: /assets/\n    static: public/assets\n    cache: 1y\n"
            "  - path: /\n    port: 3000\n",
        )
        assert builder.validate(config) == []

        text = _render(builder, config)

        assert "location /assets/ {" in text
        assert "alias /var/www/apps/x/public/assets/;" in text
        assert "expires 1y;" in text
        assert "upstream" in text and "zone_" not in text

    def test_return_is_a_redirect(self, builder, tmp_path):
        config = _load(
            builder,
            tmp_path,
            "routes:\n  - path: /old\n    return: {code: 301, to: /new}\n"
            "  - path: /\n    port: 3000\n",
        )
        assert builder.validate(config) == []

        text = _render(builder, config)

        assert "location /old {\n        return 301 /new;\n    }" in text

    def test_the_global_rate_limit_applies_to_routes_without_their_own(self, builder, tmp_path):
        """It declared a zone nobody used."""
        config = _load(
            builder,
            tmp_path,
            "rate_limit: 50r/s\nroutes:\n  - path: /api\n    port: 3000\n    rate_limit: 5r/s\n"
            "  - path: /\n    port: 3001\n",
        )

        text = _render(builder, config)

        assert "limit_req_zone $binary_remote_addr zone=example_com_global:10m rate=50r/s;" in text
        assert "limit_req zone=example_com_global burst=5 nodelay;" in text
        assert "limit_req zone=example_com_api:10m" not in text
        assert "limit_req zone=example_com_api burst=5 nodelay;" in text
        assert text.count("limit_req zone=") == 2

    def test_without_options_the_route_renders_as_before(self, builder, sample_config):
        text = _render(builder, sample_config)

        assert "client_max_body_size" not in text
        assert "proxy_buffering on;" in text
        assert "proxy_read_timeout 60s;" in text


class TestProxyTemplate:
    """
    A noust.nginx.yaml whose routes proxy to no port of their own tunes the proxy site.

    Server options, routes that serve files or redirect, and a ``/`` route
    without a port carrying options for the application's own location.
    """

    def _proxy(self, builder, config, **extra):
        from noust.managers.nginx_manager import NginxManager

        tuning = builder.proxy_context(config, "example.com", app_path=extra.get("app_path", ""))
        context = {"port": 3000, "ssl": False, **tuning, **extra}
        return NginxManager().render_config("example.com", "proxy", context)

    def test_server_options_without_routes_are_valid(self, builder, tmp_path):
        config = _load(builder, tmp_path, "max_body_size: 50m\nrate_limit: 20r/s\n")

        assert builder.validate(config) == []
        assert config.proxies_the_app

    def test_a_route_with_a_port_is_the_advanced_site(self, builder, tmp_path):
        config = _load(builder, tmp_path, "routes:\n  - path: /\n    port: 3000\n")

        assert not config.proxies_the_app

    def test_an_empty_file_still_needs_something(self, builder, tmp_path):
        config = _load(builder, tmp_path, "security_headers: {}\n")

        assert any("No routes" in e for e in builder.validate(config))

    def test_max_body_size(self, builder, tmp_path):
        text = self._proxy(builder, _load(builder, tmp_path, "max_body_size: 50m\n"))

        assert "    client_max_body_size 50m;" in text

    def test_rate_limit(self, builder, tmp_path):
        text = self._proxy(builder, _load(builder, tmp_path, "rate_limit: 20r/s\n"))

        assert "limit_req_zone $binary_remote_addr zone=example_com_global:10m rate=20r/s;" in text
        assert "limit_req zone=example_com_global burst=5 nodelay;" in text

    def test_buffering_and_timeouts_of_the_root(self, builder, tmp_path):
        text = self._proxy(
            builder,
            _load(
                builder,
                tmp_path,
                "routes:\n  - path: /\n    buffering: false\n    read_timeout: 600\n"
                "    send_timeout: 90\n    max_body_size: 2g\n",
            ),
        )

        assert "proxy_buffering off;" in text and "proxy_buffer_size" not in text
        assert "proxy_read_timeout 600s;" in text and "proxy_send_timeout 90s;" in text
        assert "        client_max_body_size 2g;" in text

    def test_static_and_return(self, builder, tmp_path):
        text = self._proxy(
            builder,
            _load(
                builder,
                tmp_path,
                "routes:\n  - path: /media/\n    static: storage/media\n    cache: 30d\n"
                "  - path: /old\n    return: {code: 308, to: 'https://example.com/new'}\n",
            ),
            app_path="/var/www/apps/x",
        )

        assert "alias /var/www/apps/x/storage/media/;" in text and "expires 30d;" in text
        assert "return 308 https://example.com/new;" in text

    def test_without_options_the_proxy_site_is_unchanged(self, builder):
        from noust.managers.nginx_manager import NginxManager

        plain = NginxManager().render_config("example.com", "proxy", {"port": 3000})
        tuned = self._proxy(builder, NginxAdvancedConfig())

        assert tuned == plain
