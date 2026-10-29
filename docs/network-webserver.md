# Moving to the other web server

Part of [The network of an application and of the server](network.md).

nginx or Apache: `webserver` in the configuration is the one **new** applications are put behind, and
every application keeps the one it was deployed behind. Changing the setting moves none of them, so it
is refused while applications or sites are still behind the other server. This page is how they get
there.

```bash
noust webserver status                       # which holds ports 80 and 443, and what is behind each
noust webserver plan --to apache --all       # what moving everything would do; changes nothing
noust webserver migrate --to apache --all    # does it, after asking
noust config set webserver apache            # now that nothing is behind nginx
```

## One pair of ports

Ports 80 and 443 belong to the web server that is running. That decides what a move is:

- **The server you move to is not running** (the usual case: it is installed and stopped, because nginx
  holds the ports). The move is a **handover**: the new server tests every site, nginx stops, Apache
  starts, and every site is asked for through Apache. It takes **every** site at once, because moving
  some would leave the others without a web server; asking for some is refused, naming the command
  that moves all of them.
- **The server you move to is already serving** (a machine you set up with both, or one an earlier
  move left a site on). A site moves on its own: `noust webserver migrate --to apache shop.example.com`.

The plan says which one it is. A handover leaves every site unreachable for the seconds the swap
takes, and nothing is final until all of them answer.

## The plan

```bash
noust webserver plan --to apache --all
noust webserver plan --to apache --all --show shop.example.com   # the translated site in full
noust webserver plan --to apache --all --json
```

Each site of the old server is **translated** to the other's configuration. The plan shows, per site,
where its file would be, the Apache modules it needs, and:

| Finding | What it means | What you do |
|---|---|---|
| A line **with no equivalent** | The new site will not do what that line did (a `sub_filter`, an `include` of a file, a variable Apache cannot read). The line is named with its number. | Read it. `--accept-losses` goes ahead anyway. |
| A **protection** that is lost | Something kept visitors out or slowed them down and the new server has nothing for it: a rate limit, forward authentication, a firewall, an address rule that cannot be carried over. | Put another protection in front of the site first, or leave it where it is. `--accept-losses` goes ahead. |
| A note | Carried over in another form, or a setting of the old server that changes no answer (buffer sizes, `gzip_vary`). | Nothing. |
| A **blocker** | It cannot move. | The plan says why and how to fix it. |

What is translated: the names, TLS (certificates, protocols, ciphers), the redirect to HTTPS and the
certificate authority's directory beside it, a proxy per path with its timeouts and WebSocket,
static files with a single-page fallback, the PHP handler, headers, compression, caching, refusals
(hidden files, dependency manifests), rewrites and redirects, a password and an address list, the
visitor's address behind a proxy, and the log in the format the traffic page reads. The new site is
Noust's like any other: the next deploy writes it again from the templates of the new server.

What stops a site:

- **A site you wrote.** Noust never rewrites it. It moves only when you already wrote its equivalent
  for the new server (the plan names the path): both files are left as they are, the old one only
  disabled, and the application moves.
- A **Docker Compose** application, which only nginx serves.
- **Zero-downtime mode**, which only nginx supports: `noust app zero-downtime shop.example.com off`.
- The public name of the **console** (`noust console unexpose`) and of the **status page**
  (`noust probe status-page unpublish`), which Noust sets up behind nginx only.
- In a handover, **any site of the old server that is not moving** and would stop being served:
  the plan names each.
- The new server **not being installed**: the plan gives the command that installs it.

## Moving

```bash
noust webserver migrate --to apache --all           # shows the plan, asks
noust webserver migrate --to apache --all --yes     # a script: does not ask
noust webserver migrate --to apache --all --yes --accept-losses
noust --dry-run webserver migrate --to apache --all --yes   # the rehearsal
```

In order:

1. The Apache modules the sites need are enabled, and **the new server tests every translated site**
   inside its own live configuration. If it refuses one, you get its own message and nothing was
   changed.
2. The new files are written beside the old ones, with the accounts of a password-protected site.
3. In a handover the old server stops and the new one starts; otherwise the new one reloads.
4. **Every site is asked for through the new server**, with the application's own health check (its
   path and the statuses it expects), over HTTP and, for a site with a certificate, over TLS with the
   name in the handshake.
5. Only then the store says where each site is, the old files are removed (an operator's is only
   disabled), and the old server no longer starts at boot.

If the new server will not start, or one site does not answer, **everything is put back**: the files,
the links, the old server running and starting at boot, the records. What could not be put back is
said, with where it is. The job's log and `sites.webserver` in the audit trail record the attempt.

Going back is the same command with `--to nginx`. The setting of new applications is yours:
`noust config set webserver apache` works once nothing is behind nginx.

## The API and the console

| | |
|---|---|
| `GET /api/webserver/status` | The setting, and each server's state and counts. |
| `GET /api/webserver/migration/plan?to=apache&all=true` | The plan. Repeat `domain=` for some sites; `text=true` adds the translated sites. |
| `POST /api/webserver/migrate` | Queues the move as a job. Without `confirm` the answer is the confirmation to ask for; without `accept_losses`, what the new sites will not do. |
| `GET /api/apps/{domain}/webserver/plan`, `POST /api/apps/{domain}/webserver/migrate` | The same for one application. |

Every route needs the whole server (a team's role cannot) and the moving ones need sudo mode.
Refusals are `409 webserver_move`, and every sentence carries a code the console translates.
