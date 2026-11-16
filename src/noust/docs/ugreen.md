# Noust on a UGREEN NAS

The App Center of a UGREEN NAS (UGOS Pro) installs Noust as a **central**: one console for all of your
Linux servers, with its data on the NAS. This page is the app of the store. Installing the same central
by hand, with a compose file, is [Install on a UGREEN NAS](CENTRAL.md#install-on-a-ugreen-nas-ugos-pro) and
gives you the one thing this app does not: deploying on the NAS itself ([below](#what-this-app-does-and-does-not-do)).

## Install it

1. Open **App Center**, find **Noust** and press **Install**. Docker is a dependency: if it is not on the
   NAS yet, the App Center installs it with Noust.
2. The form asks for one thing and offers two:
   - **Console port** (required): the port the console opens on, at the address of the NAS. **8443** is the
     usual one.
   - **Who may open the console** (optional): addresses or networks separated by spaces, such as
     `192.0.2.0/24`. Empty is this machine and the private networks, which is what you want at home.
   - **Names of the NAS for the certificate** (optional): the address and names you open the console with,
     such as `192.0.2.10 nas.example.net`. The container cannot see them by itself, and a browser trusts
     a certificate only for the names it was made for.
3. Open **Docker > Container > noust > Log** and copy the line `Access Token: noust_...`. It is shown on the
   first start **only**, and the log carries the certificate's SHA-256 fingerprint beside it.

## Open it

Press the icon. The App Center opens `http://<the NAS>:8442`, because it can only open an application over
plain HTTP, and that port answers with nothing but a redirect to `https://<the NAS>:<the console port>`:
the console itself only speaks HTTPS. The redirect carries no page, no sign-in and no cookie, and the
address you reached the NAS by is kept (`nas.example.net` stays `nas.example.net`). The details, and what
the port answers to every kind of request, are in
[A port that only redirects](CENTRAL.md#a-port-that-only-redirects).

The first time, the browser warns about the certificate: the console made it for itself and nobody else
vouches for it. Compare the fingerprint it shows with the one in the log and continue. Then sign in with the
token: a central with no accounts opens on **Create the first account**, and every account needs a second
factor before the central adds its first server ([First sign-in and accounts](CENTRAL.md#first-sign-in-and-accounts)).
Lost the token: `docker exec -it <container> noust web token --new`, where the first start's log says which
container.

## The certificate

The certificate is self-signed, valid 825 days, and names the NAS's own addresses and the ones you wrote in
**Names of the NAS for the certificate**. Change that setting and the console makes a new one: browsers ask
about the new fingerprint once. To install one of your own (your organisation's, or Let's Encrypt's for a name
of yours) the app has no place to put the files, which is a reason to use the compose file of
[the container guide](CENTRAL.md#the-consoles-certificate) instead.

## Change the port

The console's port is a setting of the app: **Control Panel > About > Applications**, choose Noust, and its
configuration. UGOS warns that it closes the app, and starts it again with the new port. The icon keeps
opening the same fixed port (8442), which follows the console's port by itself.

**UGOS does not check that a port is free.** If another app of the NAS has the one you chose, Noust is left in
**Disabled**: in **Docker > Container** the `noust` container is stuck in *Created*, because Docker could not
bind the port. Choose another port in the app's settings. The same goes for 8442, the port of the redirect: it
is fixed and few apps use it, but if another one does, the icon opens that other app and UGOS says nothing.

## What this app does and does not do

- **It manages your servers.** Each one is added once, over an SSH tunnel that the NAS opens outward and that
  can reach that server's console and nothing else. Nothing is opened on your router.
- **It has no access to the NAS.** It runs as an unprivileged user in a container with no capabilities and a
  read-only root, and it has no Docker socket. That is what was promised to the store, and it is why the app
  **does not deploy on the NAS**: giving a container the socket is giving it root on the machine.
- **To deploy on the NAS** (containers and Compose stacks on its own Docker) use the compose file of
  [Deploy where the central runs](central-deploy-here.md). It is another way to install the same central, with
  the socket mounted on purpose, and it is not the app of the store.
- **It is not a way into the console without TLS.** The port of the icon redirects and serves nothing else.
- **Keep it off the internet**, as the console says of itself: it answers loopback and the private networks
  unless **Who may open the console** says otherwise, and the central only dials out.

## New versions

A new version of Noust arrives through the App Center, as a package that UGREEN reviews, and not by a command
of Docker's: running `docker compose pull` on the container would leave the app in a state the App Center knows
nothing about. The console says so where it would show that command (the package says where it came from in
`NOUST_INSTALLED_FROM`), and it does not offer to update itself.

## Your data

Everything the central keeps is in one Docker volume, `noust-data`, which the App Center creates under the name
of its project (`central_noust-data` in **Docker > Volume**). What happens to the volume when the app is
uninstalled is the App Center's and Docker's to decide, not Noust's: take a copy of the central first
(`docker exec -it <container> noust central backup`, [Look after the central](central-maintain.md#backups)).

## Build the package

The package is in [`packaging/ugreen/`](../packaging/ugreen/project.yaml): `project.yaml`, the compose file, the icon and
`build.sh`. Its version is the release's (`scripts/release.py` keeps it, and `--check` holds it), and the image
is the published one, by its version: the store refuses `latest`.

```bash
UGCLI=/path/to/ugcli-linux-amd64 sh packaging/ugreen/build.sh      # build number 1
UGCLI=/path/to/ugcli-linux-amd64 sh packaging/ugreen/build.sh 2    # the next one for the same version
```

`UGCLI` is the binary UGREEN publishes at [developer.ugnas.com](https://developer.ugnas.com). The script works in
a temporary directory outside the repository (`OUT` names another), exports the image for amd64 and arm64 into
it, and runs `ugcli check` and `ugcli pack` in a Debian 12 container. The `.upk` files land in
`build_dir/pkgs/upk/` under that directory.

The developer signature UGREEN gives you is a file for the NAS you test on (App Center > Settings > developer
settings): it never goes in the repository, and neither does a `.upk`. Each version is packed again and sent to
UGREEN for its review.
