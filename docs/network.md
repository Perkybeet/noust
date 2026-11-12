# The network of an application and of the server

What a site asks of a visitor, the DNS records Noust keeps for its names, and the three ways to
reach a service that are not "a name pointing at this machine's port 443": a published TCP or
UDP port, and a Cloudflare Tunnel. Domains are in [domains.md](domains.md) and certificates in
[certificates.md](certificates.md).

The rest of this guide is on pages of its own:

- [Routes at a prefix, and signing in with a service of your own](network-routes.md)
- [HTTP/3 and a web application firewall](network-http3-waf.md)
- [DNS accounts, private networks, tunnels and ports](network-dns.md)
- [Noust DNS: a name server of your own](network-dns-server.md)
- [Moving to the other web server](network-webserver.md)

## Access: a password, addresses, limits and headers

Every application's site carries a **policy**, kept in the store and rendered into the site by the
same writer that renders everything else (`nginx` and `Apache` from one document). The site is
tested by the web server before it is reloaded, and put back if the web server refuses it. A site
without a policy renders byte for byte as it did before.

```bash
noust app policy show shop.example.com
noust app policy user add shop.example.com ana --password-stdin     # an account of the password
noust app policy set shop.example.com --password --realm "Staff"
noust app policy set shop.example.com --allow 203.0.113.0/24 --allow 198.51.100.7
noust app policy set shop.example.com --rate-limit 10r/s --rate-burst 20 --connection-limit 50
noust app policy set shop.example.com --real-ip cloudflare          # or custom, with --real-ip-header and --trusted-proxy
noust app policy set shop.example.com --header "X-Frame-Options: DENY" --hide-header Referrer-Policy
noust app policy set shop.example.com --noindex --hsts on --canonical www
noust app policy reset shop.example.com
noust app policy ranges --refresh                                   # Cloudflare's published ranges
```

