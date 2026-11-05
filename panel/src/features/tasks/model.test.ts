import { describe, expect, it } from "vitest";

import { isMine, isOnItsPage, pathOf, placeOf, readFleetRows, readOperationEvent, sectionsOf, stateOfJob, taskKey, taskOfJob, taskOfOperation } from "./model";
import type { Task } from "./model";
import { job, operation } from "./taskFixtures";

const NOW = Date.parse("2026-11-04T12:00:00Z");

describe("a job as a task", () => {
  it("takes its title and its words from what the server sent", () => {
    const task = taskOfJob(job({ title: { code: "jobs.title.deploy", params: { domain: "shop.example.com" } }, metadata: { domain: "shop.example.com" } }), null, NOW);
    expect(task.title).toEqual({ code: "jobs.title.deploy", params: { domain: "shop.example.com" } });
    expect(task.name).toBe("Deploy shop.example.com");
    expect(task.domain).toBe("shop.example.com");
    expect(task.kind).toBe("job");
  });

  it("is told apart by its server, so two servers may hold the same id", () => {
    expect(taskKey(null, "job", "ab12")).not.toBe(taskKey("web-02", "job", "ab12"));
    expect(taskOfJob(job({ id: "ab12" }), "web-02", NOW).key).toBe("web-02|job:ab12");
  });

  it("reads the state the server gives, and the status word of a server that gives none", () => {
    expect(stateOfJob({ status: "running", state: "queued" })).toBe("queued");
    expect(stateOfJob({ status: "pending" })).toBe("queued");
    expect(stateOfJob({ status: "completed" })).toBe("succeeded");
    expect(stateOfJob({ status: "failed" })).toBe("failed");
    expect(stateOfJob({ status: "cancelled" })).toBe("cancelled");
    expect(stateOfJob({ status: "running" })).toBe("running");
  });

  it("measures its progress against its steps, only while it runs", () => {
    expect(taskOfJob(job({ progress: 46, total_steps: 100 }), null, NOW).progress).toBe(46);
    expect(taskOfJob(job({ progress: 3, total_steps: 5 }), null, NOW).progress).toBe(60);
    expect(taskOfJob(job({ progress: 3, total_steps: 0 }), null, NOW).progress).toBeNull();
    expect(taskOfJob(job({ status: "completed", state: "succeeded", progress: 100 }), null, NOW).progress).toBeNull();
  });

  it("keeps what a failed job said, verbatim, and where a queued one stands", () => {
    expect(taskOfJob(job({ status: "failed", state: "failed", error: "npm ERR! code ELIFECYCLE" }), null, NOW).error).toBe("npm ERR! code ELIFECYCLE");
    const waiting = taskOfJob(job({ status: "pending", state: "queued", queue_position: 2, waiting_for: "the deploy of shop.example.com" }), null, NOW);
    expect(waiting.queuePosition).toBe(2);
    expect(waiting.waitingFor).toBe("the deploy of shop.example.com");
  });

  it("does not know whether it can be stopped when its event does not say", () => {
    expect(taskOfJob(job(), null, NOW).cancellable).toBeNull();
    expect(taskOfJob(job({ cancellable: true }), null, NOW).cancellable).toBe(true);
  });
});

describe("an operation as a task", () => {
  it("is running, with a subject and no progress", () => {
    const task = taskOfOperation(operation({ title: { code: "operations.app_restart", params: { target: "api.example.com" } }, target: "api.example.com", domain: "api.example.com" }), null, NOW);
    expect(task).toMatchObject({ kind: "operation", state: "running", progress: null, domain: "api.example.com", code: "operations.app_restart" });
    expect(task.key).toBe("|operation:op-1f2e3d4c");
  });

  it("is read from its event, and a body that is not one is refused", () => {
    expect(readOperationEvent({ id: "op-1", phase: "start", title: { code: "operations.power_reboot", params: {} }, target: null, domain: null, actor: "ben", at: "2026-11-04T10:00:00+00:00" })).toMatchObject({ id: "op-1", phase: "start", actor: "ben" });
    expect(readOperationEvent({ id: "op-1", phase: "end", title: { code: "operations.power_reboot" }, at: "x", result: "failed" })).toMatchObject({ phase: "end", result: "failed" });
    expect(readOperationEvent({ id: "op-1", phase: "middle", title: { code: "x" } })).toBeNull();
    expect(readOperationEvent({ phase: "start", title: { code: "x" } })).toBeNull();
    expect(readOperationEvent("nope")).toBeNull();
  });
});

describe("the fleet's rows", () => {
  it("are read field by field, and a row with no server is skipped", () => {
    const rows = readFleetRows([
      { node: "central-01", local: true, status: "ok", jobs: [job()], operations: [] },
      { node: "web-02", status: "unsupported", jobs: [], operations: [] },
      { status: "ok", jobs: [] },
      { node: "web-03", status: "something new" },
    ]);
    expect(rows.map((row) => [row.name, row.local, row.answer, row.jobs.length])).toEqual([
      ["central-01", true, "ok", 1],
      ["web-02", false, "unsupported", 0],
      ["web-03", false, "error", 0],
    ]);
  });

  it("carry the version their server reported, when the central knows it", () => {
    const rows = readFleetRows([{ node: "web-02", status: "unsupported" }, { node: "web-03", status: "ok" }], [{ name: "web-02", version: "4.1.0" }, { name: "web-03", version: null }]);
    expect(rows.map((row) => row.version)).toEqual(["4.1.0", null]);
  });
});

