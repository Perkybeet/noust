/**
 * What the console knows of the server's software that the server does not say (item 118): how the pieces are
 * grouped on the page, which permission installing each one takes, and where what depends on one of them is
 * looked after. The server's registry (`managers/server/components.py`) is the list of components and the one
 * authority on whether any of them may be removed; this file only draws it.
 *
 * A component this console has never heard of (a server newer than the console) is not hidden: it is listed
 * under "Other", by the id the server gave it and with the purpose sentence the server sent.
 */

import type { PlainKey } from "../../../i18n";

/** The components the 4.2 server registers, in the order the page shows them. */
export const KNOWN = [
  "noust",
  "systemd",
  "sshd",
  "firewall",
  "git",
  "curl",
  "gnupg",
  "nginx",
  "apache",
  "certbot",
  "nodejs",
  "php",
  "docker",
  "postgresql",
  "mysql",
  "redis",
  "mongodb",
  "cloudflared",
  "nginx_stream_module",
  "waf",
  "knot",
  "fail2ban",
  "chrony",
  "auto_updates",
  "rclone",
] as const;

export type KnownComponent = (typeof KNOWN)[number];

export function isKnown(id: string): id is KnownComponent {
  return (KNOWN as readonly string[]).includes(id);
}

export type GroupId = "machine" | "sites" | "apps" | "databases" | "network" | "protection" | "other";

/** The groups, in the order the page lists them. */
export const GROUPS: readonly GroupId[] = ["machine", "sites", "apps", "databases", "network", "protection", "other"];

export const GROUP_LABELS: Readonly<Record<GroupId, PlainKey>> = {
  machine: "software.groups.machine",
  sites: "software.groups.sites",
  apps: "software.groups.apps",
  databases: "software.groups.databases",
  network: "software.groups.network",
  protection: "software.groups.protection",
  other: "software.groups.other",
};

const GROUP_OF: Readonly<Record<KnownComponent, GroupId>> = {
  noust: "machine",
  systemd: "machine",
  sshd: "machine",
  firewall: "machine",
  git: "machine",
  curl: "machine",
  gnupg: "machine",
  nginx: "sites",
  apache: "sites",
  certbot: "sites",
  nodejs: "apps",
  php: "apps",
  docker: "apps",
  postgresql: "databases",
  mysql: "databases",
  redis: "databases",
  mongodb: "databases",
  cloudflared: "network",
  nginx_stream_module: "network",
  waf: "network",
  knot: "network",
  fail2ban: "protection",
  chrony: "protection",
  auto_updates: "protection",
  rclone: "protection",
};

export function groupOf(id: string): GroupId {
  return isKnown(id) ? GROUP_OF[id] : "other";
}

/** One literal key per component, so `tsc` checks each: a name is a product's, said once in each language. */
const NAMES: Readonly<Record<KnownComponent, PlainKey>> = {
  noust: "software.names.noust",
  systemd: "software.names.systemd",
  sshd: "software.names.sshd",
  firewall: "software.names.firewall",
  git: "software.names.git",
  curl: "software.names.curl",
  gnupg: "software.names.gnupg",
  nginx: "software.names.nginx",
  apache: "software.names.apache",
  certbot: "software.names.certbot",
  nodejs: "software.names.nodejs",
  php: "software.names.php",
  docker: "software.names.docker",
  postgresql: "software.names.postgresql",
  mysql: "software.names.mysql",
  redis: "software.names.redis",
  mongodb: "software.names.mongodb",
  cloudflared: "software.names.cloudflared",
  nginx_stream_module: "software.names.nginxStreamModule",
  waf: "software.names.waf",
  knot: "software.names.knot",
  fail2ban: "software.names.fail2ban",
  chrony: "software.names.chrony",
  auto_updates: "software.names.autoUpdates",
  rclone: "software.names.rclone",
};

