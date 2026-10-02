/**
 * The Structure view's reading of a site's model (noust.managers.siteconf, through
 * `/api/sites/{d}/structure`) and the edit operations it sends to `/config/edit`. Pure: the
 * view renders what these return, and every change is an operation the backend applies to the
 * text, never a string assembled here.
 */

import type { SiteEditOp, SiteLocation, SiteRawDirective, SiteServer, SiteStructure, SiteUpstream } from "../../api/queries/sites";

/** How a location matches a request path, in nginx's terms (or Apache's rule). */
export type MatchKind = "exact" | "prefix" | "prefixNoRegex" | "regex" | "regexCaseless" | "named";

/** The match type of a location, from its modifier. */
export function matchKind(location: Pick<SiteLocation, "modifier" | "path">): MatchKind {
  switch (location.modifier) {
    case "=":
      return "exact";
    case "^~":
      return "prefixNoRegex";
    case "~":
      return "regex";
    case "~*":
      return "regexCaseless";
    default:
      return location.path.startsWith("@") ? "named" : "prefix";
  }
}

/** A location as nginx writes it: `= /uaap/`, `~ /\.`, `/api/`. */
export function locationLabel(location: Pick<SiteLocation, "modifier" | "path">): string {
  return `${location.modifier} ${location.path}`.trim();
}

/** Whether a location is a block of its own (nginx `location`, Apache `<Location>`), not a rule. */
export function isBlock(location: Pick<SiteLocation, "id">): boolean {
  return /\/l\d+$/.test(location.id);
}

export interface OrderedLocation {
  location: SiteLocation;
  /** 0 for a server's own locations, 1 for one nested in those, and so on. */
  depth: number;
}

/**
 * A server's locations in the order the web server tries them (`evaluation_order`), each
 * followed by those nested in it, in theirs. A location the web server never tries on its own
 * (a named `@fallback`) comes last, in file order: it is still part of the file.
 */
export function locationsInOrder(server: Pick<SiteServer, "locations" | "evaluation_order">): OrderedLocation[] {
  const ordered: OrderedLocation[] = [];
  const walk = (locations: readonly SiteLocation[], order: readonly string[], depth: number): void => {
    const byId = new Map(locations.map((location) => [location.id, location]));
    const seen = new Set<string>();
    const visit = (location: SiteLocation): void => {
      seen.add(location.id);
      ordered.push({ location, depth });
      if (location.locations.length > 0) walk(location.locations, location.evaluation_order, depth + 1);
    };
    for (const id of order) {
      const location = byId.get(id);
      if (location !== undefined && !seen.has(id)) visit(location);
    }
    for (const location of locations) if (!seen.has(location.id)) visit(location);
  };
  walk(server.locations, server.evaluation_order, 0);
  return ordered;
}

/** Every location of a server, nested ones included, by id. */
export function locationsById(structure: SiteStructure): Map<string, SiteLocation> {
  const found = new Map<string, SiteLocation>();
  const walk = (locations: readonly SiteLocation[]): void => {
    for (const location of locations) {
      found.set(location.id, location);
      walk(location.locations);
    }
  };
  for (const server of structure.servers) walk(server.locations);
  return found;
}

// -- Arguments as the operator types them ----------------------------------------------------

/**
 * A directive's arguments from a line the operator typed: words separated by spaces, a quoted
 * word kept whole (`"max-age=63072000; includeSubDomains"`) with its quotes taken off, `\"`
 * inside quotes kept as a quote. Null when a quote is left open. The backend quotes again what
 * needs it when it writes the file.
 */
