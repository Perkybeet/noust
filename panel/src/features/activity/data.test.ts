import { describe, expect, it } from "vitest";

import { bindT } from "../../i18n/useT";
import type { AuditEntry, ActivityJob } from "./data";
import {
  actionWords,
  actorSource,
  actorWords,
  auditActionLabel,
  auditCategory,
  auditResultStatus,
  describeActor,
  inKind,
  isFiltered,
  jobActionLabel,
  matchesText,
  mergeActivity,
  resourceOf,
  resourceWords,
  resultOptions,
  resultValidFor,
  resultView,
  rowActor,
  validateActivitySearch,
} from "./data";

const t = bindT("en");

function job(overrides: Partial<ActivityJob> = {}): ActivityJob {
  return {
    id: "abc123",
    type: "update",
    name: "Update example.com",
    description: "Updating the application at example.com",
    status: "completed",
    progress: 100,
    total_steps: 100,
    current_step: "",
    created_at: "2026-09-25T10:00:00Z",
    started_at: "2026-09-25T10:00:01Z",
    completed_at: "2026-09-25T10:00:30Z",
    result: null,
    error: null,
    logs: [],
    metadata: {},
    actor: null,
    ...overrides,
  };
}

/** The one row a single-row merge produced, asserting there is exactly one rather than assuming it. */
function only<T>(items: readonly T[]): T {
  expect(items).toHaveLength(1);
  const [item] = items;
  return item as T;
}

function entry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    timestamp: "2026-09-25T10:00:00+00:00",
    action: "auth.login",
    result: "success",
    actor: "a1b2c3d4e5f6",
    client_ip: "203.0.113.1",
    resource: "/api/auth/login",
    detail: null,
    sensitive: false,
    ...overrides,
  };
}

describe("jobActionLabel", () => {
  it("translates every JobType to a sentence-case word", () => {
    expect(jobActionLabel(t, "deploy")).toBe("Deploy");
    expect(jobActionLabel(t, "cert_renew")).toBe("Renew certificate");
    expect(jobActionLabel(t, "migrate")).toBe("Turn on instant rollback");
    expect(jobActionLabel(t, "push")).toBe("Copy backup to destination");
    expect(jobActionLabel(t, "zero_downtime")).toBe("Zero-downtime mode");
  });

  it("shows an unrecognised type verbatim rather than guessing", () => {
    expect(jobActionLabel(t, "mystery")).toBe("mystery");
  });
});

describe("auditActionLabel", () => {
  it("translates a known action", () => {
    expect(auditActionLabel(t, "auth.login")).toBe("Sign-in attempt");
    expect(auditActionLabel(t, "auth.scope")).toBe("Failed a scope check");
  });

  it("words the security middleware's generic per-request entry by its method", () => {
    expect(auditActionLabel(t, "api.post")).toBe("POST request");
    expect(auditActionLabel(t, "api.delete")).toBe("DELETE request");
  });

  it("shows an unrecognised action verbatim rather than guessing", () => {
    expect(auditActionLabel(t, "something.new")).toBe("something.new");
  });
});

describe("auditResultStatus", () => {
  it("maps every result the backend writes", () => {
    expect(auditResultStatus(t, "success").state).toBe("running");
    expect(auditResultStatus(t, "denied").state).toBe("failed");
    expect(auditResultStatus(t, "denied").attention).toBe(true);
    expect(auditResultStatus(t, "locked").label).toBe("Locked out");
  });

  it("humanises an unrecognised result instead of guessing its state", () => {
    const view = auditResultStatus(t, "weird_thing");
    expect(view.state).toBe("unknown");
    expect(view.label).toBe("Weird thing");
  });

  it("treats the security middleware's generic error:<status> as a failure", () => {
    const view = auditResultStatus(t, "error:401");
    expect(view.state).toBe("failed");
    expect(view.label).toBe("Error 401");
    expect(view.attention).toBe(true);
  });
});

