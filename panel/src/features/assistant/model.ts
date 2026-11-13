/**
 * The assistant's turns as the console draws them. The API describes a turn, a step and a proposal loosely (`object` for most of what is
 * inside them), so what arrives is read here once, with every field checked, into types the panel can rely on. A field that is not
 * what it should be is left out, never guessed: the panel draws what the server said and nothing it made up.
 */

import { planFrom } from "../../components/ui/PlanPreview";
import type { Plan } from "../../components/ui/PlanPreview";

export interface Sentence {
  code: string;
  params: Record<string, string | number | boolean>;
  text: string;
  hint: string | null;
}

export type StepState = "running" | "done" | "failed" | "refused" | "proposed";
export type ProposalKlass = "alone" | "ask" | "you" | "two";
export type ProposalState = "waiting" | "running" | "second" | "done" | "failed" | "rejected";
export type TurnState = "running" | "done" | "failed" | "cancelled" | "stopped";

export interface Step {
  id: string;
  tool: string;
  what: Sentence | null;
  server: string;
  state: StepState;
  outside: boolean;
  size: { unit: string; count: number } | null;
  masked: number;
  error: string | null;
  proposal: string | null;
}

export interface Foreign {
  kind: string;
  source: string | null;
  line: number | null;
  label: Sentence | null;
}

export interface Fact {
  name: string;
  value: string;
  foreign: Foreign | null;
}

export interface Call {
  method: string;
  path: string;
  query: readonly (readonly [string, string])[];
  body: unknown;
  headers: Readonly<Record<string, string>>;
}

export interface Proposal {
  id: string;
  turn: string;
  tool: string;
  klass: ProposalKlass;
  state: ProposalState;
  title: Sentence | null;
  effect: string;
  why: Sentence | null;
  permission: string;
  environment: string;
  server: string;
  facts: readonly Fact[];
  plan: Plan | null;
  call: Call;
  fingerprint: string;
  decidedAt: string | null;
  decidedBy: string | null;
  status: number | null;
  result: Sentence | string | null;
  output: string | null;
  approval: number | null;
  jobId: string | null;
  verified: boolean;
}

export type Part = { type: "text"; text: string } | { type: "literal"; source: { id: string; kind: string; label: Sentence | null }; lines: readonly { n: number; text: string }[] };

export interface NotOffered {
  title: Sentence | null;
  why: Sentence | null;
  floor: boolean;
}

export interface Usage {
  steps: number;
  tokens: number;
  spend: number;
  conversation: { tokens: number; spend: number; steps: number } | null;
  currency: string;
  actionsLastHour: number | null;
}

export interface Turn {
  id: string;
  seq: number;
  state: TurnState;
  question: string;
  page: Record<string, unknown> | null;
  server: string;
  model: string;
  location: string;
  startedAt: string;
  endedAt: string | null;
  steps: readonly Step[];
  text: string;
  parts: readonly Part[];
  proposals: readonly Proposal[];
  notOffered: readonly NotOffered[];
  warnings: readonly Sentence[];
  stop: Sentence | null;
  error: (Sentence & { output: string | null }) | null;
  usage: Usage | null;
}

type Raw = Record<string, unknown>;

function rec(value: unknown): Raw | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Raw) : null;
}
const str = (value: unknown, fallback = ""): string => (typeof value === "string" ? value : fallback);
const num = (value: unknown, fallback = 0): number => (typeof value === "number" && Number.isFinite(value) ? value : fallback);
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

export function asSentence(value: unknown): Sentence | null {
  const raw = rec(value);
  if (raw === null || typeof raw["code"] !== "string") return null;
  const params: Record<string, string | number | boolean> = {};
  for (const [key, item] of Object.entries(rec(raw["params"]) ?? {})) {
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") params[key] = item;
  }
  return { code: raw["code"], params, text: str(raw["text"], raw["code"]), hint: typeof raw["hint"] === "string" ? raw["hint"] : null };
}

export function asStep(value: unknown): Step | null {
  const raw = rec(value);
  if (raw === null || typeof raw["id"] !== "string") return null;
  const size = rec(raw["size"]);
  const state = str(raw["state"]);
  return {
    id: raw["id"],
    tool: str(raw["tool"]),
    what: asSentence(raw["what"]),
    server: str(raw["server"]),
    state: state === "running" || state === "failed" || state === "refused" || state === "proposed" ? state : "done",
    outside: raw["outside"] === true,
    size: size !== null && typeof size["unit"] === "string" ? { unit: size["unit"], count: num(size["count"]) } : null,
    masked: num(raw["masked"]),
    error: typeof raw["error"] === "string" ? raw["error"] : null,
    proposal: typeof raw["proposal"] === "string" ? raw["proposal"] : null,
  };
}

