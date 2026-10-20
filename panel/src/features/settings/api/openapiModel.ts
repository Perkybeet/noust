/**
 * The server's own OpenAPI document read as a list of operations a person can browse: what each
 * one is, what it needs, what it takes and what it answers, with an example body and a `curl` to
 * copy. The document is the API's single description (`GET /api/openapi.json`); nothing here
 * invents a route, a permission or a field, and a shape this reader does not understand is shown
 * as its type name rather than guessed.
 */

type Json = Record<string, unknown>;

function isRecord(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

const METHODS = ["get", "post", "put", "patch", "delete"] as const;

export interface ApiParameter {
  name: string;
  where: string;
  required: boolean;
  description: string;
  type: string;
}

export interface ApiField {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

export interface ApiResponseInfo {
  status: string;
  description: string;
}

export interface ApiOperation {
  /** `GET /api/apps`: unique, and what a row is called. */
  id: string;
  method: string;
  path: string;
  tag: string;
  summary: string;
  /** The first paragraph of the route's docstring; the Args/Returns/Raises sections are for developers. */
  description: string;
  permission: string | null;
  needsSudoMode: boolean;
  needsApproval: boolean;
  deprecated: boolean;
  parameters: ApiParameter[];
  body: ApiField[] | null;
  /** An example request body, or null when the route takes none. */
  example: unknown;
  /** The route takes a body that is not JSON (a file, raw bytes): the page cannot compose one. */
  rawBody: boolean;
  responses: ApiResponseInfo[];
}

export interface ApiDocument {
  operations: ApiOperation[];
  tags: string[];
  title: string;
  version: string;
}

function resolve(doc: Json, schema: unknown): unknown {
  let current = schema;
  for (let hops = 0; hops < 8; hops += 1) {
    if (!isRecord(current)) return current;
    const ref = text(current["$ref"]);
    if (ref === "") return current;
    const parts = ref.replace(/^#\//, "").split("/");
    let found: unknown = doc;
    for (const part of parts) found = isRecord(found) ? found[part] : undefined;
    current = found;
  }
  return current;
}

/** The type of a schema in a word or two: `string`, `integer[]`, `object`, a model's name. */
export function typeOf(doc: Json, schema: unknown): string {
  if (!isRecord(schema)) return "any";
  const ref = text(schema["$ref"]);
  if (ref !== "") return ref.slice(ref.lastIndexOf("/") + 1);
  const variants = schema["anyOf"] ?? schema["oneOf"];
  if (Array.isArray(variants)) {
    const named = variants.map((variant) => typeOf(doc, variant)).filter((name) => name !== "null");
    return named.length === 0 ? "null" : named.join(" | ");
  }
  const enumeration = schema["enum"];
  if (Array.isArray(enumeration) && enumeration.length > 0 && enumeration.length <= 6) return enumeration.map((value) => JSON.stringify(value)).join(" | ");
  const kind = text(schema["type"]);
  if (kind === "array") return `${typeOf(doc, schema["items"])}[]`;
  return kind === "" ? "any" : kind;
}

/**
 * A plausible value of a schema, for the body of an example. Bounded in depth, and a schema that
 * refers to itself stops at its second visit, so a recursive model cannot run away.
 */
export function exampleOf(doc: Json, schema: unknown, depth = 0, seen: readonly string[] = []): unknown {
  if (depth > 4 || !isRecord(schema)) return null;
  const ref = text(schema["$ref"]);
  if (ref !== "") {
    if (seen.includes(ref)) return null;
    return exampleOf(doc, resolve(doc, schema), depth + 1, [...seen, ref]);
  }
  if ("example" in schema) return schema["example"];
  if (schema["default"] !== undefined && schema["default"] !== null) return schema["default"];
  const variants = schema["anyOf"] ?? schema["oneOf"];
  if (Array.isArray(variants)) {
    const options: unknown[] = variants;
    const first: unknown = options.find((variant) => !(isRecord(variant) && variant["type"] === "null"));
    return exampleOf(doc, first, depth + 1, seen);
  }
  const enumeration = schema["enum"];
  if (Array.isArray(enumeration) && enumeration.length > 0) return enumeration[0];
  switch (text(schema["type"])) {
    case "object": {
      const properties = isRecord(schema["properties"]) ? schema["properties"] : {};
      const result: Json = {};
      for (const [name, property] of Object.entries(properties)) result[name] = exampleOf(doc, property, depth + 1, seen);
      return result;
    }
    case "array":
      return [exampleOf(doc, schema["items"], depth + 1, seen)];
    case "string":
      return text(schema["format"]) === "date-time" ? "2026-01-01T00:00:00Z" : "string";
    case "integer":
    case "number":
      return 0;
    case "boolean":
      return false;
    default:
      return null;
  }
}

function fieldsOf(doc: Json, schema: unknown): ApiField[] {
  const resolved = resolve(doc, schema);
  if (!isRecord(resolved) || !isRecord(resolved["properties"])) return [];
  const required = Array.isArray(resolved["required"]) ? resolved["required"].map(text) : [];
  return Object.entries(resolved["properties"]).map(([name, property]) => {
    const described = resolve(doc, property);
    return {
      name,
      type: typeOf(doc, property),
      required: required.includes(name),
      description: isRecord(described) ? text(described["description"]).split("\n")[0] ?? "" : "",
    };
  });
}

/** The prose of a docstring without its Args/Returns/Raises sections, which are the developers'. */
export function proseOf(docstring: string): string {
  const cut = docstring.search(/\n\s*(Args|Arguments|Returns|Raises|Attributes|Yields):/);
  return (cut < 0 ? docstring : docstring.slice(0, cut)).trim();
}

function parametersOf(doc: Json, operation: Json, shared: unknown): ApiParameter[] {
  const own: unknown = operation["parameters"];
  const raw: unknown[] = [...(Array.isArray(shared) ? (shared as unknown[]) : []), ...(Array.isArray(own) ? (own as unknown[]) : [])];
  const seen = new Map<string, ApiParameter>();
  for (const entry of raw) {
    const parameter = resolve(doc, entry);
    if (!isRecord(parameter)) continue;
    const where = text(parameter["in"]);
    if (where !== "path" && where !== "query") continue;
    const name = text(parameter["name"]);
    seen.set(`${where}:${name}`, {
      name,
      where,
      required: parameter["required"] === true,
      description: text(parameter["description"]),
      type: typeOf(doc, parameter["schema"]),
    });
  }
  return [...seen.values()];
}

/** Reads the OpenAPI document into operations, sorted by tag then path. Anything else in it is ignored. */
export function readDocument(source: unknown): ApiDocument {
  const doc: Json = isRecord(source) ? source : {};
  const info = isRecord(doc["info"]) ? doc["info"] : {};
  const paths = isRecord(doc["paths"]) ? doc["paths"] : {};
  const operations: ApiOperation[] = [];
  for (const [path, item] of Object.entries(paths)) {
    if (!isRecord(item)) continue;
    for (const method of METHODS) {
      const operation = item[method];
      if (!isRecord(operation)) continue;
      const content = isRecord(operation["requestBody"]) && isRecord(operation["requestBody"]["content"]) ? operation["requestBody"]["content"] : {};
      const json = isRecord(content["application/json"]) ? content["application/json"] : null;
      const schema = json === null ? undefined : json["schema"];
      const responses = isRecord(operation["responses"]) ? operation["responses"] : {};
      const tags = Array.isArray(operation["tags"]) ? operation["tags"].map(text).filter((tag) => tag !== "") : [];
      operations.push({
        id: `${method.toUpperCase()} ${path}`,
        method: method.toUpperCase(),
        path,
        tag: tags[0] ?? "",
        summary: text(operation["summary"]),
        description: proseOf(text(operation["description"])),
        permission: text(operation["x-noust-permission"]) === "" ? null : text(operation["x-noust-permission"]),
        needsSudoMode: operation["x-noust-requires-elevation"] === true,
        needsApproval: operation["x-noust-requires-approval"] === true,
        deprecated: operation["deprecated"] === true,
        parameters: parametersOf(doc, operation, item["parameters"]),
        body: schema === undefined ? null : fieldsOf(doc, schema),
        example: schema === undefined ? null : exampleOf(doc, schema),
        rawBody: isRecord(operation["requestBody"]) && json === null,
        responses: Object.entries(responses).map(([status, response]) => ({ status, description: isRecord(response) ? text(response["description"]) : "" })),
      });
    }
  }
  operations.sort((a, b) => a.tag.localeCompare(b.tag) || a.path.localeCompare(b.path) || METHODS.indexOf(a.method.toLowerCase() as (typeof METHODS)[number]) - METHODS.indexOf(b.method.toLowerCase() as (typeof METHODS)[number]));
  return {
    operations,
    tags: [...new Set(operations.map((operation) => operation.tag).filter((tag) => tag !== ""))].sort(),
    title: text(info["title"]),
    version: text(info["version"]),
  };
}

/** What a search keeps: the text is looked for in the route, its summary, its tag and its permission. */
export function filterOperations(operations: readonly ApiOperation[], search: { q?: string | undefined; tag?: string | undefined; method?: string | undefined }): ApiOperation[] {
  const needle = search.q?.trim().toLowerCase() ?? "";
  return operations.filter((operation) => {
    if (search.tag !== undefined && operation.tag !== search.tag) return false;
    if (search.method !== undefined && operation.method !== search.method) return false;
    if (needle === "") return true;
    return [operation.path, operation.summary, operation.tag, operation.permission ?? ""].some((field) => field.toLowerCase().includes(needle));
  });
}

/** The route's address with the values given for its path parameters; a missing one stays as `{name}`. */
export function addressOf(operation: ApiOperation, values: Readonly<Record<string, string>>): string {
  const path = operation.path.replace(/\{([^}]+)\}/g, (whole, name: string) => {
    const value = values[name];
    return value === undefined || value === "" ? whole : encodeURIComponent(value);
  });
  const query = new URLSearchParams();
  for (const parameter of operation.parameters) {
    const value = values[parameter.name];
    if (parameter.where === "query" && value !== undefined && value !== "") query.set(parameter.name, value);
  }
  const suffix = query.toString();
  return suffix === "" ? path : `${path}?${suffix}`;
}

/** A `curl` that makes the call with a token held in `$NOUST_TOKEN`: the token itself is never written out. */
export function curlOf(operation: ApiOperation, origin: string, values: Readonly<Record<string, string>>): string {
  const parts = ["curl -sS", `-X ${operation.method}`, '-H "Authorization: Bearer $NOUST_TOKEN"'];
  if (operation.body !== null) {
    parts.push('-H "Content-Type: application/json"');
    parts.push(`-d '${JSON.stringify(operation.example).replaceAll("'", "'\\''")}'`);
  }
  parts.push(`"${origin}${addressOf(operation, values)}"`);
  return parts.join(" ");
}
