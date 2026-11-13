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
- the stacks the NAS already runs, brought to Noust without touching them,
- a way to reach what you deploy by a name: the NAS's own reverse proxy (Noust says what to paste) or a
  Cloudflare tunnel that dials out and opens nothing,
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

## Bringing the stacks you already have

The first thing to do on a NAS is to take the stacks it already runs under Noust: Docker lists them
(**Server > Docker**, *Bring to Noust*) and `noust app adopt` does the same from a command line. Nothing is
recreated, restarted or changed, and the compose file is not rewritten. Two things are specific to a central
that deploys here:

- **The central has to see the stack's folder, at the same path.** It runs `docker compose` inside its own
  container, so it reads the compose file and the `.env` there. Mount the folder that holds your stacks
  next to the one that holds Noust's own data, with the same path on both sides, and recreate the central:

  ```yaml
      volumes:
        - /volume1/docker/noust:/volume1/docker/noust
        - /volume1/docker/stacks:/volume1/docker/stacks      # where your stacks live
  ```

  A stack in a folder the central does not see is refused before anything is tried, with the line to add
  for that folder. The central runs as user 10001: the compose file, the `.env` and the folder must be
  readable by it (a group, an ACL, or the owner), and it says which one is not.
- **No unit is written, and no site is looked for.** What keeps the stack alive after the NAS restarts is
  Docker, by the restart policy of its containers, and what reaches it is the port its own file publishes.
  A container whose policy would not bring it back (`no`, `on-failure`, or stopped under `unless-stopped`) is
  said **before** you accept, with the line that fixes it (`restart: unless-stopped`); Noust does not
  change your file, because it is yours. A stack that publishes on `127.0.0.1` only is said to be reachable
  from the NAS and not from your network.

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

### A name and HTTPS: the NAS's own reverse proxy