function asFact(value: unknown): Fact | null {
  const raw = rec(value);
  if (raw === null || typeof raw["name"] !== "string") return null;
  const foreign = rec(raw["foreign"]);
  return {
    name: raw["name"],
    value: typeof raw["value"] === "string" ? raw["value"] : JSON.stringify(raw["value"] ?? ""),
    foreign: foreign === null ? null : { kind: str(foreign["kind"], "value"), source: typeof foreign["source"] === "string" ? foreign["source"] : null, line: typeof foreign["line"] === "number" ? foreign["line"] : null, label: asSentence(foreign["label"]) },
  };
}

export function asProposal(value: unknown): Proposal | null {
  const raw = rec(value);
  const call = rec(raw?.["call"]);
  if (raw === null || call === null || typeof raw["id"] !== "string") return null;
  const klass = str(raw["klass"]);
  const state = str(raw["state"]);
  const result = raw["result"];
  return {
    id: raw["id"],
    turn: str(raw["turn"]),
    tool: str(raw["tool"]),
    klass: klass === "alone" || klass === "you" || klass === "two" ? klass : "ask",
    state: state === "running" || state === "second" || state === "done" || state === "failed" || state === "rejected" ? state : "waiting",
    title: asSentence(raw["title"]),
    effect: str(raw["effect"]),
    why: asSentence(raw["why"]),
    permission: str(raw["permission"]),
    environment: str(raw["environment"]),
    server: str(raw["server"]),
    facts: list(raw["facts"]).flatMap((fact) => asFact(fact) ?? []),
    plan: rec(raw["plan"])?.["effect"] !== undefined ? planFrom(raw["plan"] as Parameters<typeof planFrom>[0]) : null,
    call: {
      method: str(call["method"], "POST"),
      path: str(call["path"]),
      query: list(call["query"]).flatMap((pair) => (Array.isArray(pair) && typeof pair[0] === "string" ? [[pair[0], String(pair[1] ?? "")] as const] : [])),
      body: call["body"],
      headers: Object.fromEntries(Object.entries(rec(call["headers"]) ?? {}).flatMap(([name, item]) => (typeof item === "string" ? [[name, item]] : []))),
    },
    fingerprint: str(raw["fingerprint"]),
    decidedAt: typeof raw["decided_at"] === "string" ? raw["decided_at"] : null,
    decidedBy: typeof raw["decided_by"] === "string" ? raw["decided_by"] : null,
    status: typeof raw["status"] === "number" ? raw["status"] : null,
    result: asSentence(result) ?? (typeof result === "string" ? result : null),
    output: typeof raw["output"] === "string" ? raw["output"] : null,
    approval: typeof raw["approval"] === "number" ? raw["approval"] : null,
    jobId: typeof raw["job_id"] === "string" ? raw["job_id"] : null,
    verified: raw["verified"] !== false,
  };
}

function asPart(value: unknown): Part | null {
  const raw = rec(value);
  if (raw === null) return null;
  if (raw["type"] === "text") return { type: "text", text: str(raw["text"]) };
  if (raw["type"] === "literal") {
    const source = rec(raw["source"]);
    return {
      type: "literal",
      source: { id: str(source?.["id"]), kind: str(source?.["kind"]), label: asSentence(source?.["label"]) },
      lines: list(raw["lines"]).flatMap((line) => {
        const item = rec(line);
        return item !== null && typeof item["n"] === "number" ? [{ n: item["n"], text: str(item["text"]) }] : [];
      }),
    };
  }
  return null;
}

function asUsage(value: unknown): Usage | null {
  const raw = rec(value);
  if (raw === null) return null;
  const conversation = rec(raw["conversation"]);
  return {
    steps: num(raw["steps"]),
    tokens: num(raw["tokens"]),
    spend: num(raw["spend"]),
    conversation: conversation === null ? null : { tokens: num(conversation["tokens"]), spend: num(conversation["spend"]), steps: num(conversation["steps"]) },
    currency: str(raw["currency"]),
    actionsLastHour: typeof raw["actions_last_hour"] === "number" ? raw["actions_last_hour"] : null,
  };
}

