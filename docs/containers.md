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
| A repository in a language Noust has no deployer for (Go, Rust, Ruby, Java, Kotlin, .NET, Elixir and Deno are built natively when they have a program to run: [build.md](build.md)), or one of those you want as a container, and no Dockerfile | `noust setup railpack`, then `noust create ... -t container --builder railpack` | Railpack's plan, built by the Docker daemon |
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

Railpack's frontend merges layers, which Docker allows only with its **containerd image store**
(`docker info` names the store under `Storage Driver`). A new installation of Docker 29 or later starts on it; a server that was upgraded
keeps the store it had, and the build then stops with BuildKit's `only enabled with containerd image
store backend`, which Noust follows with the change to make: `{"features": {"containerd-snapshotter":
true}}` in `/etc/docker/daemon.json` and a restart of Docker. Images and containers made with the
previous store are not carried over, which is why Noust does not do it for you.

### Following a tag

`noust update` pulls the tag again and recreates the container behind the health gate. To have it
done when the registry has something new, and only then, there are two triggers, and they ask the
same question: does the tag now point at a digest other than the one that runs?

```bash
noust app container check shop.example.com        # pulls the tag, compares digests, deploys nothing
noust deploy schedule set shop.example.com --at hourly     # or '*:0/15' for every quarter of an hour
noust app webhook rotate shop.example.com         # a secret for the registry's webhook
noust app webhook registry shop.example.com       # what to give the registry
```

- **A schedule** is the application's update timer with `only_if_new` (on by default): for an image
  it pulls the tag and runs the update only when the digest moved, so a timer is not an hourly
  recreation of the same image. It is held by a pause or a freeze of deploys, takes the
  pre-update backup and passes the health gate like any update.
- **A registry's webhook** pushes instead of polling. GitHub Container Registry: add a webhook to
  the repository (or the organisation) that sends **Packages** events (`registry_package`) to
  `https://<your hooks address>/hooks/registry/<domain>` with the application's webhook secret
  (the same one a push webhook uses). Docker Hub signs nothing and cannot send a header, so its
  address carries a token made from that secret: `noust app webhook registry <domain> --reveal`
  prints the whole address to paste into the repository's **Webhooks** on hub.docker.com (the
  console shows it after sudo mode). A secret that is rotated retires the token.
  A delivery is acknowledged and ignored when it names another image or another tag, when the
  application is pinned by digest, or when the digest it announces already runs; otherwise the
  update is queued behind the application's other operations, merged into one that waits, and the
  job asks the registry again before it recreates anything (a CI that pushes the same image twice
  deploys once). Every delivery is in `noust app webhook deliveries`.
- An image **pinned by digest** (`name@sha256:...`) never changes, so it never follows anything.

### The command and the entrypoint

An image runs its own `ENTRYPOINT` and `CMD`. To run something else, whichever way the image
came to exist:

```bash
noust app container set shop.example.com --entrypoint "/app/run" --command "serve --port 8080"
noust app container set shop.example.com --entrypoint ""         # no ENTRYPOINT: the image's CMD is the program
noust app container set shop.example.com --clear command        # the image's own again
```

`--entrypoint` replaces the program (Docker's `ENTRYPOINT`) and `--command` its arguments
(Docker's `CMD`). Each is an **argument vector, never a shell line**: the line is split into
words the way a shell would split it, and nothing runs it through one, so `&&`, `|` and `$HOME`
are plain words. A program that needs a shell is started with one:
`--command "sh -c 'migrate && serve'"`. A `$` is the process's, not Compose's (Noust writes `$$`
into the compose file). Over the API the settings are `entrypoint` and `command`, lists of words;
`null` leaves the image's own, and `clear` names what goes back to the image's. A change takes
effect with the next `noust update`.

Two rules come from Docker itself, and the real engine is what settled them
(`container_command_and_entrypoint` in the integration harness):

- **Setting an entrypoint drops the image's CMD.** An image with `ENTRYPOINT ["/app/run"]` and
  `CMD ["serve"]` given `--entrypoint /other` runs `/other` with no arguments, not `/other serve`;
  give `--command` as well if the arguments should still apply.
- **A CMD cannot be cleared on its own.** There is no way to run an image's ENTRYPOINT without its
  CMD: an empty `command:` in a compose file means "the image's own", so `--command ""` and an
  empty list over the API are refused instead of promising what the container would not do. An
  empty **entrypoint** is valid and means no ENTRYPOINT: the image's CMD is then the program that
  runs.

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

## Registry accounts

A private image, or a Dockerfile whose `FROM` is one, needs a login. Instead of a password per
application, keep the accounts once and say which applications use them:

```bash
echo "$TOKEN" | noust registry add ghcr-acme --kind ghcr -u acme-bot --password-stdin
noust registry test ghcr-acme            # tries it in a private Docker configuration
noust registry attach shop.example.com ghcr-acme
noust create -d shop.example.com -s https://github.com/acme/shop.git --registry ghcr-acme
```

Kinds: `dockerhub`, `ghcr`, `gitlab`, `ecr`, `acr`, `gar` and `custom` (any host; the others know
theirs). The password is read from standard input or `NOUST_REGISTRY_PASSWORD`, never an
argument, and is kept as a secret: it is not a column and no answer returns it. Amazon ECR trades
its AWS keys for a token that lasts twelve hours, asked each time. Docker writes the credential it
is given to a file in clear, so every `docker` command Noust runs for an application (pull, build,
Compose `up`) logs in to a configuration directory of its own, with the password on standard
input, and removes the directory straight after. The console does the same from Settings, Integrations,
Container registries (`/api/registries`), and an application's Compose or Container settings choose which
accounts it uses; attaching accounts to an application is sudo mode, because an account reaches
every image it can read.

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
- It does not update itself unless you ask it to: see [Following a tag](#following-a-tag).
