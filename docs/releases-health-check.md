# Configuring the health check

Part of [Releases](releases.md).

An application that has a health endpoint, needs longer to start, or must not count a 404 as
up can say so. Three settings, each with the default of [the health gate](how-releases-work.md#the-health-gate) when unset:

| Setting | Accepts | Default |
|---|---|---|
| Path | A path on the application: begins with a single `/`, printable ASCII, no spaces. A query string is fine (`/health?deep=1`); a scheme or a host is refused, because the probe always asks the application itself on `127.0.0.1`. | `/` |
| Expected statuses | Statuses and inclusive ranges from 100 to 599, separated by commas: `200`, `200-399`, `200,204`, `200-299,301`. Only these pass; a redirect is not followed, so a `301` passes only if it is listed. | any status below 500 |
| Timeout | Seconds from 5 to 600 the release gets to answer, wall-clock: one probe every 2 seconds until they run out. | 30 |

```bash
noust app health shop.example.com                                  # the current settings, defaults marked
noust app health shop.example.com --path /healthz --expect 200-299
noust app health shop.example.com --timeout 120                    # options not named keep their value
noust app health shop.example.com --reset                          # back to every default
```

Over the API, `PATCH /api/apps/{domain}/health` sets everything together (a field left out or
null goes back to its default) and needs sudo mode, and `GET /api/apps/{domain}/health` reads
it, defaults marked. See [api.md](api.md). The values are validated where they are stored, so the
CLI, the console and the API refuse the same input with the same message. Nothing restarts: the
next activation uses the new settings. A redeploy keeps them. A static site has no settings to
change: its check is its files.

## The request, a command, and the timing

Since 4.0 the check can say more about the request, can be a command instead of a request, and
has the timing of a container's health check. Each option not named keeps its value.

| Option | Accepts | Default |
|---|---|---|
| `--method` | `GET`, `HEAD`, `OPTIONS` or `POST`, for an endpoint that only answers one of them. No body is sent. | `GET` |
| `--scheme` | `http` or `https`: how the probe speaks to the application. It connects to `127.0.0.1` either way, and an `https` probe does not verify the certificate, because an address is not what a certificate names. | `http` |
| `--host` | The `Host` header to present, for an application that answers one name and refuses the address (`shop.example.com`). | none |
| `--port` | A port on `127.0.0.1` to probe instead of the application's own. | the application's |
| `--command`, `--service` | A command line, split like a POSIX shell would and run without one, **as the application** in its sandbox (`pg_isready`, `php artisan health:check`); for a Compose stack `--service` names the service it runs in, by `docker exec`. The application is up when the command exits 0. It replaces the request. | a request |
| `--interval` | Seconds between two probes, 1 to 60. | 2 |
| `--retries` | The most probes after the start period, 1 to 100. The timeout still ends the wait first if it comes first. | as many as fit in the timeout |
| `--start-period` | Seconds added to the wait during which a failed probe does not count against the timeout or the retries, 0 to 600, for an application that takes a while to open its port. The first probe that passes ends the wait, inside it or after it. | 0 |

```bash
noust app health shop.example.com --method HEAD --path /healthz --interval 5 --retries 6
noust app health shop.example.com --command "test -s run/ready" --start-period 20
noust app health api.example.com --scheme https --host api.example.com --port 8443
```

A failed gate reports the probes it made, and for a command check the end of the command's own
output, verbatim. The same check is what a new copy of the web process answers on its own port
([runtime.md](runtime.md#copies-of-the-web-process)) and what `noust diagnose` asks.
