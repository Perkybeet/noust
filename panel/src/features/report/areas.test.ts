import { describe, expect, it } from "vitest";

import { AREAS, areaForPath } from "./areas";

describe("where a problem happens", () => {
  // The options are held to the form's own, one by one, by tests/test_console_report_contract.py.
  it("offers each option once, with a text of its own in the catalog", () => {
    expect(new Set(AREAS.map((area) => area.value)).size).toBe(AREAS.length);
    expect(new Set(AREAS.map((area) => area.key)).size).toBe(AREAS.length);
    expect(AREAS.at(-1)?.value).toBe("Other");
  });

  it.each([
    ["/apps", "Applications and deploys"],
    ["/apps/shop.example.com/deployments", "Applications and deploys"],
    ["/catalog", "Recipes (the catalogue)"],
    ["/databases", "Databases"],
    ["/databases/postgresql/shop_prod/data", "Databases"],
    ["/domains", "Domains and certificates"],
    ["/network", "Network, DNS and the web server"],
    ["/server/services", "The server (services, monitoring, software, updates)"],
    ["/server/services/nginx.service", "The server (services, monitoring, software, updates)"],
    ["/logs", "The server (services, monitoring, software, updates)"],
    ["/alerts", "The server (services, monitoring, software, updates)"],
    ["/cron", "The server (services, monitoring, software, updates)"],
    ["/backups", "Backups and restore"],
    ["/security", "Security, accounts and access"],
    ["/account", "Security, accounts and access"],
    ["/teams/platform", "Security, accounts and access"],
    ["/settings/about", "Console (the browser)"],
    ["/", "Console (the browser)"],
    ["/fleet", "Fleet (a central and its servers)"],
    ["/fleet/updates", "Fleet (a central and its servers)"],
    ["/nowhere/at/all", "Other"],
  ])("puts %s under %s", (path, area) => {
    expect(areaForPath(path)).toBe(area);
  });

  it("always answers one of the options", () => {
    const options = new Set<string>(AREAS.map((area) => area.value));

    for (const path of ["/", "/apps", "/databases/x/y", "/fleet", "/zzz", "/settings/security", "/activity"]) {
      expect(options.has(areaForPath(path))).toBe(true);
    }
  });
});
