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

## When a name goes

Removing a domain from an application orders its certificate again with only the names it still
answers on (`noust domain certificate APP --reduce` retries it) and lets go of the DNS record Noust
made for it. Coolify's issue #6800, a certificate that kept a deleted domain, is this case.
Nothing is revoked: the old certificate expires on its own and the new one never covered the name.

## Renewal

Certbot's timer renews. A deploy hook (`/etc/letsencrypt/renewal-hooks/deploy/noust-reload`)
reloads the web server afterwards, so a renewed certificate is the one being served without anyone
running anything. The monitor's expiry warnings reach the notification channels, and an uploaded certificate is
warned about as well.

## Backing it up

`/etc/letsencrypt` (accounts, keys, renewal configuration, the hook above) is part of
`noust self-backup` (see [disaster-recovery.md](disaster-recovery.md)), with its links, taken on a
schedule, with retention, proven restorable and put back on a fresh install. So are the uploaded
certificates (`/etc/noust/certs`) and the secrets of the DNS accounts and of the external account.
