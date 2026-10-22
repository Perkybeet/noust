/**
 * The one query language of every log surface: words, "phrases", `@unit:`, `@app:`, `@level:`,
 * `@pid:`, `-` or `NOT`, `AND`, `OR` and (groups). It is the journal's grammar
 * (noust.managers.server.logquery), read the same way, so that what the operator types means the
 * same on the Logs page, on the system's journal and on an application's tab.
 *
 * The server judges a query that goes to it (a search over a range) and writes it back as it
 * understood it. This file reads the same text on the client for what the server never sees: the
 * lines of a stream that is followed live, which are filtered here, and the line that says how a
 * live query was understood. A source then turns the fields into the parameters of its own
 * endpoint (`features/logs/data.ts`).
 *
 * Pure, so every rule is tested without a page.
 */

/** Longest query accepted, in characters: the server's own ceiling. */
export const MAX_QUERY = 500;
/** Most terms, fields and operators one query may hold. */
const MAX_NODES = 40;
/** Deepest nesting of parentheses and negations. */
const MAX_DEPTH = 8;
/** Most `*` a unit pattern may hold. */
const MAX_WILDCARDS = 4;

/** The fields a query may name, and the words that stand for them. */
const FIELDS: Readonly<Record<string, FieldName>> = { unit: "unit", service: "unit", app: "app", level: "level", priority: "level", pid: "pid" };

export type FieldName = "unit" | "app" | "level" | "pid";

/** The levels by name, with the spellings people use. */
const LEVELS: Readonly<Record<string, number>> = {
  emerg: 0,
  alert: 1,
  crit: 2,
  critical: 2,
  err: 3,
  error: 3,
  warning: 4,
  warn: 4,
  notice: 5,
  info: 6,
  debug: 7,
};

export type QueryNode =
  | { kind: "any" }
  | { kind: "term"; text: string }
  | { kind: "field"; name: FieldName; value: string }
  | { kind: "not"; node: QueryNode }
  | { kind: "and"; nodes: readonly QueryNode[] }
  | { kind: "or"; nodes: readonly QueryNode[] };

/** A query that cannot be read: what is wrong and where, in the server's words. */
export class QueryError extends Error {
  readonly at: number | null;

  constructor(message: string, at: number | null = null) {
    super(at === null ? message : `${message} (at character ${String(at + 1)})`);
    this.name = "QueryError";
    this.at = at;
  }
}

interface Token {
  kind: "(" | ")" | "phrase" | "not" | "field" | "and" | "or" | "word";
  text: string;
  at: number;
}

const WORD = /[^\s()"]+/y;
const FIELD = /@([A-Za-z]+):("([^"]*)"|[^\s()"]*)/y;

function tokenize(text: string): Token[] {
  if (text.length > MAX_QUERY) throw new QueryError(`The query is longer than ${String(MAX_QUERY)} characters`);
  const tokens: Token[] = [];
  let at = 0;
  while (at < text.length) {
    const char = text.charAt(at);
    if (/\s/.test(char)) {
      at += 1;
    } else if (char === "(" || char === ")") {
      tokens.push({ kind: char, text: char, at });
      at += 1;
    } else if (char === '"') {
      const end = text.indexOf('"', at + 1);
      if (end === -1) throw new QueryError("A quote is not closed", at);
      tokens.push({ kind: "phrase", text: text.slice(at + 1, end), at });
      at = end + 1;
    } else if (char === "-" && at + 1 < text.length && !/\s/.test(text.charAt(at + 1))) {
      tokens.push({ kind: "not", text: "-", at });
      at += 1;
    } else if (char === "@") {
      FIELD.lastIndex = at;
      const match = FIELD.exec(text);
      if (match === null) throw new QueryError("A field is written @name:value, such as @unit:nginx", at);
      const value = match[3] ?? match[2] ?? "";
      tokens.push({ kind: "field", text: `${match[1] ?? ""}:${value}`, at });
      at = FIELD.lastIndex;
    } else {
      WORD.lastIndex = at;
      const match = WORD.exec(text);
      const word = match?.[0] ?? char;
      const kind = word === "AND" ? "and" : word === "OR" ? "or" : word === "NOT" ? "not" : "word";
      tokens.push({ kind, text: word, at });
      at += word.length;
    }
  }
  return tokens;
}

class Parser {
  private at = 0;
  private nodes = 0;
  private readonly tokens: readonly Token[];

  constructor(tokens: readonly Token[]) {
    this.tokens = tokens;
  }

  private peek(): Token | undefined {
    return this.tokens[this.at];
  }

  private take(): Token {
    const token = this.tokens[this.at];
    if (token === undefined) throw new QueryError("The query ends where a word, a field or a group was expected");
    this.at += 1;
    return token;
  }

  private count(token: Token): void {
    this.nodes += 1;
    if (this.nodes > MAX_NODES) throw new QueryError(`The query has more than ${String(MAX_NODES)} parts`, token.at);
  }

