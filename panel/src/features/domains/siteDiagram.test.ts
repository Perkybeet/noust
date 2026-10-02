import { describe, expect, it } from "vitest";

import type { SiteBackend } from "../../api/queries/sites";
import { buildSiteDiagram, parseTriedUrl, routePath } from "./siteDiagram";
import type { DiagramWords } from "./siteDiagram";
import { PROGGEST_ROUTE_LOGIN, PROGGEST_STRUCTURE, SMALL_ROUTE_LOGIN, SMALL_STRUCTURE, SMALL_TOPOLOGY_FACTS } from "./siteTestFixtures";

const WORDS: DiagramWords = {
  layers: { ports: "Ports", names: "Names", locations: "Locations", destinations: "Destinations", backends: "Backends" },
  groups: { upstreams: "Upstreams", files: "Files", answers: "Answers", proxied: "Proxied" },
  families: (ipv4, ipv6) => (ipv4 && ipv6 ? "IPv4 and IPv6" : ipv6 ? "IPv6" : "IPv4"),
  alsoNames: (names) => (names.length > 0 ? `Also ${names.join(", ")}` : undefined),
  serverReturns: (code, destination) => `Answers ${String(code)} ${destination ?? ""}`,
  expiresIn: (days) => `Expires in ${String(days)} days`,
  expired: (days) => `Expired ${String(days)} days ago`,
  upstreamServers: (count) => `${String(count)} servers`,
  owner: (backend: SiteBackend) => (backend.owner?.app ? `Application ${backend.owner.app}` : undefined),
  denied: "Refused",
  answersItself: "Answered by the web server",
  fastcgi: "FastCGI",
};

function layer(diagram: ReturnType<typeof buildSiteDiagram>, id: string) {
  const found = diagram.layers.find((entry) => entry.id === id);
  if (found === undefined) throw new Error(`no layer ${id}`);
  return found;
}

describe("a site as a diagram", () => {
  it("draws ports, names, locations in evaluation order, destinations and backends", () => {
    const diagram = buildSiteDiagram(SMALL_STRUCTURE, SMALL_TOPOLOGY_FACTS, WORDS);
    expect(diagram.layers.map((entry) => entry.title)).toEqual(["Ports", "Names", "Locations", "Destinations", "Backends"]);
    expect(layer(diagram, "ports").nodes.map((node) => node.label)).toEqual(["80", "443 · TLS · HTTP/2"]);
    const names = layer(diagram, "servers").nodes;
    expect(names[1]).toMatchObject({ id: "s1", label: "shop.example.com", detail: "Also www.shop.example.com", state: "ok", stateLabel: "Expires in 60 days" });
    expect(layer(diagram, "locations").nodes.filter((node) => node.group?.endsWith("443")).map((node) => node.label)).toEqual([
      "= /old",
      "/api/v1/auth/login",
      "/assets/",
      "/api/",
      "~ /\\.",
    ]);
    expect(layer(diagram, "destinations").nodes.map((node) => [node.kind, node.label])).toEqual([
      ["upstream", "shop_backend"],
      ["static", "/var/www/shop/assets/"],
      ["redirect", "301 https://$host$request_uri"],
      ["redirect", "302 /new"],
      ["other", "deny all"],
    ]);
    // The backend is matched to the topology: its owner, and that it does not answer.
    expect(layer(diagram, "backends").nodes).toEqual([
      { id: "b:127.0.0.1:3000", kind: "upstream-server", label: "127.0.0.1:3000", state: "fail", detail: "Application shop.example.com" },
    ]);
    expect(diagram.edges).toContainEqual({ from: "p:443", to: "s1", tls: true });
    expect(diagram.edges).toContainEqual({ from: "s1", to: "s1/l1", tls: true, websocket: true });
    expect(diagram.edges).toContainEqual({ from: "u:shop_backend", to: "b:127.0.0.1:3000" });
  });

  it("without the topology, draws the backends from the file with an unknown state", () => {
    const diagram = buildSiteDiagram(SMALL_STRUCTURE, null, WORDS);
    expect(layer(diagram, "backends").nodes).toEqual([{ id: "b:127.0.0.1:3000", kind: "upstream-server", label: "127.0.0.1:3000", state: "unknown" }]);
    expect(layer(diagram, "servers").nodes[1]?.state).toBeUndefined();
  });

  it("lights only the path /route chose: the exact location, its upstream and its backend", () => {
    const diagram = buildSiteDiagram(SMALL_STRUCTURE, SMALL_TOPOLOGY_FACTS, WORDS);
    expect(routePath(SMALL_ROUTE_LOGIN, diagram, 443)).toEqual(["p:443", "s1", "s1/l0", "u:shop_backend", "b:127.0.0.1:3000"]);
    expect(routePath({ ...SMALL_ROUTE_LOGIN, server_id: null, location_id: null }, diagram, 8443)).toEqual([]);
    expect(routePath({ ...SMALL_ROUTE_LOGIN, redirect: "/api/" }, diagram, 443)).toEqual(["p:443", "s1", "s1/l0"]);

    // Proggest: of 25 locations, /api/v1/auth/login, not the catch-all /api/.
    const proggest = buildSiteDiagram(PROGGEST_STRUCTURE, null, WORDS);
    const path = routePath(PROGGEST_ROUTE_LOGIN, proggest, 443);
    expect(path).toEqual(["p:443", "s1", "s1/l2", "u:nestjs_upstream", "b:127.0.0.1:3000"]);
    expect(layer(proggest, "locations").nodes.find((node) => node.id === "s1/l2")?.label).toBe("/api/v1/auth/login");
    expect(layer(proggest, "locations").nodes).toHaveLength(27);
  });

  it("reads what the operator types in Try a URL", () => {
    expect(parseTriedUrl("https://proggest.es/api/v1/auth/login?x=1", "proggest.es")).toEqual({
      scheme: "https",
      host: "proggest.es",
      path: "/api/v1/auth/login",
      port: 443,
      explicitPort: false,
    });
    expect(parseTriedUrl("/api/", "shop.example.com")).toMatchObject({ scheme: "https", host: "shop.example.com", path: "/api/" });
    expect(parseTriedUrl("www.shop.example.com/assets/a.css", "shop.example.com")).toMatchObject({ host: "www.shop.example.com", path: "/assets/a.css" });
    expect(parseTriedUrl("http://shop.example.com:8080/", "shop.example.com")).toMatchObject({ scheme: "http", port: 8080, explicitPort: true });
    expect(parseTriedUrl("ftp://shop.example.com/", "shop.example.com")).toBeNull();
    expect(parseTriedUrl("   ", "shop.example.com")).toBeNull();
  });
});
