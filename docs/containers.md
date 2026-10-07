# Container applications

How Noust deploys a Dockerfile, an image from a registry, or a repository built by
Railpack, as a container application (`--type container`), and how it goes back to an earlier
version of one.

A container application is not a Docker Compose project you wrote: Noust writes the compose
file itself, from the settings it keeps, and the application is one container. If you already
have a `docker-compose.yml`, deploy it as a [Compose stack](compose.md) instead; a repository
that has both is detected as the stack.

## The three ways to make the image

| You have | Deploy it with | The image comes from |
|---|---|---|
| A repository with a `Dockerfile` (or `Containerfile`) | `noust create -d shop.example.com -s <repository> -t container` | `docker build`, by the Docker daemon |
| A repository in Go, Rust, Ruby, Java, Kotlin, Elixir, Deno... and no Dockerfile | `noust setup railpack`, then `noust create ... -t container --builder railpack` | Railpack's plan, built by the Docker daemon |
| An image in a registry | `noust create -d shop.example.com --image ghcr.io/owner/app:1.4` | `docker pull` |

`--type auto` finds a Dockerfile by itself, and builds with Railpack a repository nothing else
recognises when Railpack is installed. `POST /api/apps/inspect`, which the console's
new-application wizard asks, proposes the container type for a repository with only a
Dockerfile, and for the languages Noust has no deployer for it says to add a Dockerfile or to
install Railpack (and, where Railpack is installed, proposes it).

### From a Dockerfile

```bash
noust create -d shop.example.com -s https://github.com/owner/shop.git -t container \
  --dockerfile docker/Dockerfile.prod --context services/api --target runtime \
  --build-arg NODE_ENV=production --container-port 3000 --volume /data
```

- `--dockerfile` is relative to the repository root and defaults to `Dockerfile`, then
  `Containerfile`, in the context. `--context` is the build context, relative to the repository
  (the root by default). `--target` is the stage of a multi-stage Dockerfile.
- The port is the one the Dockerfile `EXPOSE`s unless `--container-port` says otherwise.
- A path that leaves the repository, or a Dockerfile or context that is a link out of it, is
  refused: the daemon that builds is root.

**Build arguments are not secrets.** An argument is written into the image's history, so a name
that looks like a secret (`*_TOKEN`, `*_PASSWORD`, `*_SECRET`, `API_KEY`...) or a value with
credentials in a URL is refused, with the right way to give it:

```bash
noust app container set shop.example.com --build-secret npmrc=./npmrc
```

A build secret is read from the file, kept in Noust's secret store (0600, sealed with the rest
on a sealed central) and mounted for the build step that asks for it with BuildKit
(`RUN --mount=type=secret,id=npmrc ...`). It is never an argument of any command and never
part of the image. The value of a secret is never shown again, by the CLI, the API or the
audit trail.

### From a registry

```bash
noust create -d shop.example.com --image ghcr.io/owner/app:1.4 --container-port 8080
printenv REGISTRY_TOKEN | noust app container set shop.example.com \
  --registry-user ci --registry-password-stdin
```

`name[:tag][@sha256:digest]`. A private registry's account is given with
`--registry-user` and `--registry-password-stdin` (the password is read from standard input,
there is no option that takes it as an argument, because every local user can read a command
line). It is kept in the secret store and removed with the application; `docker login` writes
to a directory of its own that exists only while the pull runs, so nothing is left in
`/root/.docker`. An account is only ever sent to the registry it belongs to: for an image, the
registry its name says.

Every update pulls the image again: that is how a moving tag (`:latest`, `:1.4`) reaches the
server. A reference pinned by digest never changes. The digest each pull resolved is shown by
`noust app container show`.

### From Railpack

```bash
noust setup railpack                 # once
noust create -d shop.example.com -s <repository> -t container --builder railpack
```

`noust setup railpack` installs the one Railpack release this version of Noust pins, from the
project's GitHub release, over HTTPS, after checking its SHA-256 against the value pinned in
the code; nothing that does not match is installed, and nothing is fetched from "latest". The
build runs Railpack's plan through the Docker daemon with the BuildKit frontend of the same
version (`ghcr.io/railwayapp/railpack-frontend`), so the server needs to reach that registry
and the base images the plan uses. Railpack variables (`RAILPACK_NODE_VERSION`...) are build
arguments; its secrets are build secrets, given to it as environment variables of the commands
that build (their names are checked: `PATH`, `LD_*`, `DOCKER_*` and the like are reserved).
The image listens on `$PORT`, which Noust sets to the container port (3000 by default).

