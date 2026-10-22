# Troubleshooting

One section per symptom. Each starts with the command that finds the cause, because Noust has
one: `noust diagnose` correlates the unit, the port, the application's own answer, nginx's answer,
the journal, nginx's error log, the certificate, the last deployment, out-of-memory kills and the
disk, and names the most likely cause first. Its output is the system's own words, never a
paraphrase: paste it in a report if you need help ([support.md](SUPPORT.md)).

- [The site answers 502, or does not answer](#the-site-answers-502-or-does-not-answer)
- [A certificate was not issued, or is about to expire](#a-certificate-was-not-issued-or-is-about-to-expire)
- [The name does not point at the server](#the-name-does-not-point-at-the-server)
- [A build or a deploy failed](#a-build-or-a-deploy-failed)
- [I cannot sign in to the console](#i-cannot-sign-in-to-the-console)
- [The console does not open](#the-console-does-not-open)
- [The installation did not work](#the-installation-did-not-work)
- [An update says it is on the way, or the package index is behind](#an-update-says-it-is-on-the-way-or-the-package-index-is-behind)
- [Still stuck](#still-stuck)

## The site answers 502, or does not answer

```bash
noust diagnose shop.example.com
```

In the console, the application's **Diagnose** page shows the same, with the evidence of each check
under it. The verdict is `healthy`, `degraded` or `down`, and the line in bold above the checks is
the probable cause. These are the causes it names, and what to do:

| It says | What it means | What to do |
|---|---|---|
| No systemd unit found for the application | It was never started, or its unit was removed | `noust start DOMAIN`; if the unit is gone, redeploy: `noust update DOMAIN` |
| Killed by the kernel for running out of memory | The application, or the machine, ran out of memory | Give the machine room (`noust server swap create`), or limit what the application takes (`noust app limits DOMAIN`); the build is the usual culprit on a small server |
| The process exits with status N at start | The application starts and stops; the last journal lines are below the verdict | Read them: a missing environment variable, a missing script (`Missing script: start`), a port already taken. Fix it and update, or go back: `noust releases rollback DOMAIN` |
| The unit failed to start (Result=..., exit status ...) | systemd could not run the command | The journal lines below say why; a `start` of your own that is wrong is corrected with `noust app build set DOMAIN --start '...'` ([build.md](build.md)) |
| The process is running but not listening on port N | The application listens somewhere other than where Noust routes | Make it listen on the `PORT` in its environment, or redeploy with `--port` (do not edit the unit's `PORT` in `.env`: the unit's own wins) |
| Listening on X, Noust routes to Y | The same, and Noust saw where it does listen | Redeploy with `--port X` or change the application to listen on Y |
| The app answers directly, but nginx returns 502 / cannot connect | The application is fine and nginx cannot reach it | `nginx -t` and `noust site show DOMAIN`; a hand-edited site (an operator's site) is not rewritten by Noust, so check its `proxy_pass` ([domains.md](domains.md#operator-sites)) |
| nginx proxies to port A but the blue instance listens on B | Zero-downtime mode: the site points at the other instance | `noust update DOMAIN` writes the site again and switches |
| The TLS certificate expired on DATE | The site answers, the browser refuses | See [certificates](#a-certificate-was-not-issued-or-is-about-to-expire) |
| The disk holding the applications is N% full | Nothing can be written | `noust server storage` shows what takes it; `noust server cleanup` removes what Noust can prove is unused |
| A stack's containers are not healthy (Docker Compose) | The worker or the stack's service is down | The containers' state and their last output are the evidence below; [compose.md](compose.md) |

If the verdict is `healthy` and the browser still fails, the problem is in front of the server: DNS
([below](#the-name-does-not-point-at-the-server)), a firewall, or a CDN.

Going back is always one command and takes seconds: `noust releases rollback DOMAIN`
([releases.md](releases.md)). A deploy that does not pass the health gate never replaces what
served before, so a 502 right after an update usually means the failure is older than the update.

## A certificate was not issued, or is about to expire

```bash
noust cert list                       # which certificates exist and when they expire
noust cert renew                      # ask for the ones that are due
noust domain add APP NAME             # again, once DNS is right: it retries the certificate
```

- **Certbot's own output is shown, verbatim.** Read its last lines: "Timeout during connect"
  means the Let's Encrypt validation could not reach port 80 of this server (a firewall, a
  security group, another web server on the port); "DNS problem: NXDOMAIN" or "no valid A
  records" means the name does not point here ([below](#the-name-does-not-point-at-the-server)).
- **Port 80 must be open from the internet**, even for a site served only over HTTPS, because that
  is how the certificate authority checks the name.
- **Let's Encrypt limits issuance** (certificates per domain per week, failed validations per
  hour). After repeated failures, wait; do not hammer. A certificate that already covers the
  names asked for is left alone.
- A domain that could not get its certificate is kept, and the site keeps serving the one it had;
  adding the same name again retries ([domains.md](domains.md#certificates)).
- The server's health check (`noust health`) lists the certificates close to expiry. A certificate
  that expired shows in `noust diagnose` as the probable cause.
- An application without TLS gets its first certificate with `noust cert create -d NAME`.

## The name does not point at the server

Noust checks before it asks for a certificate: the new-application wizard and the Domains tab show
this server's addresses next to the ones the name resolves to, and `noust domain add` prints a
warning with both lists. Fix the A (and AAAA) record at your DNS provider, wait for it to
propagate, and run the command again. A stray AAAA record (IPv6) to another machine is a common
cause: Let's Encrypt prefers IPv6 when there is one.

The check can be wrong in two known cases, and says so: behind NAT (cloud instances whose public
address is mapped by the provider: the machine only sees its private one) and behind a proxying CDN
such as Cloudflare with the proxy enabled. There it is advisory, and certbot is the final judge
([domains.md](domains.md#dns-check)).

## A build or a deploy failed

A build that fails changes nothing: the release that was serving keeps serving, and the
deployment is recorded as failed with the output. Where to read it:

- In the console, the application's **Deployments** tab: open the deployment for its build log.
- `noust logs DOMAIN` is the application's journal (what it printed once running), not the build.
- The error of a failed health gate carries the probes and the journal verbatim: it says whether
  the application did not start, did not listen, or answered with an error.

The usual causes, in the order they are met:

1. **The wrong Node, Python or PHP version.** Pin it: `runtime: node@22` in the `build:` block of
   `noust.yaml`, or a `.nvmrc`, `.python-version` or `require.php` in `composer.json`
   ([build.md](build.md#runtimes)).
2. **A variable the build needs is missing.** Add it on the application's **Environment** page (or
   with `noust env configure DOMAIN`) and update.
3. **The build ran out of memory.** On a small server, add swap: `noust server swap create`.
4. **The build is not run in a shell.** A command with `&&`, a pipe or `$(...)` is passed as arguments, not
   to a shell. Commit a script and run it (`build: ./scripts/build.sh`) ([build.md](build.md#the-build-block)).
5. **The sandbox.** Builds run as an unprivileged account in a sandbox (`noust app sandbox status
   DOMAIN`). `noust app sandbox test DOMAIN` builds what the next update would build, in the
   sandbox, without activating it, so a failure can be reproduced without touching the live
   application.
6. **A migration failed.** A failing `pre_deploy` hook stops the deploy before anything serves the
   new version, and its output is the error ([compose.md](compose.md#what-a-hook-failing-means)).
7. **The framework is not one a server can run.** SvelteKit with `adapter-auto` is refused with
   the way out ([frameworks.md](frameworks.md#sveltekit)).

## I cannot sign in to the console

- **A wrong code or password.** Five failed attempts lock the account for 15 minutes (and five
  from one address lock that address). `noust user unlock USERNAME` lifts it now. The page counts
  down.
- **The code is refused although it is right.** An authenticator depends on the clock of both
  sides: check the server's (`noust server time status`) and your phone's.
- **You lost the authenticator.** `noust user reset-mfa USERNAME`: the account enrols a new one at
  its next sign-in. Use a backup code if you saved them.
- **No account can sign in.** The access token still does: `noust web token` shows its state and
  `noust web token --new` issues another (the old one stops working). Each use of it is recorded.
- **The passkey button is missing.** Browsers offer passkeys only on a certificate they trust, so
  not over an SSH tunnel or with a self-signed certificate. Use the authenticator app there.
- **You are signed out all the time.** A session ends after 30 minutes without activity and 12 hours
  after sign-in (`auth.session.*` in [reference-config.md](reference-config.md)).

## The console does not open

The console listens on `127.0.0.1:8080` of the server and, on purpose, nowhere else. From your
computer, open a tunnel and browse to it:

```bash
ssh -N -L 8080:127.0.0.1:8080 root@server.example.com
# then open http://127.0.0.1:8080/login?with=token
```

The installer prints exactly this, with the server's own address in it, and the link opens the
sign-in on the access token. When it could not tell the address (a machine behind NAT, or one whose
only address is private) it says so and leaves `<your server's public address>` to replace with the
one you SSH to.

If that is refused, on the server: `noust web status` (is it running), `journalctl -u noust-web`
(why not) and `noust web enable` (set it up as a service). To reach it without a tunnel, publish it
on a name of your own over TLS: `noust console expose --domain console.example.com --allow-ip ...`
([console.md](console.md#reaching-it)); it refuses while any account can sign in without a second
factor.

## The installation did not work

```bash
noust setup doctor
```

lists what is missing and the command that fixes each item; the console shows the same under
**Settings > About Noust > Installation check**. Then:

- **The installer refused the platform (exit status 3).** The distribution or the processor is not
  on the list ([platforms.md](PLATFORMS.md)). `--force --target <repository directory>` tries
  anyway, on your own account.
- **"a different key stops it before anything is written."** The repository key the installer found
  does not have the fingerprint it pins: do not go on. [platforms.md](PLATFORMS.md#when-the-repository-key-is-rotated)
  says how to tell a rotated key from a wrong one.
- **`noust: command not found` after installing with pip.** The installer links
  `/opt/noust/bin/noust` into `/usr/local/bin`; a pip install of your own is on the `PATH` of the
  user that ran it.
- **The console did not start.** `noust web install`, then `noust web enable`.
- **No package for the version yet.** The packages are built by the openSUSE Build Service after a
  release, one repository per distribution, which takes 15 to 30 minutes
  ([below](#an-update-says-it-is-on-the-way-or-the-package-index-is-behind)).
- **To see what the installer would do without doing it:** `sh install.sh --dry-run`.

## An update says it is on the way, or the package index is behind

A release exists on GitHub the moment it is tagged, but the packages are built afterwards, one
repository per distribution, so for a while some distributions serve the new version and others
the old one. Noust says so instead of telling you to upgrade to something that does nothing:

- **Being published / on the way:** the package for this server is not available yet. Nothing to
  do; try again later. The fleet's Updates view shows the target version and how many servers are
  there.
- **Package index behind:** the repository has the version but this server's index has not seen it.
  `noust server updates refresh` refreshes it (the update does it for you).
- **The update stopped before installing anything.** Noust rehearses the update with the package
  manager first, and checks the room on disk and the store; the refusal carries their words. Fix
  what it names and update again. To go back to an earlier release, see [downgrading.md](downgrading.md).

## Still stuck

Run `noust support report` and open an issue with its output ([support.md](SUPPORT.md)). It holds
what is needed to see the problem, with the secrets masked.