describe("the panel's sections", () => {
  const make = (id: string, patch: Partial<Task>): Task => ({ ...taskOfJob(job({ id }), null, NOW), ...patch });
  const mine = (task: Task): boolean => task.actor === "ana";

  it("puts yours first, what the server does that is not yours next, and what ended last", () => {
    const sections = sectionsOf(
      [
        make("a", { actor: "ana", state: "running", startedAt: NOW - 60_000 }),
        make("b", { actor: "ana", state: "queued" }),
        make("c", { actor: "ben", state: "running", startedAt: NOW - 360_000 }),
        make("d", { actor: "system", state: "queued" }),
        make("e", { actor: "ana", state: "failed", completedAt: NOW - 1000 }),
        make("f", { actor: "ben", state: "succeeded", completedAt: NOW - 5000 }),
      ],
      mine,
      NOW,
    );
    expect(sections.mineRunning.map((task) => task.id)).toEqual(["a"]);
    expect(sections.mineQueued.map((task) => task.id)).toEqual(["b"]);
    expect(sections.others.map((task) => task.id)).toEqual(["c", "d"]);
    expect(sections.done.map((task) => task.id)).toEqual(["e", "f"]);
  });

  it("shows only the last few that ended, and nothing that ended days ago", () => {
    const ended = Array.from({ length: 8 }, (_, index) => make(`e${String(index)}`, { state: "succeeded", completedAt: NOW - (index + 1) * 1000 }));
    const old = make("old", { state: "succeeded", completedAt: NOW - 3 * 24 * 3600_000 });
    const sections = sectionsOf([...ended, old], mine, NOW);
    expect(sections.done.map((task) => task.id)).toEqual(["e0", "e1", "e2", "e3", "e4"]);
  });
});

describe("whose a task is", () => {
  const me = { username: "ana", session: null };
  it("is the person's when they asked, directly or through a central", () => {
    expect(isMine({ actor: "ana" }, me)).toBe(true);
    expect(isMine({ actor: "fleet-fra-1 on behalf of ana" }, me)).toBe(true);
    expect(isMine({ actor: "fleet-fra-1 on behalf of ben" }, me)).toBe(false);
    expect(isMine({ actor: "ben" }, me)).toBe(false);
    expect(isMine({ actor: null }, me)).toBe(false);
  });

  it("is the browser's own session for the master token, which has no account", () => {
    const token = { username: null, session: "abcdef123456" };
    expect(isMine({ actor: "abcdef123456" }, token)).toBe(true);
    expect(isMine({ actor: "master" }, token)).toBe(false);
    expect(isMine({ actor: "abcdef123456" }, { username: null, session: null })).toBe(false);
  });
});

describe("where a task's page is", () => {
  const base = { kind: "job", id: "ab12", server: null, domain: null, target: null, code: null } as const;

  it("is its own page for a job, on its server when it is not this one", () => {
    expect(placeOf(base)).toEqual({ to: "/activity/tasks/$id", params: { id: "ab12" } });
    expect(placeOf({ ...base, server: "web-02" })).toEqual({ to: "/activity/tasks/$id", params: { id: "ab12" }, search: { node: "web-02" } });
    expect(pathOf(placeOf(base))).toBe("/activity/tasks/ab12");
  });

  it("is what an operation was about", () => {
    const op = { ...base, kind: "operation", id: "op-1" } as const;
    expect(placeOf({ ...op, domain: "api.example.com", code: "operations.app_restart" })).toEqual({ to: "/apps/$domain", params: { domain: "api.example.com" } });
    expect(placeOf({ ...op, target: "nginx", code: "operations.service_restart" })).toEqual({ to: "/server/services/$name", params: { name: "nginx" } });
    expect(placeOf({ ...op, code: "operations.cron_run", target: "nightly" })).toEqual({ to: "/cron" });
    expect(placeOf({ ...op, code: "operations.power_reboot" })).toEqual({ to: "/activity/tasks" });
  });

  it("is the person's page when the path is the task's, or the application's it acts on", () => {
    const deploy = { ...base, domain: "shop.example.com" };
    expect(isOnItsPage(deploy, "/activity/tasks/ab12")).toBe(true);
    expect(isOnItsPage(deploy, "/apps/shop.example.com/deployments")).toBe(true);
    expect(isOnItsPage(deploy, "/apps/other.example.com")).toBe(false);
    expect(isOnItsPage(deploy, "/databases")).toBe(false);
    expect(isOnItsPage(base, "/activity/tasks/ab123")).toBe(false);
  });
});
