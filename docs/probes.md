# Probes and the status page

The monitor knows an application is up from the inside: its unit runs, its health check passes on the
loopback. A visitor asks something else. Does the name resolve? Does the certificate hold? Does the
proxy answer? Does the page say what it should? A **probe** asks that, on a clock, from where Noust
runs. Run Noust on a small server of its own, or on a central that manages the others, and the probes
are a point of view outside the machines they watch; run them on the machine itself and they still
catch the expired certificate and the firewall rule that only the loopback survives.

A probe that stops answering is told through the [notification channels](notifications.md) once
(`probe_failed`) and told again when it answers (`probe_recovered`). The probes you mark public are the
components of a **status page** you can put on a name of its own.

Everything here is in the console under Settings > Probes and status page and in the command line as `noust probe ...`;
both are views of the same probes, the monitor asks them (`noust monitor enable`), and the API is
`/api/probes` ([api.md](api.md)).

## A probe

```bash
noust probe set shop --url https://shop.example.com/health --public --title "Shop"
noust probe set blog --url https://blog.example.com/ --expect-text "Latest posts" --interval 120
noust probe list
noust probe run shop              # ask it now; exits 1 on a bad answer
noust probe history shop          # its incidents
noust probe remove blog
```

| Setting | Default | |
|---|---|---|
| `--url` | | `http://` or `https://`. No user or password in it, and no secret: the address is shown in lists. |
| `--method` | `GET` | `GET` or `HEAD`. |
| `--expect-status` | `200-399` | The statuses that mean well: a range, or `200,204`. |
| `--expect-text` | | A word the page must contain, looked for in its first 64 kB (GET only). A site that answers 200 with its maintenance page is down. |
| `--interval` | `60` | Seconds between two asks, 30 to 3600. The monitor looks every scan (a minute by default), so it is the least. |
| `--timeout` | `10` | Seconds an answer may take, below the interval. |
| `--fail-after` | `3` | Bad answers in a row that make it **down**. A restart or a deploy is two bad answers, not an outage. |
| `--public` / `--title` | off | Show it on the status page, under that name. |

An answer is *good* when the status is one the probe accepts and, if it names one, the word is in the page. A
refused connection, a name that does not resolve, a certificate that does not verify and a timeout are bad
answers like a 503, each with one line saying which. The line never quotes the address (its query may be a
key), and a page is never stored.

**It is asked the way a notification is sent.** The address is resolved and every address it resolves to
is judged, at every redirect too, so a name that points into the machine's own network (or a redirect to
the cloud metadata service) is refused. To probe something on your own network, list its host under
`notifications.allow_private_hosts`.

## What happens when one goes down

| State | Meaning |
|---|---|
| `waiting` | Never answered yet. |
| `up` | The last answer was good. |
| `down` | `--fail-after` bad answers in a row. Told once; the incident starts at the first bad answer, not the third. |

The first good answer after *down* ends the incident and says how long it lasted. Changing a probe's
address, or switching it off, forgets what the old one had decided and ends its open incident. Raw
answers are kept a week; one row per probe and day (what uptime is read from) is kept past a year, so
"99.94% over ninety days" does not depend on rows about to be purged.

## The status page

```bash
noust probe status-page set --enable --title "Acme status"
noust probe status-page publish status.example.com     # an nginx site, a certificate, the page
noust probe status-page show
noust probe status-page unpublish                      # removes exactly what publish made
```

The page is **a directory of static files**, `index.html` and `status.json`, that the monitor rewrites after
every answer that changed anything (and at least every five minutes). It lives in `/var/lib/noust/status-page`
unless `status_page.directory` says another. Nothing of the console is public for it and there is no route to
attack: a request is a file nginx reads. Put it on a server that is not the one it reports about, or it is
gone when the server is.

`publish` makes an nginx site on a name of its own (it refuses an application's name and a site Noust did not
make), obtains a certificate, and remembers what it made so that `unpublish` removes that and nothing else. To
serve the files with your own web server, point it at the directory and skip `publish`.

What a visitor sees is what you chose to show, and no more: each component's **title** (never its address),
whether it answers, how often it did over ninety days, a bar for each day, and when it was last down and for how
long. Never the reason of a failure, a latency or a count of checks. The page is one self-contained document with
no script, no external font and no image, with a `Content-Security-Policy` that allows nothing else, light and
dark by the visitor's preference, and in the language of `notifications.language`. A title with markup in it is
text. `status.json` is the same for a program.

| Setting | Default | |
|---|---|---|
| `status_page.enabled` | `false` | The monitor writes the files. |
| `status_page.directory` | `/var/lib/noust/status-page` | An absolute path. |
| `status_page.title` | `Status` | At most 80 characters. |

## The API

| Route | |
|---|---|
| `GET /api/probes` | The probes with where each stands, its uptime over a day, a week, a month and ninety days, and the status page |
| `PUT`, `DELETE /api/probes/{name}` | Create or replace, and remove |
| `POST /api/probes/{name}/check` | Ask it now |
| `GET /api/probes/{name}/history` | Its incidents and its latest answers |
| `GET`, `PUT /api/probes/status-page` | The page's settings (sudo mode to change) |
| `POST /api/probes/status-page/write` | Write the files now |
| `POST /api/probes/status-page/publish`, `DELETE /api/probes/status-page/site` | Put the page on a name, take it off (jobs; they change how the host is reached) |
