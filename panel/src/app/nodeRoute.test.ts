import { describe, expect, it } from "vitest";

import { contextOf, isCentralOnlyPath, nodeFromSearch, nodeOfConsolePath, nodeRewrite, onNodeDropped, returnTarget, serverPath, switchTarget, validateNodeSearch } from "./nodeRoute";

function input(href: string): string {
  const url = new URL(href, "http://console.test");
  const result = nodeRewrite.input?.({ url }) ?? url;
  const rewritten = typeof result === "string" ? new URL(result) : result;
  return rewritten.pathname + rewritten.search;
}

function output(href: string): string {
  const url = new URL(href, "http://console.test");
  const result = nodeRewrite.output?.({ url }) ?? url;
  const rewritten = typeof result === "string" ? new URL(result) : result;
  return rewritten.pathname + rewritten.search;
}

describe("the node in the URL", () => {
  it("reads /n/{node}/... as the same page with the node as a search parameter", () => {
    expect(input("/n/web-2/apps/shop.example.com/logs")).toBe("/apps/shop.example.com/logs?node=%22web-2%22");
    expect(input("/n/web-2")).toBe("/?node=%22web-2%22");
    expect(input("/n/web-2/")).toBe("/?node=%22web-2%22");
    expect(input("/n/web-2/apps?q=shop")).toBe("/apps?q=shop&node=%22web-2%22");
  });

  it("leaves this server's addresses alone", () => {
    expect(input("/apps/shop.example.com")).toBe("/apps/shop.example.com");
    expect(input("/")).toBe("/");
    expect(input("/n")).toBe("/n");
  });

  it("never carries a node on a page of the fleet's or the central's", () => {
    expect(input("/n/web-2/settings/security")).toBe("/settings/security");
    expect(input("/n/web-2/login?next=%2F")).toBe("/login?next=%2F");
    expect(input("/settings/servers?node=web-2")).toBe("/settings/servers");
    expect(output("/settings/tokens?node=web-2")).toBe("/settings/tokens");
    expect(output("/fleet?node=web-2")).toBe("/fleet");
    expect(output("/fleet/apps?node=web-2")).toBe("/fleet/apps");
  });

  it("keeps a server's own settings on that server", () => {
    expect(input("/n/web-2/settings")).toBe("/settings?node=%22web-2%22");
    expect(input("/n/web-2/settings/notifications")).toBe("/settings/notifications?node=%22web-2%22");
    expect(output("/settings/about?node=web-2")).toBe("/n/web-2/settings/about");
  });

  it("remembers the server an old address named on a page that cannot carry one", () => {
    const dropped: string[] = [];
    const restore = onNodeDropped((node) => dropped.push(node));
    try {
      expect(input("/n/web-2/fleet")).toBe("/fleet");
      expect(input("/n/db-1/settings/tokens")).toBe("/settings/tokens");
      expect(input("/n/db-1/apps")).toBe("/apps?node=%22db-1%22");
    } finally {
      restore();
    }
    expect(dropped).toEqual(["web-2", "db-1"]);
  });

  it("writes a location with a node back as /n/{node}/...", () => {
    expect(output("/apps?node=web-2")).toBe("/n/web-2/apps");
    expect(output("/?node=web-2")).toBe("/n/web-2");
    expect(output("/apps?q=shop&node=web-2")).toBe("/n/web-2/apps?q=shop");
    // The router quotes a name that would otherwise read as a number.
    expect(output("/apps?node=%22123%22")).toBe("/n/123/apps");
    expect(output("/apps")).toBe("/apps");
  });

  it("round-trips", () => {
    for (const path of ["/n/web-2/apps/shop.example.com/deployments/42", "/n/web-2", "/n/db-1/databases/postgresql/shop"]) {
      const internal = new URL(input(path), "http://console.test");
      // What the router would stringify back: the plain name.
      internal.searchParams.set("node", nodeFromSearch({ node: JSON.parse(internal.searchParams.get("node") ?? "null") as unknown }) ?? "");
      expect(output(internal.pathname + internal.search)).toBe(path);
    }
  });

  it("validates the search parameter", () => {
    expect(validateNodeSearch({ node: "web-2" })).toEqual({ node: "web-2" });
    expect(validateNodeSearch({ node: 123 })).toEqual({ node: "123" });
    expect(validateNodeSearch({ node: "" })).toEqual({});
    expect(validateNodeSearch({})).toEqual({});
    expect(validateNodeSearch({ node: { evil: true } })).toEqual({});
  });

  it("splits an address-bar path into the node it names and the plain path, for a caller that cannot trust navigate({ href })", () => {
    expect(nodeOfConsolePath("/n/web-2/apps/shop.example.com/logs")).toEqual({ node: "web-2", pathname: "/apps/shop.example.com/logs" });
    expect(nodeOfConsolePath("/n/web-2")).toEqual({ node: "web-2", pathname: "/" });
    expect(nodeOfConsolePath("/n/web-2/")).toEqual({ node: "web-2", pathname: "/" });
    // Already the router's own form, or of the central's only: nothing to split out.
    expect(nodeOfConsolePath("/apps?node=web-2")).toEqual({ node: null, pathname: "/apps?node=web-2" });
    expect(nodeOfConsolePath("/apps/shop.example.com")).toEqual({ node: null, pathname: "/apps/shop.example.com" });
    expect(nodeOfConsolePath("/")).toEqual({ node: null, pathname: "/" });
  });

  it("builds the address of a page on a server", () => {
    expect(serverPath("web-2", "/apps")).toBe("/n/web-2/apps");
    expect(serverPath("web-2", "/")).toBe("/n/web-2");
    expect(serverPath(null, "/apps")).toBe("/apps");
    expect(serverPath("web-2", "/settings")).toBe("/n/web-2/settings");
    expect(serverPath("web-2", "/settings/security")).toBe("/settings/security");
    expect(serverPath("web-2", "/fleet/apps")).toBe("/fleet/apps");
    expect(isCentralOnlyPath("/settingsx")).toBe(false);
    expect(isCentralOnlyPath("/fleetx")).toBe(false);
  });
});

