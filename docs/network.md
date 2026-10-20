# The network of an application and of the server

What a site asks of a visitor, the DNS records Noust keeps for its names, and the three ways to
reach a service that are not "a name pointing at this machine's port 443": a published TCP or
UDP port, and a Cloudflare Tunnel. Domains are in [domains.md](domains.md) and certificates in
[certificates.md](certificates.md).

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

## Routes: what happens at a prefix

A route is a prefix of the site (`/admin`, `/api/v2`) with its own rule. It is part of the policy
above, so it is kept in the store, rendered by whoever writes the site, and still there after a
deploy, a new alias or a certificate order. A route matches the prefix and everything under it,
never a longer name that only starts the same (`/admin` is not `/administrator`).

```bash
noust app policy route list shop.example.com
noust app policy route add shop.example.com /admin --password                  # ask for it here
noust app policy route add shop.example.com /hooks --open                       # lift the site's password here
noust app policy route add shop.example.com /embed --header "X-Frame-Options: ALLOWALL"
noust app policy route add shop.example.com /api --proxy-port 4000 --strip      # another service, prefix removed
noust app policy route add shop.example.com /blog --proxy-port 5000             # ...or kept
noust app policy route add shop.example.com /docs --redirect-to https://docs.example.org/ --code 308
noust app policy route remove shop.example.com /docs
```

