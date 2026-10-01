/**
 * The Server area's answers for component tests: one believable Ubuntu 24.04 VPS with security
 * updates pending, a reboot due, passwords on in SSH and Docker publishing around the firewall,
 * so every tab has something to say. Only tests import this module.
 */

import { vi } from "vitest";

import type { RouteHandler } from "../../test/fakes";
import { json } from "../../test/fakes";
import type {
  AccountKeys,
  Checks,
  Fail2ban,
  Firewall,
  Identity,
  PendingChange,
  Power,
  SecurityOverview,
  ServerClock,
  ServerSummary,
  SshStatus,
  Storage,
  Swap,
  Updates,
} from "./queries";

const NOW = "2026-09-29T10:00:00+00:00";

export const SUMMARY: ServerSummary = {
  hostname: "web-01",
  os: { id: "ubuntu", name: "Ubuntu 24.04.1 LTS", version: "24.04", eol: { status: "ok", end_date: "2029-05-31", days_left: 973, source: "table" } },
  kernel: "6.8.0-45-generic",
  uptime_seconds: 12 * 86_400 + 4 * 3_600,
  updates: { supported: true, security_scope: true, pending: 23, security: 4, kept_back: 1, broken: false, checked_at: NOW, notes: [] },
  reboot: { required: true, since: "2026-09-20T04:00:00+00:00", reasons: ["/var/run/reboot-required exists"], packages: ["linux-image-6.8.0-47-generic"] },
  stale_services: 2,
  auto_updates: { mechanism: "unattended-upgrades", supported: true, enabled: true, security_only: true, reboots: false },
  disk: { worst_mount: "/", worst_percent: 71.4, inodes_percent: 4.1, free_bytes: 18 * 1024 ** 3, status: "ok", mounts: 2 },
  time: { timezone: "Europe/Madrid", synchronized: true, ntp_enabled: true, ntp_supported: true },
  swap: { total_bytes: 0, used_bytes: 0, recommended: true },
  power: { scheduled: null },
  system: { state: "running", failed_units: [] },
  capabilities: { packages: "apt", updates: true, security_updates: true, transactional: false, container: null, systemd: true, swap: true, docker: true },
  checked_at: NOW,
};

const DOCKER_BYPASS = {
  id: "fw.docker_bypass",
  group: "firewall",
  title: "Docker publishes ports around the firewall",
  severity: "critical",
  status: "fail",
  reason: "Port 5432 (arenna_postgres) is published on 0.0.0.0 by Docker.",
  evidence: ["0.0.0.0:5432->5432/tcp arenna_postgres"],
  fix: { kind: "guided", summary: "Publish the port on 127.0.0.1", steps: ["Edit the Compose file: ports: - \"127.0.0.1:5432:5432\""], blocked: "", reverts: false },
} satisfies Checks["checks"][number];

const PASSWORDS = {
  id: "ssh.password_auth",
  group: "ssh",
  title: "SSH accepts passwords",
  severity: "warning",
  status: "warn",
  reason: "passwordauthentication is yes.",
  evidence: ["passwordauthentication yes"],
  fix: {
    kind: "automatic",
    summary: "Turn password logins off in /etc/ssh/sshd_config.d/00-noust.conf",
    action: "ssh:disable-passwords",
    cli: "noust server security fix ssh.password_auth",
    steps: [],
    blocked: "",
    reverts: true,
  },
} satisfies Checks["checks"][number];

const SECURITY_PENDING = {
  id: "upd.security_pending",
  group: "updates",
  title: "Security updates are pending",
  severity: "critical",
  status: "fail",
  reason: "4 security updates are pending.",
  evidence: [],
  fix: { kind: "action", summary: "Install them", cli: "noust server updates apply --security-only", endpoint: "POST /api/server/updates/apply", steps: [], blocked: "", reverts: false },
} satisfies Checks["checks"][number];

export const SECURITY: SecurityOverview = {
  checked_at: NOW,
  counts: { critical: 2, warning: 1, accepted: 0, unknown: 0, passed: 22, not_applicable: 2 },
  attention: [DOCKER_BYPASS, SECURITY_PENDING, PASSWORDS],
  pending: [],
  checking: false,
};

export const CHECKS: Checks = {
  checked_at: NOW,
  counts: SECURITY.counts ?? { critical: 0, warning: 0, accepted: 0, unknown: 0, passed: 0, not_applicable: 0 },
  checks: [
    DOCKER_BYPASS,
    SECURITY_PENDING,
    PASSWORDS,
    { id: "ssh.empty_passwords", group: "ssh", title: "Accounts can log in with an empty password", severity: "critical", status: "pass", reason: "permitemptypasswords is no.", evidence: [] },
    { id: "os.eol", group: "system", title: "The operating system is out of support", severity: "critical", status: "pass", reason: "Supported until 2029-05-31.", evidence: [] },
  ],
};

