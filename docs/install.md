# Install Noust

**Goal:** Noust installed on a server, its console running, and you signed in as its first
administrator.

**Time:** about a minute for the install, a few more to sign in.

**You need:** a server on [a supported Linux](PLATFORMS.md) with at least
[1 GB of memory](requirements.md), root access over SSH, and ports 80 and 443 free.

## Steps

1. **Connect to the server as root.**

   ```bash
   ssh root@203.0.113.10
   ```

   Noust needs root: it installs packages, writes to `/etc` and `/var`, and manages systemd.

2. **Download the installer and check it.** The installer is published with every release, next
   to a list of checksums.

   ```bash
   curl -fsSLO https://github.com/Perkybeet/noust/releases/latest/download/install.sh
   curl -fsSLO https://github.com/Perkybeet/noust/releases/latest/download/SHA256SUMS
   sha256sum -c --ignore-missing SHA256SUMS
   ```

   The last command must print `install.sh: OK`. [Platforms](PLATFORMS.md#verifying-the-installer)
   explains how to check the signature of the checksums as well.

3. **Run it.**

   ```bash
   sh install.sh
   ```

   It reads the distribution and the processor, and refuses one that is not on the list. It adds the
   Noust package repository after checking its signing key, installs the `noust` package, prepares
   the server (`noust setup init`: nginx, certbot, git, Node.js and Noust's directories) and starts
   the console as a service (`noust web enable`). To see what it would do without doing it, run
   `sh install.sh --dry-run`.

4. **Open the console from your computer.** The console listens on `127.0.0.1:8080` of the server
   and nowhere else, so you reach it through an SSH tunnel. The installer prints the exact command,
   with your server's address in it. On your computer:

   ```bash
   ssh -N -L 8080:127.0.0.1:8080 root@203.0.113.10
   ```

   Leave it running and open <http://127.0.0.1:8080/login?with=token> in your browser.

5. **Sign in with the access token, and create your account.** Paste the access token (the
   installer printed it once, in a box) and press **Sign in**. The console then asks for the first
   administrator: a username and a password. A second factor is offered next; you can set it up
   later, but do it before you publish the console or add a second server.

## What you should see

The installer ends with these lines, your server's address in them:

```text
Noust is installed.

The console listens on 127.0.0.1:8080 of this server, and on purpose nowhere else.

To open the console from your computer, run this there:
  ssh -N -L 8080:127.0.0.1:8080 root@203.0.113.10
Then browse to:
  http://127.0.0.1:8080/login?with=token
Its access token is in the box above; it is shown only once.
```

On the server, `noust setup doctor` lists what deployments need, each line green:

```bash
noust setup doctor
```

In the console, the overview of a new server is one card: **Deploy your first app**.

## Other ways to install

The one-line installer is the recommended way. These do the same by hand. After any of them, run
`noust setup init` and `noust web enable` yourself.

```bash tab="Ubuntu and Debian"
# Ubuntu 26.04. For another release, change xUbuntu_26.04 in both lines:
# xUbuntu_24.04, xUbuntu_22.04, Debian_12 or Debian_13.
curl -fsSL https://download.opensuse.org/repositories/home:/Perkybeet/xUbuntu_26.04/Release.key | \
  gpg --dearmor | tee /usr/share/keyrings/noust.gpg > /dev/null
echo 'deb [signed-by=/usr/share/keyrings/noust.gpg] https://download.opensuse.org/repositories/home:/Perkybeet/xUbuntu_26.04/ /' | \
  tee /etc/apt/sources.list.d/noust.list
apt update
apt install noust
```

```bash tab="Fedora"
dnf config-manager --add-repo \
  https://download.opensuse.org/repositories/home:/Perkybeet/Fedora_42/home:Perkybeet.repo
dnf install noust
```

```bash tab="openSUSE"
# Tumbleweed. On Leap 15.6, use openSUSE_Leap_15.6 instead of openSUSE_Tumbleweed.
zypper ar -f https://download.opensuse.org/repositories/home:/Perkybeet/openSUSE_Tumbleweed/ home_Perkybeet
zypper install noust
```

```bash tab="PyPI"
pip install 'noust[all]'     # the command line, the console, the API and the monitor
```

```bash tab="From source"
git clone https://github.com/Perkybeet/noust.git
cd noust
pip install -e ".[all]"
```

The packages come from the `home:Perkybeet` project on the openSUSE Build Service. The RHEL family
(AlmaLinux, Rocky Linux, CentOS Stream) has no package: the one-line installer puts Noust in a
virtual environment under `/opt/noust` instead.

The console needs FastAPI, Uvicorn and psutil. The Debian package recommends them and the RPM
package suggests them; if they are missing, `noust web install` installs them.

### A container, to manage other servers

The image `ghcr.io/perkybeet/noust` runs a *central*: a console that manages your other servers
and deploys nothing itself. It is the way to run Noust on a NAS or on any Docker host.

```bash
docker run -d --name noust --restart unless-stopped \
  -p 8443:8443 -v noust-data:/data \
  --read-only --tmpfs /tmp:size=16m --cap-drop ALL \
  --security-opt no-new-privileges:true \
  ghcr.io/perkybeet/noust:latest
docker logs noust    # the sign-in token, once, and the certificate's fingerprint
```

[Run a central](CENTRAL.md) covers it, including a NAS running UGOS Pro.

### Without logging in

To install Noust on a new server from its first boot, with a cloud-init user-data file or Ansible,
see [Install without logging in](cloud-init.md).

## If it fails

- **`error: Noust does not install on ...`** and exit status 3: the distribution or the processor is
  not on [the list](PLATFORMS.md). The lines after it say why.
- **`The repository key does not have the expected fingerprint. Nothing was installed.`**: stop
  there. [Platforms](PLATFORMS.md#when-the-repository-key-is-rotated) says how to tell a rotated key
  from a wrong one.
- **The browser cannot reach `127.0.0.1:8080`**: the tunnel is not open, or the console is not
  running. On the server, `noust web status` says whether it runs.
- **You lost the access token**: on the server, `noust web token --new` prints a new one. The old one
  stops working.

[The installation did not work](troubleshooting-install.md) has the rest.

## Next

[Deploy your first application](first-application.md).
