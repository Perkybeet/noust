# Noust DNS: a name server of your own

Part of [The network of an application and of the server](network.md).

[DNS accounts](network-dns.md) write records at a provider that answers for your domain. **Noust
DNS** is the other way: Noust installs a name server on your own servers, you edit the zones in
Noust, and the registrar of the domain points at your servers. It is an option and never the
default, and it is worth reading what it takes on before you set it up.

## What it takes on

- **Availability.** A name server is a service the whole internet depends on: if it is down, every
  name it serves is down. One server is a single point of failure, so Noust DNS is a **primary** and
  **secondaries** on servers in other networks. The delegation steps warn when a zone has only one
  name server, or two in the same network.
- **Amplification.** A name server on the internet can be used to flood other people with traffic.
  Noust DNS never recurses (a question about a name it does not serve is refused), answers each
  address at a limited rate (response rate limiting), and hands a zone transfer only to a server you
  named, with a key. It still answers on port 53 for anyone, so keep the server updated.

The same two sentences are shown by `noust dns server setup` before it does anything, and by the
console before it is set up.

## The program, and why

Noust installs [Knot DNS](https://www.knot-dns.cz) from the system's own packages (apt, dnf or
zypper; on RHEL and its rebuilds it comes from EPEL and Noust asks before enabling it). Secondaries
learn the zones through catalog zones, which Knot has from version 3.1, so Noust checks the version
after installing and says so when the system's package is older. It was chosen over PowerDNS for four
reasons, all of them checked against the real program (Knot 3.4 on Debian 13) by
`tests/integration/network_run.py --scenario authdns`:

- **The zones stay in Noust.** The editor writes Noust's own store and a zone file is rendered from
  it, so the name server holds no second database that has to be kept in step with the first.
- **DNSSEC is the program's.** Knot keeps the key, signs, signs again, publishes the CDS and CDNSKEY
  that a registrar can read, and prints the DS to give the registrar. A signed zone reaches a
  secondary as an ordinary transfer, so secondaries hold no key.
- **Secondaries learn the zones by themselves.** The primary lists its zones in a *catalog zone*
  (RFC 9432). A zone added or removed on the primary appears or goes on every secondary with nothing
  sent between servers and nothing from the central.
- **It never recurses**, and it has no management API or key to protect: its control socket is for
  root.

Noust writes the whole configuration (`/etc/knot/knot.conf`, marked on its first line) and a zone
file for each zone. A Knot that already serves zones of its own is **not** taken over: Noust refuses
and says which zones it found. The package's own sample configuration is kept as
`knot.conf.before-noust` and put back when Noust DNS is removed.

## One server

```bash
noust dns server status                          # whether it is installed, what it could answer on
noust dns server setup --role primary --hostname ns1.example.net
noust dns zone create example.com --glue ns1.example.net=203.0.113.10   # glue only for a name server inside the zone
noust dns zone record add example.com www A 203.0.113.7
noust dns zone record add example.com @ MX "10 mail.example.net"
noust dns zone show example.com
```

`setup` installs Knot, answers on the server's public addresses (name them with `--listen
ADDRESS`; a server behind NAT answers on its private address and the router forwards port 53),
opens port 53 over UDP and TCP in ufw or firewalld, and starts it. Port 53 on those addresses is
recorded as Noust DNS's, so a published stream or another service cannot take it. An error in any
step puts the earlier ones back.

## Several servers

A secondary needs the primary's address and the key the two sign their transfers with. From the
central, one action does it all, the primary first and alone:

```bash
curl -X POST https://central/api/fleet/dns/cluster -d '{
  "primary":     {"node": "web-1", "hostname": "ns1.example.net"},
  "secondaries": [{"node": "web-2", "hostname": "ns2.example.net"}]
}'
```

(or `POST /api/fleet/actions` with the action `dns_cluster`; add `"plan": true` to see the plan.)
Each server must run a Noust that has Noust DNS, and must have authorized this central to change
its host (`noust fleet authorize --allow-host-access` on the node): installing a program and
opening port 53 are host access, and a server that did not is left out of the plan with the
command that raises it. The secondary reads the primary's address and key, sets itself up and tells
the primary where it answers. The key crosses the central in the body of one request and is kept
nowhere: not in the job's record, not in its log.

By hand it is the same three steps:

```bash
primary$ noust dns server setup --role primary --hostname ns1.example.net
primary$ noust dns server key                       # prints the key; it is a secret
secondary$ printf %s "$KEY" | noust dns server setup --role secondary --hostname ns2.example.net \
             --primary 203.0.113.10 --key-stdin
