# Domains

An application in Noust answers on one primary domain and any number of aliases and
redirects. Every name is a row in the store, the web server site is rendered from those rows,
and one certificate covers all of them.

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
```

`noust domain list` takes `--json`. `noust domain add` takes `--no-cert` to skip extending the
certificate now; run the same command again later to extend it.

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

### Applications deployed before WASM 2.0

The store migration gives every existing application its primary domain and nothing else,
because whether a WASM 1.x deploy also served `www` was never recorded. The live site is not
changed by the upgrade: a WASM 1.x application deployed with `--www` keeps serving both names.

The first `noust domain add` or `noust domain remove` on such an application reads the names its
live site answers on and records the ones the store does not have as aliases (the result
reports them as `adopted`), so adding one name never silently drops another. To turn an
adopted `www` alias into a redirect, remove it and add it back with `--kind redirect`.

## Certificates

- When an application serves TLS, `noust domain add` extends its certificate to the new name
  right away. If certbot fails (typically because DNS does not point here yet), the domain is
  kept, the site keeps serving TLS with the certificate it had, and certbot's own output is
  shown. Run the same `noust domain add` again once DNS is right: adding a name the application
  already has retries its certificate.
- When an application does not serve TLS, a new name is served over plain HTTP like the rest
  of it. Obtain the first certificate with `noust cert create -d <primary> -d <alias> ...`.
- Removing a name does not revoke anything. The certificate keeps covering the removed name
  until it is next issued with a different set of names.
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

- The name is resolved with the system resolver (`getaddrinfo`, A and AAAA).
- It is compared with the addresses of this machine's network interfaces, leaving out
  loopback, link-local, multicast and unspecified addresses.
- `points_here` is true when the name resolves and every address it resolves to is one of
  this machine's.

Known false negatives:

- **Behind NAT** (cloud instances whose public address is mapped by the provider, as on AWS
  and Google Cloud, and servers at home or in an office), the machine only sees its private
  address, so a name pointing at the public address reads as not pointing here.
- **Behind a proxying CDN** such as Cloudflare with the proxy enabled, the name resolves to
  the CDN's addresses, and the check reports it as not pointing here.

In both cases the check is advisory. `noust domain add` runs it first and prints a warning with
both address lists when the name does not point here, then goes ahead; `noust cert create`
does not run it. Certbot is the final judge.

## Operator sites

Noust writes the site of every application it deploys. A site that is already there, written by
someone else, is an **operator's site**: its file in `sites-available` does not carry Noust's marker,
the comment `Generated by Noust` (`Generated by WASM` is still recognised) near the top of the
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
  application deployed before WASM 2.0 (see above), so changing one name never drops another.

**The file's name.** Noust names a site after its domain, and an operator's site often is not:
`proggest` serves `proggest.es`. Adopting a stack records the file's name (`site_name`, which `noust
app adopt --site` can name when the file is not found by its `server_name`), and from then on Noust
resolves the domain to that file wherever it reads one: the one place a domain becomes a file name
is the web server manager's `config_path`. `noust site show proggest.es` and the console's site
page open `proggest`, and `GET /api/sites` reports it as `site_name`.

[compose.md](compose.md#your-own-site) has the same rules from a Compose stack's side.

**The servers file include.** Updating a Docker Compose stack without a cut sends nginx through
a file of Noust's, `/etc/nginx/noust-upstreams/<app>/<service>.servers`, which holds only `server
127.0.0.1:<port>;` lines and changes while a service is recreated. A site Noust writes includes it
itself. An operator's site must include it inside the `upstream` block that proxies to the service,
in place of its `server` line, keeping its own names:

```nginx
upstream nestjs_upstream {
    include /etc/nginx/noust-upstreams/proggest-es/backend.servers;
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
