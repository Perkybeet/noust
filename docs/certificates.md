# Certificates

Noust orders, renews, serves and warns about the certificates of the sites it writes. Certbot
does the ordering; Noust decides **from where**, **how the name is proven**, **which certificate a
site serves**, and puts the result in front of the web server.

## The certificate authority

Certificates are ordered from Let's Encrypt unless you choose another authority.

```bash
noust cert acme                                    # show the authority and the account
noust cert acme --ca letsencrypt-staging           # try a setup without spending the rate limit
noust cert acme --ca zerossl --email ops@example.com --eab-kid KID --eab-hmac-key-stdin --register
printf %s "$HMAC" | noust cert acme --ca zerossl --eab-kid KID --eab-hmac-key-stdin
noust cert acme --ca custom --server https://acme.example.com/directory
```

| `--ca` | What it is |
|---|---|
| `letsencrypt` | Let's Encrypt (the default). |
| `letsencrypt-staging` | Let's Encrypt's staging environment: certificates browsers **do not trust**, with generous rate limits. The place to try DNS, ports and hooks before spending the real ones. |
| `zerossl`, `google` | Authorities that ask for an **external account** (EAB): create it at the authority and give its key id and HMAC key. |
| `custom` | Another ACME directory, with `--server`. |

The HMAC key is read from **standard input**, never an argument (everything on a command line is
visible in `ps`), is kept in the secret store (a `0600` file, sealed with the rest on a sealed
central) and is passed to certbot through a configuration file, never on its command line. It is
never in an API response or in the console. `--register` registers the account with the authority
now, so a wrong key fails where you are looking and not at the next order. The settings live in
`ssl.ca`, `ssl.acme_server`, `ssl.eab_kid` and `ssl.email` ([CONFIG.md](CONFIG.md)); the console
has them under **Domains and certificates, More actions, Certificate authority**.

## How a name is proven

| Challenge | How | Needs |
|---|---|---|
| `http-01` (the default) | The authority fetches a file from port 80. | The name points at this server, port 80 reachable. |
| `dns-01` | The authority looks up `_acme-challenge.<name>`. | A [DNS account](network.md#dns-accounts-and-the-records-noust-owns) that holds the zone. Port 80 can stay closed. **A wildcard needs it.** |

For a Cloudflare, DigitalOcean, Hetzner, Gandi or Amazon Route 53 account Noust answers the
challenge itself, through two hooks certbot keeps in the lineage's renewal configuration
(`noust dns challenge auth|cleanup --account NAME`), so every renewal runs them again with nobody
there. The credential stays in the secret store; no file with it is left for certbot to point at.
The record is published with a marker where the provider keeps notes and removed afterwards, and
only the record that order published is removed. After publishing, Noust waits for the record to
be served: 20 seconds, 30 for Hetzner and 60 for Route 53, or what the account sets with
`noust dns provider set NAME --propagation SECONDS`. For the other providers (Linode, OVH,
RFC 2136) certbot's own plugin answers, from a credentials file Noust writes `0600`; the plugin
package must be installed (`noust dns provider kinds` says which).

## Where an application's certificate comes from

Each application chooses, and the choice is kept in the store:

```bash
noust cert use shop.example.com --acme                      # ordered, proven over HTTP
noust cert use shop.example.com --acme --dns cf             # ordered, proven with a DNS record in cf
noust cert use shop.example.com --upload origin             # serve an uploaded certificate
noust cert source shop.example.com                          # where it comes from, and what is served
```

Changing how a certificate that exists is proven orders it again, **once**: certbot remembers the
method in the lineage's renewal configuration. A failed order puts the choice back, and the
certificate that served keeps serving. A name that a certificate you already have covers (a
wildcard from another lineage) can be served without ordering one.

A site that serves no TLS yet (deployed with `--no-ssl`, or whose first certificate could not be
ordered over a port 80 nobody can reach) only **keeps the choice**. Its first certificate is
`noust cert create -d APP`, which proves the name the way the choice says (DNS included), and then
writes the site with TLS, so the same two commands take a name behind a closed port 80 to HTTPS:

```bash
noust cert use wiki.example.com --acme --dns cf
noust cert create -d wiki.example.com
```

## A certificate you bring

```bash
noust cert upload origin --cert origin.pem --key origin.key [--chain chain.pem]
noust cert uploads                                          # names, expiry, issuer, who serves it
noust cert remove-upload origin
```

For a Cloudflare Origin Certificate, a corporate CA's or a purchased one. **Before anything is
written** the key must be the certificate's and have no passphrase, the certificate must not have
expired, and it must name something. The key may come from standard input (`--key -`). The files
are kept in `/etc/noust/certs/<name>/` (the key `0600` in a `0700` directory), the web server is
tested before it is reloaded, and a certificate an application serves cannot be deleted. **It is
never renewed or revoked by Noust**: it appears in `noust cert list` and in the health report, and
you are warned as it expires. Upload the new one over it.

## An authority of your own, for private names

Let's Encrypt signs a name it can prove, and `wiki.home.arpa` or `grafana.lan` (a name that only
resolves on a home network, a VPN or a tailnet) has no such proof. The usual answers are a browser
warning for ever or an authority of your own. This server can be its own:

