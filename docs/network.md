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

## DNS accounts and the records Noust owns

A DNS account is a provider and a credential. The credential is **proven when the account is added**
and kept only in the secret store.

```bash
noust dns provider kinds                       # Cloudflare (records, tunnels, challenge) and certbot's plugins
printf %s "$TOKEN" | noust dns provider add cf --kind cloudflare --token-stdin --automatic
noust dns provider list | verify cf | set cf --ask --propagation 45 | remove cf
noust dns ensure shop.example.com api.example.com --plan      # what would be done, changing nothing
noust dns ensure shop.example.com api.example.com
noust dns records | release shop.example.com | sweep | check api.example.com
```

A Cloudflare token needs Zone:Read and DNS:Edit on the zones to manage, and Account, Cloudflare
Tunnel:Edit for tunnels. Other providers answer the certificate challenge through certbot's
plugin (see [certificates.md](certificates.md)); Noust makes records only in Cloudflare.

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
its policy and accounts, and its certificate choice. Each step is attempted whatever the one
before it did, and what failed is returned as a warning.

## Upkeep

The monitor asks once an hour whether there is anything to do and, when there is, starts a
transient unit (`noust-network-maintenance`) because its own sandbox is read-only for the web
server's files. The unit sweeps the DNS records nobody has used for an hour and fetches
Cloudflare's ranges again when they are a week old and a site trusts them, rendering those sites
again.

## Permissions and audit

Published ports and tunnels are the server's, not an application's: reading them is
`server.read`; publishing, closing, creating and routing are `server.host_access` (ports) and
`server.manage` (tunnels) and ask for sudo mode. DNS accounts are `server.manage`. A site's policy
is `apps.manage`. Each change is an audit event (`sites.policy`, `sites.policy.user`, `sites.port`,
`domains.canonical`, `dns.account`, `dns.record`, `dns.challenge`, `certs.upload`, `certs.assign`,
`certs.acme`, `streams.open`, `streams.close`, `tunnels.create`, `tunnels.route`, `tunnels.delete`).