export function asTurn(value: unknown): Turn | null {
  const raw = rec(value);
  if (raw === null || typeof raw["id"] !== "string") return null;
  const state = str(raw["state"]);
  const error = rec(raw["error"]);
  const errorSentence = asSentence(error);
  return {
    id: raw["id"],
    seq: num(raw["seq"]),
    state: state === "done" || state === "failed" || state === "cancelled" || state === "stopped" ? state : "running",
    question: str(raw["question"]),
    page: rec(raw["page"]),
    server: str(raw["server"]),
    model: str(raw["model"]),
    location: str(raw["location"], "unknown"),
    startedAt: str(raw["started_at"]),
    endedAt: typeof raw["ended_at"] === "string" ? raw["ended_at"] : null,
    steps: list(raw["steps"]).flatMap((step) => asStep(step) ?? []),
    text: str(raw["text"]),
    parts: list(raw["parts"]).flatMap((part) => asPart(part) ?? []),
    proposals: list(raw["proposals"]).flatMap((proposal) => asProposal(proposal) ?? []),
    notOffered: list(raw["not_offered"]).flatMap((item) => {
      const found = rec(item);
      return found === null ? [] : [{ title: asSentence(found["title"]), why: asSentence(found["why"]), floor: found["floor"] === true }];
    }),
    warnings: list(raw["warnings"]).flatMap((warning) => asSentence(warning) ?? []),
    stop: asSentence(raw["stop"]),
    error: errorSentence === null ? null : { ...errorSentence, output: typeof error?.["output"] === "string" ? error["output"] : null },
    usage: asUsage(raw["usage"]),
  };
}

/** What a page the panel reads is, as the API takes it (`page` of a turn). */
export type PageRef = { kind: "deployment"; id: number; domain?: string } | { kind: "diagnosis"; domain: string } | { kind: "alert"; rule: string } | { kind: "page"; route: string };

/** The body of the question's `page` member, without the console's own `domain` of a deployment, which the API reads from the id. */
export function pageBody(page: PageRef | null, server: string | null): Record<string, unknown> | null {
  if (page === null) return null;
  const body: Record<string, unknown> = page.kind === "deployment" ? { kind: "deployment", id: page.id } : { ...page };
  if (server !== null) body["server"] = server;
  return body;
}

/** Applies one event of the live stream to a turn that is being written. */
export interface LiveTurn {
  id: string;
  model: string;
  location: string;
  server: string;
  /** What has been said so far; a draft until the end replaces it. */
  draft: string;
  steps: readonly Step[];
  proposals: readonly Proposal[];
  warnings: readonly Sentence[];
  usage: Usage | null;
}

export function startLive(event: Raw): LiveTurn | null {
  const turn = event["turn"];
  if (typeof turn !== "string") return null;
  return { id: turn, model: str(event["model"]), location: str(event["location"], "unknown"), server: str(event["server"]), draft: "", steps: [], proposals: [], warnings: [], usage: null };
}

function upsert<T extends { id: string }>(items: readonly T[], item: T): T[] {
  return items.some((candidate) => candidate.id === item.id) ? items.map((candidate) => (candidate.id === item.id ? item : candidate)) : [...items, item];
}

/**
 * One more event of a turn in progress.
 *
 * @param live - The turn so far.
 * @param event - What arrived on `/events`.
 * @returns The turn with it applied, or null when the turn ended (the end carries the whole turn).
 */
export function applyLive(live: LiveTurn, event: Raw): LiveTurn | "end" {
  switch (event["type"]) {
    case "text":
      return { ...live, draft: live.draft + str(event["delta"]) };
    case "step": {
      const step = asStep(event["step"]);
      // What the model said before it called a tool is a draft: the answer is what follows the last step, and the end replaces it all.
      return step === null ? live : { ...live, steps: upsert(live.steps, step), draft: "" };
    }
    case "proposal": {
      const proposal = asProposal(event["proposal"]);
      return proposal === null ? live : { ...live, proposals: upsert(live.proposals, proposal) };
    }
    case "warning": {
      const warning = asSentence(event["warning"]);
      return warning === null ? live : { ...live, warnings: [...live.warnings, warning] };
    }
    case "usage":
      return { ...live, usage: asUsage(event["usage"]) ?? live.usage };
    case "end":
      return "end";
    default:
      return live;
  }
}
