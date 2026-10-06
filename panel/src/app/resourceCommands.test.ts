import { describe, expect, it, vi } from "vitest";

import type { CertEntry } from "../api/queries/certs";
import type { Database } from "../api/queries/databases";
import type { SiteEntry } from "../api/queries/sites";
import { bindT, loadCatalog } from "../i18n";
import { filterCommands } from "./CommandPalette";
import { RECENT_DEPLOYMENTS, resourceCommands } from "./resourceCommands";
import type { DeploymentEntry, NavTarget, ResourceIndex, ServiceEntry } from "./resourceCommands";

const DATABASE = {
  engine: "postgresql",
  name: "shop_prod",
  app: "shop.example.com",
  apps: ["shop.example.com"],
  username: "shop",
  missing: false,
  tracked: true,
  unverified: false,
} as Database;

const SITE = {
  name: "shop.example.com",
  site_name: "shop.example.com",
  webserver: "nginx",
  enabled: true,
  config_path: "/etc/nginx/sites-available/shop.example.com",
  has_ssl: true,
  noust_managed: true,
  server_names: ["shop.example.com", "www.shop.example.com"],
  app: "shop.example.com",
} as SiteEntry;

const CERT = {
  domain: "shop.example.com",
  domains: ["shop.example.com", "www.shop.example.com"],
  days_remaining: 29,
  expires_on: "2026-10-24",
  auto_renew: true,
} as CertEntry;

const SERVICE = {
  name: "noust-web",
  description: "Noust console",
  active: true,
  enabled: true,
  status: "running",
  managed: true,
} as ServiceEntry;

const DEPLOYMENT = {
  id: 12,
  domain: "shop.example.com",
  status: "failed",
  triggered_by: "panel",
  git_commit: "c07d5e3",
  git_branch: "main",
  commit_message: "Fix the checkout total",
  has_log: true,
} as DeploymentEntry;

function build(overrides: Partial<ResourceIndex> = {}, locale: "en" | "es" = "en") {
  const navigate = vi.fn<(target: NavTarget) => void>();
  const commands = resourceCommands(
    {
      databases: [DATABASE],
      sites: [SITE],
      certificates: [CERT],
      services: [SERVICE],
      deployments: [DEPLOYMENT],
      ...overrides,
    },
    { t: bindT(locale), navigate },
  );
  return { commands, navigate };
}

function ids(commands: ReturnType<typeof build>["commands"], query: string): string[] {
  return filterCommands(commands, query).flatMap((group) => group.items.map((item) => item.id));
}

