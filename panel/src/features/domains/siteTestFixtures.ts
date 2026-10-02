/**
 * Site models for the domain views' tests, produced by the real analyzer
 * (noust.managers.siteconf) from a small site and from Proggest's
 * (tests/fixtures/siteconf/proggest/proggest.es): what the API answers, field for field.
 */

import type { SiteEdit, SiteRoute, SiteStructure, SiteTopology } from "../../api/queries/sites";

/** A shop: an upstream, a plain HTTP server that redirects, a TLS server with five locations, a map and an if. */
export const SMALL_CONFIG = "map $http_upgrade $connection_upgrade {\n    default upgrade;\n    '' close;\n}\n\nupstream shop_backend {\n    server 127.0.0.1:3000;\n    keepalive 16;\n}\n\n# Plain HTTP only sends visitors to HTTPS.\nserver {\n    listen 80;\n    listen [::]:80;\n    server_name shop.example.com;\n\n    location / {\n        return 301 https://$host$request_uri;\n    }\n}\n\nserver {\n    listen 443 ssl;\n    listen [::]:443 ssl;\n    http2 on;\n    server_name shop.example.com www.shop.example.com;\n\n    ssl_certificate /etc/letsencrypt/live/shop.example.com/fullchain.pem;\n    ssl_certificate_key /etc/letsencrypt/live/shop.example.com/privkey.pem;\n    client_max_body_size 20m;\n\n    if ($host = www.shop.example.com) {\n        return 301 https://shop.example.com$request_uri;\n    }\n\n    # Sign-in is rate limited before it reaches the app.\n    location /api/v1/auth/login {\n        limit_req zone=auth_limit burst=20 nodelay;\n        proxy_pass http://shop_backend;\n        proxy_set_header Host $host;\n    }\n\n    location /api/ {\n        proxy_pass http://shop_backend;\n        proxy_http_version 1.1;\n        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection \"upgrade\";\n        proxy_read_timeout 120s;\n        proxy_buffering off;\n    }\n\n    location /assets/ {\n        alias /var/www/shop/assets/;\n        expires 30d;\n    }\n\n    location = /old {\n        return 302 /new;\n    }\n\n    location ~ /\\. {\n        deny all;\n    }\n}\n";

