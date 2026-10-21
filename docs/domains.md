# Domains

An application in Noust answers on one primary domain and any number of aliases and
redirects. Every name is a row in the store, the web server site is rendered from those rows,
and one certificate covers all of them, unless a name asks for one of its own
([certificates.md](certificates.md#a-certificate-for-one-name)).

## Kinds

| Kind | What the web server does |
|---|---|
| `primary` | The application's own domain: the one it was created with, the one every command names it by. Exactly one per application, and it cannot be removed. |
| `alias` | Serves the application exactly like the primary (listed in the same `server_name`). |
| `redirect` | Answers with a permanent redirect (`301`) to the primary, keeping the path and query: `https://` when the site has a certificate, `http://` otherwise. |

A name belongs to at most one application. Adding a name that is already another
application's alias, redirect or primary is refused.

## Commands

```bash
noust domain list shop.example.com                       # primary, then aliases, then redirects
noust domain add shop.example.com shop.example.org       # alias (the default kind)
noust domain add shop.example.com old-shop.example.com --kind redirect
noust domain remove shop.example.com shop.example.org
noust domain disable shop.example.com shop.example.org   # stop answering on it, keep it
noust domain enable shop.example.com shop.example.org    # and serve it again
```

`noust domain list` takes `--json`. `noust domain add` takes `--no-cert` to skip extending the
certificate now; run the same command again later to extend it.

More, each going through the steps below:

```bash
noust domain add shop.example.com '*.example.com'            # a wildcard alias (quote the star)
noust domain add shop.example.com admin.example.com --port 9000
noust domain add shop.example.com legacy.example.com --force
noust domain add shop.example.com api.example.com --no-dns
noust domain port shop.example.com admin.example.com 9100    # or "none" for the application's own
noust domain canonical shop.example.com www                  # www, apex or none
noust domain certificate shop.example.com [--reduce]         # bring the certificate in line with the names
noust domain certificate shop.example.com admin.example.com --own   # a certificate of its own for one name
noust domain add shop.example.com api.example.com --own-certificate
```

- **A wildcard** (`*.example.com`) is an alias that takes every name one level under it. Its
  certificate can only be proven with a DNS record, so choose that first
  ([certificates.md](certificates.md)): `noust cert use APP --acme --dns ACCOUNT`.
- **A port of its own.** An alias can be proxied to another port of the application than its own
  (an admin interface, an API on a second listener). It gets a server block of its own; the rest
  of the site is unchanged. The port is checked against what the application can honour: a Docker
  Compose stack may send a name to a port the stack publishes on the host (read from its `.env`, as
  Compose reads it, so a recipe that gives a service an address of its own works this way,
  [recipes.md](recipes.md#several-public-addresses)) and to no other, and routes by path with
  `noust.nginx.yaml`, where each route names its port.
- **Another site already answers on the name.** Adding it is refused, naming the site, unless
  `--force` says it is meant (a wildcard that covers the name counts). In the console it is a
  question with the other site's words in front, and an "Add it anyway" button.
- **`www` or the bare name.** `noust domain canonical` serves one spelling and redirects the other
  to it, over TLS too: both are recorded, so the certificate covers both. `none` serves both as
  recorded.
- **DNS.** When a [DNS account](network.md#dns-accounts-and-the-records-noust-owns) that makes
  records without asking holds the name's zone, adding the name makes its record, and removing the
  name lets go of it (it is deleted only if Noust made it and nothing else uses it). `--no-dns`
  leaves DNS alone.

- **A certificate of its own.** A name can hold a certificate that covers it alone
  (`--own`, or `--own-certificate` when adding it): a lineage named after it, a server block of its
  own, and the application's certificate stops naming it. It is ordered before the site changes, and
  a refusal changes nothing. [certificates.md](certificates.md#a-certificate-for-one-name) has the
  order of the steps, how it is proven (HTTP-01 or a DNS account) and how it is renewed. Removing
  the name deletes just that lineage. `noust domain list` says which certificate serves each name.
- **Switching a name off.** `noust domain disable` lets a name rest without forgetting it: the
  application stops answering on it, and its row, its kind, its port and the DNS record Noust made
  for it stay. The site is rendered again without the name, the web server tests it before the
  reload, and, when the site serves TLS, the certificate is ordered again without the name (the
  console queues that as a job, like removing a name). `noust domain enable` brings all of it back and
  extends the certificate. `noust domain list` marks the names that are off, `--json` carries
  `enabled`, and so does `GET /api/apps/{domain}/domains`; the switch is
  `PUT /api/apps/{domain}/domains/{name}/enabled` with `{"enabled": false}`. The primary and the
  names of the canonical `www` pair cannot be switched off (choose `none` first), and adding a name
  that is off says to switch it on instead. Nothing is revoked.

Each change goes through the same steps, in this order:

1. **The store** records the name, refusing one that is not a domain, belongs to another
   application, or would be a second primary.
2. **The site** is rendered again by the application's own deployer, exactly as a deploy
   renders it, and the whole web server configuration is tested before the reload. A change
   the web server refuses is put back, the row and the file both, and the error shows the web
   server's own output.
3. **The certificate** is expanded under the same certbot lineage to cover every name,
   redirects included (a browser reaching `https://old-shop.example.com` needs a valid
   certificate before it can be redirected). This only happens for an application that
   already serves TLS; see below.

The unit, the build and the release are not touched: adding a domain does not restart the
application.

### `www`

`noust create --www` records `www.<domain>` as a **redirect** to the domain. To serve the
`www` name as the canonical one instead, create the application on `www.example.com` and add
`example.com` as a redirect:

```bash
noust create -d www.example.com -s git@github.com:you/site.git
noust domain add www.example.com example.com --kind redirect
```

`noust site create --www` (a bare site, not an application) is unchanged: it serves both names
and covers both with the certificate.

### Applications deployed before 2.0

The store migration gives every existing application its primary domain and nothing else,
because whether a 1.x deploy also served `www` was never recorded. The live site is not
changed by the upgrade: a 1.x application deployed with `--www` keeps serving both names.

The first `noust domain add` or `noust domain remove` on such an application reads the names its
live site answers on and records the ones the store does not have as aliases (the result
reports them as `adopted`), so adding one name never silently drops another. To turn an
adopted `www` alias into a redirect, remove it and add it back with `--kind redirect`.

## A temporary address

A server with no domain yet can still serve an application over HTTPS. `sslip.io` answers a name
made of an address with that address (`203-0-113-10.sslip.io` is `203.0.113.10`), and a certificate
can be ordered for it, so the application can be tried out before a real name points here.

```bash
noust domain temporary shop.example.com --show      # the name this server would get, changing nothing
noust domain temporary shop.example.com             # add it as an alias, with the certificate
noust domain temporary shop.example.com --no-cert   # add it without the certificate for now
```

The name is made from the server's public address (the one declared in `dns.public_addresses`, else
the one read from a network interface), never a private one. It is added as an ordinary alias, so
the DNS check, the site and the certificate are the ones of any other name, and `noust domain list`
and the console mark it **temporary**. It is for trying things out: Let's Encrypt limits are per
registered domain and `sslip.io` is everybody's, so an order for it can be refused at a busy
moment, and the name belongs to a service that is not Noust's to promise. Remove it with
`noust domain remove` when the real name is there. API: `GET /api/domains/temporary` (the
suggestion, writing nothing) and the ordinary `POST /api/apps/{domain}/domains`. Console: **Add
domain, Use a temporary address**.

## Certificates

- When an application serves TLS, `noust domain add` extends its certificate to the new name
  right away. If certbot fails (typically because DNS does not point here yet), the domain is
  kept, the site keeps serving TLS with the certificate it had, and certbot's own output is
  shown. Run the same `noust domain add` again once DNS is right: adding a name the application
  already has retries its certificate.
- When an application does not serve TLS, a new name is served over plain HTTP like the rest
  of it. Obtain the first certificate with `noust cert create -d <primary> -d <alias> ...`.
- Removing a name orders the certificate again with only the names that remain, so it does not
  keep naming a domain that is gone; if that fails, certbot's output is shown and
  `noust domain certificate APP --reduce` retries it. Nothing is revoked: the old certificate
  expires on its own and the new one never covered the name.
- Through the API, `POST /api/apps/{domain}/domains` returns as soon as the name is recorded
  and the site reloaded; extending the certificate is queued as a job whose id is returned as
  `certificate_job_id`. Follow it with `GET /api/jobs/{id}` or the `/events` stream.

Certificates come from Let's Encrypt through certbot, which rate limits issuance. A
certificate that already covers the requested names is left alone.

## DNS check

Before asking for a certificate, check that the name resolves to this server:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://panel.example.com/api/apps/shop.example.com/domains/shop.example.org/dns
# {"domain":"shop.example.org","expected_addresses":["203.0.113.10"],
#  "resolved_addresses":["203.0.113.10"],"points_here":true}
```

`GET /api/domains/dns?name=<domain>` runs the same check for a name that is not attached to
any application yet; the new-app wizard in the console uses it. The console also runs the
check when you add a name to an application, and from each domain's menu on the Domains tab,
showing this server's addresses next to the ones the name resolves to.

How it decides:

- The name is resolved against the **public resolvers** (`dns.resolvers`, 1.1.1.1 and 8.8.8.8 by
  default) with a built-in client, as the certificate authority's validators will, not the
  machine's own cache, which on a server often still holds the answer from before the record
  changed. When none answers, the machine's resolver is asked. `noust dns check NAME --resolver IP`
  asks others.
- It is compared with the addresses of this machine's network interfaces, leaving out
  loopback, link-local, multicast and unspecified addresses, plus `dns.public_addresses`.
- The answer is a **verdict**: `here` (every address is this machine's), `cdn` (every address is
  one CDN's, which proxies the name and reaches this server by itself, so it is not a mismatch:
  Cloudflare, Fastly, Bunny CDN or ArvanCloud, and the answer says which), `elsewhere` (a name that
  mixes two CDNs is this, because no one proxy reaches the server for it), `none` (it does not
  resolve) or `unknown`. Cloudflare's networks are fetched and kept fresh; the others are the ones
  their operators publish, shipped with Noust. Bunny's list changes most: an edge address that is not
  in it reads as `elsewhere` until the next release, and the certificate is not affected, only the
  sentence the check says.
- `points_here` is true for `here` and `cdn`.

A server **behind NAT** (cloud instances whose public address is mapped by the provider, servers
at home or in an office) only sees its private address, so it cannot say what its public one is.
It says so (`unknown`) instead of calling a correct record wrong; declare the public address to
have it compared: `noust config set dns.public_addresses 203.0.113.10`.

The check is advisory. `noust domain add` runs it first and prints a warning with both address
lists when the name does not point here, then goes ahead; `noust cert create` does not run it.
Certbot is the final judge. `noust dns check NAME` runs the same check alone.

## Answering over HTTP as well

Once a site has a certificate, every request on port 80 is redirected to HTTPS. Some applications
cannot live with that: an old client that cannot follow a redirect or speak TLS 1.2, a probe on a
device with no trust store, a vendor webhook that refuses redirects. The site policy has a switch for
it, per application:

```bash
noust app policy set shop.example.com --no-force-https     # one server answers on 80 and 443
noust app policy set shop.example.com --force-https        # the default again
```

With it off, the nginx site is **one server on ports 80 and 443** with no redirect; the challenge of
the certificate authority stays open to everyone on port 80, even when the site asks for a password;
and **HSTS is not sent**, because it promises browsers that the site is only reached over HTTPS (a
policy with HSTS on is refused together with `--no-force-https`, and the server-wide `ssl.hsts` is
ignored for that site). Apache is refused: the answer says to move the application to nginx. A site
without a certificate has nothing to force. API: `force_https` in
`GET|PUT /api/apps/{domain}/policy`.

## Operator sites

Noust writes the site of every application it deploys. A site that is already there, written by
someone else, is an **operator's site**: its file in `sites-available` does not carry Noust's marker,
the comment `Generated by Noust` (the first line of before 3.0 is recognised too, and rewritten once by `noust setup rename`) near the top of the
file. A hand-written file, a repository's `deploy/nginx.conf` copied in, and the site of a stack
you [adopted](compose.md#adopting-a-running-stack) are all operator's sites. Every path that
writes or removes an application's site, whatever the application's type, asks the same question
first, through one helper, and for an operator's site the answer is to leave it alone:

- **It is never rewritten.** A deploy, an update, a certificate order, adding or removing a domain
  and a rollback leave the file exactly as it is, and say so: "has a site configuration Noust did
  not write; it is kept as it is". Nothing is tested or reloaded for a change that did not happen.
- **It is never deleted with the application.** `noust delete` removes the application and says
  the site was kept, and its certificate with it, as something left on purpose and not a failure.
  To remove it as well, once nothing should answer on the name any more: `noust site delete
  DOMAIN`.
- **What Noust would need in it is checked and reported, never edited in.** If the primary domain
  or an alias the store records for the application is missing from the file's `server_name`, the
  warning names the names and the file to edit; redirects are not checked. The certificate is
  still ordered under the lineage named after the primary domain, covering every name the store
  records, but your site has to point at it (`/etc/letsencrypt/live/<primary>/fullchain.pem`):
  Noust orders certificates with `certbot certonly`, which does not touch your file.
- **The names it already answers on are kept.** `noust domain add` and `noust domain remove` record,
  as aliases, any name the live site already serves that the store does not know, as they do for an
  application deployed before 2.0 (see above), so changing one name never drops another.

**The file's name.** Noust names a site after its domain, and an operator's site often is not:
`shop` serves `shop.example.com`. Adopting a stack records the file's name (`site_name`, which `noust
app adopt --site` can name when the file is not found by its `server_name`), and from then on Noust
resolves the domain to that file wherever it reads one: the one place a domain becomes a file name
is the web server manager's `config_path`. `noust site show shop.example.com` and the console's site
page open `shop`, and `GET /api/sites` reports it as `site_name`.

[compose.md](compose.md#your-own-site) has the same rules from a Compose stack's side.

**The servers file include.** Updating a Docker Compose stack without a cut sends nginx through
a file of Noust's, `/etc/nginx/noust-upstreams/<app>/<service>.servers`, which holds only `server
127.0.0.1:<port>;` lines and changes while a service is recreated. A site Noust writes includes it
itself. An operator's site must include it inside the `upstream` block that proxies to the service,
in place of its `server` line, keeping its own names:

```nginx
upstream nestjs_upstream {
    include /etc/nginx/noust-upstreams/shop-example-com/backend.servers;
    keepalive 64;
}
```

`<app>` is the application's name, the domain with dots as dashes. Turning the mode on
(`noust app zero-downtime DOMAIN on`) writes the servers files first, so the include loads, and
refuses with the exact line that is missing if the site does not include it; Noust never adds it
to your file. See [compose.md](compose.md#zero-downtime-updates-the-relay).

**The list of sites.** The console's Sites tab and `GET /api/sites` list every file in the sites
directory, not only the ones Noust has a record of: before 3.2 the hand-written sites disappeared
from the list as soon as Noust had written one of its own. Each row says whether Noust wrote it
(`noust_managed`, "Written by" in the console), which application it serves, and the file's name.
A site Noust has a record of whose file is gone is still listed.

## Testing a site's configuration

Editing a site's text in the console, or `PUT /api/sites/{domain}/config`, tests it with the web
server before anything is written, and so does `POST /api/sites/{domain}/config/test`, which only
tests. The candidate is tested **inside the live main configuration**: Noust stages a copy of
`nginx.conf` (or `apache2.conf`) in a private temporary directory, replaces its include of
`sites-enabled` with the list of enabled sites with this one swapped for the candidate, and asks
the web server to check that copy. The live configuration is never touched and the copy is gone
when the answer arrives. Two things follow that a test of the site on its own could not give:

- A site that uses a `limit_req_zone`, a `map` or a `log_format` declared in `nginx.conf` is valid
  and passes; tested alone it was refused for a zone it could not see.
- A candidate that declares an `upstream` another enabled site already declares fails, as it would
  at the reload.

When the main configuration cannot be read, does not parse, or does not include `sites-enabled`
itself, the candidate is checked on its own inside a minimal wrapper, as before. The server's own
output is shown verbatim, on a pass as well as a failure.

## History of a site's file, and drift

Every write of a site's file keeps its text: what a deploy renders, what the editor or
`PUT /api/sites/{domain}/config` saves, and an earlier version put back. A site keeps its latest 30
versions, and rendering the same text again adds nothing.

```bash
noust site history shop.example.com                  # the versions, newest first; warns if the file has drifted
noust site diff shop.example.com 3                   # what changed between version 3 and the file now
noust site diff shop.example.com 3 --against 5
noust site restore shop.example.com 3                # the editor's own save: tested by the web server first
noust site restore shop.example.com 3 --reload
```

- **A hand edit is never lost to a deploy.** A generated site that somebody edited is rewritten by
  the next deploy (the file carries Noust's marker). Before it is, the text on disk is kept as a
  version of its own (`found`), so it can be read, compared and put back.
- **Restoring is an edit.** The web server tests the text before it is installed, with its own words
  as the error, and the restore is a version too. The web server is not reloaded unless asked
  (`--reload`; the console's "Use this version" puts the text in the editor as a draft).
- **Drift.** The sites list, a site's page and `noust site history` say when a file is waiting for a
  reload and when it was changed outside Noust. *Reload pending* means the file was written after the
  web server last started its workers (a reload makes new ones, so the process table answers it
  without a daemon): Noust reloads right after writing, so it is a reload that failed or a hand edit
  nobody applied. *Edited outside Noust* means the file's hash is not that of the newest version Noust
  kept. A site Noust never wrote has no such baseline and is never said to have drifted.

API: `GET /api/sites/{domain}/config/versions`, `GET .../versions/{version}` (with a diff to the file
now) and `POST .../versions/{version}/restore` (sudo mode, root-equivalent like the editor's save);
`pending_reload` and `edited_outside` on every site of `GET /api/sites`. Console: a site's page,
**History**.

## Limitations

- **Which applications have other names.** Applications built by Noust's deployers (Node, Python,
  PHP, static sites, Vite and the rest), Docker Compose stacks that publish a port, and monorepos
  answer on aliases and redirects, through the same helper that writes their sites. A Compose stack
  that publishes no port (a worker) has no site, and `noust domain add` refuses it, saying so:
  publish a port in the compose file and redeploy to serve it.
- **Monorepos.** The domain and every alias redirect to the primary workspace's subdomain
  (`<workspace>.<domain>`), and each workspace keeps its own subdomain; one certificate covers
  the domain, the subdomains and the aliases. A monorepo is only served through nginx.
- **Operator sites.** For a site you wrote, Noust records the name and orders the certificate but
  cannot add the name to your file: it tells you which names your `server_name` lacks and which
  file to edit (see [Operator sites](#operator-sites)).
- `noust domain` changes need an application deployed (or adopted) by Noust. For a bare site, use
  `noust site` and `noust cert`.
- Wildcard names (`*.example.com`) are not accepted as domains.
