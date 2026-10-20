# From Dokploy to Noust

For someone who runs Dokploy and wants to know what Noust does differently, what each Dokploy screen
and setting is called here, what is not done on purpose, and how to move. It is written from Dokploy's
own code and documentation (the 0.2x line, read at its repository's current `main`) and from Noust's;
where something here is a choice of design, it says so.

## The difference that explains the rest

Dokploy runs **everything in Docker, on Docker Swarm**: each application is built into an image (with
Nixpacks, Railpack, Heroku or Paketo buildpacks, a Dockerfile, or a static server), runs as a Swarm
service behind **Traefik**, which Dokploy writes the configuration of. A server is a Swarm node;
several servers are a Swarm cluster. Noust runs applications **as ordinary processes under systemd**,
behind the nginx or Apache the server already has, with a certificate from Let's Encrypt through
certbot. Docker is only there when a project is a Docker Compose project, a Dockerfile or an image
(then Noust runs it as a unit too).

What follows from that:

- **No Swarm, no replicas, no placement.** One server is one server. Several servers are a fleet,
  each with its own applications and its own Noust ([CENTRAL.md](CENTRAL.md)); nothing balances an
  application over them.
- **No Traefik.** The web server is the proxy and its site files are plain text you can read and edit
  ([domains.md](domains.md#operator-sites)). Dokploy's **Traefik File System** page has no counterpart
  because there is no second configuration language.
- **Every deploy is a release on disk**, switched only if the new version passes a health gate;
  rollback is flipping a link back, with no registry in between. Dokploy's rollbacks keep images
  (and need a registry to keep them somewhere).
- **Two interfaces to the same thing**: the console and the `noust` command, both clients of the same
  code. `--dry-run` rehearses any command; `--json` makes it scriptable.

## Concepts

| Dokploy | Noust | Notes |
|---|---|---|
| Organization | The server (or the fleet) and its **teams** | Accounts belong to teams; a team sees only its projects ([teams.md](teams.md)). |
| Project and Environment | Project and Environment | The same grouping, optional: a server nobody organised shows the sidebar it always had. |
| Application | An application of a type: Next.js, Nuxt, SvelteKit, Astro, Remix, Angular, Vite, Node.js, Python, PHP, static, Go, Rust, Ruby, Java, .NET, Elixir, Deno; Docker Compose; container | Native types need no build file ([frameworks.md](frameworks.md)). |
| Build type: Nixpacks, Railpack, Heroku or Paketo buildpacks | Detection of the stack, and the commands of its type | A buildpack has no counterpart for the native types; **Railpack** is supported for the container type ([containers.md](containers.md)). |
| Build type: Dockerfile | `--type container` (a Dockerfile) | One image per release; rollback is as quick. |
| Build type: Static | A static application, with a `build:` block when it is built first | [build.md](build.md). |
| Source: Docker image | A container application from a registry image | `noust create -d app.example.com --image IMAGE`; registry accounts are kept once (`noust registry`). |
| Source: GitHub, GitLab, Bitbucket, Gitea, Git by SSH | The same hosts, and a deploy key per application | [sources.md](sources.md). |
| Source: Drop (an uploaded archive) | `noust upload` | An archive becomes the code of an application. |
| Docker Compose / Stack | A Docker Compose application | `Compose` runs the project as Compose runs it; the Swarm "Stack" mode has no counterpart ([compose.md](compose.md)). |
| Databases (PostgreSQL, MySQL, MariaDB, MongoDB, Redis, libSQL) | Databases of the host, or an instance Noust runs in a container | `noust db`; another version, or an engine the host lacks, is `noust db instance` ([databases.md](databases.md)). |
| Templates | Recipes (a catalog), and the converter for a Dokploy template | [recipes.md](recipes.md); the converter is below. |
| Domains (host, path, port, HTTPS, certificate type) | An application's domains | Aliases and redirects; Let's Encrypt, a custom or an internal certificate ([domains.md](domains.md), [certificates.md](certificates.md)). A path routing is `noust.nginx.yaml` ([compose.md](compose.md#routing-with-noustnginxyaml)). |
| Environment, build arguments, secrets | An application's variables, with build-only ones and secret managers | [variables.md](variables.md). |
| Advanced > Resources (memory and CPU limits and reservations) | `noust app limits` | systemd resource control; a reservation has no counterpart. |
| Advanced > Volumes (volume, bind, file mounts) | Persistent paths (`--persist`), and `noust app mount` | A file Dokploy writes is kept with `noust app mount file`. |
| Advanced > Ports | Published TCP and UDP ports through nginx | `noust stream` ([network.md](network.md)). |
| Advanced > Swarm settings (replicas, placement, update and rollback config, labels, networks) | Not applicable | Noust runs one process per application on a server. |
| Advanced > Redirects, Security (basic auth) | Redirects of a domain, and the site policy | `noust domain`, `noust app policy set DOMAIN --password`. |
| Preview deployments | Previews | `noust preview enable`; one per pull request. |
| Schedules (cron in the container, or on the server) | Cron jobs: systemd timers | `noust cron create`; with history. |
| Volume backups, Database backups, S3 destinations | Backups, with destinations | `noust backup`, `noust db backup-schedule`; destinations are rclone's (S3 and compatibles, SFTP, SMB, WebDAV, Google Drive, OneDrive, Dropbox, B2, pCloud) with optional encryption. |
| Rollbacks | Releases | `noust releases rollback`, in seconds, with no rebuild. |
| Server (remote servers, build servers, cluster) | A fleet of servers, each its own Noust, and a build server | [CENTRAL.md](CENTRAL.md). |
| Registry | Container registry accounts | `noust registry`; Settings > Integrations. |
| Webhooks (deploy on push) | The deploy webhook | `noust app webhook show`; signed, with a log of every delivery. |
| API keys (`x-api-key`) | API tokens with a scope, and roles | `noust token create`; roles are `viewer`, `operator`, `admin`, `security`, `auditor`. |
| Notifications (Slack, Discord, Telegram, email, Gotify, ntfy...) | Notification channels | `noust notify` ([notifications.md](notifications.md)). |
| Users, Audit logs, SSO | Accounts, the audit trail, single sign-on | Settings > Accounts, `noust audit`, [sso.md](sso.md). |
| Tags | App tags | Settings > General of an application; `noust app tag restart`. |

## Screens

Where each page of Dokploy's sidebar is in the console. Everything in Noust's list is in the command
palette (`Ctrl K`) too.

| Dokploy | Noust |
|---|---|
| Home, Projects | **Overview** and **Applications** (and **Projects** when there is something to organise) |
| Overview, Monitoring | **Overview**, the application's **Metrics**, **Server > System**, the resource monitor and **Settings > Alerts** ([MONITOR.md](MONITOR.md)). There is no agent to install: Noust reads the machine itself |
| Schedules | **Cron** |
| Traefik File System | The web server's sites, as text, as a structure or as a diagram (**Domains and certificates**, the sites tab); nothing is generated that you cannot read |
| Docker | **Server > Storage**: what the images, volumes and build cache take, and one-off cleanups; an application's **Logs** and **Services** for what runs |
| Requests | The application's traffic ([MONITOR.md](MONITOR.md#traffic-of-an-application)): requests, status classes, latency, visitors |
| Settings > Web Server | **Settings > General**, **Server > Updates** and **Settings > About** |
| Settings > Profile, Sessions, Users | **Settings > Security** (two-step verification, passkeys), **Settings > Accounts** |
| Settings > Remote Servers | **Fleet**, and **Settings > Servers** on a central |
| Settings > Deployments | **Activity**: every job and event with its log and who started it |
| Settings > Audit Logs | The audit trail (`noust audit`, and the console's Activity and Settings) |
| Settings > SSH Keys | **Server > Security**; deploy keys on each application |
| Settings > Git | **Settings > Integrations** |
| Settings > Registry | **Settings > Integrations > Container registries** |
| Settings > Logs & Metrics | **Settings > Log shipping** and **Metrics** |
| Settings > Secrets | **Settings > Integrations**: secret managers (Vault, Doppler, Infisical) ([variables.md](variables.md)) |
| Settings > DNS Providers | **Domains and certificates**: DNS accounts (`noust dns`) |
| Settings > S3 Destinations | **Backups > Destinations** |
| Settings > Certificates | **Domains and certificates**: the certificates tab |
| Settings > Notifications | **Settings > Notifications** |
| Settings > SSO | **Settings > Security**, single sign-on ([sso.md](sso.md)) |
| Settings > Billing, License, Whitelabeling | None. Noust is AGPL-3.0-or-later and has one edition; nothing is licensed per server or per seat |
| An application: General, Environment, Domains, Deployments, Preview Deployments, Schedules, Volume Backups, Logs, Monitoring, Advanced | The application's **Overview**, **Environment**, **Domains**, **Deployments**, **Settings > Previews**, **Cron**, **Backups**, **Logs**, **Metrics** and **Settings** |
| Rollback (in Deployments) | **Roll back** on the application, from **Deployments** |

## What Noust does not do, on purpose

- **No Swarm.** One server is one server; several are a fleet.
- **No proxy to choose or configure.** nginx or Apache, with sites you can read.
- **No image registry in the path of a native deploy.** A release is a directory; a container
  application keeps its images on the server and can push them to a registry you name.
- **No cluster of build nodes that a deploy is spread over.** A build can run on one server and be
  served from another ([delivery.md](delivery.md)); that is one build, shipped.
- **No licensed tier.** Dokploy's Billing, License, SSO and Whitelabeling pages belong to its
  commercial parts (the code under a `proprietary` folder of its repository has a separate
  licence). Noust's single sign-on, audit trail, teams, roles and approvals are part of the one edition.
- **No telemetry or error reporting to third parties.** The requests Noust makes on its own are the
  update check (`updates.check: false` turns it off) and the ones you configure.

## Moving across

Dokploy keeps an application's configuration in its own database, not in the repository. Noust reads
it from Dokploy's API instead: in the console, **New application > Import an application** under
*From another platform*, or on the command line:

```bash
# an API key made under Dokploy's Settings > API Keys, in a file only you read
noust import --from dokploy --url https://dokploy.example.com --token-file dokploy.key
noust import --from dokploy --url https://dokploy.example.com --token-file dokploy.key --app shop
noust import --from dokploy --url https://dokploy.example.com --token-file dokploy.key --app shop \
  --deploy shop.example.com --env-file shop.env
```

The key is used for those requests and kept nowhere. What carries over: the repository and branch (GitHub,
GitLab, Bitbucket or a plain git URL), the build path, the publish directory of a static site, the
domains and the port, the variables (the ones whose value looks like a credential are asked for
again, so a connection string that names a Dokploy database is not carried to a place it cannot
reach), the watch paths, submodules, the memory and processor limits, the volumes and bind mounts
(as `--persist` paths where they sit under `/app`, with the command that copies the data out of the
old server and the one that puts it back) and the databases of the same environment that a
variable names, with the `docker exec` dump and the `noust db import` that loads it. What is only
said, with what to do instead: Swarm settings, an application that runs an image or a Dockerfile (the
`noust create` command that creates it is given), a Gitea source, an uploaded archive, and a file
Dokploy writes and mounts.

The same path by hand is mechanical, and each step is checked by Noust:

1. **Install Noust on a new server** ([cloud-init.md](cloud-init.md), [PLATFORMS.md](PLATFORMS.md)).
2. **Deploy the application** from its repository: `noust create -d app.example.com -s
   https://github.com/you/app.git --env-file app.env --persist uploads`. A Nixpacks application is
   detected by its files like any other; a Dockerfile application is `--type container`.
3. **Check it before moving the name**: `curl --resolve app.example.com:443:NEW-IP
   https://app.example.com/`, and `noust diagnose app.example.com`. Dokploy's copy keeps serving the
   real name meanwhile.
4. **Move the data.** A database that Dokploy runs is dumped where it runs (`docker exec ...
   pg_dump`, the importer prints the command) and loaded with `noust db import NAME FILE -e
   postgresql`; a volume is copied with the `tar` pair the importer prints.
5. **Move DNS**, then turn Dokploy's service off. Keep its backup until Noust's schedule has made
   and verified a few of its own: `noust backup schedule create` and `noust backup verify`.

### A template from Dokploy's catalogue

Dokploy's one-click templates (https://github.com/Dokploy/templates, MIT) are a folder per
application: a `docker-compose.yml` and a `template.toml` with variables written as expressions
(`${password:32}`, `${hash:8}`, `${base64:32}`, `${uuid}`, `${domain}`, `${jwt:secret:payload}`, ...),
the domains, and files to write beside the stack. Noust converts one into a stack it deploys:

```bash
git clone --depth 1 https://github.com/Dokploy/templates.git
noust compose import templates/blueprints/ghost --from dokploy \
  --domain blog.example.com --out ./blog
noust create -d blog.example.com -s ./blog
```

The command line and the API do this; the console's **New application > Compose > Convert a
template** reads Coolify's format only, because a Dokploy template can carry files to write beside the
stack and the console's form has one document. API: `POST /api/compose/import` with
`"format": "dokploy"`, the Compose text in `document` and the `template.toml` in `template`; the
answer lists the `files` and, apart, the ones that hold a secret (`secret_files`).

- **The expressions are expanded the way Dokploy's own processor does it**, with Noust's generators:
  `${password:N}` is N lower-case letters and digits, `${hash:N}` N hexadecimal digits, `${base64:N}`
  the base64 of N random bytes, `${jwt:secret:payload}` a token signed with the variable `secret` and
  carrying the JSON in `payload` (with Dokploy's `iss`, `iat` and `exp` when the payload has none).
  The values go to `.env` (mode 0600), never to the screen.
- **The domain.** A template that builds its addresses from `${domain}` needs `--domain`. The service
  its `[[config.domains]]` names is published on `127.0.0.1` (`127.0.0.1:${PORT:-8080}:8080`), which is
  how Noust's web server reaches a stack; a second service, another host name or a path is said, never
  guessed.
- **The files** its `[[config.mounts]]` write are written under `files/` beside the document, and the
  Compose file's `../files/x` becomes `./files/x`. A path that leaves the folder is refused. A file
  that holds a generated secret is written readable by its owner only.
- **What means nothing here is dropped, and said**: `dokploy-network`, Traefik labels, the isolated
  deployment. The restart policy, the declaration of the named volumes and the `${NAME}` spelling are
  what Compose needs and Dokploy did.
- **The result is judged like any Compose file** (`noust compose check`): a template that mounts the
  Docker socket, runs privileged or mounts the host's root is refused, as a repository's would be.

`python scripts/template_catalogue.py dokploy PATH/TO/templates/blueprints` converts a whole checkout
and prints the figures. On the catalogue at the time of writing (572 templates), **541 convert to a
document nothing refuses and 31 are refused** for what the guard refuses in any Compose file: the
Docker socket (27), a directory that holds Noust's own state (2), a privileged container (1), a build
context (1). The templates are MIT (Copyright 2024 Dokploy and Carlos Ortiz); a template's own authors
keep their rights in it. Noust ships none of them: you bring the files, the converted document says
where it came from, and the logos a template names are not fetched.

## Moving the other way

`noust app export APP -o app.json` writes everything that defines an application as JSON, and
`noust app import` deploys it on another Noust. Nothing Noust keeps is in a format only Noust can
read: the sites are nginx files, the units are systemd units, the backups are `.tar.gz` files with
checksums, and the database dumps are the engine's own ([leaving.md](leaving.md)).
