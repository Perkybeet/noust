# Searching the logs

The logs of an application are in the journal (its unit says `StandardOutput=journal`) or, for a Docker
Compose stack, in Docker. Reading one means choosing one application and paging through it. This page is
about the question you ask after something went wrong: *where did this error appear, in which of my
applications, around what time?*

```bash
noust log search timeout "connection refused" --not healthcheck --since 2h
noust log search 'level:err' --app shop.example.com --since 1d
noust log search 'redis OR memcached timeout' --since 30m --json
noust fleet logs error --since 1h            # every server of a central at once
```

In the console it is the log search of the application list and of the fleet; the API is
`GET /api/logs/search` and `GET /api/fleet/logs` ([api.md](api.md)).

## The query

| You write | It means |
|---|---|
| `timeout redis` | Lines that have **both** words (case does not matter). |
| `"connection refused"` | That phrase. |
| `-healthcheck` (`--not healthcheck` on the command line) | Not lines that have it. |
| `redis timeout OR nginx 502` | Lines that have all the words of one side **or** of the other. |
| `app:shop.example.com` | Only that application (a part of the name is enough). |
| `unit:sshd.service` | Add a system unit to the search. Only for someone who sees the whole server. |
| `level:err` | That level or worse (`emerg alert crit err warning notice info debug`). |

Fields are global to the query, not to one side of an `OR`. A query has at most twelve words. The window is the
last hour unless `--since`/`--until` (or `since`/`until`) say otherwise: epoch seconds, a time ago (`15m`, `2h`,
`7d`, `1w`) or a date (`2026-10-16`, `2026-10-16T08:30`, UTC), up to 400 days.

## What it reads, and what it shows

It runs on the server, over `journalctl` and `docker logs` as argv through the command runner, and reads a bounded
amount: the window, a filter on the units of the applications you can see and the first word every way of matching
has (as journalctl's `--grep`) narrow what the journal hands over, at most 50 000 entries are examined, and the answer
says when that was reached (`truncated`) so you know older matches may exist. A stack's containers are read the same
way (the last 5 000 lines of each); they have no levels, so `level:` leaves them out and says so.

- **Masked.** Colour codes are stripped, and the secrets the application holds as variables and anything shaped like a
  credential (a token, a `password=` assignment, a private key, a password in a URL) are replaced before a line leaves,
  as in every other log Noust shows. The words are looked for in what is shown, so a search cannot be used to find out
  whether a hidden secret appears.
- **Yours only.** It searches the applications you can see: a member of a team searches that team's applications and
  never another's. A system unit can be named only by someone who sees the whole server.
- **Another application's unit is never shown**, even one whose name starts the same (`shop-example-com-au` is not
  `shop-example-com`).

## A central

`noust fleet logs` (and `/api/fleet/logs`) asks every server's own search with the same words, labels each line with
its server and the page of the application there, and orders them newest first. A server that cannot search, or
stopped at its limit, is a row of its own after the lines; one that is down or too old to offer the search is said so
with its own words and the rest still answer. A central searches nothing itself.

## How long they are kept

Searching only reaches back as far as the journal keeps. By default journald keeps entries until they use a tenth of the
disk, which can be a week on a busy server and a year on a quiet one, and *nothing* across a reboot when the journal is
volatile (there is no `/var/log/journal`). Set it as numbers:

```bash
noust log retention show
noust log retention set --days 30 --max-use 2048 --persistent
noust log retention clear
```

That writes `/etc/systemd/journald.conf.d/50-noust-retention.conf` (`MaxRetentionSec`, `SystemMaxUse` and, with
`--persistent`, `Storage=persistent`) and restarts journald, which keeps what it has. A file of that name that Noust
did not write is never overwritten or removed, and `clear` removes the file Noust wrote and nothing else. For a longer
life than the server's disk, ship the logs out with [`noust logdrain`](reference-cli.md).
