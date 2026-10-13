import { describe, expect, it } from "vitest";

import generated from "../routeTree.gen.ts?raw";
import { helpFor, helpRoutes, helpUrl } from "./help";

/** Every path of the console, as the router generated it. */
function consoleRoutes(): string[] {
  const block = /export interface FileRoutesByTo \{([\s\S]*?)\n\}/.exec(generated);
  if (block === null) throw new Error("routeTree.gen.ts has no FileRoutesByTo");
  return [...(block[1] ?? "").matchAll(/^\s+"([^"]+)":/gm)].flatMap((match) => (match[1] === undefined ? [] : [match[1]]));
}

/** Pages outside the console's own area: signing in, invitations, the first-run flow, a redirect target. */
const OUTSIDE = new Set(["/login", "/invite", "/setup", "/welcome", "/integrations/github/callback", "/integrations/gitlab/callback"]);

describe("helpFor", () => {
  it("finds the page for a path with its variable parts filled in", () => {
    expect(helpFor("/apps/shop.example.com/settings/build")).toMatchObject({ slug: "build", anchor: "the-build-block" });
    expect(helpFor("/databases/postgresql/shop/query").slug).toBe("databases");
  });

  it("takes the most specific entry, not the first", () => {
    expect(helpFor("/apps/new").slug).toBe("first-application");
    expect(helpFor("/apps/shop.example.com").anchor).toBe("one-application");
    expect(helpFor("/apps/shop.example.com/domains").slug).toBe("domains");
  });

  it("answers a path nobody wrote an entry for with the area it is in", () => {
    expect(helpFor("/apps/shop.example.com/settings/something-new").slug).toBe("releases");
    expect(helpFor("/server/services/nginx/logs").anchor).toBe("server");
  });

  it("answers a path of no area with the documentation of the console", () => {
    expect(helpFor("/nowhere/at/all").slug).toBe("console");
    expect(helpFor("/").anchor).toBe("overview");
  });

  it("does not take a static segment for a parameter", () => {
    // /apps/new is its own page, not an application called "new".
    expect(helpFor("/apps/new/anything").slug).toBe("first-application");
  });
});

describe("helpUrl", () => {
  it("points at the Markdown on GitHub until a documentation site is published", () => {
    expect(helpUrl({ slug: "build", source: "docs/build.md", anchor: "the-build-block" })).toBe(
      "https://github.com/Perkybeet/noust/blob/main/docs/build.md#the-build-block",
    );
    expect(helpUrl({ slug: "console", source: "docs/console.md" })).toBe("https://github.com/Perkybeet/noust/blob/main/docs/console.md");
  });
});

describe("the table", () => {
  it("covers every page of the console, with an entry of its own or of the area it is in", () => {
    const missing = consoleRoutes()
      .filter((route) => !OUTSIDE.has(route) && !route.startsWith("/_console"))
      .filter((route) => !helpRoutes().includes(route) && !prefixed(route));
    expect(missing).toEqual([]);
  });

  it("names no route the console does not have", () => {
    const real = new Set(consoleRoutes());
    const unknown = helpRoutes().filter((route) => !real.has(route));
    expect(unknown).toEqual([]);
  });

  it("has no route twice", () => {
    const routes = helpRoutes();
    expect(new Set(routes).size).toBe(routes.length);
  });
});

/** A route that has no entry of its own is acceptable when a parent area's entry covers it. */
function prefixed(route: string): boolean {
  const parts = route.split("/").filter(Boolean);
  for (let length = parts.length - 1; length >= 1; length -= 1) {
    const parent = `/${parts.slice(0, length).join("/")}`;
    if (helpRoutes().includes(parent)) return true;
  }
  return false;
}