describe("describeActor", () => {
  it("names the master token", () => {
    expect(describeActor(t, "master")).toEqual({ label: "The master token", raw: "master" });
  });

  it("names an API token by its own name, keeping the raw value", () => {
    expect(describeActor(t, "token:ci-deploy")).toEqual({ label: 'Token "ci-deploy"', raw: "token:ci-deploy" });
  });

  it("names a webhook delivery", () => {
    expect(describeActor(t, "webhook").label).toBe("A webhook delivery");
  });

  it("names an anonymous, unauthenticated attempt", () => {
    expect(describeActor(t, "anonymous").label).toBe("Anonymous");
  });

  it("names a browser session by a short id, keeping the full raw value", () => {
    const words = describeActor(t, "a1b2c3d4e5f6g7h8");
    expect(words.label).toBe("Session a1b2c3d4");
    expect(words.raw).toBe("a1b2c3d4e5f6g7h8");
  });
});

describe("actorWords / rowActor", () => {
  it("reads a job row's actor", () => {
    const row = only(mergeActivity({ jobs: [job({ actor: "master" })], jobsComplete: true, entries: [], auditComplete: true }).rows);
    expect(rowActor(row)).toBe("master");
    expect(actorWords(t, row).label).toBe("The master token");
  });

  it("says a job with no recorded actor is not recorded, rather than guessing", () => {
    const row = only(mergeActivity({ jobs: [job({ actor: null })], jobsComplete: true, entries: [], auditComplete: true }).rows);
    expect(actorWords(t, row)).toEqual({ label: "Not recorded", raw: "-" });
  });

  it("reads an audit row's actor", () => {
    const row = only(mergeActivity({ jobs: [], jobsComplete: true, entries: [entry({ actor: "token:ci" })], auditComplete: true }).rows);
    expect(rowActor(row)).toBe("token:ci");
  });
});

describe("actionWords / resourceOf", () => {
  it("words a job row from its type and domain", () => {
    const row = only(
      mergeActivity({
        jobs: [job({ type: "deploy", metadata: { domain: "shop.example.com" } })],
        jobsComplete: true,
        entries: [],
        auditComplete: true,
      }).rows,
    );
    expect(actionWords(t, row)).toEqual({ label: "Deploy", raw: "deploy" });
    expect(resourceOf(row)).toBe("shop.example.com");
  });

  it("words an audit row from its action and resource", () => {
    const row = only(
      mergeActivity({
        jobs: [],
        jobsComplete: true,
        entries: [entry({ action: "auth.scope", resource: "/api/apps/shop.example.com" })],
        auditComplete: true,
      }).rows,
    );
    expect(actionWords(t, row)).toEqual({ label: "Failed a scope check", raw: "auth.scope" });
    expect(resourceOf(row)).toBe("/api/apps/shop.example.com");
  });
});

