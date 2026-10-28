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

  it("goes through the sections of an application's settings, four levels at most", () => {
    expect(ids("/apps/$domain/settings/source")).toEqual(["apps", "app", "app.settings", "app.settings.source"]);
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
    // A screen of the fleet that is no area's face is a step of its area.
    expect(ids("/fleet/updates", "fleet")).toEqual(["server", "fleet.updates"]);
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