describe("where switching servers lands", () => {
  it("keeps a page every server has", () => {
    expect(switchTarget("/apps", "/_console/apps/")).toBe("/apps");
    expect(switchTarget("/apps/new", "/_console/apps/new")).toBe("/apps/new");
    expect(switchTarget("/server", "/_console/server")).toBe("/server");
  });

  it("goes to the overview from a page about one thing, or from the fleet's", () => {
    expect(switchTarget("/apps/shop.example.com/logs", "/_console/apps/$domain/logs")).toBe("/");
    expect(switchTarget("/databases/postgresql/shop", "/_console/databases/$engine/$name")).toBe("/");
    expect(switchTarget("/fleet/certificates", "/_console/fleet/certificates")).toBe("/");
    expect(switchTarget("/nowhere", undefined)).toBe("/");
  });

  it("goes to the server's own settings from the central's", () => {
    expect(switchTarget("/settings/security", "/_console/settings/security")).toBe("/settings");
    expect(returnTarget("/settings/servers")).toBe("/settings");
    expect(returnTarget("/fleet/jobs/abc")).toBe("/");
  });
});

describe("the three contexts", () => {
  it("tells a server's page from the fleet's and the central's", () => {
    expect(contextOf("/apps", null)).toEqual({ kind: "server", node: null });
    expect(contextOf("/apps", "web-2")).toEqual({ kind: "server", node: "web-2" });
    expect(contextOf("/settings", "web-2")).toEqual({ kind: "server", node: "web-2" });
    expect(contextOf("/settings/notifications", null)).toEqual({ kind: "server", node: null });
    expect(contextOf("/fleet", null)).toEqual({ kind: "fleet" });
    expect(contextOf("/fleet/jobs/abc", null)).toEqual({ kind: "fleet" });
    expect(contextOf("/settings/servers", null)).toEqual({ kind: "central" });
    expect(contextOf("/settings/central", null)).toEqual({ kind: "central" });
    expect(contextOf("/settings/tokens", null)).toEqual({ kind: "central" });
  });
});
