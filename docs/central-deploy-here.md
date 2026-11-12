# Deploy where the central runs

The central in a container ([The Noust central](CENTRAL.md)) is a **hub**: it manages your servers
and deploys nothing itself, because its image has no nginx, no systemd and no certbot. That is a
fact about the image, not about the machine under it. A NAS has Docker, and Docker is all a
container needs: given the machine's Docker, the central can deploy **containers** on that machine
too, the way a server does.

It is off until you turn it on, on purpose, in the file that creates the container, and the
console cannot turn it on.

## What you get, and what you do not

With it on, the central deploys what needs only Docker:

- applications that run as a container (a Dockerfile, an image from a registry, or Railpack),
- Docker Compose stacks, and the catalogue's recipes (435 of the 440 are Compose stacks),
- database engines in containers,
- updates with the health check and the way back, stopping, deleting, logs and a console,
- copies of volumes and of the databases of a stack,
- the whole Docker zone of the console (**Server > Docker**).

Everything that needs systemd or a web server on the machine stays where it is, and the console does
not offer it here: applications that run natively (Node.js, Python, PHP and the rest of the
types), domains and certificates of Noust, preview environments, an application's scheduled tasks,
the zero-downtime relay, and the machine's own services, firewall and clock. Where a server of your
fleet has them, they are one click away from this central's console. What the console does not offer
the commands refuse in the same words: *"Domains: not available here, which deploys containers and
nothing else"*.

A central that was not told to deploy here is exactly what it was, with Docker or without it.

## What you are agreeing to

Docker's socket is root on the machine: whoever can use it can start a container that mounts the
whole disk. Giving it to the central means that **anyone who signs in to the central's console as an
administrator can do what root can on the NAS**. That is also what a server's Noust has always been
(it runs as root there), so nothing is added to a server; on a NAS it is new, and it is the reason
this is never switched on from the console. Two conditions keep it honest:

- the central stays what it is: it runs as an unprivileged user (uid 10001, no capabilities, a
  read-only file system), and only gains the group that owns Docker's socket;
- a stack that is privileged or mounts the Docker socket is refused for a new deployment unless
  you recorded an exception for that application, with a reason: the applications you deploy do not
  get what the central has by default.

