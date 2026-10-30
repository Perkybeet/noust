/**
 * The Web server section's answers for component tests: an Ubuntu server with nginx serving nine
 * applications, Apache installed or not, and the plan of moving them. Only tests import this module.
 */

import type { RouteHandler } from "../../../test/fakes";
import { json } from "../../../test/fakes";
import type { MovePlan, WebserverStatus } from "./queries";

export const STATUS: WebserverStatus = {
  webserver: "nginx",
  holds_ports: "nginx",
  servers: {
    nginx: { installed: true, running: true, starts_at_boot: true, applications: 9, sites: 0, version: "1.24.0" },
    apache: { installed: true, running: false, starts_at_boot: false, applications: 0, sites: 0, version: "2.4.58" },
  },
};

export const NOT_INSTALLED: WebserverStatus = {
  ...STATUS,
  servers: { ...STATUS.servers, apache: { installed: false, running: false, starts_at_boot: false, applications: 0, sites: 0 } },
};

export const PLAN: MovePlan = {
  source: "nginx",
  target: "apache",
  mode: "handover",
  source_running: true,
  target_installed: true,
  target_running: false,
  install_command: "",
  sites: [
    {
      domain: "shop.example.net",
      kind: "app",
      source: "nginx",
      target: "apache",
      source_path: "/etc/nginx/sites-available/shop.example.net",
      target_path: "/etc/apache2/sites-available/shop.example.net.conf",
      operator_site: false,
      keeps_operator_site: false,
      movable: true,
      findings: [
        {
          reason: "protection",
          severity: "loses",
          directive: "limit_req",
          line: 12,
          protection: true,
          code: "webserver_move.protection",
          params: { directive: "limit_req", line: 12, target: "Apache" },
          text: "limit_req (line 12) protects the site, and Apache has nothing equivalent",
          hint: "The new site would not be protected that way. Put another protection in front of it first, or leave the application where it is.",
        },
      ],
    },
    {
      domain: "blog.example.org",
      kind: "app",
      source: "nginx",
      target: "apache",
      source_path: "/etc/nginx/sites-available/blog.example.org",
      target_path: "/etc/apache2/sites-available/blog.example.org.conf",
      operator_site: false,
      keeps_operator_site: false,
      movable: true,
      findings: [],
    },
  ],
  blocked: false,
  losses: 1,
  confirmation: {
    code: "webserver_move.confirm_handover",
    params: { target: "Apache", source: "nginx", count: 2 },
    text: "Apache will take over ports 80 and 443 from nginx for 2 site(s)",
  },
};

export const BLOCKED: MovePlan = {
  ...PLAN,
  target_installed: false,
  install_command: "apt-get install -y apache2",
  blocked: true,
  blockers: [
    {
      code: "webserver_move.target_missing",
      params: { target: "Apache", command: "apt-get install -y apache2" },
      text: "Apache is not installed on this server",
      hint: "Install it first: apt-get install -y apache2",
    },
  ],
};

export const JOB = {
  id: "move-1",
  type: "webserver_move",
  name: "Moving 2 site(s) from nginx to Apache",
  description: "",
  status: "completed",
  progress: 100,
  total_steps: 100,
  current_step: "",
  created_at: "2026-10-30T10:00:00",
  logs: [{ message: "Apache accepted every site" }, { message: "shop.example.net answered through Apache" }],
  result: { source: "nginx", target: "apache", mode: "handover", sites: [], modules: [], losses: 1, warnings: [] },
  error: null,
};

/** The reads of the section, for `fakeBackend`. */
export function webserverRoutes(overrides: { status?: WebserverStatus; plan?: MovePlan } = {}): Record<string, RouteHandler> {
  return {
    "GET /api/webserver/status": () => json(200, overrides.status ?? STATUS),
    "GET /api/webserver/migration/plan": () => json(200, overrides.plan ?? PLAN),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
  };
}
