# A certificate was not issued, or is about to expire

```bash
noust cert list                              # every certificate and when it expires
noust domain add wrenfield.io wrenfield.io   # try the application's certificate again
noust cert renew                             # renew the ones that are due
```

When Let's Encrypt refuses a certificate, nothing else fails. The name is kept, the site keeps
serving with the certificate it had (or over HTTP when it had none), and certbot's own output is
shown, verbatim: on the command line, and in the console under **What certbot said**. Its last
lines say why.

## Let's Encrypt could not reach the server

```text
Timeout during connect (likely firewall problem)
```

Let's Encrypt proves that you hold a name by fetching a file from port 80 of the server, even for a
site served only over HTTPS. Open port 80 to the internet in the firewall and, at a cloud provider,
in the security group. Another web server holding the port has the same effect.

## The name does not point here

```text
DNS problem: NXDOMAIN looking up A for wrenfield.io
```

or `no valid A records found`: the name does not resolve, or resolves to another machine. See
[The name does not point at the server](troubleshooting-dns.md).

## Too many attempts

Let's Encrypt limits how many certificates a registered domain gets each week and how many
validations may fail each hour; certbot's answer names the limit. Wait, and fix the cause before
trying again: do not retry in a loop. Noust never asks again for a certificate that already covers
the names.

A temporary address (`*.sslip.io`) shares its limit with everyone who uses sslip.io, so it can be
refused at a busy moment. The application then serves over HTTP; add your own domain when it
points here ([Put it on your domain](first-domain.md)).

## The site has no certificate at all

```text
SSL was requested but could not be obtained.
To add SSL later, run: noust cert create -d wrenfield.io
```

The first certificate failed when the application was created. Once the cause is fixed, run the
command it printed, with a `-d` for each name the application answers on.

## A certificate is about to expire

Certbot renews each certificate by itself once a third of its life is left (its last thirty days
before certbot 4). `noust health` lists the ones that are past that point and have not been renewed,
and says when the last renewal failed, with certbot's own words; a notification channel hears about
both ([Make it safe to leave alone](first-safety.md)). When renewal keeps failing, the cause is one of
the above: `noust cert renew -d wrenfield.io` shows certbot's words. A certificate that expired is
the probable cause `noust diagnose` names:

```text
The TLS certificate for wrenfield.io expired on 2026-10-12.
```

## A wildcard

A wildcard name (`*.wrenfield.io`) can only be proven with a DNS record, so the certificate needs a
DNS account Noust can write the record in: `noust cert use wrenfield.io --acme --dns ACCOUNT`.
[Certificates](certificates.md) covers DNS accounts, uploaded certificates and the server's own
certificate authority for private names.
