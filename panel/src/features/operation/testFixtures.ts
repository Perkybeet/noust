/**
 * What `/api/views/<resource>` answers on a central with three servers beside it: for the tests of
 * Logs, Alerts, Activity and Backups across the fleet. web-01 is the central, web-2 answers, db-1
 * and old-1 refuse the operator's role (the answer of a node that does not let it read the audit
 * log, with the raw body the node sent, which a person must never see).
 */

const NOW = new Date().toISOString();

/** What a node answers when a role lacks a permission: the console never shows this text. */
export const RAW_REFUSAL = '{"error":"permission_denied","detail":"scope \'viewer\' lacks audit.read"}';

export const SERVERS = [
  { name: "web-01", selector: "@central", local: true },
  { name: "web-2", selector: "web-2", local: false },
  { name: "db-1", selector: "db-1", local: false },
  { name: "old-1", selector: "old-1", local: false },
];

function outcome(name: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    name,
    local: name === "web-01",
    status,
    code: null,
    message: null,
    hint: null,
    error_verbatim: null,
    age_seconds: status === "ok" ? 0 : null,
    fetched_at: status === "ok" ? NOW : null,
    elapsed_ms: 40,
    version: "4.1.0",
    missing: [],
    warnings: [],
    permission: null,
    refusal: null,
    ...extra,
  };
}

/** The four servers all answered. */
export const ALL_OK = SERVERS.map((server) => outcome(server.name, "ok"));

/** db-1 and old-1 refused the audit log: the same permission, so one group. */
export const REFUSED_NODES = [
  outcome("web-01", "ok"),
  outcome("web-2", "ok"),
  outcome("db-1", "forbidden", { code: "permission_denied", message: RAW_REFUSAL, permission: "audit.read", refusal: "permission" }),
  outcome("old-1", "forbidden", { code: "permission_denied", message: RAW_REFUSAL, permission: "audit.read", refusal: "permission" }),
];

export const AUDIT_REFUSAL = {
  reason: "permission",
  code: "permission_denied",
  permission: "audit.read",
  resource: "activity",
  servers: ["db-1", "old-1"],
  count: 2,
  includes_central: false,
  message: "Your role operator does not hold the permission audit.read on these servers",
  hint: "Ask a security officer of each of them for an account with a role that holds it, or have that permission added to your role there.",
  coded: { code: "fleet.refused.permission", params: { role: "operator", permission: "audit.read" } },
};

/** A row of a view: what its server lists, with the server and its page there. */
export function row(node: string, page: string, fields: Record<string, unknown>) {
  const local = node === "web-01";
  return { ...fields, node, local, page, href: local ? page : `/n/${node}${page === "/" ? "" : page}` };
}

/** The envelope every view answers, around its rows. */
export function envelope(resource: string, items: unknown[], extra: Record<string, unknown> = {}) {
  return { resource, generated_at: NOW, partial: false, fleet: true, servers: SERVERS, nodes: ALL_OK, refusals: [], items, ...extra };
}

/** Audit events of two servers, one by a person at the console of web-2 and one through the central. */
export const ACTIVITY_ITEMS = [
  row("web-2", "/activity", {
    timestamp: NOW,
    action: "apps.update",
    actor: "marta",
    who: { kind: "user", name: "marta", id: "marta", role: "operator", source: null, via: null },
    result: "success",
    resource: "/api/apps/api.example.com/update",
    category: "change",
  }),
  row("web-01", "/activity", {
    timestamp: new Date(Date.now() - 600_000).toISOString(),
    action: "apps.restart",
    actor: "fleet-web-01 on behalf of joan",
    who: { kind: "fleet", name: "joan", id: "joan", role: "operator", source: null, via: "fleet-web-01" },
    result: "success",
    resource: "/api/apps/shop.example.com/restart",
    category: "change",
  }),
  row("web-2", "/activity", {
    timestamp: new Date(Date.now() - 1_200_000).toISOString(),
    action: "auth.login",
    actor: "9f8e7d6c5b4a",
    who: null,
    result: "denied",
    resource: "/api/auth/login",
    category: "access",
  }),
];

/** Every application's backups, as each server lists them: one without any, one checked and recent. */
export const BACKUPS_ITEMS = [
  row("web-2", "/backups?domain=api.example.com", { domain: "api.example.com", backups: 0, size: 0, last_backup: null, verified: "never", scheduled: false }),
  row("web-01", "/backups?domain=shop.example.com", {
    domain: "shop.example.com",
    backups: 3,
    size: 52_428_800,
    last_backup: { timestamp: NOW, verified_ok: true },
    verified: "ok",
    scheduled: true,
  }),
];
