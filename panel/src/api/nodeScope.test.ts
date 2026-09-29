import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { createQueryClient } from "../app/App";
import { CENTRAL_ONLY_PATHS } from "../app/nodeRoute";
import { fakeBackend, json } from "../test/fakes";
import {
  activeNode,
  CENTRAL_API_PREFIXES,
  CENTRAL_QUERY_ROOTS,
  installNodeSource,
  isCentralApiPath,
  nodeApiPath,
  nodeEventsPath,
  nodeOfProxyPath,
  nodeOfQueryHash,
  nodeQueryKeyHash,
  nodeSocketPath,
  runOnNode,
} from "./nodeScope";

describe("the paths of a node", () => {
  it("sends an API call to the node through the central's proxy", () => {
    expect(nodeApiPath("web-2", "/api/apps")).toBe("/api/nodes/web-2/api/apps");
    expect(nodeApiPath("web-2", "/api/apps/shop.example.com/logs?lines=5")).toBe("/api/nodes/web-2/api/apps/shop.example.com/logs?lines=5");
    expect(nodeApiPath(null, "/api/apps")).toBe("/api/apps");
  });

  it("keeps the central's own paths on the central, whatever server is selected", () => {
    for (const path of ["/api/auth/elevate", "/api/auth/session", "/api/auth/ws-ticket", "/api/nodes", "/api/nodes/web-2/test", "/api/fleet", "/api/central/unlock"]) {
      expect(isCentralApiPath(path)).toBe(true);
      expect(nodeApiPath("web-2", path)).toBe(path);
    }
    // Segment-wise: an API that merely starts with the same letters is the node's.
    expect(isCentralApiPath("/api/authors")).toBe(false);
    expect(nodeApiPath("web-2", "/api/nodesmith")).toBe("/api/nodes/web-2/api/nodesmith");
  });

  it("maps the event stream and the WebSockets", () => {
    expect(nodeEventsPath(null)).toBe("/events");
    expect(nodeEventsPath("web-2")).toBe("/api/nodes/web-2/events");
    expect(nodeSocketPath(null, "/ws/logs/shop.example.com")).toBe("/ws/logs/shop.example.com");
    expect(nodeSocketPath("web-2", "/ws/logs/shop.example.com")).toBe("/ws/nodes/web-2/logs/shop.example.com");
    expect(nodeSocketPath("web-2", "/ws/jobs/a1b2")).toBe("/ws/nodes/web-2/jobs/a1b2");
  });

  it("encodes a node's name as one segment", () => {
    expect(nodeApiPath("a b", "/api/apps")).toBe("/api/nodes/a%20b/api/apps");
    expect(nodeOfProxyPath("/api/nodes/a%20b/api/apps")).toBe("a b");
    expect(nodeOfProxyPath("/api/nodes/web-2/events")).toBe("web-2");
    expect(nodeOfProxyPath("/api/nodes/web-2/test")).toBeNull();
    expect(nodeOfProxyPath("/api/apps")).toBeNull();
  });
});

describe("the active server", () => {
  it("is read from the installed source, and a scope overrides it for its duration", () => {
    expect(activeNode()).toBeNull();
    installNodeSource(() => "web-2");
    expect(activeNode()).toBe("web-2");
    expect(runOnNode(null, () => activeNode())).toBeNull();
    expect(runOnNode("db-1", () => runOnNode("web-2", () => activeNode()))).toBe("web-2");
    expect(activeNode()).toBe("web-2");
  });
});

describe("the query cache, per server", () => {
  it("hashes this server's entries exactly as before, and a node's apart", () => {
    expect(nodeQueryKeyHash(["apps"])).toBe('["apps"]');
    installNodeSource(() => "web-2");
    const hash = nodeQueryKeyHash(["apps"]);
    expect(hash).not.toBe('["apps"]');
    expect(nodeOfQueryHash(hash)).toBe("web-2");
    expect(nodeOfQueryHash('["apps"]')).toBeNull();
    // The central's own entries are shared by every server.
    expect(nodeQueryKeyHash(["auth", "session"])).toBe('["auth","session"]');
    expect(nodeQueryKeyHash(["nodes", "list"])).toBe('["nodes","list"]');
  });

  it("keeps the same key on two servers as two answers, each read from its own server", async () => {
    const backend = fakeBackend({
      "GET /api/apps": () => json(200, { apps: [{ domain: "here.example.com" }] }),
      "GET /api/nodes/web-2/api/apps": () => json(200, { apps: [{ domain: "there.example.com" }] }),
    });
    const { request } = await import("./client");
    const client = createQueryClient();
    let selected: string | null = "web-2";
    installNodeSource(() => selected);
    const query = { queryKey: ["apps"], queryFn: () => request("get", "/api/apps") };

    const onNode = await client.query(query);
    selected = null;
    const here = await client.query(query);
    expect(onNode).toEqual({ apps: [{ domain: "there.example.com" }] });
    expect(here).toEqual({ apps: [{ domain: "here.example.com" }] });

    // A refetch of web-2's entry after the operator left it still reads web-2.
    await client.refetchQueries({ queryKey: ["apps"] });
    expect(backend.callsTo("GET /api/nodes/web-2/api/apps")).toHaveLength(2);
    expect(backend.callsTo("GET /api/apps")).toHaveLength(2);
    selected = "web-2";
    expect(client.getQueryData(["apps"])).toEqual({ apps: [{ domain: "there.example.com" }] });
  });

  it("is only a default: a plain QueryClient still works", () => {
    const client = new QueryClient();
    client.setQueryData(["apps"], 1);
    expect(client.getQueryData(["apps"])).toBe(1);
  });
});

describe("the central-only lists agree with each other", () => {
  // CENTRAL_ONLY_PATHS (nodeRoute.ts, which page never carries a node),
  // CENTRAL_API_PREFIXES and CENTRAL_QUERY_ROOTS (here, which calls and cache
  // entries are always the central's) are three hand-maintained lists of the
  // same underlying fact, in three different vocabularies (a route, an API
  // prefix, a query key root) that cannot be mechanically generated from one
  // another in general - "/settings" and "/login" read the central's own
  // /api/auth, and "/integrations" has no query root of its own at all. But
  // where a name is shared, verbatim, across two of them, it must stay
  // shared: a fleet or server page whose entry silently stopped being
  // treated as the central's own would start being scoped to whichever node
  // happens to be on screen, mixing that data between servers.

  it("CENTRAL_QUERY_ROOTS is CENTRAL_API_PREFIXES' own names, plus the one documented exception", () => {
    const fromApiPrefixes = new Set(CENTRAL_API_PREFIXES.map((prefix) => prefix.replace(/^\/api\//, "")));
    const queryRoots = new Set(CENTRAL_QUERY_ROOTS);
    // The Servers page reads /api/nodes, but partitions its own cache entries
    // under a query root named for the page, not the endpoint.
    expect(queryRoots.has("servers")).toBe(true);
    queryRoots.delete("servers");
    expect(queryRoots).toEqual(fromApiPrefixes);
  });

  it("the fleet and servers routes keep the same name in the query cache", () => {
    expect(CENTRAL_ONLY_PATHS).toContain("/fleet");
    expect(CENTRAL_QUERY_ROOTS.has("fleet")).toBe(true);
    expect(CENTRAL_API_PREFIXES).toContain("/api/fleet");

    expect(CENTRAL_ONLY_PATHS).toContain("/servers");
    expect(CENTRAL_QUERY_ROOTS.has("servers")).toBe(true);
  });
});