export const UPDATES: Updates = {
  supported: true,
  security_scope: true,
  pending: 3,
  security: 2,
  packages: [
    { name: "curl", installed: "8.5.0-2ubuntu10.3", candidate: "8.5.0-2ubuntu10.4", security: false, kernel: false, origin: "noble-updates", kind: "package" },
    { name: "openssl", installed: "3.0.13-0ubuntu3.3", candidate: "3.0.13-0ubuntu3.4", security: true, kernel: false, origin: "noble-security", kind: "package", advisory: "USN-7020-1" },
    { name: "linux-image-6.8.0-47-generic", installed: null, candidate: "6.8.0-47.47", security: true, kernel: true, origin: "noble-security", kind: "package" },
  ],
  kept_back: [],
  holds: [],
  broken: false,
  checked_at: NOW,
  notes: [],
  reboot: { required: true, packages: ["linux-image-6.8.0-47-generic"], reasons: ["/var/run/reboot-required exists"], since: "2026-09-20T04:00:00+00:00", source: "debian", detector_available: true },
  stale_services: ["nginx.service", "cron.service"],
  auto: { mechanism: "unattended-upgrades", supported: true, installed: true, enabled: true, security_only: true, reboots: false, last_run: NOW, detail: "" },
  running: null,
};

export const PLAN = {
  scope: "security",
  full: false,
  packages: UPDATES.packages.filter((item) => item.security),
  removals: [],
  impact: ["nginx"],
  restarts_console: false,
  command: "apt-get -y install --only-upgrade openssl linux-image-6.8.0-47-generic",
};

export const POWER: Power = {
  scheduled: null,
  boot_id: "3f1c2a4b",
  uptime_seconds: 1_051_200,
  mode: null,
  due_at: null,
  checks: [
    { id: "jobs", status: "ok", message: "Nothing is running" },
    { id: "console", status: "ok", message: "The console starts on boot" },
    { id: "apps", status: "warn", message: "These applications are not enabled and will not start again: blog.example.org" },
    { id: "fstab", status: "ok", message: "fstab is correct" },
    { id: "ramdisk", status: "ok", message: "The newest kernel is complete" },
  ],
};

export const STORAGE: Storage = {
  mounts: [
    {
      mount_point: "/",
      device: "/dev/vda1",
      fstype: "ext4",
      total_bytes: 78 * 1024 ** 3,
      used_bytes: 55 * 1024 ** 3,
      free_bytes: 23 * 1024 ** 3,
      percent_used: 71.4,
      inodes_total: 5_000_000,
      inodes_free: 4_800_000,
      inodes_percent: 4,
      readonly: false,
      status: "ok",
    },
  ],
  worst: null,
  candidates: [
    { id: "journal", size_bytes: 1_200 * 1024 ** 2, reclaimable_bytes: 1_000 * 1024 ** 2, action: "journal", detail: "Archived and active journal files take up 1.2G in the file system.", measured_at: NOW },
    { id: "docker-images", size_bytes: 1_400 * 1024 ** 2, reclaimable_bytes: 900 * 1024 ** 2, action: null, detail: "Images no container uses.", measured_at: NOW },
  ],
  analysis_at: NOW,
};

export const SWAP: Swap = {
  devices: [],
  total_bytes: 0,
  used_bytes: 0,
  swappiness: 60,
  memory_bytes: 2 * 1024 ** 3,
  suggested_bytes: 2 * 1024 ** 3,
  recommended: true,
  supported: true,
  reason: "",
  noust_swapfile: false,
  warnings: [],
};

export const CLOCK: ServerClock = {
  timezone: "Europe/Madrid",
  local_time: "Tue 2026-09-29 12:00:00 CEST",
  utc: "Tue 2026-09-29 10:00:00 UTC",
  ntp_supported: true,
  ntp_enabled: true,
  synchronized: true,
  local_rtc: false,
  offset_seconds: null,
};

export const IDENTITY: Identity = {
  hostname: { hostname: "web-01", static: "web-01", pretty: null, machine_id: "5d3e2f1a9b8c7d6e5f4a3b2c1d0e9f8a", boot_id: "3f1c2a4b", chassis: "vm", cloud_init: true, cloud_init_resets: false },
  os_id: "ubuntu",
  os_version: "24.04",
  os_name: "Ubuntu 24.04.1 LTS",
  codename: "noble",
  kernel: "6.8.0-45-generic",
  architecture: "x86_64",
  container: null,
  eol: { status: "ok", end_date: "2029-05-31", days_left: 973, source: "table" },
  uptime_seconds: 1_051_200,
  booted_at: "2026-09-17T06:00:00+00:00",
  load: [0.42, 0.38, 0.31],
  cpu_count: 2,
};