describe("mergeActivity", () => {
  it("merges both sources into one newest-first timeline", () => {
    const jobs = [job({ id: "j1", started_at: "2026-09-25T09:00:00Z" })];
    const entries = [entry({ timestamp: "2026-09-25T09:30:00+00:00" }), entry({ timestamp: "2026-09-25T08:30:00+00:00" })];
    const { rows } = mergeActivity({ jobs, jobsComplete: true, entries, auditComplete: true });
    expect(rows.map((row) => row.timestamp)).toEqual([
      "2026-09-25T09:30:00+00:00",
      "2026-09-25T09:00:00Z",
      "2026-09-25T08:30:00+00:00",
    ]);
  });

  it("reports no more pages once both sources are complete", () => {
    const { hasMore } = mergeActivity({ jobs: [job()], jobsComplete: true, entries: [entry()], auditComplete: true });
    expect(hasMore).toBe(false);
  });

  it("filters the merged rows by actor without affecting which raw rows are trusted", () => {
    const jobs = [job({ id: "j1", actor: "master", started_at: "2026-09-25T09:00:00Z" })];
    const entries = [entry({ actor: "token:ci", timestamp: "2026-09-25T09:30:00+00:00" })];
    const { rows } = mergeActivity({ jobs, jobsComplete: true, entries, auditComplete: true, actor: "master" });
    expect(only(rows).kind).toBe("job");
  });

  it("holds back rows older than the shallower source's cutoff, so a later page never reorders what is already shown", () => {
    // Audit's fetched page reaches back only to 10:00 (more of it may exist, unfetched); jobs
    // already reached all the way to 08:00. A job at 09:00 sits in the gap: audit might yet
    // turn out to have an entry between 09:00 and 10:00, which would have to sort above it -
    // so it must not be shown until "Load more" extends audit's coverage past it.
    const jobs = [
      job({ id: "recent", started_at: "2026-09-25T11:00:00Z" }),
      job({ id: "in-the-gap", started_at: "2026-09-25T09:00:00Z" }),
      job({ id: "oldest", started_at: "2026-09-25T08:00:00Z" }),
    ];
    const entries = [entry({ timestamp: "2026-09-25T10:30:00+00:00" }), entry({ timestamp: "2026-09-25T10:00:00+00:00" })];

    const { rows, hasMore } = mergeActivity({ jobs, jobsComplete: true, entries, auditComplete: false });

    expect(hasMore).toBe(true);
    const ids = rows.filter((row) => row.kind === "job").map((row) => row.job.id);
    expect(ids).toEqual(["recent"]);
    expect(rows.some((row) => row.kind === "job" && row.job.id === "in-the-gap")).toBe(false);
  });

  it("shows everything once the previously incomplete source catches up past the gap", () => {
    const jobs = [
      job({ id: "recent", started_at: "2026-09-25T11:00:00Z" }),
      job({ id: "in-the-gap", started_at: "2026-09-25T09:00:00Z" }),
    ];
    // A second page of audit now reaches back to 08:30, past the job in the gap.
    const entries = [
      entry({ timestamp: "2026-09-25T10:30:00+00:00" }),
      entry({ timestamp: "2026-09-25T10:00:00+00:00" }),
      entry({ timestamp: "2026-09-25T08:30:00+00:00" }),
    ];

    const { rows, hasMore } = mergeActivity({ jobs, jobsComplete: true, entries, auditComplete: true });

    expect(hasMore).toBe(false);
    expect(rows).toHaveLength(5);
    expect(rows.some((row) => row.kind === "job" && row.job.id === "in-the-gap")).toBe(true);
  });
});

describe("resultValidFor / resultOptions", () => {
  it("accepts a job status wherever jobs are shown, and not among sign-ins", () => {
    expect(resultValidFor("failed", undefined)).toBe(true);
    expect(resultValidFor("failed", "all")).toBe(true);
    expect(resultValidFor("failed", "access")).toBe(false);
  });

  it("accepts an audit result in every view", () => {
    expect(resultValidFor("denied", "access")).toBe(true);
    expect(resultValidFor("denied", undefined)).toBe(true);
  });

  it("offers the audit's vocabulary alone among sign-ins", () => {
    expect(resultOptions(t, "access").map((o) => o.value)).not.toContain("failed");
    expect(resultOptions(t, "access").find((o) => o.value === "denied")?.label).toBe("Denied");
  });

  it("prefixes both vocabularies where jobs and actions mix, so the same word is not offered twice unlabelled", () => {
    const options = resultOptions(t, undefined);
    expect(options.find((o) => o.value === "failed")?.label).toBe("Job: Failed");
    expect(options.find((o) => o.value === "failure")?.label).toBe("Action: Failed");
  });
});

describe("validateActivitySearch", () => {
  it("keeps a known view, a result valid for it, the search and any actor", () => {
    expect(validateActivitySearch({ kind: "all", result: "failed", q: " deploy ", actor: "master" })).toEqual({
      kind: "all",
      result: "failed",
      q: "deploy",
      actor: "master",
    });
  });

  it("drops a result that does not apply to the view, instead of failing", () => {
    expect(validateActivitySearch({ kind: "access", result: "failed" })).toEqual({ kind: "access" });
  });

  it("opens 3.0's views: jobs are operations now, and the audit log alone is everything", () => {
    expect(validateActivitySearch({ kind: "jobs" })).toEqual({});
    expect(validateActivitySearch({ kind: "audit" })).toEqual({ kind: "all" });
    expect(validateActivitySearch({ kind: "everything" })).toEqual({});
  });
});

