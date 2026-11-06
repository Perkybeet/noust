# Expose the console safely

Part of [Security](security.md).

## Exposing the console

The console acts as root, so how it is reached matters more than anything else on this page.

| Setup | Command |
|---|---|
| Loopback only (default). Reach it through an SSH tunnel. | `noust web start -d` |
| TLS served by Noust | `noust web start -d --host 0.0.0.0 --tls-cert fullchain.pem --tls-key privkey.pem` |
| TLS with a self-signed certificate | `noust web start -d --host 0.0.0.0 --self-signed` |
| Behind a TLS-terminating reverse proxy | `noust web start -d --trusted-proxy 127.0.0.1` |

- **Beyond loopback, TLS is mandatory.** Binding to any other address without a certificate
  and key (or `--self-signed`) is refused, both when the options are parsed and again where
  the socket is bound. `--insecure-http` overrides this, and says what it does: the access
  token and the session cookie then cross the network in clear.
- `--self-signed` mints a certificate under `/etc/noust/panel-tls/` and reuses it while it is
  valid.
- `--allow-ip ADDR/CIDR` (repeatable) answers only those clients; everyone else gets `403`
  before authentication. It restricts who connects; it encrypts nothing, and does not lift the
  TLS rule.
- `--trusted-proxy ADDR/CIDR` is the only way `X-Forwarded-For`, `X-Real-IP` and
  `X-Forwarded-Proto` are believed, and only from that peer. By default Noust trusts nobody's
  forwarding headers. Declaring the proxy is what makes the session cookie `Secure` and the
  client address in the audit log the real one.
- Requests are rate limited. A request without a valid credential (the sign-in, the forge
  webhooks, anything anonymous) is counted per client address, 120 a minute by default
  (`web.rate_limit_requests`). A request with a valid credential is counted per credential
  instead (one sign-in, one API token, the master token), 1200 a minute by default
  (`web.rate_limit_authenticated_requests`): over an SSH tunnel every request comes from
  `127.0.0.1`, and a per-address budget made every tab and script share one. A wrong
  credential buys nothing: it is counted as anonymous, and guessing is stopped by the
  lockout below, which is separate. An IPv6 client is counted by its /64, not by its
  address: a customer is given 2^64 of them, and counting each one gave every request a fresh
  budget (and a fresh lockout, and a way to fill the limiter's table with entries that pushed
  everyone else's out). The audit trail and the session's binding keep the exact address.
  The limiter is not a way to test a credential. It only looks at one on `/api`, `/events`
  and `/ws`, where an endpoint checks it too; `/health` and the forge webhooks, which never
  check one, are always counted by address, whatever they carry. A wrong credential it does
  look at counts towards the lockout exactly once, like one an endpoint refuses, even on a
  route that never checks it (a public endpoint, a `404`). An address that is locked out,
  or already over the anonymous budget, is counted by address without its credential being
  checked at all, so a flood that is refused anyway costs no database query per request.
  The console's shell (`index.html` on any console
  address) and its hashed build assets spend no budget, so reloading the page cannot lock
  the operator out. A refusal is `429` with `"error": "rate_limited"` and a `Retry-After`
  of the seconds until the budget frees; every counted response carries
  `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` (the UNIX time at which the
  budget is whole again) for the budget it was counted in. Both
  budgets share `web.rate_limit_window` (60 seconds) and `web.rate_limit_enabled`.
- Request bodies are capped before authentication and before routing: 1 MiB for everything
  but the forge webhooks, which get 5 MiB. A larger body is refused with `413` and
  `"error": "payload_too_large"`, from its `Content-Length` or, for a chunked body, as it is
  read. The API's largest legitimate bodies (a unit file, a site configuration, an `.env`)
  are a few kilobytes. The one other cap is the picture of the sign-in screen
  (`PUT /api/auth/sign-in/appearance`), whose body carries a file of up to 1 MiB as base64; and a dump
  uploaded in chunks has the size of one chunk.
- `web.allowed_hosts` (names, or `*.example.com`) makes the console answer only those `Host`
  headers, plus loopback and the hosts of `web.public_url` and `web.hooks_url`; any other host
  gets `400`. Empty answers any host.

## The browser

The console is a static build served by Noust itself, loads nothing from any other origin,
and runs under a Content Security Policy with no `unsafe-inline` for scripts or styles and Trusted
Types, which forbid string-to-DOM sinks. The end-to-end suite fails on any CSP violation, and
every response carries the same headers whatever it answers: a page, the API, an asset, a 404, and
a refusal made before routing (a host or an address that is not allowed, a rate limit, a body too
large). `Strict-Transport-Security` is added when the request came over TLS (directly, or through a
declared proxy); everything is `Cache-Control: no-store` except the content-hashed files under
`/assets/` and the picture of the sign-in screen asked for by its own digest (`?v=<sha256>`), which are
immutable for a year. The exact values of every header are printed in
[SECURITY-MODEL.md](SECURITY-MODEL.md#the-browser), generated from the code that sends them and
checked against it by a test.

## Streams

The event stream (`GET /events`) and the WebSockets (`/ws/...`) are authenticated once, at
the handshake, and then stay open for hours. So:

- **The credential is checked again** every 30 seconds on a WebSocket and every 25 on
  `/events`. Revoking an API token, rotating the master token (`noust web token --new` or
  `--regenerate`), signing out, revoking a session or letting it expire ends every stream it
  opened: a WebSocket closes with `4401`, the event stream ends. A session renewal does not;
  it is the same sign-in under a new id.
- **One credential holds at most 8 WebSockets at once.** Every log socket is a
  `journalctl -f` running as root; the ninth handshake is refused with `4429` until one
  closes. The budget is per credential, so a script at its limit does not stop the console.
- **A WebSocket lasts at most 12 hours.** It then closes with `4408`, and the client
  reconnects, authenticating again.
- **One credential holds at most 16 event streams** (`GET /events`, and a central's relay of a
  node's, `/api/nodes/{node}/events`), counted where every request passes and given back however
  the stream ends; the seventeenth is refused with `429` (`"error": "rate_limited"`). Each stream is
  a subscription and a periodic render of the machine, so without a cap one low-privilege token could
  open streams until the console had nothing left for anyone else. A stream ends after 12 hours as
  well, and the browser's `EventSource` reconnects by itself.
- **`/ws/logs/{name}` streams only a unit Noust manages**, decided by the same ownership rule
  every other service operation goes through. A unit Noust did not create, `ssh` for example,
  is refused.
