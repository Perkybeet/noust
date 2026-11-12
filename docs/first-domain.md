# Put it on your domain, with HTTPS

**Goal:** your application answering on your own domain, `www` sending visitors to it, and a
certificate that renews itself.

**Time:** two minutes, plus however long your DNS provider takes to publish a record.

**You need:** [a deployed application](first-application.md), a domain you control, and ports 80
and 443 of the server open to the internet.

The examples use Kelmoor's client Wrenfield: the domain `wrenfield.io` and the server
`203.0.113.10`.

## Before you start

**Which name to give the commands.** An application keeps the name it was created with. If you
created it on `wrenfield.io`, that is the name; if you created it on a temporary address, it is
that address (`wrenfield-site.203-0-113-10.sslip.io`), and your domain is added to it. `noust list`
shows every application's name.

**Port 80 must be open, even for a site served only over HTTPS.** Let's Encrypt proves that you
hold the name by asking for a file on port 80 of the server. Check the firewall and, at a cloud
provider, the security group.

## Steps

1. **Point the name at the server.** At your DNS provider, create an `A` record for `wrenfield.io`
   with the server's address, `203.0.113.10`. If the server has an IPv6 address, add an `AAAA`
   record with it too; if it has none, make sure there is no `AAAA` record for the name, because
   Let's Encrypt tries IPv6 first.

2. **Check that it points here.**

   ```bash
   noust dns check wrenfield.io
   ```

   Noust asks the public resolvers, as Let's Encrypt will, rather than the server's own cache.
   Wait until it says `points at this server`.

3. **Add the name to the application.** The first argument is the application's name, the second
   the name to add:

   ```bash
   noust domain add wrenfield-site.203-0-113-10.sslip.io wrenfield.io
   ```

   Noust adds the name to the site, tests the configuration before nginx reloads it, and extends
   the certificate to cover it. In the console: the application's **Domains** tab, **Add domain**.

   If you created the application on `wrenfield.io`, the name is already its own. Run
   `noust domain add wrenfield.io wrenfield.io` only if its certificate was refused at the first
   deploy: adding a name the application already has asks for the certificate again.

4. **Send `www` to it.** Create the `A` record for `www.wrenfield.io` first, as in step 1, then,
   with the application's name first as in step 3:

   ```bash
   noust domain add wrenfield.io www.wrenfield.io --kind redirect
   ```

   A redirect answers with a permanent redirect to the primary name, keeping the path.

5. **Check the certificate.**

   ```bash
   noust domain list wrenfield.io
   noust cert list
   ```

   Then open `https://wrenfield.io`.

## What you should see

`noust dns check wrenfield.io` once the record is published:

```text
  wrenfield.io: points at this server
  Resolves to: 203.0.113.10
  This server: 203.0.113.10
  Asked: 1.1.1.1
```

`noust domain list` shows the primary name, then the aliases and redirects. `noust cert list`
shows one certificate covering every name, with its expiry date. Certbot renews it on its own once
a third of its life is left; `noust health` warns you if one is past that point and has not been
renewed.

When the application was created on a temporary address, the console shows your domain as its
address from now on. The temporary name keeps working: it is still the application's name, the one
its unit and its directory are called after.

## If it fails

The name is kept even when the certificate fails, and the site keeps serving with the certificate
it had. Run the same `noust domain add` again once the cause is fixed: adding a name the
application already has retries its certificate.

- **`points somewhere else`** or **`does not resolve`**: the record is wrong or not published yet.
  The command shows both lists of addresses side by side.
- **`resolves, but this server cannot tell its own public address (it sits behind NAT)`**: the
  server only sees a private address. Tell it the public one:
  `noust config set dns.public_addresses 203.0.113.10`.
- **Certbot says `Timeout during connect`**: Let's Encrypt could not reach port 80. Open it.
- **Certbot's answer names a rate limit**: Let's Encrypt limits how many certificates a domain
  gets each week, and how many validations may fail each hour. Wait; do not retry in a loop.
- **The site answers only over HTTP**: the application has no certificate at all yet (the one for
  the temporary address was refused, or it was created with `--no-ssl`). Ask for one that covers
  every name:
  `noust cert create -d wrenfield-site.203-0-113-10.sslip.io -d wrenfield.io -d www.wrenfield.io`.

[A certificate was not issued](troubleshooting-certificate.md) has the rest, and
[Domains](domains.md) covers aliases, wildcards, a certificate per name and the DNS check in
depth.

## Next

[Give it a database](first-database.md).
