/**
 * The Diagram view's data: a site's model (`/structure`) and what is behind it now
 * (`/topology`) laid out as the `FlowDiagram`'s fixed layers - ports, names, locations,
 * destinations, backends - and the route `/route` chose, as the ids of that path. Pure: the
 * words come in from the view, already translated.
 */

import type { SiteBackend, SiteCertificate, SiteLocation, SiteRoute, SiteServer, SiteStructure } from "../../api/queries/sites";
import type { FlowEdge, FlowLayer, FlowNode } from "../../components/ui/flowDiagram.types";
import { listenChip, listenGroups, locationLabel, locationsInOrder } from "./siteStructure";

/** Everything the diagram says in words, translated by the view. */
export interface DiagramWords {
  layers: { ports: string; names: string; locations: string; destinations: string; backends: string };
  groups: { upstreams: string; files: string; answers: string; proxied: string };
  /** "IPv4 and IPv6", "IPv6 only", "IPv4 only". */
  families: (ipv4: boolean, ipv6: boolean) => string;
  /** The names a server answers besides its first: "Also www.proggest.es". */
  alsoNames: (names: string[]) => string | undefined;
  /** "Redirects every request", when a server's own `return` answers. */
  serverReturns: (code: number | null, destination: string | null) => string;
  expiresIn: (days: number) => string;
  expired: (days: number) => string;
  /** "2 servers" under an upstream. */
  upstreamServers: (count: number) => string;
  /** Who holds a backend's port, in words; null when nobody is known to. */
  owner: (backend: SiteBackend) => string | undefined;
  denied: string;
  answersItself: string;
  fastcgi: string;
}

export interface SiteDiagram {
  layers: FlowLayer[];
  edges: FlowEdge[];
  /** For each location, the destination node it leads to. */
  destinationOf: ReadonlyMap<string, string>;
  /** For each destination, the backends it leads to. */
  backendsOf: ReadonlyMap<string, string[]>;
}

/** A port's node id: one per port, whichever servers listen on it. */
export function listenerId(port: string | number): string {
  return `p:${String(port)}`;
}

/** The first name a server answers, or `_` (nginx's catch-all spelling) when it has none. */
export function serverLabel(server: Pick<SiteServer, "names" | "id">): string {
  return server.names[0] ?? "_";
}

function destinationOf(location: SiteLocation, words: DiagramWords): FlowNode {
  const target = location.target;
  switch (target.kind) {
    case "proxy":
      if (target.upstream) return { id: `u:${target.upstream}`, kind: "upstream", label: target.upstream, group: words.groups.upstreams };
      return { id: `d:proxy:${target.url ?? target.address ?? ""}`, kind: "other", label: target.url ?? target.address ?? "", group: words.groups.proxied };
    case "static": {
      const directory = target.alias ?? target.root ?? "";
      return { id: `d:static:${directory}`, kind: "static", label: directory, group: words.groups.files };
    }
    case "return": {
      const destination = (target.destination ?? "").trim();
      const label = `${target.code !== null && target.code !== undefined ? String(target.code) : ""} ${destination}`.trim();
      const redirect = target.code !== null && target.code !== undefined && target.code >= 300 && target.code < 400;
      return { id: `d:return:${label}`, kind: redirect ? "redirect" : "other", label, group: words.groups.answers };
    }
    case "fastcgi":
      return { id: `d:fastcgi:${target.address ?? ""}`, kind: "other", label: target.address ?? "", detail: words.fastcgi, group: words.groups.proxied };
    default:
      if (location.settings.deny) return { id: "d:deny", kind: "other", label: "deny all", detail: words.denied, group: words.groups.answers };
      return { id: `d:other:${target.detail ?? ""}`, kind: "other", label: target.detail ?? locationLabel(location), detail: words.answersItself, group: words.groups.answers };
  }
}

/** The address a direct proxy reaches, as the topology spells it. */
function proxyAddress(location: SiteLocation): string | null {
  const target = location.target;
  if (target.address) return target.address;
  if (target.host && target.port !== null && target.port !== undefined) return `${target.host}:${String(target.port)}`;
  return null;
}