primary$ noust dns server add-secondary 198.51.100.7 --hostname ns2.example.net
```

`GET /api/fleet/dns` shows every server's part in it. Once set up the servers keep each other in
step with DNS itself, so a primary that is down for a while does not stop the secondaries from
answering with what they have, and the central is not in the path.

## Zones and records

A zone is made on the primary and every secondary copies it. Records are `A`, `AAAA`, `CNAME`,
`MX`, `NS` (to delegate a name below the zone), `TXT`, `SRV`, `CAA`, `PTR` and `DS`. A name is
relative to the zone (`www`, `@` for the zone itself, `*.apps`); the serial is the server's.

What a person writes is read into the fields of its type and the zone file is written from the
fields, never from the text, so nothing in a value can end its line or add a directive. A change
the name server does not take (it checks a zone before it serves it and keeps serving the previous
one) leaves the store, the file and the audit trail as they were, and the error carries Knot's own
words. A few rules are Noust's, at the one door every caller uses: a `CNAME` stands alone at its
name and is never the zone's own name; the zone's name servers are the zone's own
(`noust dns zone set ZONE --nameserver NAME`); a name server inside the zone it serves must have an
address (`--glue NAME=ADDRESS`), which the zone repeats.

## DNSSEC

```bash
noust dns zone dnssec example.com --enable       # signs the zone and prints the DS to give the registrar
noust dns zone dnssec example.com                # shows it again
noust dns zone dnssec example.com --ds-published # tells the server the registrar has it
```

One ECDSA P-256 key signs the zone, so there is one DS to give the registrar (the SHA-256 one, and
a SHA-384 one for registrars that ask for it). The key stays when signing is turned off and on
again, and **stays when Noust DNS is removed**: it is the only copy of what the DS points at. The
key is not rolled over by itself; that is a decision about the registrar.

**Turning signing off is refused** unless you say the DS is already gone from the registrar
(`--ds-removed`): while the parent zone holds a DS for a zone that is not signed, every validating
resolver refuses the whole domain.

## Delegating the domain

A zone answers nothing to the internet until the domain's registrar says so, and that is the one
part nobody can do for you. Noust says exactly what to do, in the order that works:

```bash
noust dns zone delegation example.com
noust dns zone check example.com
```

1. **Glue**, when a name server's name is inside the domain it serves (`ns1.example.com` for
   `example.com`): the registrar must hold its address (some call them host records or child name
   servers), or nothing can find it. A name server in another domain (`ns1.example.net`) needs none.
2. **The name servers**: set the domain's name servers at the registrar to the zone's. It reaches
   resolvers after the registrar's TTL, often a few hours and at most two days.
3. **The DS record**, for a signed zone, and only after step 2 has reached the resolvers: a DS for a
   zone that the old name servers still serve unsigned makes validating resolvers refuse the domain.

`check` looks at the world: it asks each name server directly (does it answer as the authority,
with the same serial as the others, and does it refuse a question about another domain) and the
public resolvers (which name servers and which DS they find), and says which step is missing, with
the fix. It exits 1 while something is. A DS left at the registrar for a zone that is not signed is
called out as the worst state.

## Taking it away

```bash
noust dns server remove                  # refused while the server has zones
noust dns server remove --delete-zones   # delegate the domains elsewhere first
```

Removing stops the name server, puts the previous Knot configuration back, closes the firewall
rules Noust made (and only those) and forgets the key. The Knot package and the signing keys stay.

## Where each thing is

| What | Where |
|---|---|
| Configuration | `/etc/knot/knot.conf` (Noust's, marked), `knot.conf.before-noust` |
| Zone files | `/var/lib/knot/zones/ZONE.zone` (Noust's output; edit the zone with `noust dns zone`) |
| Signing keys, journal | `/var/lib/knot/` (the keys are never removed by Noust) |
| Transfer key | the secret store, `authdns/transfer-key`, and the configuration (0640, root and knot) |
| Zones and records | the store: `authdns_zones`, `authdns_records`, `authdns_secondaries` |

## The API

`/api/dns/noust` on each server (through the central: `/api/nodes/NODE/api/dns/noust`):
`GET server`, `POST server/install` and `POST server` (jobs), `DELETE server`, `GET server/key`,
`POST server/secondaries`, `GET|POST zones`, `GET|PATCH|DELETE zones/{zone}`,
`POST zones/{zone}/records`, `PATCH|DELETE zones/{zone}/records/{id}`, `GET|PUT zones/{zone}/dnssec`,
`POST zones/{zone}/dnssec/ds-published`, `GET zones/{zone}/delegation` and
`GET zones/{zone}/delegation/check`. Installing, setting up, removing and reading the key are host
access and ask for sudo mode; the rest is managed with the server. See the
[API reference](reference-api.md).
