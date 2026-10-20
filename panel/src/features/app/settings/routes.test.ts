import { describe, expect, it } from "vitest";

import type { PolicyFields } from "../../../api/queries/network";
import { changesOf, draftOf } from "./access";
import { draftOfRoute, errorsOfRoute, normalPath, routeOf, sorted, withRoute, withoutRoute } from "./routes";
import type { PolicyRoute, RouteDraft } from "./routes";

const proxy: PolicyRoute = { path: "/api", kind: "proxy", auth: "inherit", port: 4000, strip: true, to: "", code: 301, keep_path: true, headers: {} };
const admin: PolicyRoute = { path: "/admin", kind: "site", auth: "password", port: 0, strip: false, to: "", code: 301, keep_path: true, headers: {} };

const POLICY: PolicyFields = {
  basic_auth: false,
  realm: "Restricted",
  access: "all",
  allow_ips: [],
  deny_ips: [],
  real_ip: "off",
  real_ip_header: "",
  trusted_proxies: [],
  rate_limit: "",
  rate_burst: 20,
  connection_limit: 0,
  headers: {},
  hidden_headers: [],
  hsts: "default",
  noindex: false,
  canonical: "",
  http3: false,
  client_certs: false,
  force_https: true,
  waf: "",
  waf_exclusions: [] as number[],
  forward_auth: null,
  routes: [admin],
};

function draft(changes: Partial<RouteDraft>): RouteDraft {
  return { ...draftOfRoute(), ...changes };
}

describe("a route's path", () => {
  it("is trimmed and loses a trailing slash, as the server spells it", () => {
    expect(normalPath(" /admin/ ")).toBe("/admin");
    expect(normalPath("/")).toBe("/");
  });

  it("is checked the way the server checks it, before the server is asked", () => {
    for (const path of ["", "/", "admin", "/a//b", "/a b", "/a;b", "/a$b", "/.well-known"]) {
      const found = errorsOfRoute(draft({ path, auth: "open" }), []);
      // /.well-known is the server's to refuse (it is a valid shape); the others are not paths.
      if (path === "/.well-known") expect(found.path).toBeUndefined();
      else expect(found.path, path).toBe("network.access.routes.errors.path");
    }
    expect(errorsOfRoute(draft({ path: "/api/v2.1", auth: "open" }), []).path).toBeUndefined();
  });

  it("cannot be one the site already routes", () => {
    expect(errorsOfRoute(draft({ path: "/admin/", auth: "open" }), ["/admin"]).path).toBe("network.access.routes.errors.same");
  });
});

describe("a route's other fields", () => {
  it("a proxy needs a port the server could use", () => {
    for (const port of ["", "0", "65536", "80a", "-1"]) {
      expect(errorsOfRoute(draft({ path: "/api", kind: "proxy", port }), []).port, port).toBe("network.access.routes.errors.port");
    }
    expect(errorsOfRoute(draft({ path: "/api", kind: "proxy", port: "4000" }), [])).toEqual({});
  });

  it("a redirect needs a target", () => {
    expect(errorsOfRoute(draft({ path: "/old", kind: "redirect", to: " " }), []).to).toBe("network.access.routes.errors.target");
    expect(errorsOfRoute(draft({ path: "/old", kind: "redirect", to: "/new" }), [])).toEqual({});
  });

  it("a route of the site must change something", () => {
    expect(errorsOfRoute(draft({ path: "/x" }), []).auth).toBe("network.access.routes.errors.nothing");
    expect(errorsOfRoute(draft({ path: "/x", headers: "X-A: 1" }), [])).toEqual({});
    expect(errorsOfRoute(draft({ path: "/x", auth: "open" }), [])).toEqual({});
  });

  it("a header line that is not Name: value is named", () => {
    expect(errorsOfRoute(draft({ path: "/x", auth: "open", headers: "nonsense" }), []).headers).toBe("network.access.routes.errors.header");
  });
});

describe("a route as the server takes it", () => {
  it("keeps only what its kind uses", () => {
    expect(routeOf(draft({ path: "/api/", kind: "proxy", port: "4000", strip: true, auth: "password", to: "ignored", headers: "X-A: 1" }))).toEqual({
      path: "/api",
      kind: "proxy",
      auth: "password",
      port: 4000,
      strip: true,
      to: "",
      code: 301,
      keep_path: true,
      headers: { "X-A": "1" },
    });
    expect(routeOf(draft({ path: "/old", kind: "redirect", to: "/new", code: "308", keepPath: false, auth: "open", port: "9" }))).toEqual({
      path: "/old",
      kind: "redirect",
      auth: "inherit",
      port: 0,
      strip: false,
      to: "/new",
      code: 308,
      keep_path: false,
      headers: {},
    });
  });

  it("is read back into the dialog as it was", () => {
    const back = draftOfRoute(proxy);
    expect(back).toMatchObject({ path: "/api", kind: "proxy", port: "4000", strip: true, auth: "inherit" });
    expect(routeOf(back)).toEqual(proxy);
  });
});

describe("the list", () => {
  it("is kept longest path first, as the server returns it, and a route at a path replaces the old", () => {
    const listed = withRoute([admin], proxy);
    expect(listed.map((route) => route.path)).toEqual(["/admin", "/api"]);
    expect(sorted([proxy, admin]).map((route) => route.path)).toEqual(["/admin", "/api"]);
    const replaced = withRoute(listed, { ...admin, auth: "open" });
    expect(replaced).toHaveLength(2);
    expect(replaced.find((route) => route.path === "/admin")?.auth).toBe("open");
  });

  it("loses a route by its path", () => {
    expect(withoutRoute([admin, proxy], "/admin")).toEqual([proxy]);
  });
});

describe("the access form's routes", () => {
  it("send nothing while they are as stored, and the whole list once they differ", () => {
    const stored = draftOf(POLICY);
    expect(changesOf(stored, POLICY)).toEqual({ count: 0, body: {} });

    const changed = changesOf({ ...stored, routes: withRoute(stored.routes, proxy) }, POLICY);
    expect(changed.count).toBe(1);
    expect(changed.body.routes).toEqual([admin, proxy].sort((a, b) => b.path.length - a.path.length));
  });
});