| Kind | What it does |
|---|---|
| The site (`site`) | The prefix is served as the rest of the site is, with its own answer to the password (`--password` asks for it where the site does not, `--open` lifts the site's) and its own response headers, which replace the site's header of the same name under the prefix. |
| Another service (`proxy`) | The prefix is handed to a service on this server by its port. With `--strip` the service sees `/users` for `/api/users` and is told the prefix in `X-Forwarded-Prefix`; without it the path is passed on as it came. |
| A redirect (`redirect`) | The prefix answers with a 301, 302, 307 or 308 to an address or an absolute path. The rest of the path follows the target unless `--drop-path`. |

How the web server is told:

- **nginx** asks for the password at the server and a `map` of the normalised path (`$uri`, so
  `/%61dmin` and `/./admin` are the path they say) decides, for every template alike, whether to ask
  and under which realm. A longer prefix wins over a shorter one, so a password route inside an open
  one still asks. Headers are the same: one `add_header` per name whose value the path decides.
  Proxies and redirects are locations written before the site's own.
- **Apache** has a `<Location>` section for a prefix that changes the password or the headers,
  `ProxyPassMatch` for a service and `RedirectMatch` for a redirect.

What a route cannot do, and why:

- **The addresses allowed stay the site's.** `--open` lifts the password under a prefix, not the
  address lists: those are checked for the whole server block.
- **A prefix handed to a port reaches whatever listens there**, so the port of the web server, of
  SSH and of the console are refused with who holds them, and a member of a team may name only the
  ports of applications it sees; any other is the server's to ask for.
- **Proxies and redirects do not nest.** `/api` handed to a service cannot also have `/api/v2`
  redirected: the two web servers would not agree on which wins. Routes that only change the
  password or the headers nest as they like.
- **`/.well-known` is the certificate authority's** and cannot be a route.
- The prefix is judged again after an internal redirect (`try_files` falling back to
  `/index.html`), as the path it was sent to: an open prefix under a protected single-page site asks
  for the password at the fallback. That direction fails closed.
- A password route needs accounts: saving one without them is refused, and the last account cannot be
  removed while a route needs it.

API: the routes are the `routes` field of `GET|PUT /api/apps/{domain}/policy` (a list that replaces
the whole). Console: **Application, Settings, Access, Routes**.

## Signing in with a service of your own (forward-auth)

nginx's `auth_request` asks a service about every request before it reaches the application: a 2xx lets
it through, a 401 or 403 stops it, and what the service says in its response headers (who the visitor
is) can be handed to the application. That is how an identity provider or an OAuth2 proxy protects an
application that knows nothing about it, with the accounts, second factor and single sign-on the
provider already has.

```bash
noust app policy set shop.example.com --forward-auth-preset oauth2-proxy         # or authentik
noust app policy set shop.example.com \
    --forward-auth-url http://127.0.0.1:4180/oauth2/auth \
    --forward-auth-sign-in /oauth2/start?rd= \
    --forward-auth-header X-Auth-Request-User --forward-auth-header X-Auth-Request-Email
noust app policy set shop.example.com --no-forward-auth
```

- **What nginx is told.** An internal location (`/_noust_auth`) that only nginx can reach asks the
  address, without the body, with the original address and method in `X-Original-URL` and
  `X-Original-Method`. A visitor who is not signed in is redirected to the sign-in with the page they
  asked for appended (`--forward-auth-sign-in`; empty answers 401). The service's `Set-Cookie` is
  passed to the visitor, so it can renew its session.
- **Who the visitor is.** The response headers you name are set on the request to the application (and
  to a prefix handed to another service) on every request, so a header a client sent with that name is
  replaced, never trusted.
- **Together with a password or an address list.** Both are wanted (`satisfy all`) unless the policy
  says `access: any`, which needs two ways in and lets either do.
- **It is the server's to name.** nginx will call the address from this machine, so a member of a team
  cannot set it (the server's administrator does). The web server's own port, SSH's and the console's
  are refused as the service. Apache has no `auth_request` here.
- **A service that is down refuses everybody, you included.** Before it is kept, the service is asked
  once as nginx would for a visitor with no session (any HTTP answer, 401 and 403 included, proves it
  is there). One that does not answer is refused with the reason unless you go on knowingly
  (`--accept-unreachable-auth`; the console asks).
- **Limits.** It covers the whole site: a prefix cannot be left out of it (a redirect route answers
  before authentication, as nginx's rewrite phase comes first). The page asked for travels unencoded
  in the sign-in's `rd=`, as nginx's own guides do, so a query with `&` is cut at the service.
  The presets are the defaults of oauth2-proxy and authentik; check them against your version.
  Using Noust's own accounts as the provider is not done.

API: the `forward_auth` field of the policy (an empty `url` turns it off) and
`GET /api/policy/forward-auth-presets`. Console: **Application, Settings, Access, Sign-in service**.

## HTTP/3

```bash
noust app policy http3 shop.example.com             # can this server? what is missing? is UDP 443 let in?
noust app policy set shop.example.com --http3       # serve the site over QUIC as well
noust app policy set shop.example.com --no-http3
```

A site served over HTTPS can also listen on QUIC (UDP 443) and tell browsers with `Alt-Svc`. It is
nginx's: the version must be 1.25.0 or later and built with `--with-http_v3_module` (Debian 12 and
Ubuntu 24.04 ship 1.22 and 1.24, which have neither; the packages from nginx.org do), and the answer
says which of the two is missing and where to get it. Apache's stock builds have no HTTP/3.

- **One socket for every site.** nginx takes `reuseport` once for an address and port, and QUIC needs it
  to work with more than one worker, so no application's site carries it: a small site of Noust's own
  (`00-noust-quic`, which sorts first and refuses the TLS handshake of any name nobody serves) holds the
  one `listen 443 quic reuseport`. It is written with the first site that turns HTTP/3 on, tested by
  nginx before it is reloaded, and taken away with the last.
- **The firewall.** QUIC travels over UDP 443. Noust says whether the firewall lets it in and names the
  command that opens it; a closed port costs a browser one failed attempt before it uses HTTP/2, so
  nothing breaks. Noust does not open it for you.
- A location of the site that sets a header of its own drops the server's headers, `Alt-Svc` included:
  that is nginx's rule for `add_header`.

API: the `http3` field of the policy, and `GET /api/apps/{domain}/http3` for what the server can do.
Console: **Application, Settings, Access, HTTP/3**. Event: `sites.http3` (the socket's site written or
removed).

## A web application firewall

A site can have ModSecurity with the OWASP Core Rule Set in front of it: every request is judged for
SQL injection, cross-site scripting, path traversal, remote command execution and the like before it
reaches the application. nginx only; Apache has its own module and is not driven here.

```bash
noust app policy waf show shop.example.com          # can this server run it, and what is missing
noust app policy waf install                        # Debian and Ubuntu: the module and the rules
noust app policy set shop.example.com --waf detect  # judge and write down, refuse nothing
noust app policy waf events shop.example.com        # what the rules saw, and which rules matched most
noust app policy set shop.example.com --waf block   # refuse what scores too high (403)
noust app policy set shop.example.com --waf-exclude 942100 --waf-exclude 941100
noust app policy set shop.example.com --waf off
```

**Start in `detect`.** The rules are strict, and an application's own legitimate requests (a search
box that takes SQL-looking text, an editor that posts HTML) are the usual false positives. In
`detect` nothing is refused; the events list shows each request the rules judged, the rules that
matched, and a request that `block` would have refused is flagged *would be blocked*. When one of
those is a request you want, leave the rule that matched out for this site (`--waf-exclude`, or the
button beside the rule in the console) and look again. Only then move to `block`.

- **The module and the rules are the server's.** `libnginx-mod-http-modsecurity` and `modsecurity-crs`
  on Debian and Ubuntu; `noust app policy waf install` (sudo mode, the server's administrator) runs the
  package manager. Elsewhere install nginx's ModSecurity module and the Core Rule Set yourself. A policy
  that asks for the firewall on a server without them is refused in words, with what is missing.
- **The rules are loaded once.** `modsecurity_rules_file` at the http level loads the 900-odd rules a
  single time for every site; a site only says `modsecurity on` (and, for `detect`,
  `SecRuleEngine DetectionOnly`, and the numbers it leaves out). The file (`noust-waf/rules.conf`) and the
  one-line site that loads it (`00-noust-waf`) are Noust's, written when the first site turns the firewall
  on, tested by nginx before it is reloaded, and removed with the last. libmodsecurity reads only the plain
  `Include`, so the file names the packages' setup file and rules directory itself instead of using the
  distribution's `owasp-crs.load`, which is made for Apache.
- **The log does not keep what people type.** ModSecurity's audit log (`/var/log/nginx/noust-waf-audit.log`,
  rotated with the web server's) is JSON and holds the request's headers and the rules that matched, not
  its body: a password typed into a form is not in it. Responses are not inspected.
- **A rule number is a number.** The numbers a site leaves out end up in the web server's configuration,
  so only whole numbers from 1 to 99999999, at most 100, are accepted.
- **The paranoia level and the score threshold are the Core Rule Set's defaults** (level 1, threshold 5).
  They are the rule set's own settings file, which Noust does not rewrite.

**Banning the clients that keep trying.** The firewall refuses a request; fail2ban stops the client.

```bash
noust server security fail2ban web on      # watch nginx as well as SSH
noust server security fail2ban web off
```

It adds three jails that read the sites' logs: `nginx-http-auth` (a password refused by `auth_basic`
again and again), `nginx-botsearch` (a client asking for paths no site has: `wp-login.php`,
`phpmyadmin`, `.env`) and `nginx-limit-req` (a client that keeps going over a rate limit). fail2ban
ships the three filters. fail2ban must be running (`noust server security fail2ban install`) and nginx
installed. **Who is never banned** matters more than who is: a ban is made from the address nginx
logs, which behind a CDN is the CDN's, and banning a CDN's edge takes every site behind it down. So
besides who is connected over SSH now and the console's client, Cloudflare's published ranges and every
proxy a site declares (`real_ip custom`) are ignored. A site behind a CDN Noust does not know about has
to declare it first. A file fail2ban refuses is put back. Console: **Server, Security, Brute force**.
API: `POST /api/server/security/fail2ban/web` (sudo mode). Bans lift like the SSH ones
(`noust server security fail2ban unban ADDRESS`).

API: `GET /api/apps/{domain}/waf` (what the server has), `GET /api/apps/{domain}/waf/events` (what the
rules saw and the rules that matched most), `PUT /api/apps/{domain}/policy` with `waf` and
`waf_exclusions`, and `POST /api/waf/install` (sudo mode, the server's). Console: **Access, Web
application firewall**. Event: `sites.waf`.

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
`client_certs`. Making and revoking ask for sudo mode. Console: **Access, Client certificates**.
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

API: `GET|PUT|DELETE /api/unknown-hosts`; changing it needs sudo mode. Console: **Domains and
certificates, Sites**, above the list. Event: `sites.unknown_hosts`.

## A private network: Tailscale, WireGuard

An application can answer a private network only: the devices of a tailnet or of a WireGuard
interface, and nobody else. It is not a new kind of site. It is the site's list of allowed
addresses ([Access](#access-a-password-addresses-limits-and-headers)) set to the network's ranges
by name, with everything that list already does: your own address is checked first, the site is
rendered again and tested by the web server, and a refusal puts everything back.

```bash
noust app policy private networks                              # what this server is on
noust app policy private enable shop.example.com --network tailscale
noust app policy private enable shop.example.com --network wg0 --cidr 10.9.0.0/24
noust app policy private show shop.example.com                 # private or not, and which network
noust app policy private disable shop.example.com              # answer everybody again
```

- **Tailscale.** The range is Tailscale's CGNAT block (`100.64.0.0/10`) and its IPv6 prefix, so any
  device of the tailnet that the tailnet's own access rules let reach this server is let in.
  Narrower than that is the tailnet's ACLs' job, which already know who is who.
- **WireGuard.** An interface of type `wireguard`; its network is the one its own address is in
  (`10.8.0.1/24` is `10.8.0.0/24`).
- **Ranges of your own** (`--cidr`) must be private: RFC 1918, the CGNAT block, unique-local or
  loopback. A public range is refused, because a site that answers the public internet is the
  opposite of what was asked.
- **Whether it is private is derived, never recorded.** A site is private when its allowed
  addresses are not empty and every one of them is a private range; edit the list by hand and the
  answer follows. Making it private replaces the public ranges the list had and keeps its private
  ones; making it public again takes the private ranges off and leaves a public one somebody added.
- **Connected over the internet you would be refused too.** The change is rejected unless the
  address you come from is in the new list; `--accept-lockout` (in the console, the confirmation)
  goes ahead knowingly.
- **It does not close the ports.** The web server still listens on the public address and answers a
  visitor from outside with 403; the certificate authority's challenge on port 80 stays open so a
  certificate for a public name can still be ordered. Closing 443 is the firewall's job, and
  Noust does not close a port that other sites on the server use.

**A certificate for the tailnet's own name.** Let's Encrypt cannot reach a name that only exists
inside a tailnet, and Tailscale issues one for it:

```bash
noust app policy private certificate web.tail1234.ts.net     # tailscale cert, kept and served
```

The application must answer on that name and nothing else (a browser would warn on the other
names), which is checked before Tailscale is asked; HTTPS certificates must be on for the tailnet
(Tailscale's admin console, DNS). The pair is kept as an uploaded certificate called
`tailnet-<name>` and served through the same path as any other upload. The hourly network upkeep
asks for it again when thirty days are left and reloads the sites that serve it
([Upkeep](#upkeep)). Putting a fleet's own traffic on the mesh is a separate question and is not
done here.

For a name Tailscale does not issue for (`wiki.home.arpa`, a name on a VPN or a home network), the
server can sign its own certificates: [an authority of your own](certificates.md#an-authority-of-your-own-for-private-names).

API: `GET /api/private-networks`; `GET|PUT|DELETE /api/apps/{domain}/private` (changing it is the
policy's permission) and `POST /api/apps/{domain}/private/certificate` (sudo mode, server scope:
the certificate joins the server's own). Console: **Access, Private network**.

## DNS accounts and the records Noust owns

A DNS account is a provider and a credential. The credential is **proven when the account is added**
and kept only in the secret store.

```bash
noust dns provider kinds                       # what each provider does and what it needs
printf %s "$TOKEN" | noust dns provider add cf --kind cloudflare --token-stdin --automatic
printf %s "$SECRET" | noust dns provider add aws --kind route53 --set access_key_id=AKIA... --token-stdin
noust dns provider list | verify cf | set cf --ask --propagation 45 | remove cf
noust dns ensure shop.example.com api.example.com --plan      # what would be done, changing nothing
noust dns ensure shop.example.com api.example.com
noust dns records | release shop.example.com | sweep | check api.example.com
```

Noust speaks the API of five providers itself: it lists their zones, makes and removes records
under the ownership model below, and answers the certificate challenge without a certbot plugin
(see [certificates.md](certificates.md)). Linode, OVH and RFC 2136 still answer the challenge
through certbot's plugin and make no records.

| Provider | Credential | Notes |
|---|---|---|
| Cloudflare | An API token with Zone:Read and DNS:Edit on the zones (and Account, Cloudflare Tunnel:Edit for tunnels) | The only one that carries tunnels and proxies a record. |
| DigitalOcean | A personal access token with write scope | Keeps no note with a record. |
| Hetzner | An API token of the **Cloud** project that holds the zones, with read and write rights | Hetzner moved DNS into the Cloud API and closed the old DNS Console API: a zone still on the old console must be migrated first. Keeps a comment with each value. |
| Gandi | A personal access token with the right to manage the domains' technical configuration (LiveDNS) | Keeps no note with a record; its smallest TTL is 300 seconds. |
| Amazon Route 53 | The access key id and secret of an IAM user allowed `route53:ListHostedZones`, `ListResourceRecordSets` and `ChangeResourceRecordSets` | Keeps no note with a record. Requests are signed with SigV4 by Noust itself, with no SDK. The secret half is read from standard input, like any other credential. |

A provider that keeps records in sets (Route 53, Hetzner, Gandi) has no number for a record, so
Noust names one by what identifies it: the name, the type and the value. Two records of a set that
hold the same value are one record, which is what these providers do too. An **alias** or a
record with a **routing policy** (weighted, latency, failover, geolocation) at a Route 53 name is
the operator's: Noust refuses the name and says why, and never writes it back as a plain set.

**Where the provider keeps no note**, the marker cannot be written at the provider, so the
ownership rests on Noust's own record of what it made and on the record still being exactly what it
created: same name, type and content. That is weaker than a marker an operator can read at the
provider, and it is why the console says so when such a provider is chosen. A record that was
already there is still adopted and never deleted, a record somebody edited is still left as they
left it, and the challenge record is removed by its own value, which is that order's token and
nobody else's.

**The ownership model** (what makes it safe to let software edit DNS):

- A record Noust creates carries a marker comment with an id, and Noust remembers it made it.
- A record that was already there with the right content is **adopted** and left alone when the
  name goes.
- A **different** record at the name is a conflict that names it. `--replace-record ID
  --replace-current VALUE` (or the console's confirmation) replaces exactly that one; only its
  content changes, and only if it still holds what you were shown.
- A record shared by two applications goes with the last of them.
- A record is deleted only if Noust made it, it still carries its marker and its content, and
  nothing uses it. The check and the deletion happen under a lock per name.
- An address that cannot be public (private, loopback, shared, link-local, reserved) is never
  published.

With `--automatic` an account makes the records of a domain when it is added (`noust domain add`);
otherwise the console's DNS section and `noust dns ensure` do it when you say so. Records nobody
has used for an hour are swept by an hourly pass (see Upkeep below).

## TCP and UDP ports

For a service that is not HTTP (a game server, a message broker, a WireGuard endpoint), nginx's
`stream` module carries the port:

```bash
noust stream check                                              # can this nginx do it?
noust stream add minecraft --port 25565 --to 25565 --allow 203.0.113.0/24 --plan
noust stream add minecraft --port 25565 --to 25565 --allow 203.0.113.0/24
noust stream add dns --protocol udp --port 5353 --to 10.0.0.2:53
noust stream list | remove minecraft | repair
```

It publishes in this order, and stops at the first thing that is wrong:

1. **Every question about the port** is answered before anything is touched: whether something
   listens on it (`ss`, TCP and UDP), whether another exposure or a database made public holds it,
   SSH's, the console's, 80 and 443 are refused with who holds them.
2. **nginx** must have the module (`nginx -V`), load it (`nginx -T`), and read a `stream` block
   that includes `/etc/nginx/noust-stream/*.conf`. On Debian and Ubuntu Noust writes that block
   (`modules-enabled/60-noust-stream.conf`); where you already have a `stream` block, or there is
   no such directory, it prints the one line to add and changes nothing. An operator's
   configuration is included, never rewritten.
3. The row is written first, then the file, then **nginx tests the whole configuration** and its
   own words are the error; nothing is reloaded if it refuses.
4. The **firewall** is told to allow the port for the sources given, through the one firewall
   manager: allow rules only, and only what Noust made is closed with the port. A rule that was
   already there stays.

**Who holds a port** is one answer, kept by one registry (`managers/port_claims.py`): the ports of
applications (and the second one of a zero-downtime pair), the ports a Compose stack publishes (read
from its file, and from its running containers), streams, databases made public, the engines Noust runs
itself, SSH's, the console's and what the kernel says listens. An application, a stream, a database
and a Compose stack are all asked the same way, so a stack that publishes `18081` is refused, naming
the holder and before Docker is asked, when anything else holds it; the stack that holds a port is not
refused it on an update. Someone who sees every application is told whose port it is; a team's
request is told only that it belongs to another application.

The backend must be an IP address (a name would be looked up when nginx reads its configuration,
and a lookup that fails keeps nginx from reloading). An exposure interrupted half way is finished by
asking for the same thing again, or by `noust stream repair`.

API: `GET|POST /api/streams`, `POST /api/streams/plan`, `DELETE /api/streams/{name}`. Console:
**Domains and certificates, Ports**. Publishing and closing need sudo mode.

## Cloudflare Tunnel

For a server with no public address (a NAS, a machine at home, behind CGNAT) or no port to open:
the connector (`cloudflared`) dials **out** to Cloudflare, which carries the names routed to it
back through that connection.

```bash
noust tunnel check                                  # is cloudflared installed? is there an account?
noust tunnel create home --account cf
noust tunnel route add home shop.example.com --app shop.example.com
noust tunnel route add home grafana.example.com --service http://localhost:3001
noust tunnel list | route remove home shop.example.com | remove home | repair
```

- `cloudflared` is installed by you from Cloudflare's own package repository
  (<https://pkg.cloudflare.com/>): the one program Noust never fetches itself, and never as
  `latest`.
- The connector's token goes to a file only root reads (`tunnels/<name>` of the secret store) and
  the unit hands it to the process through `EnvironmentFile=`; it is never an argument, never in
  `/tmp`, never in the unit. The unit runs as a user that exists only while it runs, with the
  sandbox of Noust's other units. A sealed central cannot run a connector (nobody types a
  passphrase at boot); run tunnels on a server.
- Noust builds the **whole ingress** from the routes it records, with the catch-all that answers
  404 last, so what Cloudflare holds is always what Noust says.
- For an application the connector reaches nginx on this machine, keeping the public host name:
  `http://localhost:80` when the site serves plain HTTP (a site that redirects to HTTPS would
  redirect forever through a proxy that already speaks HTTPS), `https://localhost:443` when it
  serves TLS (not verified: the connection never leaves the machine, and an expired certificate must
  not take the tunnel down). The site's policy and headers apply.
- The name's proxied CNAME to `<tunnel id>.cfargotunnel.com` is made through the same ownership
  model as above, in the account that holds the zone. A conflicting record is reported (the route
  works at Cloudflare as soon as the name points at it) and confirmed by naming it.
- Deleting a tunnel stops the connector, deletes the tunnel at Cloudflare (it may refuse for a
  moment while the connector still counts as connected: run the command again), and lets go of the
  records of its routes. An account a tunnel is managed with cannot be removed.

API: `GET|POST /api/tunnels`, `DELETE /api/tunnels/{name}`, `POST /api/tunnels/{name}/routes`,
`DELETE /api/tunnels/{name}/routes/{hostname}`. Console: **Domains and certificates, Tunnels**.

## When an application is deleted

`noust delete` lets go of everything made for it elsewhere: the tunnel routes (taken out of the
ingress), the published ports that were for it, its DNS records (by the ownership model above),
its policy and accounts, its client certificates and their authority, and its certificate choice. Each step is attempted whatever the one
before it did, and what failed is returned as a warning.

## Upkeep

The monitor asks once an hour whether there is anything to do and, when there is, starts a
transient unit (`noust-network-maintenance`) because its own sandbox is read-only for the web
server's files. The unit sweeps the DNS records nobody has used for an hour and fetches
Cloudflare's ranges again when they are a week old and a site trusts them, rendering those sites
again. It also asks Tailscale again for a certificate it issued when thirty days are left, and
reloads the sites that serve it.

## Permissions and audit

Published ports and tunnels are the server's, not an application's: reading them is
`server.read`; publishing, closing, creating and routing are `server.host_access` (ports) and
`server.manage` (tunnels) and ask for sudo mode. DNS accounts are `server.manage`. A site's policy
is `apps.manage`. Each change is an audit event (`sites.policy`, `sites.policy.user`, `sites.port`,
`domains.canonical`, `dns.account`, `dns.record`, `dns.challenge`, `certs.upload`, `certs.assign`,
`certs.acme`, `certs.client`, `streams.open`, `streams.close`, `tunnels.create`, `tunnels.route`, `tunnels.delete`).
