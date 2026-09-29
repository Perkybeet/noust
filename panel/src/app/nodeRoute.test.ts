import { describe, expect, it } from "vitest";

import { isCentralOnlyPath, nodeFromSearch, nodeRewrite, serverPath, switchTarget, validateNodeSearch } from "./nodeRoute";

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

  it("never carries a node on a page of the central's only", () => {
    expect(input("/n/web-2/settings/security")).toBe("/settings/security");
    expect(input("/n/web-2/login?next=%2F")).toBe("/login?next=%2F");
    expect(input("/settings?node=web-2")).toBe("/settings");
    expect(output("/settings/tokens?node=web-2")).toBe("/settings/tokens");
    expect(output("/fleet?node=web-2")).toBe("/fleet");
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

  it("builds the address of a page on a server", () => {
    expect(serverPath("web-2", "/apps")).toBe("/n/web-2/apps");
    expect(serverPath("web-2", "/")).toBe("/n/web-2");
    expect(serverPath(null, "/apps")).toBe("/apps");
    expect(serverPath("web-2", "/settings")).toBe("/settings");
    expect(isCentralOnlyPath("/settingsx")).toBe(false);
  });
});

describe("where switching servers lands", () => {
  it("keeps a page every server has", () => {
    expect(switchTarget("/apps", "/_console/apps/")).toBe("/apps");
    expect(switchTarget("/apps/new", "/_console/apps/new")).toBe("/apps/new");
    expect(switchTarget("/server", "/_console/server")).toBe("/server");
  });

  it("goes to the overview from a page about one thing, or from the central's own", () => {
    expect(switchTarget("/apps/shop.example.com/logs", "/_console/apps/$domain/logs")).toBe("/");
    expect(switchTarget("/databases/postgresql/shop", "/_console/databases/$engine/$name")).toBe("/");
    expect(switchTarget("/settings/security", "/_console/settings/security")).toBe("/");
    expect(switchTarget("/nowhere", undefined)).toBe("/");
  });
});
