# Deploy your first application

**Goal:** an application from a Git repository, running on your server and answering over HTTPS.

**Time:** about five minutes, most of it the build.

**You need:** [Noust installed](install.md), and a repository of
[a stack Noust deploys](frameworks.md). To try Noust before you use your own code,
[`examples/`](../examples/README.md) has a minimal application for every type.

The examples on this page are Kelmoor's, a small web agency: its client Wrenfield has a Next.js
site in `github.com/kelmoor-labs/wrenfield-site`, and the server's address is `203.0.113.10`.
Replace them with your own.

## Before you start

**Choose the address the application will answer on.** It becomes the application's name: its
unit, its directory and every command use it, and it cannot be changed later.

- **Your own domain**, such as `wrenfield.io`. Create an `A` record (and an `AAAA` record if the
  server has IPv6) pointing at the server, at your DNS provider, before you deploy: the certificate
  is only issued for a name that points here.
- **A temporary address**, if no name points here yet. Noust makes one from the server's address,
  such as `wrenfield-site.203-0-113-10.sslip.io`, which resolves at once. You add your own domain
  to the same application later ([Put it on your domain](first-domain.md)).

**A private repository needs a deploy key.** `noust setup ssh --generate --show` prints this
server's public key: add it to the repository as a read-only deploy key, and use the SSH address
(`git@github.com:kelmoor-labs/wrenfield-site.git`).

## Steps

1. **Check that the server is ready.**

   ```bash
   noust setup doctor
   ```

   Every line should be green. A line that is not says what is missing and the command that fixes
   it. In the console, the same list is under **Settings > About Noust > Installation check**.

2. **Deploy it.** On your own domain:

   ```bash
   noust create -d wrenfield.io -s https://github.com/kelmoor-labs/wrenfield-site.git
   ```

   Or on a temporary address:

   ```bash
   noust create --temporary -s https://github.com/kelmoor-labs/wrenfield-site.git
   ```

   Noust detects the stack (here Next.js with npm), fetches the code into a new release, installs
   the dependencies and builds it, writes a systemd unit and an nginx site, asks Let's Encrypt for a
   certificate and starts the application. It keeps the release only if the application answers
   its health check.

   In the console, the same is **Applications > New application**: paste the repository's address,
   and the wizard says what it found before anything is created ("Detected: Next.js with npm.
   Port 3000."). Choose the address, fill in any variable it asks for, and press **Deploy**. The
   build log streams until the application answers.

3. **Check that it runs.**

   ```bash
   noust status wrenfield.io
   ```

   Then open the address in a browser.

4. **Read what it prints.**

   ```bash
   noust logs wrenfield.io --follow
   ```

   This is the application's own log, the systemd journal of its unit. Press Ctrl+C to stop.

## What you should see

`noust create` ends like this:

```text
Application deployed successfully!

  URL: https://wrenfield.io
  Service: wrenfield-io
  Port: 3000
  App Path: /var/www/apps/wrenfield-io
  Server IP: 203.0.113.10
```

The application lives in `/var/www/apps/wrenfield-io/`. Each deploy is a folder under `releases/`,
and `current` points at the one that serves. Its unit is `wrenfield-io.service`, and its nginx site
is a file named `wrenfield.io` under `/etc/nginx`. They are ordinary files you can read.

When the repository has an `.env.example`, Noust writes the application's `.env` from it, with
random values for the secrets.

## If it fails

A deploy that fails changes nothing that was serving. The error names the step that failed, and
when the application did not answer its health check, it carries the probes and the last lines of
the application's log, verbatim.

- **Find the cause:** `noust diagnose wrenfield.io` checks the unit, the port, the application's
  answer, nginx and the certificate, and names the most likely cause first.
- **The build failed:** [A build or a deploy failed](troubleshooting-build.md).
- **The site answers 502:** [The site answers 502](troubleshooting-502.md).
- **There is no certificate** (`SSL was requested but could not be obtained.`): the name does not
  point here yet, or port 80 is closed. The application serves over HTTP meanwhile.
  [A certificate was not issued](troubleshooting-certificate.md).

## Next

[Put it on your domain, with HTTPS](first-domain.md), or, if it is already there,
[give it a database](first-database.md).