```bash
noust cert ca init                                          # make it (once)
noust cert ca init --permit-dns corp.example.com --permit-network 10.20.0.0/16
noust cert use wiki.home.arpa --internal                    # sign for every name the app answers on, and serve it
noust cert ca issue gitlab gitlab.lan 10.0.0.5 --days 60    # a certificate by hand (kept as internal-gitlab)
noust cert ca list | status | renew internal-gitlab | renew --due
noust cert ca root --out ca.crt                             # the public certificate to install (--der for ca.cer)
noust cert ca trust                                         # how, on each kind of device
noust cert ca trust --here                                  # trust it on this server (--remove to stop)
```

- **It can only sign what it was made for.** The limit is a *name constraints* extension in the
  authority's certificate, marked critical, so every device that trusts it enforces it: a leaked key
  cannot be used to impersonate `bank.com` to the people who installed the root. By default it signs
  `lan`, `internal`, `home.arpa`, `local`, `localhost`, `test` and the private, tailnet and loopback
  ranges; `--permit-dns` and `--permit-network` add to them and `--only` replaces them. Noust refuses
  a top-level domain (`com`) and a public address range before making anything, and refuses a name
  outside the constraints before openssl is asked. The integration harness proves the other half with
  a real client: a leaf for a public name signed with the very key is rejected by openssl itself.
- **It is made once and never again by itself.** A new root has to be trusted again on every device,
  so the authority that is about to expire is a warning (six months before, in `noust health` and the
  console; an issue in the last month), not a surprise. Coolify's own authority expires after ten
  years without a word. `noust cert ca remove` deletes it and its key.
- **A certificate it signs** is P-256, can authenticate a server and nothing else, is valid 90 days
  unless asked (up to 397) and never outlives the authority. It is kept as an uploaded certificate
  named `internal-<name>`, so everything that serves, lists, warns about and backs up an upload does
  the same for it, and an application serves it through the one path that serves an upload.
- **It renews itself.** The hourly network upkeep signs it again for the same names and reloads the
  sites that serve it when thirty days are left (a third of its term for one made for under three
  months, so a short one is not signed again every hour). One that cannot be renewed does not keep
  the others from it, and a site that cannot be reloaded is named, because it still serves the old
  certificate.
- **Trust is yours to install, per device.** `noust cert ca trust` has the steps for Debian and
  Ubuntu, RHEL and its family, openSUSE, Alpine, Arch, macOS, Windows, iOS, Android, Firefox, Java,
  Node.js and curl, each with the file it wants and the fingerprint to compare before trusting it.
  Noust changes only this server (`--trust-here`, or `--trust-here` at `init`): its own store, rebuilt
  through the runner, so curl, git and a health probe here accept the names. A program with a store
  of its own (Java, Node, a container) needs the steps for it.
- **It is not the database authority.** Engines that serve TLS (`noust db`) have a root of their own
  in `/etc/noust/tls`, with `pathlen:0`, signing for engines and clients; this one is in
  `/etc/noust/ca`, name-constrained and signing for web servers. A certificate of one does not verify
  against the other, and installing this root on a device does not make it trust an engine.
- **There is no revocation list.** A leaf is replaced, not revoked: take the certificate out of the
  site (`noust cert use APP --acme`) and delete it (`noust cert remove-upload NAME`). For a root
  whose key may be out, remove it and make another; every device has to trust the new one.

API: `GET|POST|DELETE /api/certs/ca` (making and deleting ask for sudo mode),
`GET /api/certs/ca/root?format=pem|der` (public), `GET /api/certs/ca/trust`,
`POST|DELETE /api/certs/ca/trust-here` (sudo mode), `POST /api/certs/ca/issued` and
`POST /api/certs/ca/issued/{name}/renew`; an application chooses it with
`PUT /api/apps/{domain}/certificate` and `{"source": "internal"}`. They are the server's, not a
team's.

## When a name goes

Removing a domain from an application orders its certificate again with only the names it still
answers on (`noust domain certificate APP --reduce` retries it) and lets go of the DNS record Noust
made for it. Coolify's issue #6800, a certificate that kept a deleted domain, is this case.
Nothing is revoked: the old certificate expires on its own and the new one never covered the name.

## Renewal

Certbot's timer renews. A deploy hook (`/etc/letsencrypt/renewal-hooks/deploy/noust-reload`)
reloads the web server afterwards, so a renewed certificate is the one being served without anyone
running anything. Every order writes it, whatever proves the name: certbot's own nginx plugin
reloads after a renewal it proves over HTTP, and a DNS order has no such plugin, so without the
hook a renewed certificate would sit on disk while the site went on serving the old one until it
expired. A hook you wrote under that name is left alone. `noust cert renew` is a command somebody
waits on, so it does not take the random pause (up to eight minutes) certbot adds to a renewal that
no terminal started; the timer's own runs keep it. The monitor's expiry warnings reach the notification channels, and an uploaded certificate is
warned about as well.

## Backing it up

`/etc/letsencrypt` (accounts, keys, renewal configuration, the hook above) is part of
`noust self-backup` (see [disaster-recovery.md](disaster-recovery.md)), with its links, taken on a
schedule, with retention, proven restorable and put back on a fresh install. So are the uploaded
certificates (`/etc/noust/certs`) and the secrets of the DNS accounts and of the external account.
