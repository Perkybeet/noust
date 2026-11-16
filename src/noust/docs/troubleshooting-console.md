# The console does not open

The console listens on `127.0.0.1:8080` of the server and, on purpose, nowhere else: it acts as
root, so it is never left open on the network. You reach it through an SSH tunnel from your own
computer.

## Open the tunnel

On your computer, not on the server:

```bash
ssh -N -L 8080:127.0.0.1:8080 root@203.0.113.10
```

Leave it running, and open <http://localhost:8080/login?with=token>. The link opens the sign-in on
the access token. The installer printed this command with your server's address in it.

When the installer could not tell the server's public address (a machine behind NAT, or one whose
only address is private), it says so and leaves a placeholder: use the address you SSH to.

## The tunnel opens and the page does not load

On the server:

```bash
noust web status           # is it running, as a service or in the background
journalctl -u noust-web    # why it is not
noust web enable           # run it as a service that survives reboots
```

If the console's Python packages (FastAPI, Uvicorn, psutil) are missing, install them:

```bash
noust web install
noust web enable
```

## Every request fails with "Too many open files"

The console has run out of file descriptors: it holds as many files and sockets open as its unit
allows, so it cannot open another to answer. The API says it (HTTP 503, `descriptors_exhausted`, "File
descriptors exhausted") instead of a bare error, a central that asks such a server is told that, and the
diagnosis of an application says it instead of "Could not complete this check".

```bash
noust web restart          # frees them
noust health               # "File descriptors": what noust-web and noust-monitor hold, against their limit
noust setup doctor         # names a unit that still has the limit from before 4.3, and how to write it again
```

`noust health` warns from 80% of the limit and counts it as an issue when requests are failing, so a
server is told while a restart is still a choice. The console's and the monitor's units are given 65536
open files since 4.3 (a unit without a limit gets the system's 1024, which is what a central ran out of);
`noust web refresh-unit` and `noust monitor install` write a unit again, and the package's upgrade runs
both. The leak that made a console reach the limit was fixed in 4.2.1: on 4.2.0 the connections of a
worker thread that had ended stayed open until a full garbage collection.

## Port 8080 is taken on your computer

`ssh` says `bind [127.0.0.1]:8080: Address already in use`. Use another local port, and the same
address in the browser:

```bash
ssh -N -L 8081:127.0.0.1:8080 root@203.0.113.10
```

## Without a tunnel

To reach the console at `https://console.kelmoor.dev`, publish it on a name of its own, with a
certificate:

```bash
noust console expose --domain console.kelmoor.dev --allow-ip 198.51.100.0/24
```

It refuses while any account can sign in without a second factor, or when the name does not point
at the server. `--allow-ip` limits who can even see the sign-in page; without it, the second factor
is all that stops a stranger. [Reaching the console](console-start.md#reaching-it) covers it, and
`noust console unexpose` removes exactly what it made.