export const SMALL_STRUCTURE: SiteStructure = {
  "kind": "nginx",
  "servers": [
    {
      "id": "s0",
      "line": 12,
      "end_line": 20,
      "listens": [
        {
          "id": "s0/d0",
          "address": null,
          "port": 80,
          "ssl": false,
          "http2": false,
          "quic": false,
          "ipv6": false,
          "default_server": false,
          "raw": "80"
        },
        {
          "id": "s0/d1",
          "address": "[::]",
          "port": 80,
          "ssl": false,
          "http2": false,
          "quic": false,
          "ipv6": true,
          "default_server": false,
          "raw": "[::]:80"
        }
      ],
      "names": [
        "shop.example.com"
      ],
      "tls": null,
      "http2": false,
      "root": null,
      "headers": [],
      "gzip": null,
      "client_max_body_size": null,
      "returns": null,
      "rewrites": [],
      "locations": [
        {
          "id": "s0/l0",
          "modifier": "",
          "path": "/",
          "source": "location",
          "line": 17,
          "end_line": 19,
          "target": {
            "kind": "return",
            "directive": "s0/l0/d0",
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": 301,
            "destination": "https://$host$request_uri",
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s0/l0/d0",
              "name": "return",
              "args": [
                "301",
                "https://$host$request_uri"
              ],
              "text": "return 301 https://$host$request_uri;",
              "block": false,
              "line": 18,
              "end_line": 18,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        }
      ],
      "evaluation_order": [
        "s0/l0"
      ],
      "directives": [
        {
          "id": "s0/d0",
          "name": "listen",
          "args": [
            "80"
          ],
          "text": "listen 80;",
          "block": false,
          "line": 13,
          "end_line": 13,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s0/d1",
          "name": "listen",
          "args": [
            "[::]:80"
          ],
          "text": "listen [::]:80;",
          "block": false,
          "line": 14,
          "end_line": 14,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s0/d2",
          "name": "server_name",
          "args": [
            "shop.example.com"
          ],
          "text": "server_name shop.example.com;",
          "block": false,
          "line": 15,
          "end_line": 15,
          "comments": [],
          "modeled": true
        }
      ],
      "comments": [
        "Plain HTTP only sends visitors to HTTPS."
      ],
      "notes": []
    },
    {
      "id": "s1",
      "line": 22,
      "end_line": 64,
      "listens": [
        {
          "id": "s1/d0",
          "address": null,
          "port": 443,
          "ssl": true,
          "http2": false,
          "quic": false,
          "ipv6": false,
          "default_server": false,
          "raw": "443 ssl"
        },
        {
          "id": "s1/d1",
          "address": "[::]",
          "port": 443,
          "ssl": true,
          "http2": false,
          "quic": false,
          "ipv6": true,
          "default_server": false,
          "raw": "[::]:443 ssl"
        }
      ],
      "names": [
        "shop.example.com",
        "www.shop.example.com"
      ],
      "tls": {
        "certificate": "/etc/letsencrypt/live/shop.example.com/fullchain.pem",
        "key": "/etc/letsencrypt/live/shop.example.com/privkey.pem",
        "protocols": []
      },
      "http2": true,
      "root": null,
      "headers": [],
      "gzip": null,
      "client_max_body_size": "20m",
      "returns": null,
      "rewrites": [],
      "locations": [
        {
          "id": "s1/l0",
          "modifier": "",
          "path": "/api/v1/auth/login",
          "source": "location",
          "line": 37,
          "end_line": 41,
          "target": {
            "kind": "proxy",
            "directive": "s1/l0/d1",
            "url": "http://shop_backend",
            "protocol": "http",
            "upstream": "shop_backend",
            "host": "shop_backend",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": {
              "zone": "auth_limit",
              "burst": "20",
              "nodelay": true
            },
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l0/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l0/d0",
              "name": "limit_req",
              "args": [
                "zone=auth_limit",
                "burst=20",
                "nodelay"
              ],
              "text": "limit_req zone=auth_limit burst=20 nodelay;",
              "block": false,
              "line": 38,
              "end_line": 38,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l0/d1",
              "name": "proxy_pass",
              "args": [
                "http://shop_backend"
              ],
              "text": "proxy_pass http://shop_backend;",
              "block": false,
              "line": 39,
              "end_line": 39,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l0/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 40,
              "end_line": 40,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [
            "Sign-in is rate limited before it reaches the app."
          ],
          "notes": []
        },
        {
          "id": "s1/l1",
          "modifier": "",
          "path": "/api/",
          "source": "location",
          "line": 43,
          "end_line": 50,
          "target": {
            "kind": "proxy",
            "directive": "s1/l1/d0",
            "url": "http://shop_backend",
            "protocol": "http",
            "upstream": "shop_backend",
            "host": "shop_backend",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": "120s",
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": true,
            "buffering": false,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l1/d2",
                "name": "Upgrade",
                "value": "$http_upgrade",
                "always": false
              },
              {
                "id": "s1/l1/d3",
                "name": "Connection",
                "value": "upgrade",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l1/d0",
              "name": "proxy_pass",
              "args": [
                "http://shop_backend"
              ],
              "text": "proxy_pass http://shop_backend;",
              "block": false,
              "line": 44,
              "end_line": 44,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 45,
              "end_line": 45,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l1/d2",
              "name": "proxy_set_header",
              "args": [
                "Upgrade",
                "$http_upgrade"
              ],
              "text": "proxy_set_header Upgrade $http_upgrade;",
              "block": false,
              "line": 46,
              "end_line": 46,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d3",
              "name": "proxy_set_header",
              "args": [
                "Connection",
                "upgrade"
              ],
              "text": "proxy_set_header Connection \"upgrade\";",
              "block": false,
              "line": 47,
              "end_line": 47,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d4",
              "name": "proxy_read_timeout",
              "args": [
                "120s"
              ],
              "text": "proxy_read_timeout 120s;",
              "block": false,
              "line": 48,
              "end_line": 48,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d5",
              "name": "proxy_buffering",
              "args": [
                "off"
              ],
              "text": "proxy_buffering off;",
              "block": false,
              "line": 49,
              "end_line": 49,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l2",
          "modifier": "",
          "path": "/assets/",
          "source": "location",
          "line": 52,
          "end_line": 55,
          "target": {
            "kind": "static",
            "directive": "s1/l2/d0",
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": null,
            "alias": "/var/www/shop/assets/",
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": "30d",
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s1/l2/d0",
              "name": "alias",
              "args": [
                "/var/www/shop/assets/"
              ],
              "text": "alias /var/www/shop/assets/;",
              "block": false,
              "line": 53,
              "end_line": 53,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l2/d1",
              "name": "expires",
              "args": [
                "30d"
              ],
              "text": "expires 30d;",
              "block": false,
              "line": 54,
              "end_line": 54,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l3",
          "modifier": "=",
          "path": "/old",
          "source": "location",
          "line": 57,
          "end_line": 59,
          "target": {
            "kind": "return",
            "directive": "s1/l3/d0",
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": 302,
            "destination": "/new",
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s1/l3/d0",
              "name": "return",
              "args": [
                "302",
                "/new"
              ],
              "text": "return 302 /new;",
              "block": false,
              "line": 58,
              "end_line": 58,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l4",
          "modifier": "~",
          "path": "/\\.",
          "source": "location",
          "line": 61,
          "end_line": 63,
          "target": {
            "kind": "other",
            "directive": null,
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": "deny all"
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": true,
            "headers": [],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s1/l4/d0",
              "name": "deny",
              "args": [
                "all"
              ],
              "text": "deny all;",
              "block": false,
              "line": 62,
              "end_line": 62,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        }
      ],
      "evaluation_order": [
        "s1/l3",
        "s1/l0",
        "s1/l2",
        "s1/l1",
        "s1/l4"
      ],
      "directives": [
        {
          "id": "s1/d0",
          "name": "listen",
          "args": [
            "443",
            "ssl"
          ],
          "text": "listen 443 ssl;",
          "block": false,
          "line": 23,
          "end_line": 23,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d1",
          "name": "listen",
          "args": [
            "[::]:443",
            "ssl"
          ],
          "text": "listen [::]:443 ssl;",
          "block": false,
          "line": 24,
          "end_line": 24,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d2",
          "name": "http2",
          "args": [
            "on"
          ],
          "text": "http2 on;",
          "block": false,
          "line": 25,
          "end_line": 25,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d3",
          "name": "server_name",
          "args": [
            "shop.example.com",
            "www.shop.example.com"
          ],
          "text": "server_name shop.example.com www.shop.example.com;",
          "block": false,
          "line": 26,
          "end_line": 26,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d4",
          "name": "ssl_certificate",
          "args": [
            "/etc/letsencrypt/live/shop.example.com/fullchain.pem"
          ],
          "text": "ssl_certificate /etc/letsencrypt/live/shop.example.com/fullchain.pem;",
          "block": false,
          "line": 28,
          "end_line": 28,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d5",
          "name": "ssl_certificate_key",
          "args": [
            "/etc/letsencrypt/live/shop.example.com/privkey.pem"
          ],
          "text": "ssl_certificate_key /etc/letsencrypt/live/shop.example.com/privkey.pem;",
          "block": false,
          "line": 29,
          "end_line": 29,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d6",
          "name": "client_max_body_size",
          "args": [
            "20m"
          ],
          "text": "client_max_body_size 20m;",
          "block": false,
          "line": 30,
          "end_line": 30,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d7",
          "name": "if",
          "args": [
            "($host",
            "=",
            "www.shop.example.com)"
          ],
          "text": "if ($host = www.shop.example.com) {\n        return 301 https://shop.example.com$request_uri;\n    }",
          "block": true,
          "line": 32,
          "end_line": 34,
          "comments": [],
          "modeled": false
        }
      ],
      "comments": [],
      "notes": []
    }
  ],
  "upstreams": [
    {
      "id": "u:shop_backend",
      "name": "shop_backend",
      "line": 6,
      "end_line": 9,
      "servers": [
        {
          "address": "127.0.0.1:3000",
          "params": [],
          "id": "u:shop_backend/d0",
          "source": null
        }
      ],
      "keepalive": 16,
      "directives": [
        {
          "id": "u:shop_backend/d0",
          "name": "server",
          "args": [
            "127.0.0.1:3000"
          ],
          "text": "server 127.0.0.1:3000;",
          "block": false,
          "line": 7,
          "end_line": 7,
          "comments": [],
          "modeled": true
        },
        {
          "id": "u:shop_backend/d1",
          "name": "keepalive",
          "args": [
            "16"
          ],
          "text": "keepalive 16;",
          "block": false,
          "line": 8,
          "end_line": 8,
          "comments": [],
          "modeled": true
        }
      ],
      "comments": [],
      "notes": [],
      "used_by": [
        "s1/l0",
        "s1/l1"
      ],
      "source": null
    }
  ],
  "includes": [],
  "directives": [
    {
      "id": "d0",
      "name": "map",
      "args": [
        "$http_upgrade",
        "$connection_upgrade"
      ],
      "text": "map $http_upgrade $connection_upgrade {\n    default upgrade;\n    '' close;\n}",
      "block": true,
      "line": 1,
      "end_line": 4,
      "comments": [],
      "modeled": false
    }
  ],
  "notes": []
};

/** The read timeout of /api/v1/auth/login set to 90s. */
export const SMALL_EDIT_TIMEOUT: SiteEdit = {
  "config": "map $http_upgrade $connection_upgrade {\n    default upgrade;\n    '' close;\n}\n\nupstream shop_backend {\n    server 127.0.0.1:3000;\n    keepalive 16;\n}\n\n# Plain HTTP only sends visitors to HTTPS.\nserver {\n    listen 80;\n    listen [::]:80;\n    server_name shop.example.com;\n\n    location / {\n        return 301 https://$host$request_uri;\n    }\n}\n\nserver {\n    listen 443 ssl;\n    listen [::]:443 ssl;\n    http2 on;\n    server_name shop.example.com www.shop.example.com;\n\n    ssl_certificate /etc/letsencrypt/live/shop.example.com/fullchain.pem;\n    ssl_certificate_key /etc/letsencrypt/live/shop.example.com/privkey.pem;\n    client_max_body_size 20m;\n\n    if ($host = www.shop.example.com) {\n        return 301 https://shop.example.com$request_uri;\n    }\n\n    # Sign-in is rate limited before it reaches the app.\n    location /api/v1/auth/login {\n        limit_req zone=auth_limit burst=20 nodelay;\n        proxy_pass http://shop_backend;\n        proxy_set_header Host $host;\n        proxy_read_timeout 90s;\n    }\n\n    location /api/ {\n        proxy_pass http://shop_backend;\n        proxy_http_version 1.1;\n        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection \"upgrade\";\n        proxy_read_timeout 120s;\n        proxy_buffering off;\n    }\n\n    location /assets/ {\n        alias /var/www/shop/assets/;\n        expires 30d;\n    }\n\n    location = /old {\n        return 302 /new;\n    }\n\n    location ~ /\\. {\n        deny all;\n    }\n}\n",
  "structure": {
    "kind": "nginx",
    "servers": [
      {
        "id": "s0",
        "line": 12,
        "end_line": 20,
        "listens": [
          {
            "id": "s0/d0",
            "address": null,
            "port": 80,
            "ssl": false,
            "http2": false,
            "quic": false,
            "ipv6": false,
            "default_server": false,
            "raw": "80"
          },
          {
            "id": "s0/d1",
            "address": "[::]",
            "port": 80,
            "ssl": false,
            "http2": false,
            "quic": false,
            "ipv6": true,
            "default_server": false,
            "raw": "[::]:80"
          }
        ],
        "names": [
          "shop.example.com"
        ],
        "tls": null,
        "http2": false,
        "root": null,
        "headers": [],
        "gzip": null,
        "client_max_body_size": null,
        "returns": null,
        "rewrites": [],
        "locations": [
          {
            "id": "s0/l0",
            "modifier": "",
            "path": "/",
            "source": "location",
            "line": 17,
            "end_line": 19,
            "target": {
              "kind": "return",
              "directive": "s0/l0/d0",
              "url": null,
              "protocol": null,
              "upstream": null,
              "host": null,
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": 301,
              "destination": "https://$host$request_uri",
              "detail": null
            },
            "settings": {
              "read_timeout": null,
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": false,
              "buffering": null,
              "expires": null,
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": []
            },
            "directives": [
              {
                "id": "s0/l0/d0",
                "name": "return",
                "args": [
                  "301",
                  "https://$host$request_uri"
                ],
                "text": "return 301 https://$host$request_uri;",
                "block": false,
                "line": 18,
                "end_line": 18,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          }
        ],
        "evaluation_order": [
          "s0/l0"
        ],
        "directives": [
          {
            "id": "s0/d0",
            "name": "listen",
            "args": [
              "80"
            ],
            "text": "listen 80;",
            "block": false,
            "line": 13,
            "end_line": 13,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s0/d1",
            "name": "listen",
            "args": [
              "[::]:80"
            ],
            "text": "listen [::]:80;",
            "block": false,
            "line": 14,
            "end_line": 14,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s0/d2",
            "name": "server_name",
            "args": [
              "shop.example.com"
            ],
            "text": "server_name shop.example.com;",
            "block": false,
            "line": 15,
            "end_line": 15,
            "comments": [],
            "modeled": true
          }
        ],
        "comments": [
          "Plain HTTP only sends visitors to HTTPS."
        ],
        "notes": []
      },
      {
        "id": "s1",
        "line": 22,
        "end_line": 65,
        "listens": [
          {
            "id": "s1/d0",
            "address": null,
            "port": 443,
            "ssl": true,
            "http2": false,
            "quic": false,
            "ipv6": false,
            "default_server": false,
            "raw": "443 ssl"
          },
          {
            "id": "s1/d1",
            "address": "[::]",
            "port": 443,
            "ssl": true,
            "http2": false,
            "quic": false,
            "ipv6": true,
            "default_server": false,
            "raw": "[::]:443 ssl"
          }
        ],
        "names": [
          "shop.example.com",
          "www.shop.example.com"
        ],
        "tls": {
          "certificate": "/etc/letsencrypt/live/shop.example.com/fullchain.pem",
          "key": "/etc/letsencrypt/live/shop.example.com/privkey.pem",
          "protocols": []
        },
        "http2": true,
        "root": null,
        "headers": [],
        "gzip": null,
        "client_max_body_size": "20m",
        "returns": null,
        "rewrites": [],
        "locations": [
          {
            "id": "s1/l0",
            "modifier": "",
            "path": "/api/v1/auth/login",
            "source": "location",
            "line": 37,
            "end_line": 42,
            "target": {
              "kind": "proxy",
              "directive": "s1/l0/d1",
              "url": "http://shop_backend",
              "protocol": "http",
              "upstream": "shop_backend",
              "host": "shop_backend",
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": null,
              "destination": null,
              "detail": null
            },
            "settings": {
              "read_timeout": "90s",
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": {
                "zone": "auth_limit",
                "burst": "20",
                "nodelay": true
              },
              "websocket": false,
              "buffering": null,
              "expires": null,
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": [
                {
                  "id": "s1/l0/d2",
                  "name": "Host",
                  "value": "$host",
                  "always": false
                }
              ]
            },
            "directives": [
              {
                "id": "s1/l0/d0",
                "name": "limit_req",
                "args": [
                  "zone=auth_limit",
                  "burst=20",
                  "nodelay"
                ],
                "text": "limit_req zone=auth_limit burst=20 nodelay;",
                "block": false,
                "line": 38,
                "end_line": 38,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l0/d1",
                "name": "proxy_pass",
                "args": [
                  "http://shop_backend"
                ],
                "text": "proxy_pass http://shop_backend;",
                "block": false,
                "line": 39,
                "end_line": 39,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l0/d2",
                "name": "proxy_set_header",
                "args": [
                  "Host",
                  "$host"
                ],
                "text": "proxy_set_header Host $host;",
                "block": false,
                "line": 40,
                "end_line": 40,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l0/d3",
                "name": "proxy_read_timeout",
                "args": [
                  "90s"
                ],
                "text": "proxy_read_timeout 90s;",
                "block": false,
                "line": 41,
                "end_line": 41,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [
              "Sign-in is rate limited before it reaches the app."
            ],
            "notes": []
          },
          {
            "id": "s1/l1",
            "modifier": "",
            "path": "/api/",
            "source": "location",
            "line": 44,
            "end_line": 51,
            "target": {
              "kind": "proxy",
              "directive": "s1/l1/d0",
              "url": "http://shop_backend",
              "protocol": "http",
              "upstream": "shop_backend",
              "host": "shop_backend",
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": null,
              "destination": null,
              "detail": null
            },
            "settings": {
              "read_timeout": "120s",
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": true,
              "buffering": false,
              "expires": null,
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": [
                {
                  "id": "s1/l1/d2",
                  "name": "Upgrade",
                  "value": "$http_upgrade",
                  "always": false
                },
                {
                  "id": "s1/l1/d3",
                  "name": "Connection",
                  "value": "upgrade",
                  "always": false
                }
              ]
            },
            "directives": [
              {
                "id": "s1/l1/d0",
                "name": "proxy_pass",
                "args": [
                  "http://shop_backend"
                ],
                "text": "proxy_pass http://shop_backend;",
                "block": false,
                "line": 45,
                "end_line": 45,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l1/d1",
                "name": "proxy_http_version",
                "args": [
                  "1.1"
                ],
                "text": "proxy_http_version 1.1;",
                "block": false,
                "line": 46,
                "end_line": 46,
                "comments": [],
                "modeled": false
              },
              {
                "id": "s1/l1/d2",
                "name": "proxy_set_header",
                "args": [
                  "Upgrade",
                  "$http_upgrade"
                ],
                "text": "proxy_set_header Upgrade $http_upgrade;",
                "block": false,
                "line": 47,
                "end_line": 47,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l1/d3",
                "name": "proxy_set_header",
                "args": [
                  "Connection",
                  "upgrade"
                ],
                "text": "proxy_set_header Connection \"upgrade\";",
                "block": false,
                "line": 48,
                "end_line": 48,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l1/d4",
                "name": "proxy_read_timeout",
                "args": [
                  "120s"
                ],
                "text": "proxy_read_timeout 120s;",
                "block": false,
                "line": 49,
                "end_line": 49,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l1/d5",
                "name": "proxy_buffering",
                "args": [
                  "off"
                ],
                "text": "proxy_buffering off;",
                "block": false,
                "line": 50,
                "end_line": 50,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          },
          {
            "id": "s1/l2",
            "modifier": "",
            "path": "/assets/",
            "source": "location",
            "line": 53,
            "end_line": 56,
            "target": {
              "kind": "static",
              "directive": "s1/l2/d0",
              "url": null,
              "protocol": null,
              "upstream": null,
              "host": null,
              "port": null,
              "address": null,
              "root": null,
              "alias": "/var/www/shop/assets/",
              "inherited": false,
              "code": null,
              "destination": null,
              "detail": null
            },
            "settings": {
              "read_timeout": null,
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": false,
              "buffering": null,
              "expires": "30d",
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": []
            },
            "directives": [
              {
                "id": "s1/l2/d0",
                "name": "alias",
                "args": [
                  "/var/www/shop/assets/"
                ],
                "text": "alias /var/www/shop/assets/;",
                "block": false,
                "line": 54,
                "end_line": 54,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l2/d1",
                "name": "expires",
                "args": [
                  "30d"
                ],
                "text": "expires 30d;",
                "block": false,
                "line": 55,
                "end_line": 55,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          },
          {
            "id": "s1/l3",
            "modifier": "=",
            "path": "/old",
            "source": "location",
            "line": 58,
            "end_line": 60,
            "target": {
              "kind": "return",
              "directive": "s1/l3/d0",
              "url": null,
              "protocol": null,
              "upstream": null,
              "host": null,
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": 302,
              "destination": "/new",
              "detail": null
            },
            "settings": {
              "read_timeout": null,
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": false,
              "buffering": null,
              "expires": null,
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": []
            },
            "directives": [
              {
                "id": "s1/l3/d0",
                "name": "return",
                "args": [
                  "302",
                  "/new"
                ],
                "text": "return 302 /new;",
                "block": false,
                "line": 59,
                "end_line": 59,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          },
          {
            "id": "s1/l4",
            "modifier": "~",
            "path": "/\\.",
            "source": "location",
            "line": 62,
            "end_line": 64,
            "target": {
              "kind": "other",
              "directive": null,
              "url": null,
              "protocol": null,
              "upstream": null,
              "host": null,
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": null,
              "destination": null,
              "detail": "deny all"
            },
            "settings": {
              "read_timeout": null,
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": false,
              "buffering": null,
              "expires": null,
              "cache": null,
              "deny": true,
              "headers": [],
              "proxy_headers": []
            },
            "directives": [
              {
                "id": "s1/l4/d0",
                "name": "deny",
                "args": [
                  "all"
                ],
                "text": "deny all;",
                "block": false,
                "line": 63,
                "end_line": 63,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          }
        ],
        "evaluation_order": [
          "s1/l3",
          "s1/l0",
          "s1/l2",
          "s1/l1",
          "s1/l4"
        ],
        "directives": [
          {
            "id": "s1/d0",
            "name": "listen",
            "args": [
              "443",
              "ssl"
            ],
            "text": "listen 443 ssl;",
            "block": false,
            "line": 23,
            "end_line": 23,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d1",
            "name": "listen",
            "args": [
              "[::]:443",
              "ssl"
            ],
            "text": "listen [::]:443 ssl;",
            "block": false,
            "line": 24,
            "end_line": 24,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d2",
            "name": "http2",
            "args": [
              "on"
            ],
            "text": "http2 on;",
            "block": false,
            "line": 25,
            "end_line": 25,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d3",
            "name": "server_name",
            "args": [
              "shop.example.com",
              "www.shop.example.com"
            ],
            "text": "server_name shop.example.com www.shop.example.com;",
            "block": false,
            "line": 26,
            "end_line": 26,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d4",
            "name": "ssl_certificate",
            "args": [
              "/etc/letsencrypt/live/shop.example.com/fullchain.pem"
            ],
            "text": "ssl_certificate /etc/letsencrypt/live/shop.example.com/fullchain.pem;",
            "block": false,
            "line": 28,
            "end_line": 28,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d5",
            "name": "ssl_certificate_key",
            "args": [
              "/etc/letsencrypt/live/shop.example.com/privkey.pem"
            ],
            "text": "ssl_certificate_key /etc/letsencrypt/live/shop.example.com/privkey.pem;",
            "block": false,
            "line": 29,
            "end_line": 29,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d6",
            "name": "client_max_body_size",
            "args": [
              "20m"
            ],
            "text": "client_max_body_size 20m;",
            "block": false,
            "line": 30,
            "end_line": 30,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d7",
            "name": "if",
            "args": [
              "($host",
              "=",
              "www.shop.example.com)"
            ],
            "text": "if ($host = www.shop.example.com) {\n        return 301 https://shop.example.com$request_uri;\n    }",
            "block": true,
            "line": 32,
            "end_line": 34,
            "comments": [],
            "modeled": false
          }
        ],
        "comments": [],
        "notes": []
      }
    ],
    "upstreams": [
      {
        "id": "u:shop_backend",
        "name": "shop_backend",
        "line": 6,
        "end_line": 9,
        "servers": [
          {
            "address": "127.0.0.1:3000",
            "params": [],
            "id": "u:shop_backend/d0",
            "source": null
          }
        ],
        "keepalive": 16,
        "directives": [
          {
            "id": "u:shop_backend/d0",
            "name": "server",
            "args": [
              "127.0.0.1:3000"
            ],
            "text": "server 127.0.0.1:3000;",
            "block": false,
            "line": 7,
            "end_line": 7,
            "comments": [],
            "modeled": true
          },
          {
            "id": "u:shop_backend/d1",
            "name": "keepalive",
            "args": [
              "16"
            ],
            "text": "keepalive 16;",
            "block": false,
            "line": 8,
            "end_line": 8,
            "comments": [],
            "modeled": true
          }
        ],
        "comments": [],
        "notes": [],
        "used_by": [
          "s1/l0",
          "s1/l1"
        ],
        "source": null
      }
    ],
    "includes": [],
    "directives": [
      {
        "id": "d0",
        "name": "map",
        "args": [
          "$http_upgrade",
          "$connection_upgrade"
        ],
        "text": "map $http_upgrade $connection_upgrade {\n    default upgrade;\n    '' close;\n}",
        "block": true,
        "line": 1,
        "end_line": 4,
        "comments": [],
        "modeled": false
      }
    ],
    "notes": []
  },
  "changed_lines": 1
};

/** A /health location added from the proxy template, to shop_backend. */
export const SMALL_EDIT_ADD_HEALTH: SiteEdit = {
  "config": "map $http_upgrade $connection_upgrade {\n    default upgrade;\n    '' close;\n}\n\nupstream shop_backend {\n    server 127.0.0.1:3000;\n    keepalive 16;\n}\n\n# Plain HTTP only sends visitors to HTTPS.\nserver {\n    listen 80;\n    listen [::]:80;\n    server_name shop.example.com;\n\n    location / {\n        return 301 https://$host$request_uri;\n    }\n}\n\nserver {\n    listen 443 ssl;\n    listen [::]:443 ssl;\n    http2 on;\n    server_name shop.example.com www.shop.example.com;\n\n    ssl_certificate /etc/letsencrypt/live/shop.example.com/fullchain.pem;\n    ssl_certificate_key /etc/letsencrypt/live/shop.example.com/privkey.pem;\n    client_max_body_size 20m;\n\n    if ($host = www.shop.example.com) {\n        return 301 https://shop.example.com$request_uri;\n    }\n\n    # Sign-in is rate limited before it reaches the app.\n    location /api/v1/auth/login {\n        limit_req zone=auth_limit burst=20 nodelay;\n        proxy_pass http://shop_backend;\n        proxy_set_header Host $host;\n    }\n\n    location /api/ {\n        proxy_pass http://shop_backend;\n        proxy_http_version 1.1;\n        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection \"upgrade\";\n        proxy_read_timeout 120s;\n        proxy_buffering off;\n    }\n\n    location /assets/ {\n        alias /var/www/shop/assets/;\n        expires 30d;\n    }\n\n    location = /old {\n        return 302 /new;\n    }\n\n    location ~ /\\. {\n        deny all;\n    }\n\n    location /health {\n        proxy_pass http://shop_backend;\n        proxy_http_version 1.1;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n    }\n}\n",
  "structure": {
    "kind": "nginx",
    "servers": [
      {
        "id": "s0",
        "line": 12,
        "end_line": 20,
        "listens": [
          {
            "id": "s0/d0",
            "address": null,
            "port": 80,
            "ssl": false,
            "http2": false,
            "quic": false,
            "ipv6": false,
            "default_server": false,
            "raw": "80"
          },
          {
            "id": "s0/d1",
            "address": "[::]",
            "port": 80,
            "ssl": false,
            "http2": false,
            "quic": false,
            "ipv6": true,
            "default_server": false,
            "raw": "[::]:80"
          }
        ],
        "names": [
          "shop.example.com"
        ],
        "tls": null,
        "http2": false,
        "root": null,
        "headers": [],
        "gzip": null,
        "client_max_body_size": null,
        "returns": null,
        "rewrites": [],
        "locations": [
          {
            "id": "s0/l0",
            "modifier": "",
            "path": "/",
            "source": "location",
            "line": 17,
            "end_line": 19,
            "target": {
              "kind": "return",
              "directive": "s0/l0/d0",
              "url": null,
              "protocol": null,
              "upstream": null,
              "host": null,
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": 301,
              "destination": "https://$host$request_uri",
              "detail": null
            },
            "settings": {
              "read_timeout": null,
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": false,
              "buffering": null,
              "expires": null,
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": []
            },
            "directives": [
              {
                "id": "s0/l0/d0",
                "name": "return",
                "args": [
                  "301",
                  "https://$host$request_uri"
                ],
                "text": "return 301 https://$host$request_uri;",
                "block": false,
                "line": 18,
                "end_line": 18,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          }
        ],
        "evaluation_order": [
          "s0/l0"
        ],
        "directives": [
          {
            "id": "s0/d0",
            "name": "listen",
            "args": [
              "80"
            ],
            "text": "listen 80;",
            "block": false,
            "line": 13,
            "end_line": 13,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s0/d1",
            "name": "listen",
            "args": [
              "[::]:80"
            ],
            "text": "listen [::]:80;",
            "block": false,
            "line": 14,
            "end_line": 14,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s0/d2",
            "name": "server_name",
            "args": [
              "shop.example.com"
            ],
            "text": "server_name shop.example.com;",
            "block": false,
            "line": 15,
            "end_line": 15,
            "comments": [],
            "modeled": true
          }
        ],
        "comments": [
          "Plain HTTP only sends visitors to HTTPS."
        ],
        "notes": []
      },
      {
        "id": "s1",
        "line": 22,
        "end_line": 73,
        "listens": [
          {
            "id": "s1/d0",
            "address": null,
            "port": 443,
            "ssl": true,
            "http2": false,
            "quic": false,
            "ipv6": false,
            "default_server": false,
            "raw": "443 ssl"
          },
          {
            "id": "s1/d1",
            "address": "[::]",
            "port": 443,
            "ssl": true,
            "http2": false,
            "quic": false,
            "ipv6": true,
            "default_server": false,
            "raw": "[::]:443 ssl"
          }
        ],
        "names": [
          "shop.example.com",
          "www.shop.example.com"
        ],
        "tls": {
          "certificate": "/etc/letsencrypt/live/shop.example.com/fullchain.pem",
          "key": "/etc/letsencrypt/live/shop.example.com/privkey.pem",
          "protocols": []
        },
        "http2": true,
        "root": null,
        "headers": [],
        "gzip": null,
        "client_max_body_size": "20m",
        "returns": null,
        "rewrites": [],
        "locations": [
          {
            "id": "s1/l0",
            "modifier": "",
            "path": "/api/v1/auth/login",
            "source": "location",
            "line": 37,
            "end_line": 41,
            "target": {
              "kind": "proxy",
              "directive": "s1/l0/d1",
              "url": "http://shop_backend",
              "protocol": "http",
              "upstream": "shop_backend",
              "host": "shop_backend",
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": null,
              "destination": null,
              "detail": null
            },
            "settings": {
              "read_timeout": null,
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": {
                "zone": "auth_limit",
                "burst": "20",
                "nodelay": true
              },
              "websocket": false,
              "buffering": null,
              "expires": null,
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": [
                {
                  "id": "s1/l0/d2",
                  "name": "Host",
                  "value": "$host",
                  "always": false
                }
              ]
            },
            "directives": [
              {
                "id": "s1/l0/d0",
                "name": "limit_req",
                "args": [
                  "zone=auth_limit",
                  "burst=20",
                  "nodelay"
                ],
                "text": "limit_req zone=auth_limit burst=20 nodelay;",
                "block": false,
                "line": 38,
                "end_line": 38,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l0/d1",
                "name": "proxy_pass",
                "args": [
                  "http://shop_backend"
                ],
                "text": "proxy_pass http://shop_backend;",
                "block": false,
                "line": 39,
                "end_line": 39,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l0/d2",
                "name": "proxy_set_header",
                "args": [
                  "Host",
                  "$host"
                ],
                "text": "proxy_set_header Host $host;",
                "block": false,
                "line": 40,
                "end_line": 40,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [
              "Sign-in is rate limited before it reaches the app."
            ],
            "notes": []
          },
          {
            "id": "s1/l1",
            "modifier": "",
            "path": "/api/",
            "source": "location",
            "line": 43,
            "end_line": 50,
            "target": {
              "kind": "proxy",
              "directive": "s1/l1/d0",
              "url": "http://shop_backend",
              "protocol": "http",
              "upstream": "shop_backend",
              "host": "shop_backend",
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": null,
              "destination": null,
              "detail": null
            },
            "settings": {
              "read_timeout": "120s",
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": true,
              "buffering": false,
              "expires": null,
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": [
                {
                  "id": "s1/l1/d2",
                  "name": "Upgrade",
                  "value": "$http_upgrade",
                  "always": false
                },
                {
                  "id": "s1/l1/d3",
                  "name": "Connection",
                  "value": "upgrade",
                  "always": false
                }
              ]
            },
            "directives": [
              {
                "id": "s1/l1/d0",
                "name": "proxy_pass",
                "args": [
                  "http://shop_backend"
                ],
                "text": "proxy_pass http://shop_backend;",
                "block": false,
                "line": 44,
                "end_line": 44,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l1/d1",
                "name": "proxy_http_version",
                "args": [
                  "1.1"
                ],
                "text": "proxy_http_version 1.1;",
                "block": false,
                "line": 45,
                "end_line": 45,
                "comments": [],
                "modeled": false
              },
              {
                "id": "s1/l1/d2",
                "name": "proxy_set_header",
                "args": [
                  "Upgrade",
                  "$http_upgrade"
                ],
                "text": "proxy_set_header Upgrade $http_upgrade;",
                "block": false,
                "line": 46,
                "end_line": 46,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l1/d3",
                "name": "proxy_set_header",
                "args": [
                  "Connection",
                  "upgrade"
                ],
                "text": "proxy_set_header Connection \"upgrade\";",
                "block": false,
                "line": 47,
                "end_line": 47,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l1/d4",
                "name": "proxy_read_timeout",
                "args": [
                  "120s"
                ],
                "text": "proxy_read_timeout 120s;",
                "block": false,
                "line": 48,
                "end_line": 48,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l1/d5",
                "name": "proxy_buffering",
                "args": [
                  "off"
                ],
                "text": "proxy_buffering off;",
                "block": false,
                "line": 49,
                "end_line": 49,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          },
          {
            "id": "s1/l2",
            "modifier": "",
            "path": "/assets/",
            "source": "location",
            "line": 52,
            "end_line": 55,
            "target": {
              "kind": "static",
              "directive": "s1/l2/d0",
              "url": null,
              "protocol": null,
              "upstream": null,
              "host": null,
              "port": null,
              "address": null,
              "root": null,
              "alias": "/var/www/shop/assets/",
              "inherited": false,
              "code": null,
              "destination": null,
              "detail": null
            },
            "settings": {
              "read_timeout": null,
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": false,
              "buffering": null,
              "expires": "30d",
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": []
            },
            "directives": [
              {
                "id": "s1/l2/d0",
                "name": "alias",
                "args": [
                  "/var/www/shop/assets/"
                ],
                "text": "alias /var/www/shop/assets/;",
                "block": false,
                "line": 53,
                "end_line": 53,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l2/d1",
                "name": "expires",
                "args": [
                  "30d"
                ],
                "text": "expires 30d;",
                "block": false,
                "line": 54,
                "end_line": 54,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          },
          {
            "id": "s1/l3",
            "modifier": "=",
            "path": "/old",
            "source": "location",
            "line": 57,
            "end_line": 59,
            "target": {
              "kind": "return",
              "directive": "s1/l3/d0",
              "url": null,
              "protocol": null,
              "upstream": null,
              "host": null,
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": 302,
              "destination": "/new",
              "detail": null
            },
            "settings": {
              "read_timeout": null,
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": false,
              "buffering": null,
              "expires": null,
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": []
            },
            "directives": [
              {
                "id": "s1/l3/d0",
                "name": "return",
                "args": [
                  "302",
                  "/new"
                ],
                "text": "return 302 /new;",
                "block": false,
                "line": 58,
                "end_line": 58,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          },
          {
            "id": "s1/l4",
            "modifier": "~",
            "path": "/\\.",
            "source": "location",
            "line": 61,
            "end_line": 63,
            "target": {
              "kind": "other",
              "directive": null,
              "url": null,
              "protocol": null,
              "upstream": null,
              "host": null,
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": null,
              "destination": null,
              "detail": "deny all"
            },
            "settings": {
              "read_timeout": null,
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": false,
              "buffering": null,
              "expires": null,
              "cache": null,
              "deny": true,
              "headers": [],
              "proxy_headers": []
            },
            "directives": [
              {
                "id": "s1/l4/d0",
                "name": "deny",
                "args": [
                  "all"
                ],
                "text": "deny all;",
                "block": false,
                "line": 62,
                "end_line": 62,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          },
          {
            "id": "s1/l5",
            "modifier": "",
            "path": "/health",
            "source": "location",
            "line": 65,
            "end_line": 72,
            "target": {
              "kind": "proxy",
              "directive": "s1/l5/d0",
              "url": "http://shop_backend",
              "protocol": "http",
              "upstream": "shop_backend",
              "host": "shop_backend",
              "port": null,
              "address": null,
              "root": null,
              "alias": null,
              "inherited": false,
              "code": null,
              "destination": null,
              "detail": null
            },
            "settings": {
              "read_timeout": null,
              "send_timeout": null,
              "connect_timeout": null,
              "client_max_body_size": null,
              "limit_req": null,
              "websocket": false,
              "buffering": null,
              "expires": null,
              "cache": null,
              "deny": false,
              "headers": [],
              "proxy_headers": [
                {
                  "id": "s1/l5/d2",
                  "name": "Host",
                  "value": "$host",
                  "always": false
                },
                {
                  "id": "s1/l5/d3",
                  "name": "X-Real-IP",
                  "value": "$remote_addr",
                  "always": false
                },
                {
                  "id": "s1/l5/d4",
                  "name": "X-Forwarded-For",
                  "value": "$proxy_add_x_forwarded_for",
                  "always": false
                },
                {
                  "id": "s1/l5/d5",
                  "name": "X-Forwarded-Proto",
                  "value": "$scheme",
                  "always": false
                }
              ]
            },
            "directives": [
              {
                "id": "s1/l5/d0",
                "name": "proxy_pass",
                "args": [
                  "http://shop_backend"
                ],
                "text": "proxy_pass http://shop_backend;",
                "block": false,
                "line": 66,
                "end_line": 66,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l5/d1",
                "name": "proxy_http_version",
                "args": [
                  "1.1"
                ],
                "text": "proxy_http_version 1.1;",
                "block": false,
                "line": 67,
                "end_line": 67,
                "comments": [],
                "modeled": false
              },
              {
                "id": "s1/l5/d2",
                "name": "proxy_set_header",
                "args": [
                  "Host",
                  "$host"
                ],
                "text": "proxy_set_header Host $host;",
                "block": false,
                "line": 68,
                "end_line": 68,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l5/d3",
                "name": "proxy_set_header",
                "args": [
                  "X-Real-IP",
                  "$remote_addr"
                ],
                "text": "proxy_set_header X-Real-IP $remote_addr;",
                "block": false,
                "line": 69,
                "end_line": 69,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l5/d4",
                "name": "proxy_set_header",
                "args": [
                  "X-Forwarded-For",
                  "$proxy_add_x_forwarded_for"
                ],
                "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
                "block": false,
                "line": 70,
                "end_line": 70,
                "comments": [],
                "modeled": true
              },
              {
                "id": "s1/l5/d5",
                "name": "proxy_set_header",
                "args": [
                  "X-Forwarded-Proto",
                  "$scheme"
                ],
                "text": "proxy_set_header X-Forwarded-Proto $scheme;",
                "block": false,
                "line": 71,
                "end_line": 71,
                "comments": [],
                "modeled": true
              }
            ],
            "locations": [],
            "evaluation_order": [],
            "comments": [],
            "notes": []
          }
        ],
        "evaluation_order": [
          "s1/l3",
          "s1/l0",
          "s1/l2",
          "s1/l5",
          "s1/l1",
          "s1/l4"
        ],
        "directives": [
          {
            "id": "s1/d0",
            "name": "listen",
            "args": [
              "443",
              "ssl"
            ],
            "text": "listen 443 ssl;",
            "block": false,
            "line": 23,
            "end_line": 23,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d1",
            "name": "listen",
            "args": [
              "[::]:443",
              "ssl"
            ],
            "text": "listen [::]:443 ssl;",
            "block": false,
            "line": 24,
            "end_line": 24,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d2",
            "name": "http2",
            "args": [
              "on"
            ],
            "text": "http2 on;",
            "block": false,
            "line": 25,
            "end_line": 25,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d3",
            "name": "server_name",
            "args": [
              "shop.example.com",
              "www.shop.example.com"
            ],
            "text": "server_name shop.example.com www.shop.example.com;",
            "block": false,
            "line": 26,
            "end_line": 26,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d4",
            "name": "ssl_certificate",
            "args": [
              "/etc/letsencrypt/live/shop.example.com/fullchain.pem"
            ],
            "text": "ssl_certificate /etc/letsencrypt/live/shop.example.com/fullchain.pem;",
            "block": false,
            "line": 28,
            "end_line": 28,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d5",
            "name": "ssl_certificate_key",
            "args": [
              "/etc/letsencrypt/live/shop.example.com/privkey.pem"
            ],
            "text": "ssl_certificate_key /etc/letsencrypt/live/shop.example.com/privkey.pem;",
            "block": false,
            "line": 29,
            "end_line": 29,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d6",
            "name": "client_max_body_size",
            "args": [
              "20m"
            ],
            "text": "client_max_body_size 20m;",
            "block": false,
            "line": 30,
            "end_line": 30,
            "comments": [],
            "modeled": true
          },
          {
            "id": "s1/d7",
            "name": "if",
            "args": [
              "($host",
              "=",
              "www.shop.example.com)"
            ],
            "text": "if ($host = www.shop.example.com) {\n        return 301 https://shop.example.com$request_uri;\n    }",
            "block": true,
            "line": 32,
            "end_line": 34,
            "comments": [],
            "modeled": false
          }
        ],
        "comments": [],
        "notes": []
      }
    ],
    "upstreams": [
      {
        "id": "u:shop_backend",
        "name": "shop_backend",
        "line": 6,
        "end_line": 9,
        "servers": [
          {
            "address": "127.0.0.1:3000",
            "params": [],
            "id": "u:shop_backend/d0",
            "source": null
          }
        ],
        "keepalive": 16,
        "directives": [
          {
            "id": "u:shop_backend/d0",
            "name": "server",
            "args": [
              "127.0.0.1:3000"
            ],
            "text": "server 127.0.0.1:3000;",
            "block": false,
            "line": 7,
            "end_line": 7,
            "comments": [],
            "modeled": true
          },
          {
            "id": "u:shop_backend/d1",
            "name": "keepalive",
            "args": [
              "16"
            ],
            "text": "keepalive 16;",
            "block": false,
            "line": 8,
            "end_line": 8,
            "comments": [],
            "modeled": true
          }
        ],
        "comments": [],
        "notes": [],
        "used_by": [
          "s1/l0",
          "s1/l1",
          "s1/l5"
        ],
        "source": null
      }
    ],
    "includes": [],
    "directives": [
      {
        "id": "d0",
        "name": "map",
        "args": [
          "$http_upgrade",
          "$connection_upgrade"
        ],
        "text": "map $http_upgrade $connection_upgrade {\n    default upgrade;\n    '' close;\n}",
        "block": true,
        "line": 1,
        "end_line": 4,
        "comments": [],
        "modeled": false
      }
    ],
    "notes": []
  },
  "changed_lines": 9
};

/** https://shop.example.com/api/v1/auth/login */
export const SMALL_ROUTE_LOGIN: SiteRoute = {
  "server_id": "s1",
  "location_id": "s1/l0",
  "steps": [
    "Servers listening on port 443: s1.",
    "shop.example.com is the exact name shop.example.com of s1.",
    "/api/v1/auth/login starts with the prefixes /api/, /api/v1/auth/login: the longest wins, /api/v1/auth/login.",
    "No regular expression location matches /api/v1/auth/login, so the prefix /api/v1/auth/login answers.",
    "The request goes to upstream shop_backend."
  ],
  "trace": [
    {
      "code": "port",
      "params": {
        "port": 443,
        "servers": "s1"
      },
      "text": "Servers listening on port 443: s1."
    },
    {
      "code": "server_exact",
      "params": {
        "host": "shop.example.com",
        "name": "shop.example.com",
        "server": "s1"
      },
      "text": "shop.example.com is the exact name shop.example.com of s1."
    },
    {
      "code": "location_prefixes",
      "params": {
        "path": "/api/v1/auth/login",
        "prefixes": "/api/, /api/v1/auth/login",
        "location": "/api/v1/auth/login"
      },
      "text": "/api/v1/auth/login starts with the prefixes /api/, /api/v1/auth/login: the longest wins, /api/v1/auth/login."
    },
    {
      "code": "location_no_regex",
      "params": {
        "path": "/api/v1/auth/login",
        "location": "/api/v1/auth/login"
      },
      "text": "No regular expression location matches /api/v1/auth/login, so the prefix /api/v1/auth/login answers."
    },
    {
      "code": "target_upstream",
      "params": {
        "upstream": "shop_backend"
      },
      "text": "The request goes to upstream shop_backend."
    }
  ],
  "highlight": [
    "s1",
    "s1/l0",
    "u:shop_backend"
  ],
  "redirect": null
};

/** The shop's backend (not answering) and its certificate. */
export const SMALL_TOPOLOGY_FACTS: Pick<SiteTopology, "backends" | "certificates" | "docker"> = {
  "backends": [
    {
      "address": "127.0.0.1:3000",
      "written": [
        "127.0.0.1:3000"
      ],
      "host": "127.0.0.1",
      "port": 3000,
      "local": true,
      "upstreams": [
        "shop_backend"
      ],
      "locations": [
        "s1/l0",
        "s1/l1"
      ],
      "owner": {
        "kind": "app",
        "app": "shop.example.com",
        "project": null,
        "service": null,
        "container": null,
        "unit": "shop-example-com.service",
        "process": null,
        "pid": null
      },
      "listening": true,
      "reachable": false
    }
  ],
  "certificates": [
    {
      "server_id": "s1",
      "path": "/etc/letsencrypt/live/shop.example.com/fullchain.pem",
      "name": "shop.example.com",
      "domains": [
        "shop.example.com",
        "www.shop.example.com"
      ],
      "expiry": "2026-12-01",
      "days_left": 60
    }
  ],
  "docker": false
};

/** Proggest's site: two servers, 25 locations, two upstreams. */
export const PROGGEST_STRUCTURE: SiteStructure = {
  "kind": "nginx",
  "servers": [
    {
      "id": "s0",
      "line": 12,
      "end_line": 24,
      "listens": [
        {
          "id": "s0/d0",
          "address": null,
          "port": 80,
          "ssl": false,
          "http2": false,
          "quic": false,
          "ipv6": false,
          "default_server": false,
          "raw": "80"
        },
        {
          "id": "s0/d1",
          "address": "[::]",
          "port": 80,
          "ssl": false,
          "http2": false,
          "quic": false,
          "ipv6": true,
          "default_server": false,
          "raw": "[::]:80"
        }
      ],
      "names": [
        "proggest.es",
        "www.proggest.es"
      ],
      "tls": null,
      "http2": false,
      "root": null,
      "headers": [],
      "gzip": null,
      "client_max_body_size": null,
      "returns": null,
      "rewrites": [],
      "locations": [
        {
          "id": "s0/l0",
          "modifier": "",
          "path": "/.well-known/acme-challenge/",
          "source": "location",
          "line": 17,
          "end_line": 19,
          "target": {
            "kind": "static",
            "directive": "s0/l0/d0",
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": "/var/www/certbot",
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s0/l0/d0",
              "name": "root",
              "args": [
                "/var/www/certbot"
              ],
              "text": "root /var/www/certbot;",
              "block": false,
              "line": 18,
              "end_line": 18,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s0/l1",
          "modifier": "",
          "path": "/",
          "source": "location",
          "line": 21,
          "end_line": 23,
          "target": {
            "kind": "return",
            "directive": "s0/l1/d0",
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": 301,
            "destination": "https://$host$request_uri",
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s0/l1/d0",
              "name": "return",
              "args": [
                "301",
                "https://$host$request_uri"
              ],
              "text": "return 301 https://$host$request_uri;",
              "block": false,
              "line": 22,
              "end_line": 22,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        }
      ],
      "evaluation_order": [
        "s0/l0",
        "s0/l1"
      ],
      "directives": [
        {
          "id": "s0/d0",
          "name": "listen",
          "args": [
            "80"
          ],
          "text": "listen 80;",
          "block": false,
          "line": 13,
          "end_line": 13,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s0/d1",
          "name": "listen",
          "args": [
            "[::]:80"
          ],
          "text": "listen [::]:80;",
          "block": false,
          "line": 14,
          "end_line": 14,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s0/d2",
          "name": "server_name",
          "args": [
            "proggest.es",
            "www.proggest.es"
          ],
          "text": "server_name proggest.es www.proggest.es;",
          "block": false,
          "line": 15,
          "end_line": 15,
          "comments": [],
          "modeled": true
        }
      ],
      "comments": [
        "HTTP server (redirect to HTTPS)"
      ],
      "notes": []
    },
    {
      "id": "s1",
      "line": 27,
      "end_line": 347,
      "listens": [
        {
          "id": "s1/d0",
          "address": null,
          "port": 443,
          "ssl": true,
          "http2": false,
          "quic": false,
          "ipv6": false,
          "default_server": false,
          "raw": "443 ssl"
        },
        {
          "id": "s1/d2",
          "address": "[::]",
          "port": 443,
          "ssl": true,
          "http2": false,
          "quic": false,
          "ipv6": true,
          "default_server": false,
          "raw": "[::]:443 ssl"
        }
      ],
      "names": [
        "proggest.es",
        "www.proggest.es"
      ],
      "tls": {
        "certificate": "/etc/letsencrypt/live/proggest.es/fullchain.pem",
        "key": "/etc/letsencrypt/live/proggest.es/privkey.pem",
        "protocols": [
          "TLSv1.2",
          "TLSv1.3"
        ]
      },
      "http2": true,
      "root": null,
      "headers": [
        {
          "id": "s1/d12",
          "name": "Strict-Transport-Security",
          "value": "max-age=63072000; includeSubDomains",
          "always": true
        },
        {
          "id": "s1/d13",
          "name": "X-Frame-Options",
          "value": "SAMEORIGIN",
          "always": true
        },
        {
          "id": "s1/d14",
          "name": "X-Content-Type-Options",
          "value": "nosniff",
          "always": true
        },
        {
          "id": "s1/d15",
          "name": "X-XSS-Protection",
          "value": "1; mode=block",
          "always": true
        },
        {
          "id": "s1/d16",
          "name": "Referrer-Policy",
          "value": "strict-origin-when-cross-origin",
          "always": true
        }
      ],
      "gzip": true,
      "client_max_body_size": null,
      "returns": null,
      "rewrites": [],
      "locations": [
        {
          "id": "s1/l0",
          "modifier": "",
          "path": "/health",
          "source": "location",
          "line": 55,
          "end_line": 59,
          "target": {
            "kind": "return",
            "directive": "s1/l0/d1",
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": 200,
            "destination": "healthy\n",
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [
              {
                "id": "s1/l0/d2",
                "name": "Content-Type",
                "value": "text/plain",
                "always": false
              }
            ],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s1/l0/d0",
              "name": "access_log",
              "args": [
                "off"
              ],
              "text": "access_log off;",
              "block": false,
              "line": 56,
              "end_line": 56,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l0/d1",
              "name": "return",
              "args": [
                "200",
                "healthy\n"
              ],
              "text": "return 200 \"healthy\\n\";",
              "block": false,
              "line": 57,
              "end_line": 57,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l0/d2",
              "name": "add_header",
              "args": [
                "Content-Type",
                "text/plain"
              ],
              "text": "add_header Content-Type text/plain;",
              "block": false,
              "line": 58,
              "end_line": 58,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l1",
          "modifier": "",
          "path": "/api/v1/work-reports/public/",
          "source": "location",
          "line": 69,
          "end_line": 79,
          "target": {
            "kind": "proxy",
            "directive": "s1/l1/d1",
            "url": "http://nestjs_upstream",
            "protocol": "http",
            "upstream": "nestjs_upstream",
            "host": "nestjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": "120s",
            "send_timeout": "120s",
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": {
              "zone": "auth_limit",
              "burst": "10",
              "nodelay": true
            },
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l1/d3",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l1/d4",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l1/d5",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l1/d6",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l1/d0",
              "name": "limit_req",
              "args": [
                "zone=auth_limit",
                "burst=10",
                "nodelay"
              ],
              "text": "limit_req zone=auth_limit burst=10 nodelay;",
              "block": false,
              "line": 70,
              "end_line": 70,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d1",
              "name": "proxy_pass",
              "args": [
                "http://nestjs_upstream"
              ],
              "text": "proxy_pass http://nestjs_upstream;",
              "block": false,
              "line": 71,
              "end_line": 71,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d2",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 72,
              "end_line": 72,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l1/d3",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 73,
              "end_line": 73,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 74,
              "end_line": 74,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 75,
              "end_line": 75,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d6",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 76,
              "end_line": 76,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d7",
              "name": "proxy_read_timeout",
              "args": [
                "120s"
              ],
              "text": "proxy_read_timeout 120s;",
              "block": false,
              "line": 77,
              "end_line": 77,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l1/d8",
              "name": "proxy_send_timeout",
              "args": [
                "120s"
              ],
              "text": "proxy_send_timeout 120s;",
              "block": false,
              "line": 78,
              "end_line": 78,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [
            "Acceso público de partes para los ayuntamientos.",
            "",
            "El límite va aquí y no solo en el @Throttle de Nest porque este bloque ve",
            "la IP real del visitante: la petición llega directa del navegador. El",
            "throttle de la aplicación es la segunda barrera.",
            "",
            "El PDF puede tardar si el parte lleva anexo fotográfico, de ahí el timeout",
            "ampliado: los `location` no heredan los del bloque raíz."
          ],
          "notes": []
        },
        {
          "id": "s1/l2",
          "modifier": "",
          "path": "/api/v1/auth/login",
          "source": "location",
          "line": 81,
          "end_line": 89,
          "target": {
            "kind": "proxy",
            "directive": "s1/l2/d1",
            "url": "http://nestjs_upstream",
            "protocol": "http",
            "upstream": "nestjs_upstream",
            "host": "nestjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": {
              "zone": "auth_limit",
              "burst": "20",
              "nodelay": true
            },
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l2/d3",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l2/d4",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l2/d5",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l2/d6",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l2/d0",
              "name": "limit_req",
              "args": [
                "zone=auth_limit",
                "burst=20",
                "nodelay"
              ],
              "text": "limit_req zone=auth_limit burst=20 nodelay;",
              "block": false,
              "line": 82,
              "end_line": 82,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l2/d1",
              "name": "proxy_pass",
              "args": [
                "http://nestjs_upstream"
              ],
              "text": "proxy_pass http://nestjs_upstream;",
              "block": false,
              "line": 83,
              "end_line": 83,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l2/d2",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 84,
              "end_line": 84,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l2/d3",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 85,
              "end_line": 85,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l2/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 86,
              "end_line": 86,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l2/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 87,
              "end_line": 87,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l2/d6",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 88,
              "end_line": 88,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l3",
          "modifier": "",
          "path": "/api/auth/",
          "source": "location",
          "line": 91,
          "end_line": 106,
          "target": {
            "kind": "proxy",
            "directive": "s1/l3/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": true,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l3/d2",
                "name": "Upgrade",
                "value": "$http_upgrade",
                "always": false
              },
              {
                "id": "s1/l3/d3",
                "name": "Connection",
                "value": "upgrade",
                "always": false
              },
              {
                "id": "s1/l3/d4",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l3/d5",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l3/d6",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l3/d7",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l3/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 92,
              "end_line": 92,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l3/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 93,
              "end_line": 93,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l3/d2",
              "name": "proxy_set_header",
              "args": [
                "Upgrade",
                "$http_upgrade"
              ],
              "text": "proxy_set_header Upgrade $http_upgrade;",
              "block": false,
              "line": 94,
              "end_line": 94,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l3/d3",
              "name": "proxy_set_header",
              "args": [
                "Connection",
                "upgrade"
              ],
              "text": "proxy_set_header Connection 'upgrade';",
              "block": false,
              "line": 95,
              "end_line": 95,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l3/d4",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 96,
              "end_line": 96,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l3/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 97,
              "end_line": 97,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l3/d6",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 98,
              "end_line": 98,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l3/d7",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 99,
              "end_line": 99,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l3/d8",
              "name": "proxy_cache_bypass",
              "args": [
                "$http_upgrade"
              ],
              "text": "proxy_cache_bypass $http_upgrade;",
              "block": false,
              "line": 100,
              "end_line": 100,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l3/d9",
              "name": "proxy_buffer_size",
              "args": [
                "128k"
              ],
              "text": "proxy_buffer_size 128k;",
              "block": false,
              "line": 103,
              "end_line": 103,
              "comments": [
                "Aumentar buffers para NextAuth headers grandes"
              ],
              "modeled": false
            },
            {
              "id": "s1/l3/d10",
              "name": "proxy_buffers",
              "args": [
                "4",
                "256k"
              ],
              "text": "proxy_buffers 4 256k;",
              "block": false,
              "line": 104,
              "end_line": 104,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l3/d11",
              "name": "proxy_busy_buffers_size",
              "args": [
                "256k"
              ],
              "text": "proxy_busy_buffers_size 256k;",
              "block": false,
              "line": 105,
              "end_line": 105,
              "comments": [],
              "modeled": false
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l4",
          "modifier": "",
          "path": "/api/user/",
          "source": "location",
          "line": 108,
          "end_line": 115,
          "target": {
            "kind": "proxy",
            "directive": "s1/l4/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l4/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l4/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l4/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l4/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l4/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 109,
              "end_line": 109,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l4/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 110,
              "end_line": 110,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l4/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 111,
              "end_line": 111,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l4/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 112,
              "end_line": 112,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l4/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 113,
              "end_line": 113,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l4/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 114,
              "end_line": 114,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l5",
          "modifier": "",
          "path": "/api/settings/",
          "source": "location",
          "line": 121,
          "end_line": 129,
          "target": {
            "kind": "proxy",
            "directive": "s1/l5/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": "10G",
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l5/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l5/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l5/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l5/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l5/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 122,
              "end_line": 122,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l5/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 123,
              "end_line": 123,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l5/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 124,
              "end_line": 124,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l5/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 125,
              "end_line": 125,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l5/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 126,
              "end_line": 126,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l5/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 127,
              "end_line": 127,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l5/d6",
              "name": "client_max_body_size",
              "args": [
                "10G"
              ],
              "text": "client_max_body_size 10G;",
              "block": false,
              "line": 128,
              "end_line": 128,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l6",
          "modifier": "",
          "path": "/api/clocking/photo",
          "source": "location",
          "line": 131,
          "end_line": 139,
          "target": {
            "kind": "proxy",
            "directive": "s1/l6/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": "10G",
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l6/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l6/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l6/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l6/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l6/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 132,
              "end_line": 132,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l6/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 133,
              "end_line": 133,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l6/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 134,
              "end_line": 134,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l6/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 135,
              "end_line": 135,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l6/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 136,
              "end_line": 136,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l6/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 137,
              "end_line": 137,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l6/d6",
              "name": "client_max_body_size",
              "args": [
                "10G"
              ],
              "text": "client_max_body_size 10G;",
              "block": false,
              "line": 138,
              "end_line": 138,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l7",
          "modifier": "",
          "path": "/api/upload/",
          "source": "location",
          "line": 141,
          "end_line": 149,
          "target": {
            "kind": "proxy",
            "directive": "s1/l7/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": "10G",
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l7/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l7/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l7/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l7/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l7/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 142,
              "end_line": 142,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l7/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 143,
              "end_line": 143,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l7/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 144,
              "end_line": 144,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l7/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 145,
              "end_line": 145,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l7/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 146,
              "end_line": 146,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l7/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 147,
              "end_line": 147,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l7/d6",
              "name": "client_max_body_size",
              "args": [
                "10G"
              ],
              "text": "client_max_body_size 10G;",
              "block": false,
              "line": 148,
              "end_line": 148,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l8",
          "modifier": "",
          "path": "/api/super-admin/",
          "source": "location",
          "line": 151,
          "end_line": 159,
          "target": {
            "kind": "proxy",
            "directive": "s1/l8/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": "10G",
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l8/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l8/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l8/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l8/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l8/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 152,
              "end_line": 152,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l8/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 153,
              "end_line": 153,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l8/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 154,
              "end_line": 154,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l8/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 155,
              "end_line": 155,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l8/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 156,
              "end_line": 156,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l8/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 157,
              "end_line": 157,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l8/d6",
              "name": "client_max_body_size",
              "args": [
                "10G"
              ],
              "text": "client_max_body_size 10G;",
              "block": false,
              "line": 158,
              "end_line": 158,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l9",
          "modifier": "",
          "path": "/api/proxy/",
          "source": "location",
          "line": 161,
          "end_line": 168,
          "target": {
            "kind": "proxy",
            "directive": "s1/l9/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l9/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l9/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l9/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l9/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l9/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 162,
              "end_line": 162,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l9/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 163,
              "end_line": 163,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l9/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 164,
              "end_line": 164,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l9/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 165,
              "end_line": 165,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l9/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 166,
              "end_line": 166,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l9/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 167,
              "end_line": 167,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l10",
          "modifier": "",
          "path": "/api/app-info",
          "source": "location",
          "line": 170,
          "end_line": 177,
          "target": {
            "kind": "proxy",
            "directive": "s1/l10/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l10/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l10/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l10/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l10/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l10/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 171,
              "end_line": 171,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l10/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 172,
              "end_line": 172,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l10/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 173,
              "end_line": 173,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l10/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 174,
              "end_line": 174,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l10/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 175,
              "end_line": 175,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l10/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 176,
              "end_line": 176,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l11",
          "modifier": "",
          "path": "/api/health",
          "source": "location",
          "line": 179,
          "end_line": 186,
          "target": {
            "kind": "proxy",
            "directive": "s1/l11/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l11/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l11/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l11/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l11/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l11/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 180,
              "end_line": 180,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l11/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 181,
              "end_line": 181,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l11/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 182,
              "end_line": 182,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l11/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 183,
              "end_line": 183,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l11/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 184,
              "end_line": 184,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l11/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 185,
              "end_line": 185,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l12",
          "modifier": "",
          "path": "/api/manifest",
          "source": "location",
          "line": 188,
          "end_line": 195,
          "target": {
            "kind": "proxy",
            "directive": "s1/l12/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l12/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l12/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l12/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l12/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l12/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 189,
              "end_line": 189,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l12/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 190,
              "end_line": 190,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l12/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 191,
              "end_line": 191,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l12/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 192,
              "end_line": 192,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l12/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 193,
              "end_line": 193,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l12/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 194,
              "end_line": 194,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l13",
          "modifier": "",
          "path": "/api/files/",
          "source": "location",
          "line": 202,
          "end_line": 213,
          "target": {
            "kind": "proxy",
            "directive": "s1/l13/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": "300s",
            "send_timeout": "300s",
            "connect_timeout": null,
            "client_max_body_size": "10G",
            "limit_req": null,
            "websocket": false,
            "buffering": false,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l13/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l13/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l13/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l13/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l13/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 203,
              "end_line": 203,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l13/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 204,
              "end_line": 204,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l13/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 205,
              "end_line": 205,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l13/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 206,
              "end_line": 206,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l13/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 207,
              "end_line": 207,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l13/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 208,
              "end_line": 208,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l13/d6",
              "name": "proxy_read_timeout",
              "args": [
                "300s"
              ],
              "text": "proxy_read_timeout 300s;",
              "block": false,
              "line": 209,
              "end_line": 209,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l13/d7",
              "name": "proxy_send_timeout",
              "args": [
                "300s"
              ],
              "text": "proxy_send_timeout 300s;",
              "block": false,
              "line": 210,
              "end_line": 210,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l13/d8",
              "name": "proxy_buffering",
              "args": [
                "off"
              ],
              "text": "proxy_buffering off;",
              "block": false,
              "line": 211,
              "end_line": 211,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l13/d9",
              "name": "client_max_body_size",
              "args": [
                "10G"
              ],
              "text": "client_max_body_size 10G;",
              "block": false,
              "line": 212,
              "end_line": 212,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [
            "Proxy de ficheros del gestor documental (Next.js -> NestJS -> S3).",
            "Debe ir ANTES del catch-all /api/, que apunta a NestJS: sin este bloque",
            "el visor de documentos y las descargas responderian 404.",
            "Sin buffering y con timeout largo porque un ZIP de una emision completa",
            "se genera al vuelo y se streamea."
          ],
          "notes": []
        },
        {
          "id": "s1/l14",
          "modifier": "",
          "path": "/api/",
          "source": "location",
          "line": 216,
          "end_line": 230,
          "target": {
            "kind": "proxy",
            "directive": "s1/l14/d1",
            "url": "http://nestjs_upstream",
            "protocol": "http",
            "upstream": "nestjs_upstream",
            "host": "nestjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": "90s",
            "send_timeout": null,
            "connect_timeout": "90s",
            "client_max_body_size": "10G",
            "limit_req": {
              "zone": "api_general",
              "burst": "200",
              "nodelay": true
            },
            "websocket": true,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l14/d3",
                "name": "Upgrade",
                "value": "$http_upgrade",
                "always": false
              },
              {
                "id": "s1/l14/d4",
                "name": "Connection",
                "value": "upgrade",
                "always": false
              },
              {
                "id": "s1/l14/d5",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l14/d6",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l14/d7",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l14/d8",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l14/d0",
              "name": "limit_req",
              "args": [
                "zone=api_general",
                "burst=200",
                "nodelay"
              ],
              "text": "limit_req zone=api_general burst=200 nodelay;",
              "block": false,
              "line": 217,
              "end_line": 217,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l14/d1",
              "name": "proxy_pass",
              "args": [
                "http://nestjs_upstream"
              ],
              "text": "proxy_pass http://nestjs_upstream;",
              "block": false,
              "line": 218,
              "end_line": 218,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l14/d2",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 219,
              "end_line": 219,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l14/d3",
              "name": "proxy_set_header",
              "args": [
                "Upgrade",
                "$http_upgrade"
              ],
              "text": "proxy_set_header Upgrade $http_upgrade;",
              "block": false,
              "line": 220,
              "end_line": 220,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l14/d4",
              "name": "proxy_set_header",
              "args": [
                "Connection",
                "upgrade"
              ],
              "text": "proxy_set_header Connection 'upgrade';",
              "block": false,
              "line": 221,
              "end_line": 221,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l14/d5",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 222,
              "end_line": 222,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l14/d6",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 223,
              "end_line": 223,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l14/d7",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 224,
              "end_line": 224,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l14/d8",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 225,
              "end_line": 225,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l14/d9",
              "name": "proxy_cache_bypass",
              "args": [
                "$http_upgrade"
              ],
              "text": "proxy_cache_bypass $http_upgrade;",
              "block": false,
              "line": 226,
              "end_line": 226,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l14/d10",
              "name": "proxy_read_timeout",
              "args": [
                "90s"
              ],
              "text": "proxy_read_timeout 90s;",
              "block": false,
              "line": 227,
              "end_line": 227,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l14/d11",
              "name": "proxy_connect_timeout",
              "args": [
                "90s"
              ],
              "text": "proxy_connect_timeout 90s;",
              "block": false,
              "line": 228,
              "end_line": 228,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l14/d12",
              "name": "client_max_body_size",
              "args": [
                "10G"
              ],
              "text": "client_max_body_size 10G;",
              "block": false,
              "line": 229,
              "end_line": 229,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [
            "Catch-all API -> NestJS backend"
          ],
          "notes": []
        },
        {
          "id": "s1/l15",
          "modifier": "",
          "path": "/socket.io",
          "source": "location",
          "line": 233,
          "end_line": 244,
          "target": {
            "kind": "proxy",
            "directive": "s1/l15/d0",
            "url": "http://nestjs_upstream",
            "protocol": "http",
            "upstream": "nestjs_upstream",
            "host": "nestjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": "86400s",
            "send_timeout": "86400s",
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": true,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l15/d2",
                "name": "Upgrade",
                "value": "$http_upgrade",
                "always": false
              },
              {
                "id": "s1/l15/d3",
                "name": "Connection",
                "value": "upgrade",
                "always": false
              },
              {
                "id": "s1/l15/d4",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l15/d5",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l15/d6",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l15/d7",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l15/d0",
              "name": "proxy_pass",
              "args": [
                "http://nestjs_upstream"
              ],
              "text": "proxy_pass http://nestjs_upstream;",
              "block": false,
              "line": 234,
              "end_line": 234,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l15/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 235,
              "end_line": 235,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l15/d2",
              "name": "proxy_set_header",
              "args": [
                "Upgrade",
                "$http_upgrade"
              ],
              "text": "proxy_set_header Upgrade $http_upgrade;",
              "block": false,
              "line": 236,
              "end_line": 236,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l15/d3",
              "name": "proxy_set_header",
              "args": [
                "Connection",
                "upgrade"
              ],
              "text": "proxy_set_header Connection \"upgrade\";",
              "block": false,
              "line": 237,
              "end_line": 237,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l15/d4",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 238,
              "end_line": 238,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l15/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 239,
              "end_line": 239,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l15/d6",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 240,
              "end_line": 240,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l15/d7",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 241,
              "end_line": 241,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l15/d8",
              "name": "proxy_read_timeout",
              "args": [
                "86400s"
              ],
              "text": "proxy_read_timeout 86400s;",
              "block": false,
              "line": 242,
              "end_line": 242,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l15/d9",
              "name": "proxy_send_timeout",
              "args": [
                "86400s"
              ],
              "text": "proxy_send_timeout 86400s;",
              "block": false,
              "line": 243,
              "end_line": 243,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [
            "WebSocket para notificaciones en tiempo real (Socket.IO -> NestJS)"
          ],
          "notes": []
        },
        {
          "id": "s1/l16",
          "modifier": "",
          "path": "/admin/queues",
          "source": "location",
          "line": 246,
          "end_line": 257,
          "target": {
            "kind": "proxy",
            "directive": "s1/l16/d1",
            "url": "http://nestjs_upstream",
            "protocol": "http",
            "upstream": "nestjs_upstream",
            "host": "nestjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": {
              "zone": "auth_limit",
              "burst": "10",
              "nodelay": true
            },
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l16/d3",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l16/d4",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l16/d5",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l16/d6",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l16/d0",
              "name": "limit_req",
              "args": [
                "zone=auth_limit",
                "burst=10",
                "nodelay"
              ],
              "text": "limit_req zone=auth_limit burst=10 nodelay;",
              "block": false,
              "line": 250,
              "end_line": 250,
              "comments": [
                "Panel de colas: Basic Auth con contrasena estatica y sin ThrottlerGuard",
                "(bull-board se monta como middleware, los guards de Nest no lo cubren).",
                "Sin este limite la fuerza bruta contra la contrasena es ilimitada."
              ],
              "modeled": true
            },
            {
              "id": "s1/l16/d1",
              "name": "proxy_pass",
              "args": [
                "http://nestjs_upstream"
              ],
              "text": "proxy_pass http://nestjs_upstream;",
              "block": false,
              "line": 251,
              "end_line": 251,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l16/d2",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 252,
              "end_line": 252,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l16/d3",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 253,
              "end_line": 253,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l16/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 254,
              "end_line": 254,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l16/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 255,
              "end_line": 255,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l16/d6",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 256,
              "end_line": 256,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l17",
          "modifier": "",
          "path": "/assets/",
          "source": "location",
          "line": 260,
          "end_line": 264,
          "target": {
            "kind": "static",
            "directive": "s1/l17/d0",
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": null,
            "alias": "/var/www/proggest/apps/web-gateway/public/assets/",
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": "1y",
            "cache": null,
            "deny": false,
            "headers": [
              {
                "id": "s1/l17/d2",
                "name": "Cache-Control",
                "value": "public, max-age=31536000, immutable",
                "always": false
              }
            ],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s1/l17/d0",
              "name": "alias",
              "args": [
                "/var/www/proggest/apps/web-gateway/public/assets/"
              ],
              "text": "alias /var/www/proggest/apps/web-gateway/public/assets/;",
              "block": false,
              "line": 261,
              "end_line": 261,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l17/d1",
              "name": "expires",
              "args": [
                "1y"
              ],
              "text": "expires 1y;",
              "block": false,
              "line": 262,
              "end_line": 262,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l17/d2",
              "name": "add_header",
              "args": [
                "Cache-Control",
                "public, max-age=31536000, immutable"
              ],
              "text": "add_header Cache-Control \"public, max-age=31536000, immutable\";",
              "block": false,
              "line": 263,
              "end_line": 263,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [
            "Servir archivos estáticos de assets"
          ],
          "notes": []
        },
        {
          "id": "s1/l18",
          "modifier": "",
          "path": "/work-reports/reports/download",
          "source": "location",
          "line": 270,
          "end_line": 280,
          "target": {
            "kind": "proxy",
            "directive": "s1/l18/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": "300s",
            "send_timeout": "300s",
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": false,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l18/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l18/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l18/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l18/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l18/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 271,
              "end_line": 271,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l18/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 272,
              "end_line": 272,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l18/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 273,
              "end_line": 273,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l18/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 274,
              "end_line": 274,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l18/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 275,
              "end_line": 275,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l18/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 276,
              "end_line": 276,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l18/d6",
              "name": "proxy_read_timeout",
              "args": [
                "300s"
              ],
              "text": "proxy_read_timeout 300s;",
              "block": false,
              "line": 277,
              "end_line": 277,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l18/d7",
              "name": "proxy_send_timeout",
              "args": [
                "300s"
              ],
              "text": "proxy_send_timeout 300s;",
              "block": false,
              "line": 278,
              "end_line": 278,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l18/d8",
              "name": "proxy_buffering",
              "args": [
                "off"
              ],
              "text": "proxy_buffering off;",
              "block": false,
              "line": 279,
              "end_line": 279,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [
            "Descarga del informe agregado de partes (proxy Next -> NestJS).",
            "Un informe anual DETAILED encadena varias agregaciones y el render de",
            "pdfmake: el cliente espera hasta 300s y el `location /` heredaria el",
            "default de 60s de Nginx, devolviendo un 504 en vez del fichero."
          ],
          "notes": []
        },
        {
          "id": "s1/l19",
          "modifier": "=",
          "path": "/uaap/",
          "source": "location",
          "line": 295,
          "end_line": 297,
          "target": {
            "kind": "return",
            "directive": "s1/l19/d0",
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": 302,
            "destination": "/uaap/dashboard",
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s1/l19/d0",
              "name": "return",
              "args": [
                "302",
                "/uaap/dashboard"
              ],
              "text": "return 302 /uaap/dashboard;",
              "block": false,
              "line": 296,
              "end_line": 296,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [
            "Páginas del módulo UAAP (proxy Next -> Next).",
            "El informe global va por Server Action, y una Server Action POSTea a la URL",
            "de la PÁGINA que la invoca (/uaap/dashboard y /uaap/trabajadores), no a",
            "/api/: hereda el `location /` y sus 60s por defecto de Nginx. Generar la",
            "ficha de hasta 200 trabajadores encadena 200 render de pdfmake más el",
            "merge, así que sin este bloque la descarga muere en 504 con la plantilla",
            "entera seleccionada. Mismo motivo y mismos valores que",
            "/work-reports/reports/download.",
            "Cortacircuito del bucle de redirecciones cacheado: la primera version del",
            "bloque /uaap/ emitio un 301 permanente (/uaap -> /uaap/) que los navegadores",
            "guardan; con Next devolviendo 308 (/uaap/ -> /uaap) el bucle vivia en la",
            "cache del cliente aunque el servidor ya estuviera corregido. Un 302 (no",
            "cacheable) directo al dashboard rompe el ciclo sin pedirle nada al usuario."
          ],
          "notes": []
        },
        {
          "id": "s1/l20",
          "modifier": "",
          "path": "/uaap",
          "source": "location",
          "line": 299,
          "end_line": 313,
          "target": {
            "kind": "proxy",
            "directive": "s1/l20/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": "300s",
            "send_timeout": "300s",
            "connect_timeout": null,
            "client_max_body_size": "10G",
            "limit_req": null,
            "websocket": false,
            "buffering": false,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l20/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l20/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l20/d4",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l20/d5",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l20/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 300,
              "end_line": 300,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l20/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 301,
              "end_line": 301,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l20/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 302,
              "end_line": 302,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l20/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 303,
              "end_line": 303,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l20/d4",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 304,
              "end_line": 304,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l20/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 305,
              "end_line": 305,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l20/d6",
              "name": "proxy_read_timeout",
              "args": [
                "300s"
              ],
              "text": "proxy_read_timeout 300s;",
              "block": false,
              "line": 306,
              "end_line": 306,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l20/d7",
              "name": "proxy_send_timeout",
              "args": [
                "300s"
              ],
              "text": "proxy_send_timeout 300s;",
              "block": false,
              "line": 307,
              "end_line": 307,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l20/d8",
              "name": "proxy_buffering",
              "args": [
                "off"
              ],
              "text": "proxy_buffering off;",
              "block": false,
              "line": 308,
              "end_line": 308,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l20/d9",
              "name": "client_max_body_size",
              "args": [
                "10G"
              ],
              "text": "client_max_body_size 10G;",
              "block": false,
              "line": 312,
              "end_line": 312,
              "comments": [
                "Los adjuntos de formaciones suben por Server Action a estas mismas",
                "rutas: sin repetirlo aquí caerían al default de 1M de Nginx (los",
                "bloques `location` NO heredan el 10G del `location /`)."
              ],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l21",
          "modifier": "",
          "path": "/",
          "source": "location",
          "line": 315,
          "end_line": 326,
          "target": {
            "kind": "proxy",
            "directive": "s1/l21/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": "10G",
            "limit_req": null,
            "websocket": true,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": false,
            "headers": [],
            "proxy_headers": [
              {
                "id": "s1/l21/d2",
                "name": "Upgrade",
                "value": "$http_upgrade",
                "always": false
              },
              {
                "id": "s1/l21/d3",
                "name": "Connection",
                "value": "upgrade",
                "always": false
              },
              {
                "id": "s1/l21/d4",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l21/d5",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              },
              {
                "id": "s1/l21/d6",
                "name": "X-Forwarded-For",
                "value": "$proxy_add_x_forwarded_for",
                "always": false
              },
              {
                "id": "s1/l21/d7",
                "name": "X-Forwarded-Proto",
                "value": "$scheme",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l21/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 316,
              "end_line": 316,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l21/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 317,
              "end_line": 317,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l21/d2",
              "name": "proxy_set_header",
              "args": [
                "Upgrade",
                "$http_upgrade"
              ],
              "text": "proxy_set_header Upgrade $http_upgrade;",
              "block": false,
              "line": 318,
              "end_line": 318,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l21/d3",
              "name": "proxy_set_header",
              "args": [
                "Connection",
                "upgrade"
              ],
              "text": "proxy_set_header Connection 'upgrade';",
              "block": false,
              "line": 319,
              "end_line": 319,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l21/d4",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 320,
              "end_line": 320,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l21/d5",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 321,
              "end_line": 321,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l21/d6",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-For",
                "$proxy_add_x_forwarded_for"
              ],
              "text": "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
              "block": false,
              "line": 322,
              "end_line": 322,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l21/d7",
              "name": "proxy_set_header",
              "args": [
                "X-Forwarded-Proto",
                "$scheme"
              ],
              "text": "proxy_set_header X-Forwarded-Proto $scheme;",
              "block": false,
              "line": 323,
              "end_line": 323,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l21/d8",
              "name": "proxy_cache_bypass",
              "args": [
                "$http_upgrade"
              ],
              "text": "proxy_cache_bypass $http_upgrade;",
              "block": false,
              "line": 324,
              "end_line": 324,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l21/d9",
              "name": "client_max_body_size",
              "args": [
                "10G"
              ],
              "text": "client_max_body_size 10G;",
              "block": false,
              "line": 325,
              "end_line": 325,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l22",
          "modifier": "",
          "path": "/_next/static/",
          "source": "location",
          "line": 330,
          "end_line": 337,
          "target": {
            "kind": "proxy",
            "directive": "s1/l22/d0",
            "url": "http://nextjs_upstream",
            "protocol": "http",
            "upstream": "nextjs_upstream",
            "host": "nextjs_upstream",
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": "1y",
            "cache": null,
            "deny": false,
            "headers": [
              {
                "id": "s1/l22/d5",
                "name": "Cache-Control",
                "value": "public, max-age=31536000, immutable",
                "always": false
              }
            ],
            "proxy_headers": [
              {
                "id": "s1/l22/d2",
                "name": "Host",
                "value": "$host",
                "always": false
              },
              {
                "id": "s1/l22/d3",
                "name": "X-Real-IP",
                "value": "$remote_addr",
                "always": false
              }
            ]
          },
          "directives": [
            {
              "id": "s1/l22/d0",
              "name": "proxy_pass",
              "args": [
                "http://nextjs_upstream"
              ],
              "text": "proxy_pass http://nextjs_upstream;",
              "block": false,
              "line": 331,
              "end_line": 331,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l22/d1",
              "name": "proxy_http_version",
              "args": [
                "1.1"
              ],
              "text": "proxy_http_version 1.1;",
              "block": false,
              "line": 332,
              "end_line": 332,
              "comments": [],
              "modeled": false
            },
            {
              "id": "s1/l22/d2",
              "name": "proxy_set_header",
              "args": [
                "Host",
                "$host"
              ],
              "text": "proxy_set_header Host $host;",
              "block": false,
              "line": 333,
              "end_line": 333,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l22/d3",
              "name": "proxy_set_header",
              "args": [
                "X-Real-IP",
                "$remote_addr"
              ],
              "text": "proxy_set_header X-Real-IP $remote_addr;",
              "block": false,
              "line": 334,
              "end_line": 334,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l22/d4",
              "name": "expires",
              "args": [
                "1y"
              ],
              "text": "expires 1y;",
              "block": false,
              "line": 335,
              "end_line": 335,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l22/d5",
              "name": "add_header",
              "args": [
                "Cache-Control",
                "public, max-age=31536000, immutable"
              ],
              "text": "add_header Cache-Control \"public, max-age=31536000, immutable\";",
              "block": false,
              "line": 336,
              "end_line": 336,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [
            "Con barra: sin ella casaba también con `/_next/staticX`, que Next pinta como",
            "página. X-Real-IP siempre: donde Nginx no la fija, llega la del cliente"
          ],
          "notes": []
        },
        {
          "id": "s1/l23",
          "modifier": "~",
          "path": "/\\.",
          "source": "location",
          "line": 339,
          "end_line": 341,
          "target": {
            "kind": "other",
            "directive": null,
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": null,
            "destination": null,
            "detail": "deny all"
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": true,
            "headers": [],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s1/l23/d0",
              "name": "deny",
              "args": [
                "all"
              ],
              "text": "deny all;",
              "block": false,
              "line": 340,
              "end_line": 340,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        },
        {
          "id": "s1/l24",
          "modifier": "~",
          "path": "^/(\\.env|\\.git|docker-compose|Dockerfile)",
          "source": "location",
          "line": 343,
          "end_line": 346,
          "target": {
            "kind": "return",
            "directive": "s1/l24/d1",
            "url": null,
            "protocol": null,
            "upstream": null,
            "host": null,
            "port": null,
            "address": null,
            "root": null,
            "alias": null,
            "inherited": false,
            "code": 404,
            "destination": null,
            "detail": null
          },
          "settings": {
            "read_timeout": null,
            "send_timeout": null,
            "connect_timeout": null,
            "client_max_body_size": null,
            "limit_req": null,
            "websocket": false,
            "buffering": null,
            "expires": null,
            "cache": null,
            "deny": true,
            "headers": [],
            "proxy_headers": []
          },
          "directives": [
            {
              "id": "s1/l24/d0",
              "name": "deny",
              "args": [
                "all"
              ],
              "text": "deny all;",
              "block": false,
              "line": 344,
              "end_line": 344,
              "comments": [],
              "modeled": true
            },
            {
              "id": "s1/l24/d1",
              "name": "return",
              "args": [
                "404"
              ],
              "text": "return 404;",
              "block": false,
              "line": 345,
              "end_line": 345,
              "comments": [],
              "modeled": true
            }
          ],
          "locations": [],
          "evaluation_order": [],
          "comments": [],
          "notes": []
        }
      ],
      "evaluation_order": [
        "s1/l19",
        "s1/l18",
        "s1/l1",
        "s1/l6",
        "s1/l2",
        "s1/l8",
        "s1/l5",
        "s1/l22",
        "s1/l10",
        "s1/l12",
        "s1/l16",
        "s1/l7",
        "s1/l9",
        "s1/l11",
        "s1/l13",
        "s1/l3",
        "s1/l4",
        "s1/l15",
        "s1/l17",
        "s1/l0",
        "s1/l14",
        "s1/l20",
        "s1/l21",
        "s1/l23",
        "s1/l24"
      ],
      "directives": [
        {
          "id": "s1/d0",
          "name": "listen",
          "args": [
            "443",
            "ssl"
          ],
          "text": "listen 443 ssl;",
          "block": false,
          "line": 28,
          "end_line": 28,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d1",
          "name": "http2",
          "args": [
            "on"
          ],
          "text": "http2 on;",
          "block": false,
          "line": 29,
          "end_line": 29,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d2",
          "name": "listen",
          "args": [
            "[::]:443",
            "ssl"
          ],
          "text": "listen [::]:443 ssl;",
          "block": false,
          "line": 30,
          "end_line": 30,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d3",
          "name": "server_name",
          "args": [
            "proggest.es",
            "www.proggest.es"
          ],
          "text": "server_name proggest.es www.proggest.es;",
          "block": false,
          "line": 31,
          "end_line": 31,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d4",
          "name": "ssl_certificate",
          "args": [
            "/etc/letsencrypt/live/proggest.es/fullchain.pem"
          ],
          "text": "ssl_certificate /etc/letsencrypt/live/proggest.es/fullchain.pem;",
          "block": false,
          "line": 33,
          "end_line": 33,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d5",
          "name": "ssl_certificate_key",
          "args": [
            "/etc/letsencrypt/live/proggest.es/privkey.pem"
          ],
          "text": "ssl_certificate_key /etc/letsencrypt/live/proggest.es/privkey.pem;",
          "block": false,
          "line": 34,
          "end_line": 34,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d6",
          "name": "ssl_session_timeout",
          "args": [
            "1d"
          ],
          "text": "ssl_session_timeout 1d;",
          "block": false,
          "line": 36,
          "end_line": 36,
          "comments": [],
          "modeled": false
        },
        {
          "id": "s1/d7",
          "name": "ssl_session_cache",
          "args": [
            "shared:SSL:50m"
          ],
          "text": "ssl_session_cache shared:SSL:50m;",
          "block": false,
          "line": 37,
          "end_line": 37,
          "comments": [],
          "modeled": false
        },
        {
          "id": "s1/d8",
          "name": "ssl_session_tickets",
          "args": [
            "off"
          ],
          "text": "ssl_session_tickets off;",
          "block": false,
          "line": 38,
          "end_line": 38,
          "comments": [],
          "modeled": false
        },
        {
          "id": "s1/d9",
          "name": "ssl_protocols",
          "args": [
            "TLSv1.2",
            "TLSv1.3"
          ],
          "text": "ssl_protocols TLSv1.2 TLSv1.3;",
          "block": false,
          "line": 39,
          "end_line": 39,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d10",
          "name": "ssl_ciphers",
          "args": [
            "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384"
          ],
          "text": "ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;",
          "block": false,
          "line": 40,
          "end_line": 40,
          "comments": [],
          "modeled": false
        },
        {
          "id": "s1/d11",
          "name": "ssl_prefer_server_ciphers",
          "args": [
            "off"
          ],
          "text": "ssl_prefer_server_ciphers off;",
          "block": false,
          "line": 41,
          "end_line": 41,
          "comments": [],
          "modeled": false
        },
        {
          "id": "s1/d12",
          "name": "add_header",
          "args": [
            "Strict-Transport-Security",
            "max-age=63072000; includeSubDomains",
            "always"
          ],
          "text": "add_header Strict-Transport-Security \"max-age=63072000; includeSubDomains\" always;",
          "block": false,
          "line": 43,
          "end_line": 43,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d13",
          "name": "add_header",
          "args": [
            "X-Frame-Options",
            "SAMEORIGIN",
            "always"
          ],
          "text": "add_header X-Frame-Options \"SAMEORIGIN\" always;",
          "block": false,
          "line": 44,
          "end_line": 44,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d14",
          "name": "add_header",
          "args": [
            "X-Content-Type-Options",
            "nosniff",
            "always"
          ],
          "text": "add_header X-Content-Type-Options \"nosniff\" always;",
          "block": false,
          "line": 45,
          "end_line": 45,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d15",
          "name": "add_header",
          "args": [
            "X-XSS-Protection",
            "1; mode=block",
            "always"
          ],
          "text": "add_header X-XSS-Protection \"1; mode=block\" always;",
          "block": false,
          "line": 46,
          "end_line": 46,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d16",
          "name": "add_header",
          "args": [
            "Referrer-Policy",
            "strict-origin-when-cross-origin",
            "always"
          ],
          "text": "add_header Referrer-Policy \"strict-origin-when-cross-origin\" always;",
          "block": false,
          "line": 47,
          "end_line": 47,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d17",
          "name": "gzip",
          "args": [
            "on"
          ],
          "text": "gzip on;",
          "block": false,
          "line": 49,
          "end_line": 49,
          "comments": [],
          "modeled": true
        },
        {
          "id": "s1/d18",
          "name": "gzip_vary",
          "args": [
            "on"
          ],
          "text": "gzip_vary on;",
          "block": false,
          "line": 50,
          "end_line": 50,
          "comments": [],
          "modeled": false
        },
        {
          "id": "s1/d19",
          "name": "gzip_proxied",
          "args": [
            "any"
          ],
          "text": "gzip_proxied any;",
          "block": false,
          "line": 51,
          "end_line": 51,
          "comments": [],
          "modeled": false
        },
        {
          "id": "s1/d20",
          "name": "gzip_comp_level",
          "args": [
            "6"
          ],
          "text": "gzip_comp_level 6;",
          "block": false,
          "line": 52,
          "end_line": 52,
          "comments": [],
          "modeled": false
        },
        {
          "id": "s1/d21",
          "name": "gzip_types",
          "args": [
            "text/plain",
            "text/css",
            "text/xml",
            "application/json",
            "application/javascript",
            "application/rss+xml",
            "application/atom+xml",
            "image/svg+xml"
          ],
          "text": "gzip_types text/plain text/css text/xml application/json application/javascript application/rss+xml application/atom+xml image/svg+xml;",
          "block": false,
          "line": 53,
          "end_line": 53,
          "comments": [],
          "modeled": false
        }
      ],
      "comments": [
        "HTTPS server"
      ],
      "notes": [
        {
          "text": "---- Next.js API route handlers (proxy al backend via Next.js) ----",
          "line": 117,
          "after": "s1/l4"
        },
        {
          "text": "Estas rutas tienen route.ts en Next.js que hacen proxy autenticado al backend.",
          "line": 118,
          "after": "s1/l4"
        },
        {
          "text": "DEBEN ir ANTES del catch-all /api/ que va directo al backend NestJS.",
          "line": 119,
          "after": "s1/l4"
        }
      ]
    }
  ],
  "upstreams": [
    {
      "id": "u:nextjs_upstream",
      "name": "nextjs_upstream",
      "line": 1,
      "end_line": 4,
      "servers": [
        {
          "address": "127.0.0.1:3001",
          "params": [],
          "id": "u:nextjs_upstream/d0",
          "source": null
        }
      ],
      "keepalive": 64,
      "directives": [
        {
          "id": "u:nextjs_upstream/d0",
          "name": "server",
          "args": [
            "127.0.0.1:3001"
          ],
          "text": "server 127.0.0.1:3001;",
          "block": false,
          "line": 2,
          "end_line": 2,
          "comments": [],
          "modeled": true
        },
        {
          "id": "u:nextjs_upstream/d1",
          "name": "keepalive",
          "args": [
            "64"
          ],
          "text": "keepalive 64;",
          "block": false,
          "line": 3,
          "end_line": 3,
          "comments": [],
          "modeled": true
        }
      ],
      "comments": [],
      "notes": [],
      "used_by": [
        "s1/l3",
        "s1/l4",
        "s1/l5",
        "s1/l6",
        "s1/l7",
        "s1/l8",
        "s1/l9",
        "s1/l10",
        "s1/l11",
        "s1/l12",
        "s1/l13",
        "s1/l18",
        "s1/l20",
        "s1/l21",
        "s1/l22"
      ],
      "source": null
    },
    {
      "id": "u:nestjs_upstream",
      "name": "nestjs_upstream",
      "line": 6,
      "end_line": 9,
      "servers": [
        {
          "address": "127.0.0.1:3000",
          "params": [],
          "id": "u:nestjs_upstream/d0",
          "source": null
        }
      ],
      "keepalive": 64,
      "directives": [
        {
          "id": "u:nestjs_upstream/d0",
          "name": "server",
          "args": [
            "127.0.0.1:3000"
          ],
          "text": "server 127.0.0.1:3000;",
          "block": false,
          "line": 7,
          "end_line": 7,
          "comments": [],
          "modeled": true
        },
        {
          "id": "u:nestjs_upstream/d1",
          "name": "keepalive",
          "args": [
            "64"
          ],
          "text": "keepalive 64;",
          "block": false,
          "line": 8,
          "end_line": 8,
          "comments": [],
          "modeled": true
        }
      ],
      "comments": [],
      "notes": [],
      "used_by": [
        "s1/l1",
        "s1/l2",
        "s1/l14",
        "s1/l15",
        "s1/l16"
      ],
      "source": null
    }
  ],
  "includes": [],
  "directives": [],
  "notes": []
};

/** https://proggest.es/api/v1/auth/login */
export const PROGGEST_ROUTE_LOGIN: SiteRoute = {
  "server_id": "s1",
  "location_id": "s1/l2",
  "steps": [
    "Servers listening on port 443: s1.",
    "proggest.es is the exact name proggest.es of s1.",
    "/api/v1/auth/login starts with the prefixes /, /api/, /api/v1/auth/login: the longest wins, /api/v1/auth/login.",
    "No regular expression location matches /api/v1/auth/login, so the prefix /api/v1/auth/login answers.",
    "The request goes to upstream nestjs_upstream."
  ],
  "trace": [
    {
      "code": "port",
      "params": {
        "port": 443,
        "servers": "s1"
      },
      "text": "Servers listening on port 443: s1."
    },
    {
      "code": "server_exact",
      "params": {
        "host": "proggest.es",
        "name": "proggest.es",
        "server": "s1"
      },
      "text": "proggest.es is the exact name proggest.es of s1."
    },
    {
      "code": "location_prefixes",
      "params": {
        "path": "/api/v1/auth/login",
        "prefixes": "/, /api/, /api/v1/auth/login",
        "location": "/api/v1/auth/login"
      },
      "text": "/api/v1/auth/login starts with the prefixes /, /api/, /api/v1/auth/login: the longest wins, /api/v1/auth/login."
    },
    {
      "code": "location_no_regex",
      "params": {
        "path": "/api/v1/auth/login",
        "location": "/api/v1/auth/login"
      },
      "text": "No regular expression location matches /api/v1/auth/login, so the prefix /api/v1/auth/login answers."
    },
    {
      "code": "target_upstream",
      "params": {
        "upstream": "nestjs_upstream"
      },
      "text": "The request goes to upstream nestjs_upstream."
    }
  ],
  "highlight": [
    "s1",
    "s1/l2",
    "u:nestjs_upstream"
  ],
  "redirect": null
};
