/**
 * What the machine's own reverse proxy has to be told to reach an application (`proxy_hint` of `GET /api/apps/{domain}/address`),
 * as the person pastes it: the rule as nginx writes it (what Nginx Proxy Manager's advanced box and any nginx take) and the
 * values one by one (what Synology's and the other panels ask for field by field). Noust touches nothing of the machine's proxy: it
 * says what to put. Pure, so the text is held by a test and not by looking at a page.
 */

import type { AppAddress } from "../../api/queries/apps";

export type ProxyHint = NonNullable<AppAddress["proxy_hint"]>;

/** What a proxy that waits for the answer for a long time needs more than the usual minute for: an export, an upload. */
const LONG_TIMEOUT_SECONDS = 600;

/**
 * The location block of an nginx that proxies to the application, with the headers a site of Noust's passes and the WebSocket upgrade.
 * The timeout is raised to what a long request needs: a proxy that waits a minute cuts it, and that looks like the application's fault.
 */
export function nginxRule(hint: ProxyHint): string {
  const headers = hint.headers.map((header) => `    proxy_set_header ${header.name} ${header.nginx};`);
  return [
    "location / {",
    `    proxy_pass ${hint.target};`,
    "    proxy_http_version 1.1;",
    ...headers,
    `    proxy_read_timeout ${String(Math.max(hint.read_timeout_seconds, LONG_TIMEOUT_SECONDS))}s;`,
    "}",
  ].join("\n");
}

/** The port in a target such as `http://127.0.0.1:8081`, for the panels that take the host and the port apart. */
export function targetParts(hint: ProxyHint): { protocol: string; host: string; port: string } {
  return { protocol: hint.scheme.toUpperCase(), host: hint.host, port: String(hint.port) };
}