### In the console

The new-application wizard starts a container application three ways: a repository with a
Dockerfile (the inspection proposes the container type), a repository Railpack builds (choose
Railpack on **Configuration**), and **A Docker image**, which has no source and nothing to inspect.
**Configuration** carries every setting above: the Dockerfile, context and stage, build arguments
and build secrets, the port, the volumes, the restart policy and a private registry's account. An
application's **Settings > Container** shows and changes them afterwards (`noust app container set`).
Secrets are write-only there too: a build secret is listed by its id, a registry account's password
is "kept", and neither value is ever sent back or shown. The console cannot tell whether Railpack
is installed (no API says so): it offers it, and the deploy refuses with the server's words when it
is not.

## How it runs

Noust writes `compose.noust.yaml` in the application's directory (beside the repository, which
it never changes) and runs it through the application's own unit, `<app>.service`, like any
Compose project: one service named `app`, and

- the image of one release, `noust-<app>:<release>`, with `pull_policy: never`: restarting the
  unit never fetches anything;
- the port published on `127.0.0.1` only, so the web server is the only way in;
- the application's `.env` as the container's environment;
- the restart policy (`unless-stopped` by default, so it comes back after a reboot of the
  host), the memory, CPU and task limits of `noust app limits` as the container's limits, and
  one named volume for each `--volume`;
- no `privileged`, no Docker socket, no host namespaces: nothing in the file comes from the
  repository.

The site, the certificate, the domains, the health check (`noust app health`), the hooks
(`noust app hooks`) and the logs (`noust logs`, which shows what the container printed, with
its secrets masked) work as for the other types. A hook runs in a one-off container of the
new image.

## Releases: every deployment is an image

Each deployment gets a release id, builds or pulls its image and names it
`noust-<app>:<release>`. The release that serves is recorded, and so is how each of the others
ended.

```bash
noust releases list shop.example.com
noust releases rollback shop.example.com            # the one before the active one
noust releases rollback shop.example.com 20261006-101500-ab12cd3
noust releases keep shop.example.com 10             # images kept
```

- **Going back is instant.** It writes the compose file of that release again and recreates the
  container from an image already on the host: no build and no pull. It passes the same health
  gate as a deploy, and a release that does not answer is recorded as failed and the one that
  served is put back, with the probes and the container's own output.
- **A failed update puts back what served.** The new container is judged before the old one is
  given up; when it does not pass, the previous release's compose file is written again, the
  container recreated from its image, the failed release marked and its image removed, and the
  error says so.
- **Images are kept by retention.** The last `keep` releases (5 by default, 1 to 50) are kept;
  the active release and the one a rollback goes to are kept whatever the number is. A
  release whose image was pruned is listed but cannot be activated: rebuild its commit
  (`noust update --commit`).
- **A schema change is still a decision.** Going back past a deployment whose hook changed the
  database asks first, as for every type (`--schema-changed-ok`).

## Seeing and changing the settings

```bash
noust app container show shop.example.com [--json]
noust app container set shop.example.com --target runtime --build-arg A=1 --remove-build-arg B \
  --volume /uploads --restart always --container-port 9000
noust app container set shop.example.com --clear target --clear container-port
```

`GET` and `PUT /api/apps/{domain}/container` do the same (`apps.read` and `apps.manage`). The
whole change is checked before anything is written, takes effect with the next `noust update`,
and is recorded in the audit trail as `apps.container` with the names of what changed, never a
value. An application can change between a Dockerfile and Railpack; it cannot become an image
or stop being one: that is another application.

## What a container application is not

- It has no unit to limit, no account of its own and no blue/green instances: the container
  is in Docker's cgroups, and `noust app zero-downtime` refuses it.
- It cannot be given a database at creation (`--database`): the host's databases listen on the
  host. Link one afterwards and point the container at an address it can reach.
- Its builds run in the Docker daemon, outside the `noust-build` sandbox, like a Compose stack's
  (`noust app sandbox` says so). The compose file Noust writes is the only one the daemon is
  given.
- The data in its volumes is not part of `noust backup`: the application's directory is, the
  named volumes are on the server. Volume backups are a separate feature.
- It does not update itself when the registry has a new digest. `noust update` pulls the tag
  again, and every update recreates the container; scheduling it is up to you.