describe("isFiltered", () => {
  it("is false with nothing set and true with any filter", () => {
    expect(isFiltered({})).toBe(false);
    expect(isFiltered({ actor: "master" })).toBe(true);
    expect(isFiltered({ kind: "access" })).toBe(true);
    expect(isFiltered({ q: "shop" })).toBe(true);
  });
});

describe("views", () => {
  const rows = (entries: AuditEntry[], jobs: ActivityJob[] = []) =>
    mergeActivity({ jobs, jobsComplete: true, entries, auditComplete: true }).rows;

  it("reads an entry's category from the catalog, and before 3.1 from its action", () => {
    expect(auditCategory(entry({ category: "change", action: "apps.restart" }))).toBe("change");
    expect(auditCategory(entry({ action: "auth.login" }))).toBe("access");
    expect(auditCategory(entry({ action: "ws.connect" }))).toBe("access");
    expect(auditCategory(entry({ action: "apps.env.update" }))).toBe("change");
  });

  it("opens on operations: jobs and changes, not sign-ins", () => {
    const all = rows(
      [
        entry({ action: "auth.login", category: "access", timestamp: "2026-09-25T10:03:00+00:00" }),
        entry({ action: "apps.restart", category: "change", resource: "/api/apps/shop.example.com/restart", timestamp: "2026-09-25T10:02:00+00:00" }),
        entry({ action: "auth.scope", category: "denial", timestamp: "2026-09-25T10:01:00+00:00" }),
        entry({ action: "apps.env.reveal", category: "read", timestamp: "2026-09-25T09:59:00+00:00" }),
      ],
      [job({ id: "j", started_at: "2026-09-25T10:00:00Z" })],
    );
    const actions = (kind: "access" | "all" | undefined) =>
      all.filter((row) => inKind(row, kind)).map((row) => (row.kind === "job" ? "job" : row.entry.action));
    expect(actions(undefined)).toEqual(["apps.restart", "job"]);
    expect(actions("access")).toEqual(["auth.login", "auth.scope", "apps.env.reveal"]);
    expect(actions("all")).toHaveLength(5);
  });

  it("finds a row by what it shows or what was recorded", () => {
    const [row] = rows([entry({ action: "apps.restart", resource: "/api/apps/shop.example.com/restart" })]);
    if (row === undefined) throw new Error("no row");
    expect(matchesText(row, "SHOP.example")).toBe(true);
    expect(matchesText(row, "reinició", ["Reinició una aplicación"])).toBe(true);
    expect(matchesText(row, "blog")).toBe(false);
    expect(matchesText(row, "")).toBe(true);
  });
});

describe("a two-factor sign-in", () => {
  const ask = (timestamp: string, ip = "203.0.113.1") =>
    entry({ action: "auth.login", result: "failure", detail: "second factor required but not presented", timestamp, client_ip: ip, actor: "anonymous" });
  const signIn = (timestamp: string, ip = "203.0.113.1") => entry({ action: "auth.login", result: "success", timestamp, client_ip: ip });

  it("is one row, the sign-in, which says it went through the code", () => {
    const { rows } = mergeActivity({
      jobs: [],
      jobsComplete: true,
      entries: [signIn("2026-09-25T10:00:30+00:00"), ask("2026-09-25T10:00:00+00:00")],
      auditComplete: true,
    });
    const row = only(rows);
    expect(row.kind === "audit" && row.secondFactor).toBe(true);
    expect(resultView(t, row).state).toBe("running");
  });

  it("keeps an ask nobody answered, as a step and not a failure", () => {
    const { rows } = mergeActivity({
      jobs: [],
      jobsComplete: true,
      entries: [signIn("2026-09-25T10:00:30+00:00", "198.51.100.9"), ask("2026-09-25T10:00:00+00:00")],
      auditComplete: true,
    });
    expect(rows).toHaveLength(2);
    const unanswered = rows.find((row) => row.kind === "audit" && row.entry.result === "failure");
    if (unanswered === undefined) throw new Error("no ask");
    expect(resultView(t, unanswered)).toEqual({ state: "stopped", label: "Asked for the code", attention: false });
  });
});

