# Adopting sites that already run

A server that comes from another panel, or from years of hand-written configuration, has sites Noust
never deployed: an nginx or Apache site in front of a systemd unit you wrote, a WordPress on PHP-FPM, a
folder of files. Adopting one registers it as an application so that it is in the list, its state and its
logs are read and its unit is restarted from the same place as the rest, **without rebuilding it and
without changing a byte of it**. A Docker Compose stack that already runs is adopted with
`noust app adopt` ([Compose](compose.md#adopting-a-running-stack)); this page is about everything else.

- [What is on the server](#what-is-on-the-server)
- [Adopting one](#adopting-one)
- [What Noust does to an adopted site, and what it never does](#what-noust-does-and-never-does)
- [Letting go](#letting-go)
- [What it does not do (yet)](#what-it-does-not-do-yet)

## What is on the server

```bash
noust app discover
```

lists the sites in nginx's and Apache's sites directories that Noust did not write and no application
records, and says what each one is. It changes nothing: it reads the site files with the same parser as the
site editor, and asks `ss` and `docker ps` who holds the local ports each site hands requests to.

| Kind | What the site is | What the application gets |
|---|---|---|
| `proxy` | The web server in front of a local address (`proxy_pass http://127.0.0.1:8000`) | The unit that listens on that port, found from its process's control group, as the application's unit. |
| `php` | `fastcgi_pass` to PHP-FPM, with a document root | The site and its directory. No unit: PHP runs in the pool every PHP site shares, which Noust never restarts. |
| `static` | A document root and nothing behind it | The site and its directory. |

A site is listed with the reason when it cannot be adopted: it only redirects, the web server would not read
it, nothing listens on the port it hands requests to, the port is a container's (adopt the stack instead) or
another application's, or its file name is not one Noust can record (letters, digits, `-` and `.`).

When a site hands requests to more than one local address, the one behind `/` decides and the others are
named as staying outside the application.

## Adopting one

```bash
noust --dry-run app adopt-site blog.example.com    # what it would record; changes nothing
noust app adopt-site legacy.example.com            # shows the same, asks, adopts
noust app adopt-site legacy.example.com --unit legacy-api --path /srv/legacy
```

| Option | Meaning |
|---|---|
| `--site FILE` | The site file, when more than one answers on the domain. |
| `--unit NAME` | The unit the site is in front of, when nothing listens now or the listener is not it. |
| `--path DIR` | The directory of its files: the unit's `WorkingDirectory=`, else the site's `root`, when left out. |
| `-y`, `--yes` | Adopt without asking. `--json` prints what was adopted. |

In the console it is **New application > Already running > A site on nginx or Apache**: the sites found on the
server are listed to choose from, the preview comes first and says what is kept as it is, and adopting is the
second press.

Over the API: `GET /api/apps/native-sites` (`apps.manage`) lists them, `POST /api/apps/adopt-site`
(`apps.manage`, sudo mode) takes `domain`, `site`, `unit`, `path` and `preview`, and
`GET /api/apps/{domain}/adoption` (`apps.read`) says what an adopted application is.

The unit is accepted only when it is a **service you wrote by hand in `/etc/systemd/system`**: not Noust's
own, not one an application of Noust's records, not one the system ships (`nginx`, `ssh`, `docker`,
`php8.3-fpm`), not a template. Adopting lets Noust start and stop it, so it is never the web server's or the
machine's. A unit can run one adopted site.

Adopting writes the store and nothing else: the site, the unit, the directory and the certificate are not
touched, and no command that changes anything is run. The application is recorded as type `adopted`, in place,
in your directory, with the site's file name when it is not the domain's (so Noust resolves the domain to it,
as for an adopted stack).

## What Noust does and never does

| Does | Never does |
|---|---|
| Lists it with its real state: the unit's, and whether the port answers. | Build, update, roll back, rebuild or deploy it: there is no deployer for it. `noust update` says so and touches nothing. |
| Starts, stops and restarts its unit, and reads its journal. | Rewrite, enable, disable, limit, harden, tune or delete the unit. These are refused where units are written, whoever asks, with the reason. |
| Shows the unit's CPU and memory, and judges it down when the unit is, like any application. | Write its environment: it is whatever your unit loads. A `.env` Noust made in a web root would be read by anyone who asks for it. |
| Backs up its directory, from where it is. | Rewrite your site, or change its domains or certificate: those go through a deployer. |
| Keeps your site, unit and files out of every operation that removes or replaces. | Change the account it runs as: the unit says `User=`. |

The unit is not marked as Noust's: it keeps no `# Generated by Noust` line, and the services page lists it as a
unit Noust does not manage. The only thing that lets Noust restart it is the adoption record, and only for
`start`, `stop`, `restart`, its state and its logs.

## Letting go

```bash
noust delete legacy.example.com
```

forgets the application: its record, the backup schedules Noust made for it and the names and policy it holds
elsewhere. **Nothing it is made of is removed**: the unit keeps running, and the site, the certificate and the
files stay where they are (`--remove-adopted-directory` is refused). The command says what it kept. You can
adopt the site again, or create it from its source with `noust create`.

## What it does not do (yet)

- **One unit per site.** A site that hands requests to several backends is adopted for the one behind `/`; the
  others are named as staying outside it.
- **No history.** An adopted site has no deployments, so nothing is rolled back and there is no build to read.
- **No PHP-FPM pool control.** A PHP site shows its logs and its files; restarting PHP is `systemctl restart
  php8.3-fpm`, which restarts every PHP site.
- **No containers outside Compose.** A site in front of a container that is not a Compose stack is listed with
  the reason; adopt the stack with `noust app adopt` if it is one.
- **Another server.** An adopted site belongs to the server it is on. Adopting is per server; a central lists
  the applications of its nodes, each adopted on its own.
