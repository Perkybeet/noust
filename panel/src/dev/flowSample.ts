import type { FlowEdge, FlowLayer, FlowNode } from "../components/ui/flowDiagram.types";

/**
 * The shape of Proggest's real nginx site (infra/nginx/proggest.es): two server blocks, 25
 * locations on the TLS one, two upstreams. The reference size the diagram must draw without
 * overlaps; the gallery and the layout tests both use it.
 */

const NEXT = "u:nextjs_upstream";
const NEST = "u:nestjs_upstream";

type Target = typeof NEXT | typeof NEST | "d:certbot" | "d:assets" | "d:https" | "d:uaap" | "d:healthy" | "d:404" | "d:deny";

const TLS_LOCATIONS: readonly [string, Target, { websocket?: boolean }?][] = [
  ["/health", "d:healthy"],
  ["/api/v1/work-reports/public/", NEST],
  ["/api/v1/auth/login", NEST],
  ["/api/auth/", NEXT],
  ["/api/user/", NEXT],
  ["/api/settings/", NEXT],
  ["/api/clocking/photo", NEXT],
  ["/api/upload/", NEXT],
  ["/api/super-admin/", NEXT],
  ["/api/proxy/", NEXT],
  ["/api/app-info", NEXT],
  ["/api/health", NEXT],
  ["/api/manifest", NEXT],
  ["/api/files/", NEXT],
  ["/api/", NEST],
  ["/socket.io", NEST, { websocket: true }],
  ["/admin/queues", NEST],
  ["/assets/", "d:assets"],
  ["/work-reports/reports/download", NEXT],
  ["= /uaap/", "d:uaap"],
  ["/uaap", NEXT],
  ["/", NEXT],
  ["/_next/static/", NEXT],
  ["~ /\\.", "d:deny"],
  ["~ ^/(\\.env|\\.git|docker-compose|Dockerfile)", "d:404"],
];

const PLAIN_LOCATIONS: readonly [string, Target][] = [
  ["/.well-known/acme-challenge/", "d:certbot"],
  ["/", "d:https"],
];

const HTTP_GROUP = "proggest.es · 80";
const TLS_GROUP = "proggest.es · 443";

const locations: FlowNode[] = [
  ...PLAIN_LOCATIONS.map(([path], index): FlowNode => ({ id: `s0/l${String(index)}`, kind: "location", label: path, group: HTTP_GROUP })),
  ...TLS_LOCATIONS.map(([path], index): FlowNode => ({ id: `s1/l${String(index)}`, kind: "location", label: path, group: TLS_GROUP })),
];

export const PROGGEST_LAYERS: readonly FlowLayer[] = [
  {
    id: "ports",
    title: "Ports",
    nodes: [
      { id: "p80", kind: "listener", label: "80", detail: "HTTP · IPv4 and IPv6" },
      { id: "p443", kind: "listener", label: "443 · TLS", detail: "HTTPS · IPv4 and IPv6" },
    ],
  },
  {
    id: "servers",
    title: "Names",
    nodes: [
      { id: "s0", kind: "server", label: "proggest.es", detail: "www.proggest.es · redirects to HTTPS" },
      { id: "s1", kind: "server", label: "proggest.es", detail: "www.proggest.es · certificate valid", state: "ok", stateLabel: "Expires in 61 days" },
    ],
  },
  { id: "locations", title: "Locations", nodes: locations },
  {
    id: "destinations",
    title: "Destinations",
    nodes: [
      { id: NEXT, kind: "upstream", label: "nextjs_upstream", detail: "1 server", group: "Upstreams" },
      { id: NEST, kind: "upstream", label: "nestjs_upstream", detail: "1 server", group: "Upstreams" },
      { id: "d:assets", kind: "static", label: "/var/www/proggest/apps/web-gateway/public/assets/", group: "Files" },
      { id: "d:certbot", kind: "static", label: "/var/www/certbot", group: "Files" },
      { id: "d:https", kind: "redirect", label: "301 https://$host$request_uri", group: "Answers" },
      { id: "d:uaap", kind: "redirect", label: "302 /uaap/dashboard", group: "Answers" },
      { id: "d:healthy", kind: "other", label: "200 healthy", group: "Answers" },
      { id: "d:404", kind: "other", label: "404", group: "Answers" },
      { id: "d:deny", kind: "other", label: "deny all", group: "Answers" },
    ],
  },
  {
    id: "backends",
    title: "Backends",
    nodes: [
      { id: "b:3001", kind: "upstream-server", label: "127.0.0.1:3001", detail: "Next.js · frontend service of proggest.es", state: "ok" },
      { id: "b:3000", kind: "upstream-server", label: "127.0.0.1:3000", detail: "NestJS · backend service of proggest.es", state: "ok" },
    ],
  },
];

export const PROGGEST_EDGES: readonly FlowEdge[] = [
  { from: "p80", to: "s0" },
  { from: "p443", to: "s1", tls: true },
  ...PLAIN_LOCATIONS.flatMap(([, target], index): FlowEdge[] => [
    { from: "s0", to: `s0/l${String(index)}` },
    { from: `s0/l${String(index)}`, to: target },
  ]),
  ...TLS_LOCATIONS.flatMap(([, target, extra], index): FlowEdge[] => [
    { from: "s1", to: `s1/l${String(index)}`, tls: true, ...(extra?.websocket ? { websocket: true } : {}) },
    { from: `s1/l${String(index)}`, to: target, ...(extra?.websocket ? { websocket: true } : {}) },
  ]),
  { from: NEXT, to: "b:3001" },
  { from: NEST, to: "b:3000" },
];

/** What "Try a URL" would light for https://proggest.es/api/v1/auth/login. */
export const PROGGEST_LOGIN_ROUTE: readonly string[] = ["p443", "s1", "s1/l2", NEST, "b:3000"];

/** The same site with the backend stopped: what the operator sees when NestJS is down. */
export const PROGGEST_LAYERS_BACKEND_DOWN: readonly FlowLayer[] = PROGGEST_LAYERS.map((layer) =>
  layer.id === "backends"
    ? {
        ...layer,
        nodes: layer.nodes.map((node) => (node.id === "b:3000" ? { ...node, state: "fail" as const } : node)),
      }
    : layer,
);

/** A small site: one port, one server, a proxy and a WebSocket, one backend not yet checked. */
export const SMALL_LAYERS: readonly FlowLayer[] = [
  { id: "ports", title: "Ports", nodes: [{ id: "p443", kind: "listener", label: "443 · TLS" }] },
  { id: "servers", title: "Names", nodes: [{ id: "s0", kind: "server", label: "shop.example.com" }] },
  {
    id: "locations",
    title: "Locations",
    nodes: [
      { id: "s0/l0", kind: "location", label: "/" },
      { id: "s0/l1", kind: "location", label: "/ws" },
    ],
  },
  { id: "backends", title: "Backends", nodes: [{ id: "b:3000", kind: "upstream-server", label: "127.0.0.1:3000", detail: "shop-example-com.service", state: "unknown" }] },
];

export const SMALL_EDGES: readonly FlowEdge[] = [
  { from: "p443", to: "s0", tls: true },
  { from: "s0", to: "s0/l0", tls: true },
  { from: "s0", to: "s0/l1", tls: true, websocket: true },
  { from: "s0/l0", to: "b:3000" },
  { from: "s0/l1", to: "b:3000", websocket: true },
];