  parse(): QueryNode {
    if (this.tokens.length === 0) return { kind: "any" };
    const node = this.or(0);
    const leftover = this.peek();
    if (leftover !== undefined) {
      throw new QueryError(`Unexpected ${leftover.kind === ")" ? "closing parenthesis" : `'${leftover.text}'`}`, leftover.at);
    }
    return node;
  }

  private or(depth: number): QueryNode {
    const parts = [this.and(depth)];
    while (this.peek()?.kind === "or") {
      this.count(this.take());
      parts.push(this.and(depth));
    }
    const [only] = parts;
    return parts.length === 1 && only !== undefined ? only : { kind: "or", nodes: parts };
  }

  private and(depth: number): QueryNode {
    const parts = [this.not(depth)];
    for (let token = this.peek(); token !== undefined && token.kind !== "or" && token.kind !== ")"; token = this.peek()) {
      if (token.kind === "and") this.take();
      parts.push(this.not(depth));
    }
    const [only] = parts;
    return parts.length === 1 && only !== undefined ? only : { kind: "and", nodes: parts };
  }

  private not(depth: number): QueryNode {
    const token = this.peek();
    if (depth > MAX_DEPTH) throw new QueryError(`The query is nested more than ${String(MAX_DEPTH)} deep`, token?.at ?? null);
    if (token?.kind === "not") {
      this.count(this.take());
      return { kind: "not", node: this.not(depth + 1) };
    }
    return this.atom(depth);
  }

  private atom(depth: number): QueryNode {
    const token = this.peek();
    if (token === undefined) throw new QueryError("The query ends where a word, a field or a group was expected");
    if (token.kind === "(") {
      this.take();
      const inner = this.or(depth + 1);
      if (this.peek()?.kind !== ")") throw new QueryError("A parenthesis is not closed", token.at);
      this.take();
      return inner;
    }
    if (token.kind === "and" || token.kind === "or" || token.kind === ")") {
      throw new QueryError(token.kind === ")" ? "Unexpected closing parenthesis" : `'${token.text}' needs something on both sides`, token.at);
    }
    this.take();
    this.count(token);
    if (token.kind === "field") {
      const split = token.text.indexOf(":");
      const name = token.text.slice(0, split);
      const value = token.text.slice(split + 1);
      const canonical = FIELDS[name.toLowerCase()];
      if (canonical === undefined) {
        throw new QueryError(`There is no field @${name}: use @unit, @app, @level, @pid`, token.at);
      }
      if (value === "") throw new QueryError(`@${name}: needs a value`, token.at);
      checkValue(canonical, value, token.at);
      return { kind: "field", name: canonical, value };
    }
    return { kind: "term", text: token.text };
  }
}

/** Refuse a field value that could never match, so a typo is told instead of returning nothing. */
function checkValue(name: FieldName, value: string, at: number): void {
  if (name === "level" && LEVELS[value.toLowerCase()] === undefined && !(/^\d+$/.test(value) && Number(value) <= 7)) {
    throw new QueryError(`@level:${value} is not a level: use ${Object.keys(LEVELS).join(", ")} or 0 to 7`, at);
  }
  if (name === "pid" && !/^\d+$/.test(value)) throw new QueryError(`@pid:${value} is not a process number`, at);
  if (name === "unit" && value.split("*").length - 1 > MAX_WILDCARDS) {
    throw new QueryError(`@unit:${value} has more than ${String(MAX_WILDCARDS)} wildcards`, at);
  }
}

/**
 * Reads a query.
 *
 * @param text - What the operator typed.
 * @returns The clause tree; `{ kind: "any" }` for an empty query.
 * @throws {QueryError} It cannot be read, saying where.
 */
export function parseQuery(text: string): QueryNode {
  return new Parser(tokenize(text)).parse();
}

/** The result of reading a query that may be half-typed: the tree, or why not. */
export type ReadQuery = { ok: true; node: QueryNode } | { ok: false; error: QueryError };

/** `parseQuery` for a box the operator is still typing in: a failure is a value, not an exception. */
export function readQuery(text: string): ReadQuery {
  try {
    return { ok: true, node: parseQuery(text) };
  } catch (error) {
    if (error instanceof QueryError) return { ok: false, error };
    throw error;
  }
}

/** A query written back in full, so the operator sees what was understood: `error AND @unit:nginx AND NOT timeout`. */
export function renderQuery(node: QueryNode): string {
  switch (node.kind) {
    case "any":
      return "";
    case "term":
      return /\s/.test(node.text) || node.text === "" ? `"${node.text}"` : node.text;
    case "field":
      return `@${node.name}:${/\s/.test(node.value) ? `"${node.value}"` : node.value}`;
    case "not": {
      const inner = renderQuery(node.node);
      return node.node.kind === "and" || node.node.kind === "or" ? `NOT (${inner})` : `NOT ${inner}`;
    }
    case "and":
    case "or": {
      const parts = node.nodes.map((child) => (child.kind === "or" && node.kind === "and" ? `(${renderQuery(child)})` : renderQuery(child)));
      return parts.join(node.kind === "and" ? " AND " : " OR ");
    }
  }
}

