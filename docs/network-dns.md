# DNS accounts, private networks, tunnels and ports

Part of [The network of an application and of the server](network.md).

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

## A private network: Tailscale, WireGuard

An application can answer a private network only: the devices of a tailnet or of a WireGuard
interface, and nobody else. It is not a new kind of site. It is the site's list of allowed
addresses ([Access](network.md#access-a-password-addresses-limits-and-headers)) set to the network's ranges
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
([Upkeep](network.md#upkeep)). Putting a fleet's own traffic on the mesh is a separate question and is not
done here.

For a name Tailscale does not issue for (`wiki.home.arpa`, a name on a VPN or a home network), the
server can sign its own certificates: [an authority of your own](certificates.md#an-authority-of-your-own-for-private-names).

API: `GET /api/private-networks`; `GET|PUT|DELETE /api/apps/{domain}/private` (changing it is the
policy's permission) and `POST /api/apps/{domain}/private/certificate` (sudo mode, server scope:
the certificate joins the server's own). Console: **Application, Settings, Access, Private network**.

## Cloudflare Tunnel

For a server with no public address (a NAS, a machine at home, behind CGNAT) or no port to open:
the connector (`cloudflared`) dials **out** to Cloudflare, which carries the names routed to it
back through that connection.

```bash
noust tunnel check                                  # is cloudflared installed? is there an account?
noust tunnel install-cloudflared                    # add Cloudflare's repository and install it
noust tunnel create home --account cf
noust tunnel route add home shop.example.com --app shop.example.com
noust tunnel route add home grafana.example.com --service http://localhost:3001
noust tunnel list | route remove home shop.example.com | remove home | repair
```

- `cloudflared` comes from Cloudflare's own package repository (<https://pkg.cloudflare.com/>),
  and from nowhere else: `noust tunnel install-cloudflared` (since 4.1) adds that repository the
  way Noust adds a database engine's, with the signing key **pinned**. The key is downloaded into
  a private directory, read with a throwaway gpg home and refused unless it is exactly one public
  primary key with Cloudflare's fingerprint (`CC94B39C77AE7342A68B89628A682D308D4E5E73`); only
  then is the source line (apt) or the repository file (dnf, zypper) written, naming that key
  alone. The package manager's lists are refreshed, a repository it cannot read is taken back
  out, and the package is installed through apt, dnf or zypper. A repository you already added
  yourself (Cloudflare's own instructions) is used as it is, and a machine whose packages Noust
  does not manage (an image-based system) gets the manual steps instead. It is never a script
  piped to a shell and never `latest` from a download.
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

API: `GET|POST /api/tunnels`, `POST /api/tunnels/install-cloudflared` (a job; needs sudo mode),
`DELETE /api/tunnels/{name}`, `POST /api/tunnels/{name}/routes`,
`DELETE /api/tunnels/{name}/routes/{hostname}`. Console: **Domains, Tunnels**.

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
**Domains, Ports**. Publishing and closing need sudo mode.
