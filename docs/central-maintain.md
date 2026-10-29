# Look after the central

Part of [The Noust central](CENTRAL.md).

## Sealing the secrets (recommended on a NAS)

A central holds the keys to every server it manages. Sealing encrypts them at rest (node keys
and tokens, integration keys: every file under `/data/state/secrets`) under a passphrase
that is never written anywhere:

```bash
docker exec -it noust noust central seal       # asks for the passphrase twice
```

Key derivation is scrypt (n=2^15, r=8, p=1) over a random salt; each file is encrypted with
AES-256-CBC by `openssl` and authenticated with HMAC-SHA256 (encrypt-then-MAC, the secret's
name bound in, so a file altered or moved over another is refused). The key reaches openssl
through its environment, never its command line.

After every restart a sealed central is **locked**: the console works and says why, but no
tunnel opens until you unlock it, either with the **Unlock** form in the console or:

```bash
docker exec -it noust noust central unlock
printf '%s\n' "$PASSPHRASE" | docker exec -i noust noust central unlock   # from a script
```

The console's form (`POST /api/central/unlock`) asks for a signed-in admin in sudo mode
("Confirm it's you") as well as the passphrase, so an unattended browser tab is not enough.
A wrong passphrase counts towards the same lockout as a wrong token (five in a row lock
that address out for fifteen minutes), and every attempt is in the audit log, without the
passphrase. While locked, anything that needs a node answers `423 central_locked`.

Once unlocked, ssh is handed a decrypted copy of a node's key and pinned host key in a
private, memory-backed directory (`$XDG_RUNTIME_DIR`, else the temporary directory) and the
copy is deleted as soon as the tunnel is up; any left over go when the central stops or is
locked. New node keys on a sealed central are generated there and sealed at once.

`noust central unseal` stores them in clear again; unseal and seal to change the passphrase.

**Nobody can recover a lost passphrase**, Noust included. Without it the sealed secrets are
gone and every server must be authorized again. Keep it in your password manager, not on the
NAS.

What is not sealed, because the console needs it to let you in to unlock: the console's
signing key, the token hash, the sessions and the two-factor secret (under `/data/config`).

## Backups

`noust central backup` writes everything a central needs to come back into one file, while it
runs: every file of its configuration and state directories, the databases as consistent
snapshots, and a SHA-256 manifest, encrypted and authenticated under a passphrase you type
(scrypt, then AES-256-CBC by openssl and HMAC-SHA256, the construction of sealed secrets). The
passphrase is stored nowhere.

```bash
docker exec -it noust noust central backup                       # to central/ under the backups directory
docker exec -it noust noust central backup --output /data/backups/central
docker exec -it noust noust central backup --verify FILE         # it opens, and every file matches
docker exec -it noust noust central backup --decrypt FILE --to /data/restore.tar.gz
```

Keep the file off the machine and the passphrase apart from it: without the passphrase nobody,
Noust included, can restore it. To restore, decrypt it, stop the central, and put the
archive's `noust-central/config/` and `noust-central/state/` back as the configuration and
state directories (`/data/config` and `/data/state` in the container, `/etc/noust` and
`/var/lib/noust` on a VPS); `MANIFEST.json` lists every file with its SHA-256. A sealed central's
secrets stay sealed inside the backup, so restoring them needs both passphrases.

What it holds, and what a copy of the whole volume holds too:

| Path | What |
|------|------|
| `/data/config/` | `config.yaml`, the console's signing key, token hash, sessions, two-factor state, audit log and its key, and its TLS pair (`panel-tls/`) |
| `/data/state/noust.db` | the store: servers, their pinned host keys, labels, accounts, fleet jobs, history |
| `/data/state/secrets/` | node keys and tokens (sealed or not) and the seal header `.seal` |
| `/data/backups/`, `/data/log/` | the central's own backups and logs |

A copy of the volume works too. Stop the container for a consistent copy (`docker stop noust`),
or copy it running and accept that the last minute may be missing. A sealed backup is only as
useful as the passphrase you still have: without it, restore gives you a central that cannot
reach any server.

With a named volume:

```bash
docker run --rm -v noust-data:/data -v "$PWD":/backup debian:bookworm-slim \
  tar czf /backup/noust-data.tgz -C /data .
```

## Rotating a server's key

```bash
noust node rekey vps1              # on the central: a new key pair, and the command for vps1
# on vps1, as root: the printed 'noust fleet authorize ...' (replaces the old key's line)
noust node rekey vps1 --join-code -   # on the central: paste the new code
```

The central switches to the new key and token only once `vps1` answers with them; otherwise
it keeps the old ones and the same code can be pasted again. Running `noust node rekey vps1`
again before finishing prints the same command.

The same two steps are an API, and the console's **Fleet > Servers** menu uses them (**Rotate its key**):

```
POST /api/nodes/vps1/rekey   {}                          # step 1: {"step": "authorize", "authorize_command": "..."}
POST /api/nodes/vps1/rekey   {"join_code": "<the code>"}  # step 2: {"step": "done", "node": {...}}
```

Both need `fleet.manage`, sudo mode and, when approvals are on, a second person; they are the instance's,
never a team's, because they decide who can reach the machine as root. The join code carries the server's
token, so it is left out of the approval's fingerprint and of the audit trail, which records
`fleet.node.rekey` with the account and address the server is now reached as, or as a failure with the
reason. `POST /api/nodes/vps1/migrate-tunnel` is the same for the move below (`fleet.node.migrate`).

## Troubleshooting

- **"The data directory /data is not writable by uid 10001"**: a bind-mounted folder belongs
  to someone else. `chown -R 10001:10001` it on the host.
- **The browser cannot reach the console**: check the NAS firewall allows 8443 from your LAN,
  and that your address is inside the allowed networks (`noust central status`). Docker on
  some systems presents clients with the bridge's address (`172.17.0.1`), which the defaults
  already allow.
- **"The secrets are sealed and locked"**: unlock them (above).
- **A server that dials in shows as not connected**: nothing listens on its link's port. On the
  server, `systemctl status noust-link-<central>` and `journalctl -u noust-link-<central>` say why:
  the central's address, a firewall in the way, or a host key that no longer matches the pinned one
  (the central's SSH host key changed: link it again). On the central, `noust node link status`
  says whether `noust-link` is still restricted in sshd.
- **A server shows as refused**: it no longer accepts this central's token (revoked or
  deauthorized there). The central stops presenting it, so the server's audit log does not
  fill up and its loopback address is never locked out. Authorize the central on the server
  again, then `noust node test <name>` (or Test in the console) to resume, or remove and add
  it with the new join code.
- **A command says it is "not available on this central, which is a hub"**: it deploys on the
  machine it runs on; run it on the server instead.
- **"sshd would refuse the central's tunnel"** on `authorize`: the reasons are listed, one per
  line. The usual one is `AllowUsers` or `AllowGroups` without `noust-tunnel`: add it, reload
  sshd, and authorize again. `sshd -T -C user=noust-tunnel,host=localhost,addr=127.0.0.1` shows
  what sshd applies to the account.
- **"sshd does not apply Noust's restrictions to noust-tunnel"**: an earlier file in
  `/etc/ssh/sshd_config.d/` or a `Match` block in `sshd_config` sets one of the settings first.
  The message names each one; nothing was changed.
- **The tunnel to a server is refused after many failed attempts**: fail2ban on the server may
  have banned the central's address. Add it to `ignoreip` there when it is fixed.
- **"This server is the central that issued this key"**: `noust fleet authorize` was run on
  the central (or in its container) instead of on the server to add. Run it on the server.
