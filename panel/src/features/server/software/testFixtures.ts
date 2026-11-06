/**
 * The Software section's answers for component tests: a Debian server with what Noust set up, a few pieces
 * that came with the image, some it has not been asked to install, and the answers of the three ways a removal
 * can go (a question, a refusal, a piece that was not Noust's). Only tests import this module.
 */

import type { RouteHandler } from "../../../test/fakes";
import { json } from "../../../test/fakes";
import type { Impact, SoftwareItem, SoftwareList } from "./queries";

function item(id: string, over: Partial<SoftwareItem> = {}): SoftwareItem {
  return {
    id,
    purpose: `What ${id} is for`,
    purpose_code: `software.${id}.purpose`,
    severity: "critical_while",
    installed: true,
    version: "1.0.0",
    origin: "noust",
    installable: true,
    removable: false,
    dependents: [],
    packages: [id],
    installed_at: "2026-06-03T09:00:00+00:00",
    installed_by: "yago",
    installed_via: "package",
    ...over,
  };
}

export const ITEMS: SoftwareItem[] = [
  item("noust", { severity: "never", version: "4.2.0", installable: false, dependents: [{ kind: "console", names: ["this one"] }] }),
  item("systemd", { severity: "never", version: "255.4", origin: "unknown", installed_at: null, installed_by: null, installed_via: null }),
  item("git", { severity: "never", version: "2.43.0", origin: "unknown", installed_at: null, installed_by: null, installed_via: null }),
  item("nginx", { version: "1.24.0", dependents: [{ kind: "site", names: ["shop.example.com", "blog.example.org", "api.example.com", "docs.example.com"] }] }),
  item("apache", { installed: false, version: null, origin: null, installed_at: null, installed_by: null, installed_via: null, packages: [] }),
  item("nginx_stream_module", { installed: false, version: null, origin: null, installed_at: null, installed_by: null, installed_via: null, packages: [] }),
  item("cloudflared", { dependents: [{ kind: "tunnel", names: ["oficina", "nas-casa"] }], version: "2024.10.1" }),
  item("fail2ban", { severity: "removable", removable: true, version: "1.0.2", installed_via: "fail2ban", installed_by: "cli:root" }),
  item("chrony", { severity: "removable", removable: true, version: "4.5", origin: "unknown", installed_at: null, installed_by: null, installed_via: null }),
  item("knot", { installed: false, version: null, origin: null, installed_at: null, installed_by: null, installed_via: null, packages: [] }),
];

export const SOFTWARE: SoftwareList = { managed: true, reason: null, family: "apt", items: ITEMS };

/** What removing a piece nothing depends on would do. */
export const QUESTION: Impact = {
  id: "fail2ban",
  installed: true,
  origin: "noust",
  severity: "removable",
  dependents: [],
  removes: ["fail2ban", "python3-systemd"],
  also_removes: ["python3-pyinotify", "whois"],
  repository_files: [],
  blockers: [],
  removable: true,
  needs_confirmation: false,
};

/** A piece that was there before Noust: the server asks for more than a click. */
export const NOT_NOUSTS: Impact = {
  id: "chrony",
  installed: true,
  origin: "unknown",
  severity: "removable",
  dependents: [],
  removes: ["chrony"],
  also_removes: [],
  repository_files: [],
  blockers: [],
  removable: true,
  needs_confirmation: true,
};

/** The server's refusal: sites still use nginx, said as the server says it, with a code the console has words for. */
export const REFUSED: Impact = {
  id: "nginx",
  installed: true,
  origin: "noust",
  severity: "critical_while",
  dependents: [{ kind: "site", names: ["shop.example.com", "blog.example.org", "api.example.com", "docs.example.com"] }],
  removes: ["nginx", "nginx-core"],
  also_removes: [],
  repository_files: [],
  blockers: [
    {
      reason: "dependents",
      text: "nginx cannot be removed while something depends on it: 4 sites",
      hint: "Stop using it first (remove or move what is listed), then remove it.",
      code: "software.refused.dependents",
      params: { component: "nginx", dependents: "4 sites" },
      dependents: [{ kind: "site", names: ["shop.example.com", "blog.example.org", "api.example.com", "docs.example.com"] }],
    },
  ],
  removable: false,
  needs_confirmation: false,
};

export const JOB_ACCEPTED = { job_id: "5e1f0c2a", status: "pending", message: "queued" };

/** The job as the jobs API says it, in a given state. */
export function softwareJob(status: "pending" | "running" | "completed" | "failed", over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "5e1f0c2a",
    type: "server_action",
    name: "Install nginx_stream_module",
    description: "Installing nginx_stream_module (server.software_install)",
    status,
    progress: status === "completed" ? 100 : 10,
    total_steps: 100,
    current_step: status === "completed" ? "Done" : "Installing nginx_stream_module",
    created_at: "2026-11-05T10:00:00Z",
    started_at: "2026-11-05T10:00:01Z",
    completed_at: status === "completed" || status === "failed" ? "2026-11-05T10:00:20Z" : null,
    result: null,
    error: status === "failed" ? "E: Unable to locate package libnginx-mod-stream" : null,
    logs: [],
    metadata: { component: "nginx_stream_module", action: "install" },
    state: status === "completed" ? "succeeded" : status === "pending" ? "queued" : status,
    cancellable: false,
    cancel_requested: false,
    ...over,
  };
}

/** The routes of the section; a test passes what it changes. */
export function softwareRoutes(over: { list?: SoftwareList; impacts?: Record<string, Impact> } = {}): Record<string, RouteHandler> {
  const impacts = { fail2ban: QUESTION, chrony: NOT_NOUSTS, nginx: REFUSED, ...over.impacts };
  return {
    "GET /api/server/software": () => json(200, over.list ?? SOFTWARE),
    ...Object.fromEntries(Object.entries(impacts).map(([id, impact]) => [`GET /api/server/software/${id}/impact`, () => json(200, impact)])),
  };
}