function backendNode(address: string, backend: SiteBackend | undefined, words: DiagramWords): FlowNode {
  const reachable = backend?.reachable;
  const state = reachable === null || reachable === undefined ? "unknown" : reachable ? "ok" : "fail";
  const detail = backend === undefined ? undefined : words.owner(backend);
  return { id: `b:${address}`, kind: "upstream-server", label: address, state, ...(detail !== undefined ? { detail } : {}) };
}

function certificateState(certificate: SiteCertificate | undefined, words: DiagramWords): Pick<FlowNode, "state" | "stateLabel"> {
  if (certificate?.days_left === null || certificate?.days_left === undefined) return {};
  if (certificate.days_left < 0) return { state: "fail", stateLabel: words.expired(-certificate.days_left) };
  return { state: "ok", stateLabel: words.expiresIn(certificate.days_left) };
}

/**
 * The layers and connections of a site. `facts` (from `/topology`) are optional: without
 * them, a backend is drawn from the file alone and its state is unknown; with them, matched by
 * how the file spells each address, a backend says who holds its port and whether it answers.
 */
export function buildSiteDiagram(
  structure: SiteStructure,
  facts: { backends: readonly SiteBackend[]; certificates: readonly SiteCertificate[] } | null,
  words: DiagramWords,
): SiteDiagram {
  const ports = new Map<string, FlowNode>();
  const servers: FlowNode[] = [];
  const locations: FlowNode[] = [];
  const destinations = new Map<string, FlowNode>();
  const backends = new Map<string, FlowNode>();
  const edges: FlowEdge[] = [];
  const edgeKeys = new Set<string>();
  const destinationIds = new Map<string, string>();
  const backendsOf = new Map<string, string[]>();
  const connect = (edge: FlowEdge): void => {
    const key = `${edge.from}->${edge.to}`;
    if (edgeKeys.has(key)) return;
    edgeKeys.add(key);
    edges.push(edge);
  };

  const factFor = (address: string): SiteBackend | undefined =>
    facts?.backends.find((backend) => backend.address === address || backend.written.includes(address));
  const lead = (destination: string, address: string): void => {
    const fact = factFor(address);
    const key = fact?.address ?? address;
    if (!backends.has(key)) backends.set(key, backendNode(key, fact, words));
    const list = backendsOf.get(destination) ?? [];
    if (!list.includes(`b:${key}`)) list.push(`b:${key}`);
    backendsOf.set(destination, list);
  };

  for (const server of structure.servers) {
    const groups = listenGroups(server);
    const certificate = server.tls?.certificate ? facts?.certificates.find((entry) => entry.path === server.tls?.certificate) : undefined;
    const also = words.alsoNames(server.names.slice(1));
    const detail = server.returns ? words.serverReturns(server.returns.code ?? null, server.returns.destination ?? null) : also;
    servers.push({ id: server.id, kind: "server", label: serverLabel(server), ...(detail !== undefined ? { detail } : {}), ...certificateState(certificate, words) });
    const tls = groups.length > 0 && groups.every((group) => group.tls);
    for (const group of groups) {
      const id = listenerId(group.port);
      if (!ports.has(id)) {
        ports.set(id, { id, kind: "listener", label: listenChip({ ...group, defaultServer: false }), detail: words.families(group.ipv4, group.ipv6) });
      }
      connect({ from: id, to: server.id, ...(group.tls ? { tls: true } : {}) });
    }
    const caption = `${serverLabel(server)} · ${groups.map((group) => group.port).join(", ")}`;
    for (const { location } of locationsInOrder(server)) {
      locations.push({ id: location.id, kind: "location", label: locationLabel(location), group: caption });
      const websocket = location.settings.websocket;
      connect({ from: server.id, to: location.id, ...(tls ? { tls: true } : {}), ...(websocket ? { websocket: true } : {}) });
      const destination = destinationOf(location, words);
      if (!destinations.has(destination.id)) destinations.set(destination.id, destination);
      destinationIds.set(location.id, destination.id);
      connect({ from: location.id, to: destination.id, ...(location.target.protocol === "https" ? { tls: true } : {}), ...(websocket ? { websocket: true } : {}) });
      if (location.target.kind === "proxy" && !location.target.upstream) {
        const address = proxyAddress(location);
        if (address !== null) lead(destination.id, address);
      } else if (location.target.kind === "fastcgi" && location.target.address) {
        lead(destination.id, location.target.address);
      }
    }
  }

  for (const upstream of structure.upstreams) {
    const id = `u:${upstream.name}`;
    // Set over the node a location made, keeping its place; an upstream no location uses is
    // still part of the file, drawn with the servers it leads to.
    destinations.set(id, { id, kind: "upstream", label: upstream.name, detail: words.upstreamServers(upstream.servers.length), group: words.groups.upstreams });
    for (const entry of upstream.servers) lead(id, entry.address);
  }
  for (const [destination, list] of backendsOf) for (const backend of list) connect({ from: destination, to: backend });

  // Destinations grouped as the kit's gallery draws them: upstreams, direct proxies, files,
  // then what the web server answers itself; the sort is stable, so each keeps file order.
  const groups = [words.groups.upstreams, words.groups.proxied, words.groups.files];
  const rank = (node: FlowNode): number => {
    const index = groups.indexOf(node.group ?? "");
    return index === -1 ? groups.length : index;
  };
  const destinationNodes = [...destinations.values()].sort((a, b) => rank(a) - rank(b));

  const layers: FlowLayer[] = [
    { id: "ports", title: words.layers.ports, nodes: [...ports.values()] },
    { id: "servers", title: words.layers.names, nodes: servers },
    { id: "locations", title: words.layers.locations, nodes: locations },
    { id: "destinations", title: words.layers.destinations, nodes: destinationNodes },
  ];
  if (backends.size > 0) layers.push({ id: "backends", title: words.layers.backends, nodes: [...backends.values()] });
  return { layers, edges, destinationOf: destinationIds, backendsOf };
}

