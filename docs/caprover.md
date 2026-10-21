# From CapRover to Noust

For someone who runs CapRover and wants to know what Noust does differently, what each CapRover
screen and setting is called here, what is not done on purpose, and how to move. It is written from
CapRover's own code (Apache-2.0, https://github.com/caprover/caprover) and documentation and from
Noust's; where something here is a choice of design, it says so.

## The difference that explains the rest

CapRover runs **everything in Docker Swarm**: every application is a Swarm service
(`srv-captain--NAME`), built into an image from a `captain-definition` file (a Dockerfile, a ready
image, or Dockerfile lines), behind an **nginx container** CapRover writes the configuration of, with
a wildcard base domain (`*.apps.example.com`) every application gets a name under. Noust runs
applications **as ordinary processes under systemd**, behind the nginx or Apache the server already
has, with a certificate from Let's Encrypt through certbot, one name at a time. Docker is only there
when a project is a Docker Compose project, a Dockerfile or an image (then Noust runs it as a unit
too).

What follows from that:

- **No `captain-definition`.** A Node, Python, PHP, Go, Rust, Ruby, Java, .NET, Elixir or Deno
  repository is detected from its files, built on the server in a sandbox and run as a unit; a
  repository with a Dockerfile is a **container application** ([containers.md](containers.md)).
  The commands a build runs, and the base and publish directories, are a `build:` block of
  `noust.yaml` ([build.md](build.md)).
- **No Swarm, no instance count.** One server is one server. Several are a fleet, each with its own
  applications ([CENTRAL.md](CENTRAL.md)).
