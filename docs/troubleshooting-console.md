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