/**
 * The path `/route` chose, as diagram ids: the port the request arrives on, the server, the
 * location that answers (not the prefixes it was nested in), its destination and the backends
 * behind it. A request no server takes, or one the server answers itself, stops where it does;
 * nginx's automatic redirect (a proxied prefix asked without its slash) stops at the location.
 */
export function routePath(route: SiteRoute, diagram: SiteDiagram, port: number): string[] {
  if (route.server_id === null || route.server_id === undefined) return [];
  const path = [listenerId(port), route.server_id];
  const location = route.location_id;
  if (location === null || location === undefined) return path;
  path.push(location);
  if (route.redirect !== null && route.redirect !== undefined) return path;
  const destination = diagram.destinationOf.get(location);
  if (destination === undefined) return path;
  path.push(destination);
  path.push(...(diagram.backendsOf.get(destination) ?? []));
  return path;
}

export interface TriedUrl {
  scheme: "http" | "https";
  host: string;
  path: string;
  /** The port the request arrives on: the URL's own, or the scheme's. */
  port: number;
  /** Whether the URL names its port, so the request says it too. */
  explicitPort: boolean;
}

/**
 * Reads what the operator typed in "Try a URL": a full URL, a host and path
 * (`proggest.es/api/`), or a path alone (`/api/v1/auth/login`), which is asked of the site's
 * own name over HTTPS. Null when it is not a URL this can trace (another scheme, no host).
 */
export function parseTriedUrl(text: string, site: string): TriedUrl | null {
  const value = text.trim();
  if (value === "") return null;
  const absolute = value.startsWith("/") ? `https://${site}${value}` : /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `https://${value}`;
  let url: URL;
  try {
    url = new URL(absolute);
  } catch {
    return null;
  }
  const scheme = url.protocol === "http:" ? "http" : url.protocol === "https:" ? "https" : null;
  if (scheme === null || url.hostname === "") return null;
  const explicitPort = url.port !== "";
  return { scheme, host: url.hostname, path: url.pathname || "/", port: explicitPort ? Number(url.port) : scheme === "https" ? 443 : 80, explicitPort };
}
