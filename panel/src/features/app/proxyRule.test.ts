import { describe, expect, it } from "vitest";

import { nginxRule, targetParts } from "./proxyRule";
import type { ProxyHint } from "./proxyRule";

/** What the server says for an application published on port 8081, as `proxy_hint`. */
const HINT: ProxyHint = {
  target: "http://127.0.0.1:8081",
  scheme: "http",
  host: "127.0.0.1",
  port: 8081,
  websocket: true,
  read_timeout_seconds: 60,
  headers: [
    { name: "Host", meaning: "public_host", nginx: "$host" },
    { name: "X-Forwarded-For", meaning: "client_chain", nginx: "$proxy_add_x_forwarded_for" },
    { name: "Upgrade", meaning: "upgrade_request", nginx: "$http_upgrade" },
    { name: "Connection", meaning: "upgrade", nginx: '"upgrade"' },
  ],
};

describe("the rule for the machine's own reverse proxy", () => {
  it("is the location block a site of Noust's writes for the same application, with the headers the server says", () => {
    expect(nginxRule(HINT)).toBe(
      [
        "location / {",
        "    proxy_pass http://127.0.0.1:8081;",
        "    proxy_http_version 1.1;",
        "    proxy_set_header Host $host;",
        "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "    proxy_set_header Upgrade $http_upgrade;",
        '    proxy_set_header Connection "upgrade";',
        "    proxy_read_timeout 600s;",
        "}",
      ].join("\n"),
    );
  });

  it("raises the waiting time a proxy that cuts at a minute would otherwise keep, and never lowers one that is longer", () => {
    expect(nginxRule({ ...HINT, read_timeout_seconds: 60 })).toContain("proxy_read_timeout 600s;");
    expect(nginxRule({ ...HINT, read_timeout_seconds: 900 })).toContain("proxy_read_timeout 900s;");
  });

  it("gives the values apart for the panels that ask for them one by one", () => {
    expect(targetParts(HINT)).toEqual({ protocol: "HTTP", host: "127.0.0.1", port: "8081" });
  });

  it("follows the address the stack's own file binds, when the server says it", () => {
    const bound = { ...HINT, target: "http://192.168.1.160:8090", host: "192.168.1.160", port: 8090 };
    expect(nginxRule(bound)).toContain("proxy_pass http://192.168.1.160:8090;");
    expect(targetParts(bound)).toEqual({ protocol: "HTTP", host: "192.168.1.160", port: "8090" });
  });
});
