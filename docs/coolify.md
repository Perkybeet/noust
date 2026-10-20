# From Coolify to Noust

For someone who runs Coolify and wants to know what Noust does differently, what each Coolify
screen and setting is called here, what is not done on purpose, and how to move. It is written from
Coolify's own code and documentation (v4.x) and from Noust's; where something here is a choice of
design, it says so.

## The difference that explains the rest

Coolify runs **everything in Docker**: each application is built into an image and runs as a
container behind a proxy container (Traefik or Caddy) that Coolify manages. Noust runs
applications **as ordinary processes under systemd**, behind the nginx or Apache the server already
has, with a certificate from Let's Encrypt through certbot. Docker is only there when a project is
a Docker Compose project, a Dockerfile or an image (then Noust runs it as a unit too).

What follows from that:

- **No Docker network, no proxy container, no Swarm.** The web server is the proxy; its site files
  are plain text you can read, and Noust never rewrites one it did not write.
- **Every deploy is a release on disk** (`releases/<id>/`, a `current` link, a `shared/` folder
  for `.env` and persistent paths), switched only if the new version passes a health gate. Rollback
  is flipping the link back: seconds, with no rebuild. Coolify keeps images and redeploys a commit.
- **A server is managed by the Noust on it.** A central drives its servers through an SSH tunnel
  and each server's own API; nothing SSHes into a server to run shell strings
  ([CENTRAL.md](CENTRAL.md)).
- **Two interfaces to the same thing**: the console and the `noust` command, both clients of the
  same code. `--dry-run` rehearses any command; `--json` makes it scriptable.

## Concepts

