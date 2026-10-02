import { describe, expect, it } from "vitest";

import type { SiteLocation, SiteServer } from "../../api/queries/sites";
import {
  addLocationOp,
  changedIds,
  joinArgs,
  listenChip,
  listenGroups,
  locationsInOrder,
  matchKind,
  moveOp,
  otherDirectives,
  removeLocationOp,
  settingOps,
  settingText,
  splitArgs,
  websocketOps,
} from "./siteStructure";
import { PROGGEST_STRUCTURE, SMALL_EDIT_ADD_HEALTH, SMALL_EDIT_TIMEOUT, SMALL_STRUCTURE } from "./siteTestFixtures";

function server(index: number, structure = SMALL_STRUCTURE): SiteServer {
  const found = structure.servers[index];
  if (found === undefined) throw new Error(`no server ${String(index)}`);
  return found;
}

function location(id: string, structure = SMALL_STRUCTURE): SiteLocation {
  for (const candidate of structure.servers) {
    const found = candidate.locations.find((entry) => entry.id === id);
    if (found !== undefined) return found;
  }
  throw new Error(`no location ${id}`);
}

describe("reading a site's structure", () => {
  it("lists locations in the order nginx tries them, not the file's", () => {
    const order = locationsInOrder(server(1)).map(({ location: entry }) => entry.path);
    // Exact first, then prefixes longest first, then regular expressions in file order.
    expect(order).toEqual(["/old", "/api/v1/auth/login", "/assets/", "/api/", "/\\."]);
    const proggest = locationsInOrder(server(1, PROGGEST_STRUCTURE)).map(({ location: entry }) => entry.path);
    expect(proggest.indexOf("/api/v1/auth/login")).toBeLessThan(proggest.indexOf("/api/"));
    expect(proggest).toHaveLength(25);
  });

  it("names each match type", () => {
    expect(matchKind({ modifier: "=", path: "/old" })).toBe("exact");
    expect(matchKind({ modifier: "", path: "/api/" })).toBe("prefix");
    expect(matchKind({ modifier: "^~", path: "/static/" })).toBe("prefixNoRegex");
    expect(matchKind({ modifier: "~", path: "\\.php$" })).toBe("regex");
    expect(matchKind({ modifier: "~*", path: "\\.(png|jpg)$" })).toBe("regexCaseless");
    expect(matchKind({ modifier: "", path: "@fallback" })).toBe("named");
  });

  it("groups listens by port, on both IP families, with HTTP/2 from the server", () => {
    const groups = listenGroups(server(1));
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ port: "443", tls: true, http2: true, ipv4: true, ipv6: true });
    expect(groups.map(listenChip)).toEqual(["443 · TLS · HTTP/2"]);
    expect(listenGroups(server(0)).map(listenChip)).toEqual(["80"]);
  });

  it("keeps quoted arguments whole and quotes them back", () => {
    expect(splitArgs('Strict-Transport-Security "max-age=63072000; includeSubDomains" always')).toEqual([
      "Strict-Transport-Security",
      "max-age=63072000; includeSubDomains",
      "always",
    ]);
    expect(splitArgs("  zone=auth_limit   burst=20 nodelay ")).toEqual(["zone=auth_limit", "burst=20", "nodelay"]);
    expect(splitArgs('"unclosed')).toBeNull();
    expect(splitArgs('""')).toEqual([""]);
    expect(joinArgs(["Connection", "upgrade"])).toBe("Connection upgrade");
    expect(joinArgs(["X", "a b", ""])).toBe('X "a b" ""');
  });

  it("marks what differs from the saved file, matched by what it is rather than by position", () => {
    expect(changedIds(SMALL_STRUCTURE, SMALL_STRUCTURE).size).toBe(0);
    expect([...changedIds(SMALL_STRUCTURE, SMALL_EDIT_TIMEOUT.structure)]).toEqual(["s1/l0"]);
    // A location added: only it is new; the others kept their text even if their ids moved.
    const added = changedIds(SMALL_STRUCTURE, SMALL_EDIT_ADD_HEALTH.structure);
    const health = SMALL_EDIT_ADD_HEALTH.structure.servers[1]?.locations.find((entry) => entry.path === "/health");
    expect(health).toBeDefined();
    expect([...added]).toEqual([health?.id]);
    expect(changedIds(null, SMALL_STRUCTURE).size).toBe(0);
  });
});

describe("the operations the Structure view sends", () => {
  it("sets, adds and removes an inline setting's directive", () => {
    const login = location("s1/l0");
    expect(settingText(login, "read_timeout")).toBe("");
    expect(settingOps(login, "read_timeout", "90s")).toEqual([{ op: "set_directive", parent: "s1/l0", name: "proxy_read_timeout", args: ["90s"] }]);
    expect(settingText(login, "limit_req")).toBe("zone=auth_limit burst=20 nodelay");
    expect(settingOps(login, "limit_req", "")).toEqual([{ op: "remove_directive", target: "s1/l0/d0" }]);
    expect(settingOps(login, "limit_req", '"open')).toBeNull();

    const api = location("s1/l1");
    expect(settingText(api, "read_timeout")).toBe("120s");
    expect(settingText(api, "buffering")).toBe("off");
    expect(settingOps(api, "buffering", "on")).toEqual([{ op: "set_directive", parent: "s1/l1", name: "proxy_buffering", args: ["on"] }]);
  });

  it("turns WebSocket on with HTTP/1.1 and both headers, and off by removing the headers last first", () => {
    const login = location("s1/l0");
    expect(websocketOps(login, true)).toEqual([
      { op: "set_directive", parent: "s1/l0", name: "proxy_http_version", args: ["1.1"] },
      { op: "add_directive", parent: "s1/l0", name: "proxy_set_header", args: ["Upgrade", "$http_upgrade"] },
      { op: "add_directive", parent: "s1/l0", name: "proxy_set_header", args: ["Connection", "upgrade"] },
    ]);
    const api = location("s1/l1");
    expect(websocketOps(api, false)).toEqual([
      { op: "remove_directive", target: "s1/l1/d3" },
      { op: "remove_directive", target: "s1/l1/d2" },
    ]);
  });

  it("leaves the inline settings out of More directives", () => {
    const names = otherDirectives(location("s1/l1")).map((directive) => directive.name);
    expect(names).toEqual(["proxy_pass", "proxy_http_version", "proxy_set_header", "proxy_set_header"]);
  });

  it("adds a location from a template, moves it among its siblings and removes it", () => {
    expect(addLocationOp("nginx", { server: "s1", modifier: "", path: "/health", template: "proxy", to: "shop_backend" }, SMALL_STRUCTURE.upstreams)).toEqual({
      op: "add_block",
      parent: "s1",
      name: "location",
      args: ["/health"],
      template: "proxy",
      to: "http://shop_backend",
    });
    expect(addLocationOp("nginx", { server: "s1", modifier: "=", path: "/old", template: "redirect", to: "/new", code: 302 }, [])).toMatchObject({
      args: ["=", "/old"],
      template: "redirect",
      code: 302,
    });
    const siblings = server(1).locations;
    expect(moveOp(siblings, location("s1/l0"), "up")).toBeNull();
    expect(moveOp(siblings, location("s1/l0"), "down")).toEqual({ op: "move_block", target: "s1/l0", after: "s1/l1" });
    expect(moveOp(siblings, location("s1/l4"), "up")).toEqual({ op: "move_block", target: "s1/l4", before: "s1/l3" });
    expect(removeLocationOp(location("s1/l2"))).toEqual({ op: "remove_block", target: "s1/l2" });
  });
});
