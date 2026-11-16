# Zero-downtime updates (the relay)

Part of [Docker Compose](compose.md).

`docker compose up -d` recreates a web service by stopping its container and starting the new
one: for as long as the new one takes to listen, nginx has nobody on the port. For a backend
that takes fifteen seconds to start, that is fifteen seconds of `502`. With the relay on, Noust
recreates each web service while a twin of it serves.

Measured in the project's integration harness, under a request every 200 ms through nginx: a
plain recreate failed 72 of 94 requests; the relay failed 0 of 110. On a real Next.js and
production NestJS stack, an update that also applied a Prisma migration failed 0 of 316 to 329
requests, and an update whose backend could not start was refused at the relay with 0 of 687.

```bash
noust app zero-downtime shop.example.com on               # the mode; --drain SECONDS (0 to 300, default 10)
noust app zero-downtime shop.example.com                  # the mode, and what is missing for it
noust app zero-downtime shop.example.com off
```

The same command and endpoint (`GET|PUT /api/apps/{domain}/zero-downtime`) as for blue/green
applications; for a stack it means the relay. Turning it on restarts nothing: the next update
is the first one relayed.

## What it needs

- **nginx as the web server.** A stack is only ever served through nginx.
- **Every web service the site reaches publishes a TCP port at a fixed host port**:
  `"127.0.0.1:3000:3000"` or `"3000:3000"`. A port Docker picks (`"3000"`) cannot be reached at
  a known address.
- **The site reaches each service through a servers file** of Noust's:
  `/etc/nginx/noust-upstreams/<app>/<service>.servers`, where `<app>` is the domain with dots
  as dashes (`shop-example-com`). It holds only lines of the form `server 127.0.0.1:3000;`: the
  service's own port normally, the relay's while one serves. The site includes it inside an
  `upstream` block.
  - **A site Noust wrote** is rendered with those blocks when you turn the mode on, named
    `noust_bg_<app>_<service>`, each with `keepalive 64;`. Nothing for you to do.
  - **A site you wrote** includes the file itself, inside the upstream block that proxies to
    the service, keeping your names. Noust never edits it.

## Including the servers file in your own site

Take the upstream block that proxies to the service and replace its `server` line with the
`include`:

```nginx
# before
upstream nestjs_upstream {
    server 127.0.0.1:3000;
    keepalive 64;
}

# after
upstream nestjs_upstream {
    include /etc/nginx/noust-upstreams/shop-example-com/backend.servers;
    keepalive 64;
}
```

The order of operations, because the file has to exist for nginx to accept the include:

1. Run `noust app zero-downtime shop.example.com on`. It writes the servers files first, each naming
   its service's own port, then checks your site and, for every service it does not include,
   refuses and prints the exact line that is missing and which file it goes in. It changes
   nothing in your site.
2. Add the line, then `nginx -t && systemctl reload nginx`. The site behaves exactly as before:
   the file names the same port you had.
3. Run `noust app zero-downtime shop.example.com on` again. It finds the include and turns the mode
   on.

`noust app zero-downtime shop.example.com` shows the same refusal at any time, with `eligible` and
`reason` in `GET /api/apps/{domain}/zero-downtime`.

## What an update does

The web services the site reaches through a servers file are relayed one at a time, in
`depends_on` order: a backend before the front that calls it, so a new front never talks to an
old backend. For each service:

1. `docker compose run -d --no-deps --use-aliases --name <project>-<service>-relay --publish
   127.0.0.1:<port>:<container port> <service>` starts the new image on a free loopback port
   from 20000 to 29999. `--use-aliases` makes the relay answer to the service's name inside the
   stack's network too.
2. The **health gate** asks the relay on that port (the application's health path for the
   service the site serves `/` from, the default path for the others). If it does not answer,
   the update stops here: the service's own container was never touched, and an image that does
   not start is exactly what this proves. The error has the probes and the relay's last 40 lines.
3. The servers file names the relay; `nginx -t` runs, nginx reloads, and the old container
   finishes its requests for the drain time (`--drain`, 10 seconds by default).
4. `docker compose up -d --no-deps --force-recreate <service>` recreates the service's own
   container, and the gate asks it on its own port.
5. The servers file names the service again, nginx reloads, the relay drains, and
   `docker stop -t 20` stops it and `docker rm -f` removes it.

Then the rest of the stack (databases, queues, workers, and any web service the site does not
reach through a servers file) is brought up with `docker compose up -d --remove-orphans`, as
before.

When something goes wrong, the failure leaves a site that still answers:

- **The relay does not answer:** the service is untouched and the update fails. Nothing moved.
- **The recreated container does not answer:** the relay, with the new image, keeps serving, and
  the error says which relay and on which port. See why with `docker compose logs <service>`.
- **nginx refuses the switch:** that service is recreated with a cut and the log says why. The
  same when no port is free for a relay.
- **An update that was killed midway** can leave a relay serving. The next update resolves it
  before anything is rebuilt: when the servers file still names a relay and the service's own
  container answers, traffic goes back to it and the relay is removed; when the service does not
  answer, nothing is touched and the error says why. `noust setup doctor` lists such leftovers
  under "Docker Compose relays", with the command that resolves them, and so does turning the
  mode off.

Two versions of a relayed service run side by side for a few seconds. A queue consumer inside a
web process, or a job scheduled in-process, may run twice in that window: that is the project's
to tolerate, and the reason the mode is opt-in.

## Turning it off

`noust app zero-downtime shop.example.com off` gives any relay still serving back to its container,
puts the stack back on plain recreates and, for a site Noust wrote, renders it again to proxy to
the ports directly and removes its servers files. A site you wrote keeps its `include` lines and
the files stay, each naming its service's own port, so nginx keeps loading it.