/** The catalog key of a component's name, or null for one this console does not know (its id is its name then). */
export function nameKey(id: string): PlainKey | null {
  return isKnown(id) ? NAMES[id] : null;
}

/**
 * What a feature loses without a component, in a sentence that starts with "Without it": the reason a notice gives
 * for what is missing. Only the pieces a page of the console is built on have one.
 */
const WHY: Readonly<Partial<Record<KnownComponent, PlainKey>>> = {
  nginx: "software.missing.why.nginx",
  certbot: "software.missing.why.certbot",
  php: "software.missing.why.php",
  docker: "software.missing.why.docker",
  cloudflared: "software.missing.why.cloudflared",
  nginx_stream_module: "software.missing.why.nginxStreamModule",
  rclone: "software.missing.why.rclone",
  firewall: "software.missing.why.firewall",
  fail2ban: "software.missing.why.fail2ban",
  knot: "software.missing.why.knot",
  waf: "software.missing.why.waf",
};

export function whyKey(id: string): PlainKey | null {
  return isKnown(id) ? (WHY[id] ?? null) : null;
}

/**
 * The permissions installing a component takes (`web/permissions/routes_server_software.py`), which is the offer
 * only: the server asks again on the request. A piece no function of the console installed before 4.2 changes the
 * host's packages, which is `server.host_access`.
 */
const INSTALL: Readonly<Record<string, readonly string[]>> = {
  cloudflared: ["server.manage"],
  waf: ["server.manage"],
  fail2ban: ["server.manage"],
  chrony: ["server.manage"],
  auto_updates: ["server.manage"],
  knot: ["server.manage", "server.host_access"],
  nginx_stream_module: ["server.manage", "server.host_access"],
  postgresql: ["server.manage", "databases.manage"],
  mysql: ["server.manage", "databases.manage"],
  redis: ["server.manage", "databases.manage"],
  mongodb: ["server.manage", "databases.manage"],
};

/** What installing a component asks of whoever asks: every one of these. */
export function installPermissions(id: string): readonly string[] {
  return INSTALL[id] ?? ["server.manage", "server.host_access"];
}

const ENGINES: ReadonlySet<string> = new Set(["postgresql", "mysql", "redis", "mongodb"]);

/** What removing a component asks of whoever asks: `server.host_access` always, and `databases.manage` for an engine. */
export function removePermissions(id: string): readonly string[] {
  return ENGINES.has(id) ? ["server.host_access", "databases.manage"] : ["server.host_access"];
}

/** The pieces that come from EPEL on a RHEL rebuild, which installing needs the operator's yes for. */
const FROM_EPEL: ReadonlySet<string> = new Set(["fail2ban", "knot"]);

export function comesFromEpel(id: string, family: string): boolean {
  return family === "dnf" && FROM_EPEL.has(id);
}

/** Where the things that depend on a component are looked after: the page a "see them" button opens. */
const DEPENDENT_PAGES: Readonly<Record<string, string>> = {
  application: "/apps",
  site: "/domains/sites",
  database: "/databases",
  tunnel: "/network/tunnels",
  published_port: "/network",
  certificate: "/domains/certificates",
  zone: "/domains/dns",
  destination: "/backups/destinations",
  instance: "/databases/engines",
};

/** The page of a kind of dependent, or null for one with none (the console itself, an engine that could not be asked). */
export function dependentPage(kind: string): string | null {
  return DEPENDENT_PAGES[kind] ?? null;
}

/** The kinds a dependent has words for; any other is shown by the name the server gave it. */
export const DEPENDENT_KINDS = [
  "application",
  "site",
  "database",
  "tunnel",
  "published_port",
  "certificate",
  "zone",
  "destination",
  "instance",
  "console",
  "unreadable",
] as const;
export type DependentKind = (typeof DEPENDENT_KINDS)[number];

export function isDependentKind(kind: string): kind is DependentKind {
  return (DEPENDENT_KINDS as readonly string[]).includes(kind);
}