describe("resourceCommands", () => {
  it("makes one command per resource, in its own group, each opening the page that shows it", () => {
    const { commands, navigate } = build();
    const byGroup = Object.fromEntries(commands.map((command) => [command.group, command]));
    expect(Object.keys(byGroup).sort()).toEqual(["Certificates", "Databases", "Deployments", "Domains", "Services"]);

    byGroup["Databases"]?.run();
    expect(navigate).toHaveBeenLastCalledWith({ to: "/databases/$engine/$name", params: { engine: "postgresql", name: "shop_prod" } });
    byGroup["Domains"]?.run();
    expect(navigate).toHaveBeenLastCalledWith({ to: "/domains/sites/$site", params: { site: "shop.example.com" } });
    byGroup["Certificates"]?.run();
    expect(navigate).toHaveBeenLastCalledWith({ to: "/domains", search: { q: "shop.example.com" } });
    byGroup["Services"]?.run();
    expect(navigate).toHaveBeenLastCalledWith({ to: "/server/services/$name", params: { name: "noust-web" } });
    byGroup["Deployments"]?.run();
    expect(navigate).toHaveBeenLastCalledWith({ to: "/apps/$domain/deployments/$id", params: { domain: "shop.example.com", id: "12" } });
    for (const command of commands) expect(command.kind).toBe("navigate");
  });

  it("gives every command an id of its own, so two resources with one name never collide", () => {
    const { commands } = build({ databases: [DATABASE, { ...DATABASE, engine: "mysql" }] });
    expect(new Set(commands.map((command) => command.id)).size).toBe(commands.length);
  });

  it("finds a database by its name, its engine, its owner and the application that uses it", () => {
    const { commands } = build();
    expect(ids(commands, "shop_prod")).toContain("database:postgresql:shop_prod");
    expect(ids(commands, "postgresql")).toContain("database:postgresql:shop_prod");
    expect(ids(commands, "shop.example")).toContain("database:postgresql:shop_prod");
  });

  it("finds a site by any name it answers on, not only the one its file is called", () => {
    const { commands } = build();
    expect(ids(commands, "www.shop")).toContain("site:shop.example.com");
  });

  it("finds a certificate by any name it covers and says how long it has left", () => {
    const { commands } = build();
    const cert = commands.find((command) => command.group === "Certificates");
    expect(cert?.label).toBe("shop.example.com");
    expect(cert?.detail).toBe("29 days left");
    expect(ids(commands, "www.shop")).toContain("certificate:shop.example.com");
  });

  it("says a certificate has expired, or says nothing when it has no date", () => {
    const expired = build({ certificates: [{ ...CERT, days_remaining: -3 }] }).commands.find((command) => command.group === "Certificates");
    expect(expired?.detail).toBe("Expired");
    const unknown = build({ certificates: [{ ...CERT, days_remaining: null }] }).commands.find((command) => command.group === "Certificates");
    expect(unknown?.detail).toBeUndefined();
  });

  it("shows a service with its state, and finds it by its description", () => {
    const { commands } = build();
    const service = commands.find((command) => command.group === "Services");
    expect(service?.status).toBe("running");
    expect(service?.detail).toBe("Noust console");
    expect(ids(commands, "console")).toContain("service:noust-web");
  });

  it("shows a deployment as its application and number, with its state and what it deployed", () => {
    const { commands } = build();
    const deployment = commands.find((command) => command.group === "Deployments");
    expect(deployment?.label).toBe("shop.example.com: deploy 12");
    expect(deployment?.status).toBe("failed");
    expect(deployment?.detail).toBe("Fix the checkout total");
    expect(ids(commands, "c07d5e3")).toContain("deployment:12");
    expect(ids(commands, "checkout total")).toContain("deployment:12");
  });

  it("falls back to the commit when a deployment has no message, and to nothing when it has neither", () => {
    const noMessage = build({ deployments: [{ ...DEPLOYMENT, commit_message: null }] }).commands.find((command) => command.group === "Deployments");
    expect(noMessage?.detail).toBe("c07d5e3");
    const neither = build({ deployments: [{ ...DEPLOYMENT, commit_message: null, git_commit: null }] }).commands.find((command) => command.group === "Deployments");
    expect(neither?.detail).toBeUndefined();
  });

  it("keeps only the newest deployments, however many the list holds", () => {
    const many = Array.from({ length: RECENT_DEPLOYMENTS + 15 }, (_, index) => ({ ...DEPLOYMENT, id: 1000 - index }));
    const { commands } = build({ deployments: many });
    const kept = commands.filter((command) => command.group === "Deployments");
    expect(kept).toHaveLength(RECENT_DEPLOYMENTS);
    expect(kept[0]?.id).toBe("deployment:1000");
  });

  it("is empty for an index that has not arrived", () => {
    expect(build({ databases: undefined, sites: undefined, certificates: undefined, services: undefined, deployments: undefined }).commands).toEqual([]);
  });

  it("works in Spanish as it does in English", async () => {
    await loadCatalog("es");
    const { commands } = build({}, "es");
    expect(commands.find((command) => command.group === "Certificates")?.detail).toBe("Quedan 29 días");
    expect(commands.find((command) => command.group === "Deployments")?.label).toBe("shop.example.com: despliegue 12");
  });
});
