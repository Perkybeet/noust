import { describe, expect, it } from "vitest";

import type { OverviewAttentionItem } from "../../api/queries/overview";
import { appStatus } from "../../components/page/status";
import type { FleetRow } from "../fleet/data";
import { attentionEntries, coursesOf, fleetServerHome, legendOf, serverHome, sinceOf, timelineOf } from "./homeData";
import type { HomeServer } from "./homeData";
import { overviewFixture } from "./testFixtures";

const WORDS = { describe: (status: string) => appStatus(status, "en"), unreachable: "Not answering" };

function item(id: string, severity: "fail" | "warn", title = id, when: string | null = null): OverviewAttentionItem {
  return {
    id,
    subject: { kind: "app", domain: title },
    title,
    severity,
    reasons: [{ kind: "state", severity, code: "service_failed", params: {}, detail: null, when, deployment_id: null, actions: ["diagnose"] }],
  };
}

function server(name: string, patch: Partial<HomeServer> = {}): HomeServer {
  return { key: name, name, node: name, unreachable: false, diskPercent: null, apps: [], attention: [], activity: [], ...patch };
}

describe("coursesOf", () => {
  it("makes a course of each server, in the order given, with its disk and its applications' states", () => {
    const courses = coursesOf(
      [
        server("web-01", { diskPercent: 52, apps: [{ domain: "shop.example.com", status: "running" }, { domain: "api.example.net", status: "failed" }] }),
        server("web-02", { apps: [] }),
      ],
      WORDS,
    );
    expect(courses.map((course) => course.name)).toEqual(["web-01", "web-02"]);
    expect(courses[0]).toMatchObject({ diskPercent: 52 });
    expect(courses[0]?.apps.map((app) => [app.name, app.state, app.stateLabel])).toEqual([
      ["shop.example.com", "running", "Running"],
      ["api.example.net", "failed", "Failed"],
    ]);
    expect(courses[1]).not.toHaveProperty("diskPercent");
    expect(courses[1]).not.toHaveProperty("problem");
  });

  it("identifies an application by its domain within its course, which the link to it needs", () => {
    const [course] = coursesOf([server("a", { apps: [{ domain: "x.com", status: "running" }] })], WORDS);
    expect(course?.apps[0]?.id).toBe("x.com");
  });

  it("puts a problem on a server that does not answer, and keeps what it last said it ran", () => {
    const [course] = coursesOf([server("ams-3", { unreachable: true, apps: [{ domain: "x.com", status: "running" }] })], WORDS);
    expect(course?.problem).toEqual({ state: "failed", label: "Not answering" });
    expect(course?.apps).toHaveLength(1);
  });
});

describe("legendOf", () => {
  it("counts the well ones together, lists a state only when something is in it, and says running first", () => {
    const courses = coursesOf(
      [server("a", { apps: [{ domain: "1", status: "running" }, { domain: "2", status: "static" }, { domain: "3", status: "deploying" }, { domain: "4", status: "stopped" }, { domain: "5", status: "stopped" }] })],
      WORDS,
    );
    expect(legendOf(courses)).toEqual([
      { state: "running", count: 2 },
      { state: "deploying", count: 1 },
      { state: "stopped", count: 2 },
    ]);
  });

  it("is empty for a fleet with nothing on it", () => {
    expect(legendOf(coursesOf([server("a")], WORDS))).toEqual([]);
  });
});

describe("attentionEntries", () => {
  it("puts what fails before what warns, and keeps a server's own order within one", () => {
    const entries = attentionEntries(
      [server("web-01", { attention: [item("w1", "warn", "cert.example.com")] }), server("web-02", { attention: [item("f2", "fail", "zeta.example.com"), item("f1", "fail", "alpha.example.com")] })],
      [],
      null,
    );
    expect(entries.map((entry) => entry.item.title)).toEqual(["alpha.example.com", "zeta.example.com", "cert.example.com"]);
  });

  it("says which server each is on and keeps two servers' same application apart", () => {
    const entries = attentionEntries([server("web-01", { attention: [item("app:x", "fail", "x.com")] }), server("web-02", { attention: [item("app:x", "fail", "x.com")] })], [], null);
    expect(entries.map((entry) => entry.server?.name)).toEqual(["web-01", "web-02"]);
    expect(new Set(entries.map((entry) => entry.key)).size).toBe(2);
  });

  it("adds what the console knows to the list, as its own and not as a server's", () => {
    const entries = attentionEntries([server("web-01")], [item("approval:3", "warn", "#3")], { name: "fra-1", node: null });
    expect(entries).toHaveLength(1);
    expect(entries[0]?.server).toEqual({ name: "fra-1", node: null });
  });
});

