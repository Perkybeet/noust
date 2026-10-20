# Installing Noust without logging in

For a server you create from a template, a script or a provider's API: Noust is installed, the
server is prepared, an administrator exists and the first thing you do is sign in. The installer
([platforms.md](PLATFORMS.md)) has the options for it: `--yes`, `--admin-user`,
`--admin-password-file`, `--admin-email`, `--auto-update` and `--json`, and the same as `NOUST_*`
variables.

What it never does unattended: ask. A question with nobody to answer it is an error that says how
to go on (`--yes`), never a hang. What it does not do for you: choose your password (it reads the one
you give), publish the console (that needs two-step verification on every account, and an account
enrols its authenticator at its first sign-in, which only a person can do), or deploy anything.

## cloud-init

Most providers take a cloud-init file as "user data" when a server is created. This one installs
Noust, creates the administrator `ops` and writes the result as JSON:

```yaml
#cloud-config
write_files:
  - path: /root/noust-admin.pw
    owner: root:root
    permissions: "0600"
    content: |
      change-me-at-first-sign-in-4f9c2a1e

runcmd:
  - |
    set -eu
    cd /root
    curl -fsSLO https://github.com/Perkybeet/noust/releases/latest/download/install.sh
    curl -fsSLO https://github.com/Perkybeet/noust/releases/latest/download/SHA256SUMS
    sha256sum -c --ignore-missing SHA256SUMS
    NOUST_ASSUME_YES=1 sh install.sh --json --admin-user ops \
      --admin-password-file /root/noust-admin.pw --admin-email ops@example.com \
      --auto-update=security \
      > /var/log/noust-install.json 2>> /var/log/noust-install.log
    shred -u /root/noust-admin.pw
```

- **The installer is checked before it runs.** `set -eu` stops the block at the first failing line,
  and `sha256sum -c` fails when the script is not the one that was released (cloud-init runs the
  lines of `runcmd` one after another and does not stop on a failure unless the block says so). The
  checksum file itself is signed; [platforms.md](PLATFORMS.md#verifying-the-installer) shows how to
  verify it with `cosign` when you want more than the checksum.
- **The password is in the user data, and user data is not secret.** Most providers keep it in a
  metadata service that anything on the server can read, and sometimes in their console. Treat it as
  a **throwaway** that you change at your first sign-in: the account enrols its authenticator then,
  and can change its password. The file is read with `--stdin`, so the password is on no command
  line and in no log, and `shred` removes the copy.
- **`/var/log/noust-install.json`** is one line, for whatever provisioned the server:

  ```json
  {"status":"ok","dry_run":false,"version":"3.4.0","method":"package",
   "platform":{"id":"ubuntu","version_id":"24.04","arch":"amd64","support":"supported"},
   "resources":{"memory_mb":2048,"cpus":2,"disk_free_mb":30000,
    "ports":{"80":{"state":"free","holders":null},"443":{"state":"free","holders":null}},
    "warnings":[]},
   "console":{"state":"new","address":"127.0.0.1:8080",
    "url":"http://127.0.0.1:8080/login?with=token",
    "tunnel":"ssh -N -L 8080:127.0.0.1:8080 root@203.0.113.7"},
   "admin":{"username":"ops","state":"created","email":"ops@example.com","email_state":"set"},
   "auto_update":{"state":"enabled","channel":"security","window":"03:00-05:00","days":"daily"}}
  ```

  A failure is `{"status":"error","error":"..."}` and exit status 1; a distribution Noust does not
  install on, or a machine below the [floor of memory, processors or disk](requirements.md#how-big-a-server),
  is `{"status":"refused",...}` and exit status 3 (`--force` goes on anyway). A machine below the
  minimum, or a port 80 or 443 another program holds, is a question: unattended it is answered by
  `--yes` and listed in `resources.warnings`, so the template can decide to stop on it.
- **The address of the administrator** (`--admin-email`) is where the invitations and password
  resets of the console are mailed, and the automatic update (`--auto-update=security|patch|stable`,
  with `--auto-update-window` and `--auto-update-days`) is off unless you ask for it: it installs
  Noust's own releases of the channel you chose, inside the window, and never a new major version
  ([the release policy](release-policy.md)).
- Running it again is safe: it upgrades Noust, leaves an existing configuration and console alone,
  and says the account exists instead of failing.

### After the first boot

```bash
ssh -N -L 8080:127.0.0.1:8080 root@NEW-SERVER     # the console listens on loopback only
# open http://127.0.0.1:8080, sign in as ops, enrol the authenticator, change the password
```

The console's access token is printed by `noust web enable` once, into
`/var/log/noust-install.log`; read it there if you do not use the account, then consider
`noust web token --new`. To deploy during provisioning, add `runcmd` lines after the installer:
`noust create -d app.example.com -s https://github.com/you/app.git` (the DNS record must already point
at the server, or the certificate step will say it does not).

## Ansible

```yaml
- name: Install Noust
  hosts: new_servers
  become: true
  tasks:
    - name: Download the installer and its checksums
      ansible.builtin.get_url:
        url: "https://github.com/Perkybeet/noust/releases/latest/download/{{ item }}"
        dest: "/root/{{ item }}"
        mode: "0600"
      loop: [install.sh, SHA256SUMS]

    - name: Check the installer
      ansible.builtin.command: sha256sum -c --ignore-missing SHA256SUMS
      args:
        chdir: /root
      changed_when: false

    - name: Install Noust
      ansible.builtin.command: sh /root/install.sh --json --admin-user ops
      environment:
        NOUST_ASSUME_YES: "1"
        NOUST_ADMIN_PASSWORD: "{{ noust_admin_password }}"   # from ansible-vault
      no_log: true            # the environment holds the password
      register: noust_install
      changed_when: (noust_install.stdout | from_json).admin.state == 'created'

    - name: Say what happened
      ansible.builtin.debug:
        msg: "{{ noust_install.stdout | from_json }}"
```

`--json` is what makes `from_json` work: standard output is the one document, and everything the
installer and the programs it runs print goes to standard error. The password reaches the installer
through the environment, which `ps` does not show to other users the way an argument would be shown.

## Terraform and other provisioners

Anything that can hand the server a cloud-init file or run a command over SSH after it boots can run
the same installer; the pieces are the three above: download and check the script, run it with
`NOUST_ASSUME_YES=1 --json` and an administrator, read the JSON. There is no provider image or
marketplace application yet: this is the supported way.

## Dry run

`sh install.sh --dry-run --json --admin-user ops --admin-password-file FILE` prints the plan on
standard error and the JSON (`"dry_run":true`, the admin as `would-create`) on standard output,
changes nothing and needs no root. It is how to test a template.
