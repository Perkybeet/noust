# What CHANGELOG-3.1.md and UPGRADING-3.1.md must say (from the packaging review)

- Passkeys need `cryptography`: comes with `noust[web]` and Debian Recommends; RPM: `dnf install
  python3-cryptography`; pip: `pip install -U "noust[web]"`.
- The monitor (`noust-monitor`, now also the metrics collector) is installed and enabled on install
  and upgrade unless disabled or declined; if removed in 3.0: `touch /var/lib/noust/monitor-declined`
  before upgrading. RPM needs `python3-psutil`.
- `noust config clean` runs from the package scripts: removes `logging`, `nodejs`, `python`,
  `databases.default_encoding`, `auto_start`, `auto_enable`, `backup_dir`, `monitor.use_ai`,
  `ai_interval`, `openai`; keeps comments; dated 0600 backup; never keeps the OpenAI key.
  `db install` always starts and enables the engine.
- Accounts: first account with the master token (`noust user create` or the console's first-run);
  existing API tokens go to the first admin; the master token becomes break-glass (under
  ens-medium only recovers access).
- Sandboxed builds: new apps and previews build as `noust-build` and fail closed; on WSL/containers
  without mount namespaces the first deploy fails until `noust app sandbox disable --reason`;
  existing apps keep building as before until `noust app sandbox test` + `enable`. New paths
  `/var/cache/noust/build`, `/run/noust/sandbox`.
- Nodes: `noust-tunnel` replaces root; migrate 3.0 nodes with `noust node migrate-tunnel`;
  `--ssh-user root` needs `--i-understand`; per-node ceiling `--access`, `--allow-host-access`.
  Security note: 3.0 nodes with the central key in root's authorized_keys allow Unix-socket
  forwarding as root.
- Branch pinning: an update keeps following the checkout's branch unless pinned (`noust app
  branch`, `update --branch` pins, `--unpin`); the webhook ignores other branches when pinned.
- Store v12 is one-way (a backup is taken before migrating); no downgrade.
- New settings: `ssl.hsts` (off), `web.allowed_hosts` enforced, `server.name`, `security.profile`,
  `audit.*`, `retention.*`, `metrics.retention_days`, `approval.*`, `databases.safety_copies_kept`.
  App units now carry MemoryAccounting/CPUAccounting. Database backup timers `noust-backup-db-*`.
- Container: unchanged; `[web]` extra includes `cryptography`.
