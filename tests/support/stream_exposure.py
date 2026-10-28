# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_stream_exposure.py`` that other tests build on."""

from __future__ import annotations

from noust.core.exceptions import SecurityError
from noust.managers.server.security_firewall import DirectChange, RuleRequest

DEBIAN_V = (
    "nginx version: nginx/1.24.0 (Ubuntu)\n"
    "configure arguments: --with-cc-opt='-g -O2' --with-http_ssl_module "
    "--with-stream=dynamic --with-stream_ssl_module --with-threads\n"
)

NO_STREAM_V = "nginx version: nginx/1.24.0\nconfigure arguments: --with-http_ssl_module\n"

NGINX_CONF = """\
# configuration file /etc/nginx/nginx.conf:
user www-data;
pid /run/nginx.pid;
include /etc/nginx/modules-enabled/*.conf;
events { worker_connections 768; }
http {
    include /etc/nginx/sites-enabled/*;
}
"""
STREAM_MODULE = """
# configuration file /etc/nginx/modules-enabled/50-mod-stream.conf:
load_module modules/ngx_stream_module.so;
"""


def dump(*, module: bool = True, extra: str = "", conf: str = NGINX_CONF) -> str:
    return conf + (STREAM_MODULE if module else "") + extra


class FakeFirewall:
    """A firewall manager that records what it was asked and answers as told."""

    def __init__(self) -> None:
        self.allowed: list[RuleRequest] = []
        self.removed: list[RuleRequest] = []
        self.already: set[str] = set()
        self.refuse_source: str | None = None
        self.refuse_removal = False

    def allow_direct(self, request: RuleRequest) -> DirectChange:
        if request.source == self.refuse_source:
            raise SecurityError("The firewall refused 'ufw allow'")
        self.allowed.append(request)
        if request.source in self.already:
            return DirectChange("ufw", True, False, "it is already allowed")
        return DirectChange("ufw", True, True)

    def remove_direct(self, request: RuleRequest) -> DirectChange:
        if self.refuse_removal:
            raise SecurityError("The firewall refused 'ufw delete'")
        self.removed.append(request)
        return DirectChange("ufw", True, True)