describe("resourceWords / who", () => {
  const one = (overrides: Partial<AuditEntry>) =>
    only(mergeActivity({ jobs: [], jobsComplete: true, entries: [entry(overrides)], auditComplete: true }).rows);

  it("names what an action was done to, not the API path", () => {
    expect(resourceWords(one({ action: "apps.restart", resource: "/api/apps/shop.example.com/restart" }))).toEqual({
      object: "shop.example.com",
      raw: "/api/apps/shop.example.com/restart",
    });
    expect(resourceWords(one({ action: "db.drop", resource: "/api/databases/postgresql/shop" })).object).toBe("shop");
    // An endpoint of a collection is not one of its objects.
    expect(resourceWords(one({ action: "api.post", resource: "/api/cron/preview" })).object).toBe("/api/cron/preview");
    // A sign-in is done to the console itself: no object.
    expect(resourceWords(one({ resource: "/api/auth/login" })).object).toBeNull();
    // Anything else is shown as it was recorded.
    expect(resourceWords(one({ action: "config.change", resource: "backup.max_per_app" })).object).toBe("backup.max_per_app");
  });

  it("names the person behind an action when the log records them", () => {
    const account = one({ actor: "ana", who: { kind: "user", name: "ana", role: "admin", source: "203.0.113.8" } });
    expect(actorWords(t, account)).toEqual({ label: "ana", raw: "ana" });
    expect(actorSource(account)).toBe("203.0.113.8");
    expect(actorWords(t, one({ actor: "cli:yago", who: { kind: "cli", name: "yago" } })).label).toBe("yago at the terminal");
    expect(actorWords(t, one({ actor: "hq on behalf of ana", who: { kind: "fleet", name: "ana", via: "fleet:hq" } })).label).toBe("ana, through hq");
    expect(actorWords(t, one({ actor: "token:ci", who: { kind: "token", name: "token:ci" } })).label).toBe('Token "ci"');
  });
});

describe("withoutRequestEchoes", () => {
  const entry = (action: string, timestamp: string, resource = "/api/auth/login"): AuditEntry => ({
    action,
    timestamp,
    resource,
    actor: "anonymous",
    result: "ok",
    sensitive: false,
  });
  const rows = (entries: AuditEntry[]) => mergeActivity({ jobs: [], jobsComplete: true, entries, auditComplete: true }).rows;

  it("drops a request's generic record when the endpoint recorded the same moment itself", () => {
    const merged = rows([
      entry("api.post", "2026-09-25T19:00:01+00:00"),
      entry("auth.login", "2026-09-25T19:00:01+00:00"),
    ]);
    expect(merged.map((row) => (row.kind === "audit" ? row.entry.action : row.kind))).toEqual(["auth.login"]);
  });

  it("keeps the generic record of a request nothing else described", () => {
    const merged = rows([
      entry("api.post", "2026-09-25T19:00:01+00:00", "/api/apps/shop.example.com/restart"),
      entry("auth.login", "2026-09-25T19:00:01+00:00"),
      entry("api.post", "2026-09-25T19:05:00+00:00"),
    ]);
    expect(merged.map((row) => (row.kind === "audit" ? `${row.entry.action} ${row.entry.resource ?? ""}` : row.kind))).toEqual([
      "api.post /api/auth/login",
      "api.post /api/apps/shop.example.com/restart",
      "auth.login /api/auth/login",
    ]);
  });
});