| Part | What it does |
|---|---|
| Password | HTTP basic authentication. Accounts are kept as `$6$` hashes made by `openssl passwd -6 -stdin` (the password is never an argument) in the store, and written to a file the web server's group reads, regenerated at every write. Nobody can read a password back. |
| Addresses | `allow` admits only the listed addresses and networks; `deny` is refused first. With a password too, `access: all` asks for both and `any` lets an allowed address in without a password. |
| Visitor address | Behind a CDN the address that connects is the proxy's. `cloudflare` believes the header Cloudflare sends, from the ranges it publishes (a built-in list, refreshable and validated); `custom` believes a header you name from proxies you list. The lists, the limits and the logs then see the visitor. |
| Limits | Requests per client address (`10r/s`, `300r/m`, a burst) and concurrent connections. nginx only. |
| Headers | The defaults (X-Frame-Options, nosniff, Referrer-Policy) stay unless hidden; yours replace those of the same name; HSTS follows the server or is forced on or off; `--noindex` sends `X-Robots-Tag: noindex`. |
| Preferred spelling | `www` or the bare name is served and the other redirects to it, over TLS too. |
| HTTP | `--no-force-https` leaves a site with a certificate answering on port 80 as well, with no redirect and no HSTS (nginx only; see [domains.md](domains.md#answering-over-http-as-well)). |

**What is never closed.** The path certificates are checked on (`/.well-known/acme-challenge/`)
keeps `auth_basic off; allow all;`, so a password does not stop renewals.

**The lockout.** A list that would refuse the address you are making the change from (your request,
or your SSH session) is refused until you confirm it knowingly: `--accept-lockout`, or the
confirmation box in the console that names the address.

It reaches the server block that serves the application: the HTTPS one when the site has a
certificate, the HTTP one otherwise. A policy cannot be applied to a monorepo (it has several server blocks of its own) or to a site you
wrote yourself: the answer says why and the site is not touched. Apache has no rate or connection
limit and says so.

API: `GET|PUT|DELETE /api/apps/{domain}/policy`, `PUT|DELETE /api/apps/{domain}/policy/users/{name}`,
`GET /api/policy/cloudflare-ranges` and `POST .../refresh`. Console: **Application, Settings, Access**.

## Client certificates

A site can answer **only a visitor whose browser presents a certificate made for it**: mutual TLS. It
is the strongest way to put an internal tool on the internet without a VPN, because the check happens
in the TLS handshake, before any HTTP: a request without a valid certificate never reaches nginx's
locations, let alone the application.

```bash
noust app policy clients issue shop.example.com ana-laptop --out ~/certs   # make one
noust app policy set shop.example.com --client-certs                       # start asking
noust app policy clients list shop.example.com
noust app policy clients revoke shop.example.com 1A0F...                   # by serial
noust app policy clients ca shop.example.com                               # the authority's certificate
noust app policy set shop.example.com --no-client-certs
```

`issue` writes `ana-laptop.p12` (what a browser or a keychain imports), `ana-laptop.crt`,
`ana-laptop.key` and `ca.crt`, readable by you alone, and prints the bundle's password **once**. Noust
keeps neither the key nor the password: if they are lost, revoke the certificate and make another.

- **One authority per application.** Each site has its own certificate authority
  (`/etc/noust/tls/clients/<app>/`), so a certificate made for one site opens no other, and revoking
  everything of a site never touches another. Nothing but Noust's openssl calls reads the authority's key.
- **A certificate can authenticate a client and nothing else.** It carries the client-authentication
  usage only, is not a CA, and is signed by the application's authority and by no other. A certificate
  from another application's authority, or one anybody signed themselves, is refused in the handshake.
- **Revoking is by serial and takes effect when the site is rendered again**, which `revoke` does. The
  site refuses the serial with a 403; there is no CRL to fetch or let go stale. Serials are 32
  hexadecimal digits written the same by nginx, openssl and the records, so the list and the check
  cannot disagree.
- **The application learns who came.** nginx hands the verified certificate's subject and serial to
  the application as `X-SSL-Client-Subject` and `X-SSL-Client-Serial`. They are set by the web server from
  the handshake, replacing anything the visitor sent under those names.
- **Nobody is locked out by accident.** Asking for certificates while none is valid, or revoking the last
  valid one of a site that asks, is refused in words unless you accept it (`--accept-lockout`; in the
  console, the confirmation box). Making the first certificate before turning the requirement on is
  the path that never needs it.
- **It needs HTTPS and nginx.** The certificate is asked for in the handshake, so a site with no
  certificate cannot ask for one, and Apache is not driven (Noust says so rather than writing a
  configuration that does nothing). Renewing the site's own certificate is not affected: the HTTP-01
  challenge is answered on port 80, which never asks for a client certificate, and DNS-01 does not
  reach the site at all. Only port 443 is closed to visitors without one, and the redirect from port 80
  stays.
- **The private key crosses the network once, to the person who asked for it.** Making a certificate
  needs sudo mode in the console and the API, the answer is not cached, and the console's window forgets
  the password when it is closed. The PKCS#12 bundle is made from memory-backed temporary files with the
  password passed through the environment, never on a command line.

API: `GET|POST /api/apps/{domain}/client-certs`, `DELETE /api/apps/{domain}/client-certs/{serial}`
(`accept_lockout`), `GET /api/apps/{domain}/client-certs/ca`, and `PUT /api/apps/{domain}/policy` with
`client_certs`. Making and revoking ask for sudo mode. Console: **Application, Settings, Protection, Client certificates**.
Event: `certs.client` (`issue`, `revoke`, `authority`). Deleting the application removes its
authority and records.

## Names no site serves

A web server answers a request whose `Host` matches none of its sites with the **first site it
loaded**, so anyone who points a name at this address is shown an application that never agreed to be
shown there, and a scanner learns what runs here. Noust can put a site of its own ahead of the others,
the default for ports 80 and 443, that says one of three things:

```bash
noust site unknown-hosts show
noust site unknown-hosts set close                        # nothing: the connection is closed (Apache answers 421)
noust site unknown-hosts set refuse                       # 503
noust site unknown-hosts set redirect --redirect-to https://example.org/sorry
noust site unknown-hosts set close --switch-off-default-site
noust site unknown-hosts reset                            # the first site answers again
```

- **TLS.** There is no certificate to show for a name nobody serves, so nginx 1.19.4 or later refuses
  the handshake itself (`ssl_reject_handshake`), and so does a client that sends no server name.
  Apache has no such directive and answers over plain HTTP only; an older nginx says so.
- **The certificate authority still reaches the webroot** (`/.well-known/acme-challenge/`), as it does
  in every site Noust writes, so a certificate can be ordered for a name before its site exists.
- **Only one server can be the default for a port.** Where another declares itself so, nothing is
  written and the answer names the file (the search covers everything `nginx -T` prints, so a
  `conf.d` file is found as well as a site). The web server's own `default` site may be switched off
  with `--switch-off-default-site`: the file stays and it is switched on again by `reset`. A site you
  wrote is never touched.
- The site is tested by the web server before it is reloaded, and everything is put back when it
  refuses. The file (`00-noust-default`, which sorts before the others) records its own settings.

API: `GET|PUT|DELETE /api/unknown-hosts`; changing it needs sudo mode. Console: **Domains, Sites**, above the list. Event: `sites.unknown_hosts`.

## When an application is deleted

`noust delete` lets go of everything made for it elsewhere: the tunnel routes (taken out of the
ingress), the published ports that were for it, its DNS records (by the
[ownership model](network-dns.md#dns-accounts-and-the-records-noust-owns)),
its policy and accounts, its client certificates and their authority, and its certificate choice. Each step is attempted whatever the one
before it did, and what failed is returned as a warning.

## Upkeep

The monitor asks once an hour whether there is anything to do and, when there is, starts a
transient unit (`noust-network-maintenance`) because its own sandbox is read-only for the web
server's files. The unit sweeps the DNS records nobody has used for an hour and fetches
Cloudflare's ranges again when they are a week old and a site trusts them, rendering those sites
again. It also asks Tailscale again for a certificate it issued when thirty days are left, and
reloads the sites that serve it.

## Changing where a firewall rule admits from

A rule that opens a port to everyone is often meant for one address: the office, a bastion, a
monitoring server. **Network > Firewall** has **Change where it admits from** in the menu of each
rule, and the command line has the same:

```bash
noust server security firewall status                        # each rule's id
noust server security firewall change-source 4f6c1a9d2b07 --from 203.0.113.0/24
noust server security firewall change-source 4f6c1a9d2b07 --from any
```

It is one change, not a delete and a create: the rule with the new origin is made first and the old
one is removed after it, so the port is never left without one, and the whole waits **once** to be
confirmed from a new SSH login (`noust server security confirm`), like every change that could cut
you off. When nobody confirms, the old rule comes back and the new one goes. The guard says what it
always said: moving the rule that lets everyone reach SSH or the console is refused when an SSH
session open now would be left outside the new origin, and the refusal names its address. A rule of
one port, allowed or denied, can be moved; a range, a service of the firewall, ufw's `limit` or a rich
rule with more than a port and an origin is changed by adding the rule you want and deleting that one.
`POST /api/server/security/firewall/rules/{rule_id}/source` is the same, and each rule in
`GET /api/server/security/firewall` says whether it can be moved (`source_changeable`).

## Permissions and audit

Published ports and tunnels are the server's, not an application's: reading them is
`server.read`; publishing, closing, creating and routing are `server.host_access` (ports) and
`server.manage` (tunnels) and ask for sudo mode. DNS accounts are `server.manage`. A site's policy
is `apps.manage`. Each change is an audit event (`sites.policy`, `sites.policy.user`, `sites.port`,
`domains.canonical`, `dns.account`, `dns.record`, `dns.challenge`, `certs.upload`, `certs.assign`,
`certs.acme`, `certs.client`, `streams.open`, `streams.close`, `tunnels.create`, `tunnels.route`, `tunnels.delete`).