export function splitArgs(text: string): string[] | null {
  const args: string[] = [];
  let current = "";
  let quote: '"' | "'" | null = null;
  let started = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text.charAt(index);
    if (quote !== null) {
      if (char === "\\" && index + 1 < text.length) {
        current += text.charAt(index + 1);
        index += 1;
      } else if (char === quote) {
        quote = null;
      } else {
        current += char;
      }
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      started = true;
    } else if (/\s/.test(char)) {
      if (started) args.push(current);
      current = "";
      started = false;
    } else {
      current += char;
      started = true;
    }
  }
  if (quote !== null) return null;
  if (started) args.push(current);
  return args;
}

/** Arguments back into one line to edit: a word with a space, a quote or `;{}#` is quoted. */
export function joinArgs(args: readonly string[]): string {
  return args.map((arg) => (arg === "" || /[\s"';{}#]/.test(arg) ? `"${arg.replace(/(["\\])/g, "\\$1")}"` : arg)).join(" ");
}

// -- What changed ---------------------------------------------------------------------------

function directivesText(directives: readonly SiteRawDirective[]): string {
  return directives.map((directive) => directive.text).join("\n");
}

function locationSignature(location: SiteLocation): string {
  return `${locationLabel(location)}\n${directivesText(location.directives)}\n${location.locations.map(locationSignature).join("\n")}`;
}

function serverKey(server: SiteServer): string {
  const ports = [...new Set(server.listens.map((listen) => String(listen.port ?? listen.raw)))].sort().join(",");
  return `${ports}|${server.names[0] ?? ""}`;
}

/**
 * Identity keys for the elements of a model: what makes "the same location" across two texts
 * whose ids moved (a location added above shifts every id after it). The n-th element with the
 * same key keeps its place with `#n`.
 */
function identities(structure: SiteStructure): Map<string, { id: string; signature: string }> {
  const found = new Map<string, { id: string; signature: string }>();
  const add = (key: string, id: string, signature: string): void => {
    let unique = key;
    for (let count = 2; found.has(unique); count += 1) unique = `${key}#${String(count)}`;
    found.set(unique, { id, signature });
  };
  for (const server of structure.servers) {
    const key = serverKey(server);
    add(`server:${key}`, server.id, directivesText(server.directives));
    const walk = (locations: readonly SiteLocation[], parent: string): void => {
      for (const location of locations) {
        const own = `${parent}>${locationLabel(location)}`;
        add(`location:${own}`, location.id, locationSignature(location));
        walk(location.locations, own);
      }
    };
    walk(server.locations, key);
  }
  for (const upstream of structure.upstreams) add(`upstream:${upstream.name}`, upstream.id, directivesText(upstream.directives));
  return found;
}

/**
 * The ids of the draft's elements that differ from the saved file: new ones, and ones whose
 * text changed. Matched by what they are (a server's ports and name, a location's path, an
 * upstream's name), not by position.
 */
export function changedIds(saved: SiteStructure | null, draft: SiteStructure): Set<string> {
  const changed = new Set<string>();
  if (saved === null) return changed;
  const before = identities(saved);
  for (const [key, element] of identities(draft)) {
    const previous = before.get(key);
    if (previous?.signature !== element.signature) changed.add(element.id);
  }
  return changed;
}

// -- Listens ----------------------------------------------------------------------------------

export interface ListenGroup {
  port: string;
  tls: boolean;
  http2: boolean;
  quic: boolean;
  defaultServer: boolean;
  ipv4: boolean;
  ipv6: boolean;
}

/**
 * A server's listens grouped by port and protocol: `listen 443 ssl` and `listen [::]:443 ssl`
 * are one way in, on both IP families. HTTP/2 counts when the listen says it or, on TLS, when
 * the server turns it on (`http2 on;`).
 */
export function listenGroups(server: Pick<SiteServer, "listens" | "http2">): ListenGroup[] {
  const groups = new Map<string, ListenGroup>();
  for (const listen of server.listens) {
    const port = listen.port !== null && listen.port !== undefined ? String(listen.port) : listen.raw;
    const http2 = listen.http2 || (server.http2 && listen.ssl);
    const key = `${port}|${String(listen.ssl)}`;
    const group = groups.get(key) ?? { port, tls: listen.ssl, http2: false, quic: false, defaultServer: false, ipv4: false, ipv6: false };
    group.http2 ||= http2;
    group.quic ||= listen.quic;
    group.defaultServer ||= listen.default_server;
    if (listen.ipv6) group.ipv6 = true;
    else group.ipv4 = true;
    groups.set(key, group);
  }
  return [...groups.values()];
}

/** A listen group as a chip: `443 · TLS · HTTP/2`. System tokens, never translated. */
export function listenChip(group: ListenGroup): string {
  return [group.port, group.tls ? "TLS" : null, group.http2 ? "HTTP/2" : null, group.quic ? "HTTP/3" : null, group.defaultServer ? "default_server" : null]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

// -- Edit operations --------------------------------------------------------------------------

/** The settings the Structure view edits in line on a location. */
export type InlineSetting = "read_timeout" | "client_max_body_size" | "limit_req" | "buffering";

function directiveIndex(id: string): number {
  const match = /\/d(\d+)$/.exec(id);
  return match?.[1] !== undefined ? Number(match[1]) : -1;
}

/**
 * Removing several directives of one block: last first, so the ids of the ones still to go
 * (resolved after each operation) do not move.
 */
function removeAll(directives: readonly { id: string }[]): SiteEditOp[] {
  return [...directives]
    .sort((a, b) => directiveIndex(b.id) - directiveIndex(a.id))
    .map((directive): SiteEditOp => ({ op: "remove_directive", target: directive.id }));
}

function firstNamed(location: SiteLocation, test: (name: string) => boolean): SiteRawDirective | undefined {
  return location.directives.find((directive) => !directive.block && test(directive.name));
}

/**
 * The directive that holds a location's read timeout: the one the file already has
 * (`proxy_read_timeout`, `fastcgi_read_timeout`, ...), else the one its target reads.
 */
export function readTimeoutDirective(location: SiteLocation): string {
  const existing = firstNamed(location, (name) => name.endsWith("_read_timeout"));
  if (existing !== undefined) return existing.name;
  return location.target.kind === "fastcgi" ? "fastcgi_read_timeout" : "proxy_read_timeout";
}

const SETTING_DIRECTIVE: Record<Exclude<InlineSetting, "read_timeout">, string> = {
  client_max_body_size: "client_max_body_size",
  limit_req: "limit_req",
  buffering: "proxy_buffering",
};

/** The text an inline field shows for a setting: the directive's own arguments. */
export function settingText(location: SiteLocation, setting: InlineSetting): string {
  const name = setting === "read_timeout" ? readTimeoutDirective(location) : SETTING_DIRECTIVE[setting];
  const directive = firstNamed(location, (candidate) => candidate === name);
  return directive === undefined ? "" : joinArgs(directive.args);
}

/**
 * The operations that set an inline setting of a location: the directive set (or added) with
 * the typed arguments, or removed when the field is emptied, so the server's default applies.
 * Null when the text cannot be read as arguments (a quote left open).
 */
export function settingOps(location: SiteLocation, setting: InlineSetting, text: string): SiteEditOp[] | null {
  const name = setting === "read_timeout" ? readTimeoutDirective(location) : SETTING_DIRECTIVE[setting];
  const args = splitArgs(text.trim());
  if (args === null) return null;
  if (args.length === 0) {
    const existing = location.directives.filter((directive) => !directive.block && directive.name === name);
    return removeAll(existing);
  }
  return [{ op: "set_directive", parent: location.id, name, args }];
}

/**
 * Turning WebSocket upgrades on or off for a proxied location: on sends HTTP/1.1 and passes the
 * `Upgrade` and `Connection` headers; off removes those two headers (HTTP/1.1 stays: keepalive
 * to an upstream needs it too).
 */
export function websocketOps(location: SiteLocation, on: boolean): SiteEditOp[] {
  const headers = location.settings.proxy_headers;
  const upgrade = headers.filter((header) => header.name.toLowerCase() === "upgrade");
  const connection = headers.filter((header) => header.name.toLowerCase() === "connection");
  if (!on) return removeAll([...upgrade, ...connection]);
  const ops: SiteEditOp[] = [{ op: "set_directive", parent: location.id, name: "proxy_http_version", args: ["1.1"] }];
  if (upgrade.length === 0) ops.push({ op: "add_directive", parent: location.id, name: "proxy_set_header", args: ["Upgrade", "$http_upgrade"] });
  if (connection.length === 0) ops.push({ op: "add_directive", parent: location.id, name: "proxy_set_header", args: ["Connection", "upgrade"] });
  return ops;
}

/** A location's "More directives": everything the inline fields and the target do not show. */
export function otherDirectives(location: SiteLocation): SiteRawDirective[] {
  const inline = new Set([readTimeoutDirective(location), ...Object.values(SETTING_DIRECTIVE)]);
  return location.directives.filter((directive) => directive.block || !inline.has(directive.name));
}

/** What a new location does, from the add dialog. */
export type LocationTemplate = "proxy" | "static" | "redirect";

export interface NewLocation {
  server: string;
  modifier: "" | "=" | "^~" | "~" | "~*";
  path: string;
  template: LocationTemplate;
  /** An upstream's name or a URL for a proxy, a directory for files, a URL for a redirect. */
  to: string;
  /** A redirect's status. */
  code?: number;
}

/** A location's head: `[modifier, path]`, or the path alone for a prefix. */
export function locationArgs(modifier: string, path: string): string[] {
  return modifier === "" ? [path] : [modifier, path];
}

/** The operation that adds a location from a template, in nginx or Apache. */
export function addLocationOp(kind: string, draft: NewLocation, upstreams: readonly SiteUpstream[]): SiteEditOp {
  const named = upstreams.some((upstream) => upstream.name === draft.to);
  const to = draft.template === "proxy" && named ? `http://${draft.to}` : draft.to;
  return {
    op: "add_block",
    parent: draft.server,
    name: kind === "apache" ? (draft.modifier === "" ? "Location" : "LocationMatch") : "location",
    args: kind === "apache" ? [draft.path] : locationArgs(draft.modifier, draft.path),
    template: draft.template,
    to,
    ...(draft.template === "redirect" ? { code: draft.code ?? 301 } : {}),
  };
}

/**
 * Moving a location one place up or down in the file, among its siblings. Only regular
 * expressions depend on where they are written; the view says so next to the action.
 */
export function moveOp(siblings: readonly SiteLocation[], location: SiteLocation, direction: "up" | "down"): SiteEditOp | null {
  const blocks = siblings.filter(isBlock);
  const index = blocks.findIndex((candidate) => candidate.id === location.id);
  if (index === -1) return null;
  if (direction === "up") {
    const previous = blocks[index - 1];
    return previous === undefined ? null : { op: "move_block", target: location.id, before: previous.id };
  }
  const next = blocks[index + 1];
  return next === undefined ? null : { op: "move_block", target: location.id, after: next.id };
}

/** Removing a location: a block, or the directive that is an Apache `ProxyPass` rule. */
export function removeLocationOp(location: SiteLocation): SiteEditOp {
  return isBlock(location) ? { op: "remove_block", target: location.id } : { op: "remove_directive", target: location.id };
}

/** The siblings of a location: its server's own locations, or its parent location's. */
export function siblingsOf(server: SiteServer, id: string): SiteLocation[] {
  const search = (locations: readonly SiteLocation[]): SiteLocation[] | null => {
    if (locations.some((location) => location.id === id)) return [...locations];
    for (const location of locations) {
      const found = search(location.locations);
      if (found !== null) return found;
    }
    return null;
  };
  return search(server.locations) ?? [];
}