describe("sinceOf", () => {
  it("is the first moment a reason gives", () => {
    expect(sinceOf(item("a", "fail", "a", "2026-10-29T10:00:00+00:00"))).toBe("2026-10-29T10:00:00+00:00");
    expect(sinceOf(item("a", "fail"))).toBeNull();
  });
});

describe("timelineOf", () => {
  const entry = (id: string, at: string | null) => ({ id, type: "deploy", title: id, status: "completed", domain: "x.com", actor: "marta", at });

  it("merges every server's activity, newest first, and cuts it", () => {
    const lines = timelineOf(
      [
        server("a", { activity: [entry("a1", "2026-10-29T10:00:00+00:00"), entry("a2", "2026-10-29T08:00:00+00:00")] }),
        server("b", { activity: [entry("b1", "2026-10-29T09:00:00+00:00"), entry("b0", null)] }),
      ],
      3,
      true,
    );
    expect(lines.map((line) => line.entry.id)).toEqual(["a1", "b1", "a2"]);
    expect(lines.map((line) => line.server)).toEqual(["a", "b", "a"]);
  });

  it("names no server when the console has only one", () => {
    expect(timelineOf([server("a", { activity: [entry("a1", "2026-10-29T10:00:00+00:00")] })], 3, false)[0]?.server).toBeNull();
  });

  it("puts an entry with no moment last", () => {
    expect(timelineOf([server("a", { activity: [entry("none", null), entry("dated", "2026-10-29T10:00:00+00:00")] })], 3, false).map((line) => line.entry.id)).toEqual(["dated", "none"]);
  });
});

describe("serverHome", () => {
  it("is this server, read from its Overview", () => {
    const home = serverHome(overviewFixture({ activity: [{ id: "j1", type: "deploy", title: "t", status: "completed", domain: "a.com", actor: null, at: null }] }), [{ domain: "a.com", status: "running" }], []);
    expect(home).toMatchObject({ key: "@local", name: "web-01", node: undefined, unreachable: false, diskPercent: 40 });
    expect(home.activity).toHaveLength(1);
  });

  it("does not make up a disk the server could not read", () => {
    expect(serverHome(overviewFixture({ disk: { ...overviewFixture().disk, error: "df failed" } }), [], []).diskPercent).toBeNull();
  });
});

describe("fleetServerHome", () => {
  const row = (patch: Record<string, unknown>): FleetRow =>
    ({
      name: "ams-3",
      local: false,
      reachability: "reachable",
      overview: { disk: { percent: 71, error: null }, attention: [item("app:x.com", "fail", "x.com")], attention_total: 1, activity: [{ id: "j1", type: "deploy", title: "t", status: "completed", domain: "x.com", actor: "marta", at: "2026-10-29T10:00:00+00:00" }] },
      ...patch,
    });

  it("reads a node from its row: its name, its disk, what it says needs attention and what happened", () => {
    const home = fleetServerHome(row({}), [{ domain: "x.com", status: "failed" }]);
    expect(home).toMatchObject({ key: "ams-3", name: "ams-3", node: "ams-3", unreachable: false, diskPercent: 71 });
    expect(home.attention.map((entry) => entry.id)).toEqual(["app:x.com"]);
    expect(home.activity.map((entry) => entry.id)).toEqual(["j1"]);
    expect(home.apps).toHaveLength(1);
  });

  it("adds a node's service that runs and answers nothing, which its own list cannot see, as it does for this server", () => {
    const home = fleetServerHome(row({ overview: { disk: { percent: 10, error: null }, attention: [], attention_total: 0, activity: [] } }), [{ domain: "quiet.example.com", status: "no_answer" }]);
    expect(home.attention.map((entry) => [entry.id, entry.severity, entry.reasons.map((reason) => reason.code)])).toEqual([["app:quiet.example.com", "fail", ["no_answer"]]]);
  });

  it("is this server when the row says it is local", () => {
    const home = fleetServerHome(row({ name: "fra-1", local: true }), []);
    expect(home).toMatchObject({ key: "@local", node: null, name: "fra-1" });
  });

  it("marks a server that does not answer, and one the central was refused by", () => {
    expect(fleetServerHome(row({ reachability: "unreachable", overview: null }), []).unreachable).toBe(true);
    expect(fleetServerHome(row({ reachability: "refused", overview: null }), []).unreachable).toBe(true);
    expect(fleetServerHome(row({ reachability: "unknown", overview: null }), []).unreachable).toBe(false);
  });

  it("reads a node that gave no Overview (an older Noust) as one with nothing to say", () => {
    const home = fleetServerHome(row({ overview: null }), []);
    expect(home).toMatchObject({ diskPercent: null, attention: [], activity: [] });
  });
});
