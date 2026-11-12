import { describe, expect, it } from "vitest";

import { crumbsFor } from "./crumbs";
import type { CrumbName } from "./crumbs";

const ids = (route: string, scope: "server" | "fleet" = "server"): string[] => crumbsFor(route, scope).map((step) => step.id);
const nameOf = (name: CrumbName): string => ("key" in name ? name.key : `${name.prefix ?? ""}<${name.param}>`);

describe("the breadcrumbs the register gives a page", () => {
  it("has none on the first page of an area: the bar already says where it is", () => {
    for (const route of ["/", "/apps", "/databases", "/domains", "/server", "/settings", "/catalog", "/alerts", "/fleet", "/fleet/apps"]) {
      expect(crumbsFor(route, route.startsWith("/fleet") ? "fleet" : "server"), route).toEqual([]);
    }
  });

  it("makes the Server step the page of the server the person is on, and the list of servers from All servers", () => {
    expect(crumbsFor("/server/files", "server")[0]?.to).toBe("/server");
    expect(crumbsFor("/server/files", "fleet")[0]?.to).toBe("/fleet/servers");
    // Only that area: an application's step is where it was.
    expect(crumbsFor("/apps/$domain/deployments", "server")[0]?.to).toBe("/apps");
    // The machine's other areas open their own page, and the list of servers from All servers.
    expect(crumbsFor("/network/firewall", "server")[0]?.to).toBe("/network");
    expect(crumbsFor("/network/firewall", "fleet")[0]?.to).toBe("/fleet/network");
  });

  it("names the Server step as the bar does: Server on a server's own pages, Servers from All servers", () => {
    // "Servers" in the trail under a bar that said "Server" read as two different places (item 154).
    expect(nameOf(crumbsFor("/server/files", "server")[0]?.name ?? { param: "?" })).toBe("nav.server.labelOne");
    expect(nameOf(crumbsFor("/server/files", "fleet")[0]?.name ?? { param: "?" })).toBe("nav.server.label");
    // No other area has a name of one: Applications is Applications wherever the person is.
    expect(nameOf(crumbsFor("/apps/$domain/deployments", "server")[0]?.name ?? { param: "?" })).toBe("nav.apps.label");
  });

  it("says the area, the application by its domain, and the section, for an application's page", () => {
    expect(ids("/apps/$domain/deployments")).toEqual(["apps", "app", "app.deployments"]);
    const steps = crumbsFor("/apps/$domain/deployments", "server");
    expect(steps.map((step) => nameOf(step.name))).toEqual(["nav.apps.label", "<domain>", "nav.appTabs.deployments.label"]);
    // The application is the step that is a resource of its list, so it has a switch to the other applications.
    expect(steps.map((step) => step.resource)).toEqual([false, true, false]);
  });

  it("stops at the application on its first page, and does not repeat its name as the overview", () => {
    expect(ids("/apps/$domain/")).toEqual(["apps", "app"]);
  });

  it("goes down to a deployment, which is no entry of the register but is opened from one", () => {
    const steps = crumbsFor("/apps/$domain/deployments/$id", "server");
    expect(steps.map((step) => step.id)).toEqual(["apps", "app", "app.deployments", "app.deployments.page"]);
    expect(nameOf(steps.at(-1)?.name ?? { key: "x" as never })).toBe("#<id>");
  });

  it("does not take a section of an application for the application: Diagnose is a step, not a resource", () => {
    const steps = crumbsFor("/apps/$domain/diagnose", "server");
    expect(steps.map((step) => nameOf(step.name))).toEqual(["nav.apps.label", "<domain>", "appPages.actions.diagnose"]);
  });

  it("goes through the sections of an application, three levels", () => {
    expect(ids("/apps/$domain/code")).toEqual(["apps", "app", "app.code"]);
  });

  it("says Settings and the section for the settings, and Alerts for what moved out of them", () => {
    expect(ids("/settings/notifications")).toEqual(["settings", "settings.notifications"]);
    expect(ids("/alerts/probes")).toEqual(["alerts", "alerts.probes"]);
  });

  it("puts Projects and Teams under Applications, and a team by its name", () => {
    expect(ids("/projects/teams")).toEqual(["apps", "apps.projects", "apps.projects.teams"]);
    expect(ids("/teams/$team/members")).toEqual(["apps", "apps.projects", "apps.projects.teams", "team", "team.members"]);
  });

  it("says an area once from the fleet's pages, where its fleet face is the area", () => {
    expect(ids("/fleet/placements", "fleet")).toEqual(["apps", "fleet.apps.placements"]);
    // The copies the central holds are the second view of the fleet's Backups.
    expect(ids("/fleet/central-backups", "fleet")).toEqual(["backups", "fleet.backups.central"]);
    // The updates of every server are the second view of the fleet's Servers, a step of that area as Placements is of Applications.
    expect(ids("/fleet/updates", "fleet")).toEqual(["server", "fleet.servers.updates"]);
  });

  it("opens the area at its fleet face from the fleet's pages and at its own page from a server's", () => {
    expect(crumbsFor("/fleet/placements", "fleet")[0]?.to).toBe("/fleet/apps");
    expect(crumbsFor("/apps/$domain/logs", "server")[0]?.to).toBe("/apps");
  });

  it("has none for a route the register does not know, which is a page outside the console", () => {
    expect(crumbsFor("/login", "server")).toEqual([]);
    expect(crumbsFor("", "server")).toEqual([]);
  });

  it("opens a service from the machine's Services tab, and its unit file from the service", () => {
    expect(ids("/server/services/$name")).toEqual(["server", "server.services", "service"]);
    expect(ids("/server/services/$name/unit")).toEqual(["server", "server.services", "service", "service.page"]);
  });
});
