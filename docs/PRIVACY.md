# Privacy

Noust does not tell anyone anything about your server. This page says exactly what that means, which
requests Noust does make and why, and how you can check each claim yourself instead of taking it on
trust.

## The promise

- **No telemetry.** No usage statistics, no counters, no feature flags fetched from a service, no
  licence check, no identifier of the installation or of you. The project runs no server that an
  installation reports to, so there is nothing to opt out of.
- **No error reporting to a third party.** A crash, a failed deploy or an exception is written to
  your journal and your audit trail and goes nowhere else. `noust support report` builds a report on
  your server, masks secrets, and prints it to you; it sends nothing. Sending it is your decision.
- **No third-party code in the console.** The browser console loads its scripts, styles and fonts
  from your own server (`default-src 'self'`; connect, image and font sources are `'self'` too, see
  [the browser](security.md#the-browser)). It embeds no analytics, no tag manager, no CDN.
- **No dependency whose purpose is to report.** A Sentry, PostHog, Segment, OpenTelemetry or
  similar library is not part of Noust and the build refuses one.

## The one request Noust makes without being told where

The update check. It is how the CLI's banner, `noust health`, `GET /api/system/version`, Settings >
About and the fleet's "target version" know that a newer release exists.

| It reads | From | Because |
|---|---|---|
| The newest version this installation can install | The package repository it upgrades from (apt, dnf or zypper repository on `download.opensuse.org`, or `pypi.org` for pip) | An update is only announced once your package manager can install it |
| The newest published release, and recent releases to spot a security one | `api.github.com` (`/repos/Perkybeet/noust/releases`) | To tell "on the way" from "not released" |
| The release notes of a version, before you update to it | The same GitHub release | Settings > About shows them before you decide |

What leaves your server: the request itself. The destination sees your server's IP address, as any
request shows it, and a `User-Agent` of `noust-update-check`. There is no version, no host name, no
cookie and no identifier in it, and the answer is cached for five minutes.

**Turn it off** with `noust config set updates.check false`, or `updates.check: false` in the
configuration file. Off means the request is never made: no thread, no cache read, no banner. The
version endpoint then answers `disabled`, and the console does not claim to be up to date.

## Requests you ask for

Everything else Noust sends goes to an address you configured or typed, for the purpose you
configured it for. Each carries `User-Agent: noust/<version>` (the version of the program, no more),
except where the other end is yours and needs nothing.

| Kind | Where it goes | When |
|---|---|---|
| Notifications | The webhook, Slack, Discord, Teams, ntfy, Telegram, Pushover, Gotify or email server of a channel you added | An event you routed to that channel happens |
| Log shipping, audit shipping | The syslog or HTTP destination you configured | Continuously, once you configure one |
| Backup destinations | The destination you configured, which `rclone` reaches (S3-compatible storage, B2, SFTP, SMB, WebDAV and the rest of its backends) | When a backup runs or you test the destination |
| Git and registries | The repositories and registries your applications deploy from, as the account you connected | When you deploy, update or browse a repository |
| DNS and certificates | The DNS provider's API with your token (Cloudflare, DigitalOcean, Hetzner, Gandi or Route 53), the DNS resolvers you check against, Let's Encrypt (through certbot) | When you add a domain or a certificate renews |
| Secret managers | The Vault, Infisical or Doppler server you registered | When an application using its secrets deploys |
| Single sign-on | The identity provider you set up | When someone signs in through it |
| Cloud providers | The Hetzner, DigitalOcean or Vultr API with the token you gave | When you provision or list servers |
| Import | The Coolify or Dokploy server you named, with the read-only token you typed once | When you import |
| Cloudflare's address ranges | `www.cloudflare.com` | Only when you press refresh on the trusted proxies |
| A remote console | The Noust server named by `--context` | When you run a command against it |
| Your own applications | This machine, to check a port or a health path | Every deploy; the monitor's reachability scan |
| Your fleet | Each node, through an SSH tunnel that ends on that node's loopback | While a central manages nodes |

Programs that Noust runs for you keep their own policies, and Noust gives them nothing to identify
you with: `git` talks to your Git host, `certbot` to Let's Encrypt (which receives the contact email
you gave it, as its terms require), `apt`/`dnf` to your distribution's mirrors, `npm`, `pip`, `bun` or
`docker` to the registries your application's dependencies come from. Those are the work you asked
for, not reports about it.

## How to check it

You do not need to believe this page.

1. **Read the test that holds it.** [`tests/test_privacy.py`](../tests/test_privacy.py) is the
   inventory: every module of Noust that can open an outbound connection, the address it talks to,
   and why. It fails when someone adds a telemetry or error-reporting library to the code or to the
   package's dependencies, when a module starts opening connections without being declared, when a
   new place reads the release information without being gated by `updates.check`, and when the
   update check sends anything but its fixed `User-Agent`. A change that adds a way out has to say so
   in that file, where a reviewer reads it.
2. **Block the network.** Noust works with outbound traffic denied except what you use. With
   `updates.check false` and a firewall that only allows your Git host, your DNS and Let's Encrypt, the
   only thing that stops is the update banner.
3. **Watch it.** `ss -tnp` and your firewall's logs show which connections a `noust` or
   `noust-web` process opens; the table above is the complete list of what you will see.
4. **Read the code.** Noust is [AGPL-3.0](../LICENSE) and `src/noust` is the whole program.

## What stays on your server

Your configuration, secrets, audit trail, application files, metrics history and backups live under
`/etc/noust`, `/var/lib/noust`, `/var/backups/noust` and `/var/log/noust`. Secrets are written
`0600`, redacted when printed, and never put on a command line. A central that manages other servers
keeps its secrets sealed with a passphrase it never stores (see [CENTRAL.md](CENTRAL.md)).
Removing the package leaves your applications running and removes nothing of yours; purging it on
Debian or Ubuntu deletes the configuration and the audit trail under `/etc/noust` and the logs in
`/var/log/noust`, and nothing else ([concepts.md](concepts.md#taking-noust-away)).

## Changing this page

This is a promise about the program, so a change to what leaves a server is a change to this page and
to `tests/test_privacy.py`, in the same commit. If you find a request that is not described here,
that is a bug: [report it](SUPPORT.md).