Turn on two-factor authentication and seal the central's secrets before you do this
([First sign-in and accounts](CENTRAL.md#first-sign-in-and-accounts)).

## The compose file

Two things, both in the compose file of the central: the socket is mounted (with the group that can
open it), and `NOUST_DEPLOY_HERE` says why.

```yaml
services:
  noust:
    image: ghcr.io/perkybeet/noust:latest
    container_name: noust
    restart: unless-stopped
    ports:
      - "8443:8443"
    volumes:
      # One folder of the NAS, mounted at the SAME path inside the container as outside it.
      - /volume1/docker/noust:/volume1/docker/noust
      # Docker's socket: this is what lets the central deploy.
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      NOUST_DEPLOY_HERE: containers
      NOUST_DATA_DIR: /volume1/docker/noust
      NOUST_APPS_DIR: /volume1/docker/noust/apps
      HOME: /volume1/docker/noust
      TZ: UTC
    # The group that owns the socket on the NAS: stat -c %g /var/run/docker.sock
    group_add:
      - "999"
    user: "10001:10001"
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    read_only: true
    tmpfs:
      - /tmp:size=16m,mode=1777
    stop_grace_period: 20s
```

The image carries Docker's command line (28.5.2) and Compose (2.40.3), each fixed by the sum of its file in the
build (`packaging/container/install_docker_client.py` says why these two), so nothing is installed on the NAS: the
image is the same one whether it deploys here or not, and without the socket and the setting they do nothing.

Why the folder is mounted at the same path: when the central tells Docker to mount
`./data` of a stack, **Docker resolves the path on the NAS, not inside the central's container**. If
the central sees `/data` and the NAS sees `/volume1/docker/noust`, a stack's data lands in a folder
that does not exist where you think it does. With the same path on both sides there is one folder
and no translation. Create it for the container's user first:

```bash
sudo mkdir -p /volume1/docker/noust/apps
sudo chown -R 10001:10001 /volume1/docker/noust
```

Use the path your NAS gives its shared folders (`/volume1/docker/...` on Synology and UGOS Pro). The
three variables (`NOUST_DATA_DIR`, `NOUST_APPS_DIR`, `HOME`) and the volume line must name the same
folder.

## Check it

Before you rely on it, ask the central whether it can deploy here. Each line is one check, and each
failure says the change that fixes it:

```bash
docker exec -it noust noust central deploy-here check
```

| Check | What it proves |
|-------|----------------|
| **setting** | The central was told to deploy here (`NOUST_DEPLOY_HERE=containers`). |
| **socket** | Docker's socket is mounted where the program looks for it. |
| **engine** | The engine answers, speaks API 1.41 or later (Synology's Docker 24.0 is API 1.43 and the 29.x of UGREEN is API 1.52: both are inside that) and has Compose v2. |
| **group** | The account the central runs as may open the socket. If not, the line names the group to give it: `group_add` with that number. |
| **apps folder** | The folder of the applications is **the same folder** inside the container and on the machine. Noust writes a file with a random name in it and starts a throw-away container, from the machine's side, that has to read it back: not a guess. A path that exists inside the container and not outside is found here, before a stack mounts an empty folder. |
| **data folder** | The same for Noust's own data, which copies of volumes and database engines in containers are handed paths of. Deploying works without it, so this one is a warning. |

The command exits with 1 when a check fails and with 0 when none does, and `--json` prints the
same report the console reads (`POST /api/central/container-host/check`, for an administrator). A check that
could not be made because an earlier one failed says **not checked yet**, never passed.

The folder check starts a small container from the central's own image, which the machine has
because it is running, so it needs no network. If that is not possible it uses the image of Noust's
helpers and pulls it once.

## Reaching what you deploy

There is no site and no certificate of Noust's here: the NAS keeps ports 80 and 443 for its own
interface (Synology, UGREEN and QNAP all do), and the central has no web server to put in front of
an application. An application is published on **a port of the machine** instead:

- the first free one from **8081**, or the one you ask for with `--port`;
- it is reached at `http://<the NAS>:<port>`, where the name is the one the NAS's own Docker has
  (`docker info --format '{{.Name}}'`). The console shows the link, `noust status <name>` prints
  it as **Address**, and `GET /api/apps/<name>/address` says it to a script;
- a Compose stack publishes what its own document says (a stack that brings its own file is not
  rewritten): a port written as `127.0.0.1:8080:80` in it is reachable from the NAS itself and
  not from your network, which is what it says.

The central cannot see which ports the NAS itself uses (its sockets are its own), so a port that
is taken is found when Docker refuses it: the deployment says which port and proposes another
(*"Docker could not open port 8081 of this machine: something else uses it. Try 8082: create the
application again with `--port`..."*).

For a name and HTTPS, point the NAS's own reverse proxy (Synology's Login Portal, UGOS's, QNAP's) at
`http://localhost:<port>` of the application, as you would for any container on it; its certificate is
the NAS's. A tunnel out (Cloudflare) with its connector in a container is not part of this version.

## What runs on a schedule

A NAS's central has no systemd, so no timers. The schedules you make here (the copies of an application,
which carry the databases of a stack, the copies of its volumes, and the retention sweep every
copy brings along) are kept as rows like anywhere else, and **the central runs them itself**, from a thread
of its own process that looks every half minute and runs the same `noust` commands a timer would. Nothing
is written for systemd and nothing is listed twice: the rows are the one list, and a timer or the central
is what runs it.

```bash
docker exec -it noust noust central schedule status       # what is scheduled, when it runs next, how the last run went
docker exec -it noust noust central schedule status --json   # the same, for a script (and the console: GET /api/central/schedule)
```

A timer is persistent: after a reboot it runs what came due while the machine was off. So does this. The
central remembers when each task last ran (`scheduler.json` in its state folder, private to its account) and,
when it starts again, **runs what came due while it was not running, once however many runs that was** (a
central that was off for a month does not owe a month of copies), and says so in its log and in `schedule
status`: *"... missed 2 runs while the central was not running, the last at 2026-11-12 02:00: it runs now,
once"*. A schedule created today does not owe the days before it existed.

It reads the calendar language of systemd as far as Noust accepts it: weekdays (`Mon`, `Mon,Wed`, `Mon..Fri`),
a date (`*-*-01`, `2027-01-15`, `*-*-1,15`) and a time (`02:00`, `*-*-* */4:15`), with `*`, numbers, lists,
ranges and steps, in the time zone of the container (`TZ`). What it does not understand it says, naming the
part, and never runs at a guess. The tasks run one after another; a copy that takes hours delays the next.

The checks for new image versions and a cleanup of Docker are not run by this scheduler in this version.

## What is not here

Native applications (Node.js, Python, PHP and the rest of the types), domains and certificates of Noust,
preview environments, an application's scheduled tasks and the zero-downtime relay are not offered on a
central that deploys containers: they need systemd or a web server on the machine. The console does not
show them, and the commands refuse them in the words *"... not available here, which deploys containers
and nothing else"*.

## Turning it off

Take `NOUST_DEPLOY_HERE` out of the compose file and recreate the container. Noust never copies the
value of the environment into its own configuration file, so there is nothing left behind that keeps
it on. The applications you already deployed keep running in Docker; the central stops managing
them until you turn it on again, and it can be turned on again without redeploying anything.

`central.deploy_here` also exists as a setting (`noust config set central.deploy_here containers`),
for a hub on a VPS where you have a shell. The console refuses to turn it on.