export const SSH: SshStatus = {
  effective: { passwordauthentication: "yes", permitrootlogin: "prohibit-password", pubkeyauthentication: "yes", kbdinteractiveauthentication: "no", permitemptypasswords: "no", maxauthtries: "6", loglevel: "INFO", x11forwarding: "yes" },
  dropin: "/etc/ssh/sshd_config.d/00-noust.conf",
  dropin_settings: {},
  include_present: true,
  root_password: "no",
  ports: [22],
  passwords_accepted: true,
  unit: { service: "ssh.service", active: true, socket: "ssh.socket", socket_active: true },
  fixes: {
    "disable-passwords": {
      fix: "disable-passwords",
      title: "Turn off password logins",
      check_id: "ssh.password_auth",
      changes: [{ directive: "passwordauthentication", before: "yes", after: "no" }],
      needed: true,
      allowed: true,
      blockers: [],
      guidance: [],
      proof: "root logged in with key SHA256:8Wm9... from 203.0.113.7 on 2026-09-27.",
      evidence: [],
    },
  },
  sessions: [{ peer: "203.0.113.7", port: 51234, user: "root", fingerprint: "SHA256:8Wm9" }],
  logins: { source: "journal", error: "", days: 30, recent: [{ at: 1_758_960_000, user: "root", method: "publickey", source: "203.0.113.7", fingerprint: "SHA256:8Wm9", line: "Accepted publickey for root" }] },
  confirm_window: 120,
};

export const KEYS: AccountKeys[] = [
  {
    user: "root",
    uid: 0,
    sudo: true,
    sudo_usable: true,
    password: "locked",
    login_allowed: true,
    login_refusal: "",
    files: [
      {
        path: "/root/.ssh/authorized_keys",
        exists: true,
        problems: [],
        error: "",
        keys: [{ fingerprint: "SHA256:8Wm9pLr2QvF0bKx", type: "ssh-ed25519", bits: 256, comment: "yago@laptop", kind: "operator", options: [], weak: "", last_used: 1_758_960_000, last_used_from: "203.0.113.7", in_use: true }],
      },
    ],
  },
];

export const FIREWALL: Firewall = {
  firewall: {
    backend: "ufw",
    active: true,
    default_incoming: "deny",
    rules: [{ id: "1", backend: "ufw", action: "allow", ports: [[22, 22]], proto: "tcp", source: "any", spec: "22/tcp ALLOW IN Anywhere", comment: "", noust: false, known: true }],
    installed: true,
    status: "active",
    others: [],
    warnings: [],
    error: "",
  },
  ports: [
    { proto: "tcp", port: 22, address: "0.0.0.0", process: "sshd", verdict: "open", sources: [], risky: "", baseline: true, reachable: true },
    {
      proto: "tcp",
      port: 5432,
      address: "0.0.0.0",
      process: "docker-proxy",
      verdict: "docker_bypass",
      sources: [],
      risky: "PostgreSQL",
      baseline: false,
      reachable: true,
      docker: { container: "arenna_postgres", project: "arenna", host_address: "0.0.0.0", host_port: 5432, container_port: 5432, proto: "tcp" },
    },
  ],
  protected_ports: [{ port: 22, reason: "SSH" }],
  session_sources: ["203.0.113.7"],
};

export const FAIL2BAN: Fail2ban = {
  installed: false,
  running: false,
  jails: [],
  substitutes: [],
  needs_epel: false,
  install_supported: true,
  install_hint: "",
  error: "",
};

/** A change to sshd waiting for a new SSH login, with a minute and a half left. */
export function pendingChange(now = Date.now()): PendingChange {
  return {
    id: "c1a2b3",
    kind: "ssh",
    title: "Turn off password logins",
    actor: "yago",
    applied_at: now / 1000 - 30,
    expires_at: now / 1000 + 90,
    status: "pending",
    unit: "noust-revert-c1a2b3.timer",
    files: ["/etc/ssh/sshd_config.d/00-noust.conf"],
    validate_command: ["sshd", "-t"],
    undo: [],
    commit: [],
    before: { passwordauthentication: "yes" },
    after: { passwordauthentication: "no" },
    proof: "",
    resolution: "",
  };
}

/** Every `/api/server` read of the fake VPS, for `fakeBackend`. */
export function serverRoutes(overrides: Partial<{ summary: ServerSummary; security: SecurityOverview; changes: PendingChange[] }> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/server/summary": () => json(200, overrides.summary ?? SUMMARY),
    "GET /api/server/security": () => json(200, overrides.security ?? SECURITY),
    "GET /api/server/security/changes": () => json(200, overrides.changes ?? []),
    "GET /api/server/security/checks": () => json(200, CHECKS),
    "GET /api/server/security/ssh": () => json(200, SSH),
    "GET /api/server/security/ssh/keys": () => json(200, KEYS),
    "GET /api/server/security/firewall": () => json(200, FIREWALL),
    "GET /api/server/security/fail2ban": () => json(200, FAIL2BAN),
    "GET /api/server/updates": () => json(200, UPDATES),
    "GET /api/server/updates/plan": () => json(200, PLAN),
    "GET /api/server/updates/runs": () => json(200, []),
    "GET /api/server/power": () => json(200, POWER),
    "GET /api/server/storage": () => json(200, STORAGE),
    "GET /api/server/swap": () => json(200, SWAP),
    "GET /api/server/time": () => json(200, CLOCK),
    "GET /api/server/identity": () => json(200, IDENTITY),
    "GET /api/server/processes": () => json(200, { processes: [], units: [], total: 0 }),
    "GET /api/server/logs": () => json(200, { entries: [], next_cursor: null, truncated: false }),
    "GET /api/server/logs/units": () => json(200, []),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
  };
}

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
export function onDesktop(): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}
