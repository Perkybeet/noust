# The name does not point at the server

```bash
noust dns check wrenfield.io
```

Noust asks the public resolvers (1.1.1.1 and 8.8.8.8 unless you set others), as the certificate
authority will, not the server's own cache. It prints the verdict and both lists of addresses:

```text
  wrenfield.io: points somewhere else
  Resolves to: 198.51.100.4
  This server: 203.0.113.10
  Asked: 1.1.1.1
```

The new-application wizard and the **Domains** tab of the console show the same, and
`noust domain add` warns with both lists before it goes on.

## It points somewhere else

Change the `A` record of the name at your DNS provider to the server's address, and the `AAAA`
record to its IPv6 address. Then wait for the change to reach the resolvers, and run the check
again. The provider's TTL says how long the old answer may be kept.

**A stray `AAAA` record is the usual cause** when the `A` record is right: the name has an IPv6
address of another machine, and Let's Encrypt tries IPv6 first. Remove it, or point it here.

## It does not resolve

```text
  wrenfield.io: does not resolve
```

The record does not exist yet, or the domain's name servers are not the ones you edited. Check that
the domain is delegated to your DNS provider.

## The server cannot tell its own address

```text
  wrenfield.io: resolves, but this server cannot tell its own public address (it sits behind NAT)
Declare it to compare: noust config set dns.public_addresses ADDRESS
```

A cloud instance whose public address the provider maps, or a server at home or in an office, only
sees a private address. Tell it the public one, and the check compares again:

```bash
noust config set dns.public_addresses 203.0.113.10
```

## It is behind a CDN

```text
  wrenfield.io: is behind Cloudflare's proxy, which reaches this server
```

That is not a mismatch: the CDN answers for the name and reaches the server by itself. Noust knows
the networks of Cloudflare, Fastly, Bunny CDN and ArvanCloud. Let's Encrypt still has to reach port
80 through the CDN; when the CDN does not pass it on, prove the name with a DNS record instead
([Certificates](certificates.md)).

The check is advisory: certbot is the final judge. [The DNS check](domains.md#dns-check) explains
how it decides.