| Coolify | Noust | Notes |
|---|---|---|
| Team | Team | Accounts belong to teams; a team sees only its projects ([teams.md](teams.md)). |
| Project and Environment | Project and Environment | The same grouping, optional: a server nobody organised shows the sidebar it always had. |
| Resource (an application, a database or a service) | Application, Database, Recipe | An application is a thing Noust deploys; a database is on an engine; a recipe is a ready-made application from the catalog. |
| Application (Nixpacks, Railpack, Dockerfile, Docker Compose, Docker image, Static) | An application of a type: Next.js, Nuxt, SvelteKit, Astro, Remix, Angular, Vite, Node.js, Python, PHP, static; Docker Compose; container (Dockerfile, registry image or Railpack) | Native types need no build file; see [frameworks.md](frameworks.md). Nixpacks has no counterpart: Railpack is its successor and is supported for the container type. |
| Build pack settings (install, build, start commands, base directory, publish directory) | The `build:` block of `noust.yaml`, or `noust app build set` | [build.md](build.md). Commands are argument vectors, never given to a shell. |
| One-click Service | Recipe (53 applications) | [recipes.md](recipes.md). A service Noust does not have is deployed as a Docker Compose project. |
| Server | Server; several are a fleet | Each server runs its own Noust; a central drives them. |
| Destination (a Docker network) | Not applicable | Applications are processes on the server's loopback address; there is nothing to choose. |
| Proxy (Traefik or Caddy) | nginx or Apache | Per application: `--webserver apache`. Sites are yours to edit ([domains.md](domains.md#operator-sites)). |
| Private key | Deploy key, one per application | `noust app deploy-key generate`; it can be rotated, and assigned to an application. |
| Source (GitHub App, GitLab App) | The GitHub App, and webhooks for GitHub, GitLab, Gitea and Bitbucket | Settings > Integrations. |
| S3 Storage | A backup destination (rclone: S3 and compatibles, SFTP, SMB, WebDAV, Google Drive, OneDrive, Dropbox, B2, pCloud) | `noust backup destination add`; optional encryption per destination. |
| Scheduled task | A cron job: a systemd timer | `noust cron create`; runs with its history. |
| Webhook (deploy on push) | The deploy webhook | `noust app webhook show`; signed, with a log of every delivery. |
| Preview deployments | Previews | `noust preview enable`; one per pull request. |
| Health check | The health gate | Runs on every activation and also on a rollback; configurable per application (`noust app health`). |
| Resource limits | Resource limits | `noust app limits`; systemd resource control. |
| API token (Root, Write, Deploy, Read, Read sensitive) | A token with a scope, and roles | Settings > Tokens; roles are `viewer`, `operator`, `admin`, `security`, `auditor`. |
| Tags | App tags | The labels that group applications, set under an application's **Settings > General**. The Applications list narrows to one, and **Update tagged apps** updates every application that carries it ([console.md](console.md)). Projects and environments group resources too. |
| Shared variables | Shared variables and references | A variable of a team, a project, an environment or the server, used as `{{project.NAME}}` in an application's own variables; **Settings > Variables** holds the server's, a team's **Variables** tab and a project's page the others ([variables.md](variables.md)). |

## Screens

Where each page of Coolify's sidebar is in the console. Everything in Noust's list is in the
command palette (`Ctrl K`) too.

| Coolify | Noust |
|---|---|
| Dashboard | **Overview**: key figures, charts, what needs attention with the fix, recent activity |
| Projects | **Projects** (when there is something to organise) and **Applications** |
| Terminal | The application's **Terminal** and the server's ([terminal.md](terminal.md)) |
| Servers | **Server** (this server: system, services, storage, security, updates) and **Fleet** (all servers) |
| Sources | **Settings > Integrations** |
| Destinations | None: no Docker networks |
| Registries | **Settings > Integrations > Container registries**: the accounts, kept once, and an application's **Settings > Deploys** says which it uses ([containers.md](containers.md#registry-accounts)) |
| S3 Storage | **Backups > Destinations** |
| Shared Variables | **Settings > Variables** (the server's), a team's **Variables** tab and a project's page ([variables.md](variables.md)) |
| Team | **Settings > Accounts**, and **Teams** |
| Notifications | **Settings > Notifications**: email, Telegram, Slack, Discord, webhook |
| Keys & Tokens | **Settings > Tokens**; deploy keys on each application and **Settings > Integrations**; SSH keys under **Server > Security** |
| Tags | **Applications**, narrowed to a tag, and an application's **Settings > General** |
| Settings > General | **Settings > General** |
| Settings > Updates | **Settings > About** (the version, what's new and, if you turn it on, the hours Noust may update itself in) and **Server > Updates** (the operating system's) |
| Server Patching | **Server > Updates**: what is pending, security first, applied all or only the security fixes; it says when a reboot is due and why, and never reboots by itself; automatic updates and the history of runs ([console.md](console.md)) |
| Docker Cleanup | **Server > Storage**: what takes the space, one-off cleanups, and **Automatic cleanup**, the schedule and threshold this server cleans by |
| Log Drains | **Settings > Log shipping**: Loki, Axiom, New Relic, an HTTP endpoint, OpenTelemetry or a remote syslog ([runtime.md](runtime.md#log-shipping)) |
| Analytics, Sentinel | **Metrics** on each application, **Server > System**, and the resource monitor with **Settings > Alerts**. There is no agent to install: Noust reads the machine itself ([MONITOR.md](MONITOR.md)) |
| Scheduled Jobs of the instance | **Activity**: every job and event of this server with its log and who started it; the applications' own tasks are in **Cron** |
| Cloud Tokens, provisioning a server | **Settings > Cloud** on a central: provider accounts (Hetzner, DigitalOcean, Vultr), the wizard that creates a server and its cloud-init ([cloud-init.md](cloud-init.md), [CENTRAL.md](CENTRAL.md)) |
| Settings > Email | **Settings > Notifications > Email** |
| Settings > Authentication | **Settings > Security** (two-step verification, passkeys), single sign-on ([sso.md](sso.md)) |
| Settings > Backup (the instance) | **Backups > Noust's own state** (`noust self-backup`, one encrypted file) |
| Profile, 2FA | **Settings > Security** |
| An application: General, Domains, Environment, Storage | The application's **Overview**, **Domains**, **Environment** and **Settings** (General, Deploys, Deploy on push, Deploy hooks, Build commands, Builds, Resources, Source, Previews, Export, Delete) |
| Runtime Logs, Deployment Logs | **Logs** and **Deployments** |
| Metrics | **Metrics** |
| Preview Deployments, Webhooks | The application's **Settings > Previews** and **Settings > Deploy on push** |
| Scheduled Tasks | **Cron** |
| Rollback | **Roll back** on the application, from **Deployments** |
| Resource Limits | The application's **Settings > Resources** |
| Danger Zone | The application's **Settings > Delete** |

## What Noust does not do, on purpose

- **No Swarm.** One server is one server; several are a fleet, each with its own applications.
- **No proxy to choose or configure.** nginx or Apache, with sites you can read.
- **No Docker network per application.** A Compose project keeps the network its file defines. This is
  the whole of Coolify's *Destinations*: applications are processes on the loopback address, so there is
  no network to choose.
- **No tunnel as the way a server is reached.** A server is reached by its address and a CDN in
  front of it is fine; the DNS check says when it hides the server's address. A Cloudflare Tunnel
  for a name that should not open a port is optional and managed in **Domains and certificates >
  Tunnels** ([network.md](network.md#cloudflare-tunnel)).
- **No agent beside the server (Sentinel).** The monitor, the metrics and the alerts run inside Noust and
  report what they saw; none of them signals a process or changes a file ([MONITOR.md](MONITOR.md)).
- **No GitHub self-hosted runners.** Noust builds on the server it deploys to, as an unprivileged
  account in a sandbox, or in the Docker daemon for a container application.
- **No telemetry or error reporting to third parties.** The requests Noust makes on its own are the
  update check (GitHub and the package repository; `updates.check: false` turns it off) and the ones
  you configure (notifications, backup destinations, a Git host).
- **No automatic update you did not ask for.** An update is a decision with a rehearsal first: Noust
  checks the room on disk, the store and what the package manager says, and keeps a copy to go back to
  ([downgrading.md](downgrading.md)). A server can be told to take its own new releases, but only
  inside hours you set, on days you choose, for the releases you name (security fixes only, patch
  releases, or every stable one), never while a job is working, never a new major version, and behind
  the same checks ([release-policy.md](release-policy.md#updating-by-itself)).

## Moving across

Coolify keeps an application's configuration in its own database, not in the repository, so
nothing reads it from the files. Noust reads it from Coolify's API instead: in the console,
**New application > Import an application**, under *From another platform*, takes the server's
address and a read-only token (create it in Coolify with the `read` and `read:sensitive` permissions, so the environment
variables come too), lists the applications, reads the one you choose and goes on through the same
review an export file does: the domain, the source, the values the token could not hand over. On
the command line it is `noust import --from coolify --url https://coolify.example.com
--token-file coolify.token`. The token is used for those requests and kept nowhere. Whatever has
no equivalent in Noust comes with a warning that says what to do instead, and an application that
runs an image, a Dockerfile or a Compose file comes with the `noust create` command that deploys
it, because an import creates only what Noust builds from a repository.

The same path by hand is mechanical, and each step is checked by Noust:

1. **Install Noust on a new server** (or the same one, once you decide where Coolify's proxy goes;
   see below). [platforms.md](PLATFORMS.md), or [cloud-init.md](cloud-init.md) for a template.
2. **Read Coolify's configuration of the application**: its repository and branch, its build and
   start commands (General), its environment variables (Environment Variables > Developer view),
   its domains and port, its persistent storages, its health check, its scheduled tasks. Save the
   variables to a file, `KEY=value` per line.
3. **Deploy it**:

   ```bash
   noust create -d app.example.com -s https://github.com/you/app.git \
     --env-file app.env --persist uploads
   ```

   If the commands differ from what Noust would guess, put them in a `build:` block
   ([build.md](build.md)). A Nixpacks application is detected by its files like any other; a
   Dockerfile application is `--type container`; a Docker Compose application is deployed as a
   Compose stack ([compose.md](compose.md)).
4. **Check it before moving the name.** The new application answers on its own domain only when DNS
   points at the new server; until then test it by `curl --resolve app.example.com:443:NEW-IP
   https://app.example.com/`, and run `noust diagnose app.example.com`. Coolify's copy keeps serving
   the real name meanwhile.
5. **Move the data.** A database that Coolify runs in a container is dumped there (`docker exec ...
   pg_dump`) and restored with `noust db restore NAME DUMP -e postgresql --as-new NAME_COPY`, which restores
   beside anything that exists. Uploaded files are copied into the application's `shared/` folder.
6. **Move DNS**, then turn Coolify's resource off. Keep its backup (Coolify's own, to S3) until
   Noust's schedule has made and verified a few of its own: `noust backup schedule create` and
   `noust backup verify`.

### A service template from Coolify's catalogue

Coolify's one-click services are Compose files with conventions of their own: a header of
`# documentation:`/`# port:` comments, variables Coolify fills in (`SERVICE_URL_<ID>`,
`SERVICE_USER_<ID>`, `SERVICE_PASSWORD_<ID>` and the rest), a bare `SERVICE_URL_<ID>_<PORT>` entry that
says which service the proxy sends the domain to, and Traefik labels. Noust converts one into the
Compose document it deploys:

```bash
noust compose import wordpress.yaml --domain blog.example.com --out ./blog
noust create -d blog.example.com -s ./blog
```

or, in the console, **New application > Compose > Convert a Coolify template** (paste the template,
convert, then go on as with any Compose file).

- **The variables keep their names**, so the converted file reads like the original and a template
  updated upstream can be converted again. Their values are made here with the generators the
  recipes use: a user name, 32 and 64 character passwords, hex and base64 secrets, and the pair of
  JWTs Supabase-style stacks need, signed with the template's own secret. The address variables take
  the domain. **The values go to `.env` (mode 0600), never to the screen**; the console keeps them in
  the page until the deploy and does not show them again.
- **The routing entry becomes a port on `127.0.0.1`** (`127.0.0.1:${PORT:-2368}:2368`), which is how
  Noust's web server reaches a stack, and stops being an entry. With no entry the `# port:` header goes
  to the service that asks for the stack's address, never to a database, and it is said that this was a
  guess.
- **The port is a free one**, not the container's own: 80 and 443 are the web server's, and 3000 or
  8080 are as likely to be another application's. `noust compose import` and the API convert with the
  port `noust create` would give the next application (`--port` names another). The document reads it
  through `${PORT:-3021}`, and a stack that is deployed writes `PORT=<the port its site proxies to>` in
  its `.env` when the document publishes on `${PORT}` and you have not set it.
- **What Coolify does for you and Compose does not is done here**: a service that does not say how it
  restarts gets `restart: unless-stopped` (an application that starts before its database is ready
  exits and has to be started again); the named volumes a service mounts are declared at the top
  level (Compose refuses a project that mounts one nobody declared, and 340 of the 345 templates of the
  catalogue that mount one leave the declaration out); and a default one service gives a variable
  (`${POSTGRES_DB:-umami}`) is the value another service that reads it plainly (`$POSTGRES_DB` in a
  connection string) gets, as Coolify's variables work. A default with a space or a quote in it is
  asked for instead of guessed.
- **What the template needs from you is said**: a variable with no default and no value made here
  (`${MAIL_DRIVER:?}`, or one read plainly that nothing defines) is listed by `noust compose import`
  (`required` in `--json`) and by the console's analysis, because Compose stops on the first kind
  and starts the second with a blank.
- **What means nothing here is dropped, and said**: the Traefik and Coolify labels, the `coolify`
  network, `exclude_from_hc`. `$NAME` becomes `${NAME}`, the one spelling Noust reads.
- **What cannot be translated is a warning, never a guess**: a file mounted from inline `content:` (a
  Coolify extension), a second routed service (only the first gets the domain; route more with
  [`noust.nginx.yaml`](compose.md#routing-with-noustnginxyaml)), a variable named like a Coolify one
  that Noust does not fill in.
- **The result is judged like any Compose file** (`noust compose check`): a template that mounts the
  Docker socket or runs privileged is refused, as a repository's would be. Run against Coolify's whole
  catalogue (382 templates), 353 convert to a document nothing refuses and 27 are refused for exactly
  that; two are not valid YAML as written (an anchor merged where a list is). Images are not pinned:
  the analysis says so, and a tag can be pinned by digest before deploying.

Coolify and its templates are Apache-2.0; a template's own authors keep their rights in it. Noust
ships none of them: you bring the file, the converted document says where it came from, and the logos
a template names are not fetched. API: `POST /api/compose/import` (the answer carries the values; the
request writes nothing).

### Taking over a server Coolify runs

Moving applications one by one to the *same* server is possible but needs a decision about the
proxy. Coolify's Traefik owns ports 80 and 443, and an application it runs normally publishes
**no port** to the host (Traefik reaches it by the Docker network), so
[`noust app adopt`](compose.md#adopting-a-running-stack), which registers a stack that already runs
without changing anything, would register such a stack as a worker with no site. The steps that follow
from how both work, and which have not been exercised against a server Coolify set up (do the first
on a copy, or on the least important application):

1. Move the applications first, one at a time, to Noust on a second server, as above.
2. When the last one is moved, stop Coolify's proxy and Coolify, and install Noust on that server
   (`setup init` installs nginx on the freed ports).
3. An application you could not move earlier is deployed from its repository on this server, as
   in the steps above, once the ports are free.


## Moving the other way

`noust app export APP -o app.json` writes everything that defines an application as JSON, and
`noust app import` deploys it on another Noust. Nothing Noust keeps is in a format only Noust can
read: the sites are nginx files, the units are systemd units, the backups are `.tar.gz` files with
checksums, and the database dumps are the engine's own.