For a name and HTTPS, put a rule in the NAS's own reverse proxy: the certificate and the name are the
NAS's, and Noust touches none of it. **Noust says what to put and you paste it.** The console shows it
beside the application's address, and a script reads it from the same place the address comes from:

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://<central>/api/apps/<name>/address | jq .proxy_hint
```

```json
{
  "target": "http://127.0.0.1:8081", "scheme": "http", "host": "127.0.0.1", "port": 8081,
  "websocket": true, "read_timeout_seconds": 60,
  "headers": [
    {"name": "Host",              "meaning": "public_host",     "nginx": "$host"},
    {"name": "X-Real-IP",         "meaning": "client_address",  "nginx": "$remote_addr"},
    {"name": "X-Forwarded-For",   "meaning": "client_chain",    "nginx": "$proxy_add_x_forwarded_for"},
    {"name": "X-Forwarded-Proto", "meaning": "public_scheme",   "nginx": "$scheme"},
    {"name": "X-Forwarded-Host",  "meaning": "public_host",     "nginx": "$host"},
    {"name": "X-Forwarded-Port",  "meaning": "public_port",     "nginx": "$server_port"},
    {"name": "Upgrade",           "meaning": "upgrade_request", "nginx": "$http_upgrade"},
    {"name": "Connection",        "meaning": "upgrade",         "nginx": "\"upgrade\""}
  ]
}
```

- **target** is where the NAS's proxy sends the request. A proxy that runs on the NAS reaches a port Docker
  publishes through `127.0.0.1`; a stack whose own file binds its port to one address of the machine
  (`192.168.1.160:8090:80`) is reached at that address, and the hint says it. It is `null` where a site
  of Noust's serves the application (nothing to paste) and for what serves no web.
- **headers** are exactly what a site of Noust's passes to an application (a test holds the two to
  each other), with `meaning` for the proxies that have a setting per header and `nginx` for those that take
  nginx's own words.
- **websocket** is always true: Noust does not know which application uses one, and a site of its own
  lets them through whatever the application is. A proxy that cuts WebSockets breaks the dashboards of
  half the recipes in a way that looks like the application's fault.
- **read_timeout_seconds** is what a site of Noust's waits for an answer. Many NAS proxies wait 60 seconds
  and a long request (an export, an upload) needs more: raise it where it is a setting.

The steps, in the words of each product. The names of the menus move between versions, so look for the
setting by its name; nothing here is a screenshot of somebody else's screen.

**Synology (DSM 7).** *Control Panel*, *Login Portal*, *Advanced*, *Reverse Proxy*, *Create*. Source:
HTTPS, the name you want, port 443. Destination: **HTTP** (the protocol of the *target*, not HTTPS:
HTTPS on both sides is the usual "400 Bad Request"), `localhost`, the port of the hint. In the rule's
*Custom Header* tab, *Create*, *WebSocket*: it adds `Upgrade` and `Connection`. Raise the timeouts of the rule
if it has them (DSM 7.2 and later) to 600 seconds. Then, in *Control Panel*, *Security*, *Certificate*,
give the name a certificate (Let's Encrypt from the same screen) and tick it for the rule.

**UGREEN (UGOS Pro).** UGOS keeps ports 80 and 443 for its own redirects to its interface (9999 and
9443). The usual way is a proxy that is a container of its own, such as Nginx Proxy Manager below, on
other ports, with the router forwarding to them; to give it 80 and 443 instead, untick the redirects in
*Control Panel*, *Device Connection*, *Portal* first.

**QNAP (QTS, QuTS hero).** QNAP uses 443 and 8080 for its own interface, so a proxy container (Nginx
Proxy Manager below) on other ports is the usual way, as on UGREEN; Caddy on 443 in a stack fails with
"443 in use" for the same reason.

**Nginx Proxy Manager** (a container on any NAS, Unraid and TrueNAS included). *Proxy Hosts*, *Add
Proxy Host*: *Domain Names* the name; *Scheme* `http`; *Forward Hostname / IP* `127.0.0.1` is wrong
from inside NPM's own container (it would be NPM itself): use the NAS's address, or the host-gateway
name, and the port of the hint; tick **Websockets Support** and **Block Common Exploits**. *SSL* tab:
request a certificate and tick *Force SSL*. For the timeouts, the *Advanced* tab takes nginx's own
words, so the `nginx` column of the hint is what to paste:

```nginx
proxy_read_timeout 600s;
proxy_send_timeout 600s;
```

**Caddy.** `app.example.com { reverse_proxy 127.0.0.1:8081 }`: it sets the `Host` and `X-Forwarded-*`
headers and lets WebSockets through by itself; only the timeout needs `transport http { read_timeout 600s }`.

**Any nginx.** The block a site of Noust's writes for the same application:

```nginx
location / {
    proxy_pass http://127.0.0.1:8081;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Port $server_port;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 600s;
}
```

Two traps of every NAS proxy: a name that points at your public address does not answer from inside your
own network unless the router does *hairpin NAT* (use the NAS's own name inside, or a split DNS), and a
certificate for a name needs the proxy, not the application, to hold it.

### A name without opening a port: a Cloudflare tunnel

When the NAS has no address anybody can point a name at (a home or an office behind carrier-grade NAT) or you
would rather not open anything, a tunnel dials **out** to Cloudflare and Cloudflare carries the requests for
your names back through that connection. Noust runs it with the same commands a server uses; what is
different here is only what keeps the connector alive: it is a **container** that Docker restarts by itself
when the NAS restarts, and there is nothing to install.

```bash
docker exec -i noust noust dns provider add cf --kind cloudflare --token-stdin   # a token with Account > Cloudflare Tunnel: Edit and Zone > DNS: Edit
docker exec -it noust noust tunnel create home --account cf
docker exec -it noust noust tunnel route add home shop.example.com --app shop.example.com
```

- The connector is Cloudflare's own image, pinned by **digest** (never `latest`), and runs as the central's
  user with a read-only file system and no capabilities.
- **The token never appears as an environment variable, an argument or in a job.** A variable is part of the
  container's configuration and `docker inspect` prints it to anyone who can use Docker, so the token is a
  private file (mode 0600, the central's user) that the container is given read-only, and the only variable
  names its path (`TUNNEL_TOKEN_FILE`).
- A route for an application goes to **the port Docker published it on** (`http://host.docker.internal:<port>`,
  the machine's gateway), not to a site, because there is none. A stack you brought that publishes on
  `127.0.0.1` only is not reachable that way: publish it on the network in your own file, or give the route the
  address by hand with `--service`.
- **A sealed central does not offer tunnels.** The connector starts by itself when the NAS restarts, with
  nobody to type the passphrase that opens the secrets, so its token cannot live in a sealed store. Either
  leave the central unsealed, or run the tunnel from a server, or do it by hand as below.
- The first start downloads the connector's image from Docker Hub: that is the only request, and it is made
  when you create a tunnel, never before.

By hand, without Noust's help: a stack with `cloudflare/cloudflared` and its token as a Docker secret
(`command: tunnel --no-autoupdate run --token-file /run/secrets/tunnel_token`) and its routes set in the
Cloudflare dashboard.

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

Two more schedules are run the same way, and the same commands a timer would run:

- **The cleanup of the disk and of Docker.** It is the policy a server has (`noust server cleanup-schedule
  set --enable --schedule weekly --action docker-dangling-images --allow-docker`), kept as one row, and
  where there is no timer the central runs `noust server cleanup-schedule run --scheduled` on its calendar.
  It only offers what exists on a machine that has Docker and nothing else: the releases an application
  keeps, the build cache older than a week and the images with no name and no container. There is no
  journal and no package cache here to clean, so they are not offered. Nothing the person did not accept
  is removed: volumes are never part of it.
- **The look for new image versions** (`noust docker images check`). It is a question to the registries
  of your images, so it is **off until you say when**: `noust config set central.image_check weekly`
  (any schedule above, `off` to stop). With the setting empty, which it is by default, nothing is ever
  asked. The result is what the Docker zone shows as "a new version".

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