/** The names of the fields a query mentions anywhere. */
export function fieldsOf(node: QueryNode): ReadonlySet<FieldName> {
  switch (node.kind) {
    case "field":
      return new Set([node.name]);
    case "not":
      return fieldsOf(node.node);
    case "and":
    case "or":
      return new Set(node.nodes.flatMap((child) => [...fieldsOf(child)]));
    default:
      return new Set();
  }
}

/** What a query can see of a line. A property the line does not carry is `undefined`, never a guess. */
export interface Subject {
  message: string;
  /** Syslog priority, 0 (emergency) to 7 (debug). */
  priority?: number | undefined;
  unit?: string | undefined;
  app?: string | undefined;
  pid?: number | undefined;
}

/** The priority of a level as the viewer draws it: errors are 3, warnings 4, information 6, debug 7. */
export function priorityOfViewerLevel(level: "error" | "warn" | "info" | "debug" | undefined): number | undefined {
  switch (level) {
    case "error":
      return 3;
    case "warn":
      return 4;
    case "info":
      return 6;
    case "debug":
      return 7;
    default:
      return undefined;
  }
}

const normalise = (unit: string): string => unit.toLowerCase().replace(/\.service$/, "");

/** `*` and `?` as a unit pattern, matched whole and without regular expressions. */
function globMatches(pattern: string, value: string): boolean {
  let p = 0;
  let v = 0;
  let star = -1;
  let mark = 0;
  while (v < value.length) {
    const wanted = pattern.charAt(p);
    if (p < pattern.length && (wanted === "?" || wanted === value.charAt(v))) {
      p += 1;
      v += 1;
    } else if (p < pattern.length && wanted === "*") {
      star = p;
      mark = v;
      p += 1;
    } else if (star !== -1) {
      p = star + 1;
      mark += 1;
      v = mark;
    } else {
      return false;
    }
  }
  while (pattern.charAt(p) === "*") p += 1;
  return p === pattern.length;
}

/** Whether a line is what a clause asks for: `null` when the line does not carry what the clause asks about. */
function judge(node: QueryNode, subject: Subject): boolean | null {
  switch (node.kind) {
    case "any":
      return true;
    case "term":
      return subject.message.toLowerCase().includes(node.text.toLowerCase());
    case "not": {
      const inner = judge(node.node, subject);
      return inner === null ? null : !inner;
    }
    case "and": {
      let unknown = false;
      for (const child of node.nodes) {
        const verdict = judge(child, subject);
        if (verdict === false) return false;
        if (verdict === null) unknown = true;
      }
      return unknown ? null : true;
    }
    case "or": {
      let unknown = false;
      for (const child of node.nodes) {
        const verdict = judge(child, subject);
        if (verdict === true) return true;
        if (verdict === null) unknown = true;
      }
      return unknown ? null : false;
    }
    case "field": {
      if (node.name === "unit") return subject.unit === undefined ? null : globMatches(normalise(node.value), normalise(subject.unit));
      if (node.name === "app") return subject.app === undefined ? null : subject.app.toLowerCase() === node.value.toLowerCase();
      if (node.name === "pid") return subject.pid === undefined ? null : subject.pid === Number(node.value);
      if (subject.priority === undefined) return null;
      const wanted = /^\d+$/.test(node.value) ? Number(node.value) : (LEVELS[node.value.toLowerCase()] ?? 7);
      return subject.priority <= wanted;
    }
  }
}

/**
 * Whether a line stays on screen for a query. A line that does not carry what a clause asks about
 * (a followed stream has no unit on its lines) is kept rather than hidden by a guess: the query
 * can only hide what it can judge.
 */
export function keeps(node: QueryNode, subject: Subject): boolean {
  return judge(node, subject) !== false;
}

/**
 * The clause text for a query made of the pickers and what was typed: `@unit:nginx @level:err`
 * then the words, the way the journal's own page has always composed it.
 */
export function composeQuery(fields: { unit?: string | undefined; app?: string | undefined; level?: string | undefined }, typed: string): string {
  const value = (raw: string): string => (/[\s"()]/.test(raw) ? `"${raw.replaceAll('"', "")}"` : raw);
  return [
    fields.unit !== undefined ? `@unit:${value(fields.unit)}` : "",
    fields.app !== undefined ? `@app:${value(fields.app)}` : "",
    fields.level !== undefined ? `@level:${fields.level}` : "",
    typed.trim(),
  ]
    .filter((part) => part !== "")
    .join(" ");
}
