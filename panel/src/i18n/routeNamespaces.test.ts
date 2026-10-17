import type { ParsedLocation } from "@tanstack/react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { goCold } from "../test/coldCatalog";
import { NAMESPACES, areNamespacesLoaded, catalogsIdle, hasNamespace, loadCatalog } from "./catalogs";
import { installRouteIds, loadRouteNamespaces, namespacesForRoutes } from "./routeNamespaces";
import { ROUTE_NAMESPACES } from "./routeNamespaces.gen";

/**
 * What the router loads before it shows a page: the namespaces its routes draw, from the map the
 * generator writes from the source. A route the map does not know loads everything, because showing
 * keys is worse than waiting once.
 */

let restore: (() => Promise<void>) | undefined;

afterEach(async () => {
  await restore?.();
  restore = undefined;
  await catalogsIdle();
  await loadCatalog("en");
  installRouteIds(() => []);
});

const location = (pathname: string): ParsedLocation => ({ pathname }) as ParsedLocation;

describe("the namespaces of a match", () => {
  it("are the union of what each of its routes draws, once each", () => {
    const wanted = namespacesForRoutes(["__root__", "/_console", "/_console/databases/"]);
    expect(new Set(wanted).size).toBe(wanted.length);
    for (const namespace of ROUTE_NAMESPACES["/_console/databases/"] ?? []) expect(wanted).toContain(namespace);
    expect(wanted).toContain("databases");
  });

  it("are all of them when a route is not in the map: a page nobody described is slow once, not wrong", () => {
    expect(namespacesForRoutes(["__root__", "/_console", "/_console/not-generated-yet"])).toEqual(NAMESPACES);
  });

  it("are none for the roots that only frame a page", () => {
    expect(namespacesForRoutes(["__root__", "/_console"])).toEqual([]);
  });

  it("only name namespaces that exist", () => {
    for (const [route, namespaces] of Object.entries(ROUTE_NAMESPACES)) {
      expect(namespaces.filter((namespace) => !NAMESPACES.includes(namespace)), route).toEqual([]);
    }
  });
});

describe("loading them", () => {
  it("asks for nothing, not even a tick, when it is all here", () => {
    installRouteIds(() => ["__root__", "/_console", "/_console/databases/"]);
    expect(loadRouteNamespaces("en", location("/databases"))).toBeUndefined();
  });

  it("fetches what the routes of the address draw, and is done when they are here", async () => {
    restore = goCold().restore;
    installRouteIds(() => ["__root__", "/_console", "/_console/databases/"]);
    const request = loadRouteNamespaces("en", location("/databases"));
    expect(request).toBeInstanceOf(Promise);
    expect(hasNamespace("en", "databases")).toBe(false);
    await request;
    expect(areNamespacesLoaded("en", namespacesForRoutes(["/_console/databases/"]))).toBe(true);
    expect(loadRouteNamespaces("en", location("/databases"))).toBeUndefined();
  });

  it("fetches them in the active language", async () => {
    restore = goCold().restore;
    installRouteIds(() => ["/_console/databases/"]);
    await loadRouteNamespaces("es", location("/databases"));
    expect(hasNamespace("es", "databases")).toBe(true);
    expect(hasNamespace("en", "databases")).toBe(false);
  });

  it("shows the page anyway when a chunk cannot be fetched, and says so", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    installRouteIds(() => ["/_console/databases/"]);
    // A language nobody registered has no chunk to fetch: the failure is the same as a lost connection.
    await expect(loadRouteNamespaces("zz" as never, location("/databases"))).resolves.toBeUndefined();
    expect(error).toHaveBeenCalledWith(expect.stringContaining("Could not load the zz texts of this page"), expect.any(Error));
  });
});