- **No wildcard base domain by default.** Each application answers on the names you give it (and a
  temporary `sslip.io` address to try it out, [domains.md](domains.md#a-temporary-address)); a
  wildcard certificate needs a DNS account ([certificates.md](certificates.md)).
- **Every deploy is a release on disk**, switched only if the new version passes a health gate;
  rollback is flipping a link back, with no registry in between. CapRover keeps image versions and
  redeploys one.
- **Two interfaces to the same thing**: the console and the `noust` command (the equivalent of the
  `caprover` command), both clients of the same code. `--dry-run` rehearses any command; `--json`
  makes it scriptable.

## Concepts

| CapRover | Noust | Notes |
|---|---|---|
| App (a web app) | An application of a type, or a container application | Native types need no definition file ([frameworks.md](frameworks.md)). |
| `captain-definition` with a Dockerfile | `--type container` | `noust create -d app.example.com -s REPOSITORY --type container --builder dockerfile`. One image per release. |
| `captain-definition` with `imageName` | A container application from a registry image | `noust create -d app.example.com --image IMAGE`. |
| `captain-definition` with `dockerfileLines` or a template | A Dockerfile in the repository, or pasted | The console's New application > Container takes a pasted Dockerfile. |
| One-click apps | Recipes (a catalog) | [recipes.md](recipes.md). An app Noust has no recipe for is deployed as a Docker Compose project. |
| One-click databases (PostgreSQL, MySQL, MariaDB, MongoDB, Redis...) | Databases of the host, or an instance Noust runs in a container | `noust db`; another version is `noust db instance` ([databases.md](databases.md)). |
| Deploy by `caprover deploy` (a tarball) | `noust upload`, or `noust update` from the repository | An archive becomes the code of an application. |
| Deploy by webhook | The deploy webhook | `noust app webhook show`; signed, with a log of every delivery. GitHub, GitLab, Gitea and Bitbucket. |
| App tokens | API tokens with a scope | `noust token create ci --scope deploy`; [ci.md](ci.md) has the GitHub Action. |
| Container HTTP port | The application's port | Detected, or `noust create --port`. |
| HTTP settings: force HTTPS, websocket support | The site policy | HTTPS is forced once there is a certificate; `noust app policy set DOMAIN --no-force-https` answers on both. Websockets are proxied. |
| Custom domains and "Enable HTTPS" | Domains with certificates | Aliases, redirects, DNS check ([domains.md](domains.md)). |
| Environment variables | An application's variables | [variables.md](variables.md); a secret manager is optional. |
| Persistent directories | Persistent paths (`--persist`), mounts | `noust app mount`. |
| Port mapping | A published TCP or UDP port through nginx | `noust stream` ([network.md](network.md)). |
| Number of instances | Not applicable | One process per application; zero-downtime updates are blue/green (`noust app zero-downtime`). |
| Pre-deploy script | A `pre_deploy` hook | `noust.yaml` ([compose.md](compose.md#noustyaml)); it runs before any traffic moves, and a failing one aborts the deploy. |
| Custom nginx config | The site, edited | As text, as a structure or as a diagram ([domains.md](domains.md#operator-sites)); Noust never rewrites a site it did not write. |
| HTTP basic auth | The site policy | `noust app policy set DOMAIN --password`. |
| Networks | Not applicable | Applications are processes on the server's loopback address. |
| Cluster, nodes, registry | A fleet of servers | Each server runs its own Noust. |
| NetData monitoring | The monitor and the metrics | No agent to install ([MONITOR.md](MONITOR.md)). |
| Backup (CapRover's configuration) | `noust self-backup`, and the backups of applications and databases | Noust's own state in one encrypted file; the data of applications is backed up too ([disaster-recovery.md](disaster-recovery.md)). |
| Dashboard password, 2FA | Accounts with roles, two-step verification, passkeys, single sign-on | [security.md](security.md), [sso.md](sso.md). |

## Screens

Where each page of CapRover's dashboard is in the console.

| CapRover | Noust |
|---|---|
| Dashboard (root domain, HTTPS, "Force HTTPS") | **Domains and certificates**, and **Settings > General** |
| Apps (the list; an app's Deployment, HTTP Settings, App Configs, Logs) | **Applications**, and the application's **Deployments**, **Domains**, **Settings**, **Environment** and **Logs** |
| One-Click Apps/Databases | **Catalog** (the command palette: "From a recipe"), and **Databases** |
| Monitoring (NetData) | **Overview**, the application's **Metrics**, **Server > System** and **Settings > Alerts** |
| Cluster | **Fleet** |
| Settings > Backup & restore | **Backups**, and **Backups > Noust's own state** |
| Settings > Change password, 2FA | **Settings > Security**, **Settings > Accounts** |
| Webhooks (an app's Deployment > Method 3) | **Settings > Deploy on push** of the application |
| Logs (an app's) | The application's **Logs** |

## What Noust does not do, on purpose

- **No Swarm.** One server is one server; several are a fleet.
- **No proxy container to configure.** nginx or Apache, with sites you can read.
- **No image registry in the path of a native deploy.** A release is a directory; a container
  application keeps its images on the server.
- **No shared password.** CapRover's dashboard is one password (and a second factor, if you turn it
  on). Noust has accounts with roles, and the master token is break-glass, not the normal way in.
- **No telemetry or error reporting to third parties.** The requests Noust makes on its own are the
  update check (`updates.check: false` turns it off) and the ones you configure.

## Moving across

CapRover keeps an application's configuration in its own data store, not in the repository. Noust
reads it from CapRover's API instead:

```bash
# the dashboard's password, in a file only you read
noust import --from caprover --url https://captain.apps.example.com --token-file captain.pass
noust import --from caprover --url https://captain.apps.example.com --token-file captain.pass --app shop
```

CapRover has no credential that only reads, so the importer signs in with the dashboard's password,
for the length of the request, and keeps it nowhere (a server that asks for a one-time code cannot be
read this way: turn the second factor off while you import). In the console, **New application >
Import an application**, under *From another platform*, takes the same. It only reads: after the
sign-in it issues `GET` requests.

What carries over: the repository and branch a webhook deploys from (the credentials CapRover keeps for
it are never read), the domains (the custom ones and `NAME.root-domain`), the port the container listens
on, the variables (the ones whose value looks like a credential are asked for again, so a connection
string that names a CapRover database is not carried to a place it cannot reach), the volumes (as
`--persist` paths where they sit under `/app`, with the command that copies the data out of the old
server and the one that puts it back), and the databases the application connects to. A CapRover
database is an application like the others that runs a database image, and an application reaches it by
the name `srv-captain--NAME`: a variable that holds that name is the link. The importer prints the
`docker exec` dump (the password is read inside the container) and the `noust db import` that loads it.
The dump finds the container by the application's name or by `srv-captain--NAME`: CapRover 1.15 names
its services after the application, and older ones put the prefix in front.

What is only said, with what to do instead: an application CapRover built from a `captain-definition`
(the definition is not part of the application's record, so it is a container application you create
from the same repository, and the command is given), a registry image, a pre-deploy function, a custom
nginx configuration, basic authentication, extra networks, published ports and several instances.

The same path by hand is mechanical, and each step is checked by Noust:

1. **Install Noust on a new server** ([cloud-init.md](cloud-init.md), [PLATFORMS.md](PLATFORMS.md)).
2. **Deploy the application**: `noust create -d app.example.com -s https://github.com/you/app.git
   --env-file app.env --persist uploads`, with `--type container` for a repository CapRover built from a
   Dockerfile.
3. **Check it before moving the name**: `curl --resolve app.example.com:443:NEW-IP
   https://app.example.com/`, and `noust diagnose app.example.com`. CapRover keeps serving the real
   name meanwhile.
4. **Move the data**: a database is dumped on the CapRover server and loaded with `noust db import`;
   a volume is copied with the `tar` pair the importer prints.
5. **Move DNS**, then stop the app in CapRover. Keep CapRover's own backup until Noust's schedule has
   made and verified a few of its own.

## Moving the other way

`noust app export APP -o app.json` writes everything that defines an application as JSON, and
`noust app import` deploys it on another Noust. Nothing Noust keeps is in a format only Noust can
read: the sites are nginx files, the units are systemd units, the backups are `.tar.gz` files with
checksums, and the database dumps are the engine's own ([leaving.md](leaving.md)).
