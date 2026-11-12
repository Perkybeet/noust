import { afterEach, describe, expect, it, vi } from "vitest";

import { LONG_TASK_MS } from "./browserNotice";
import { endedTask, endingOf, isLeavingLongTask, noticeStateOf } from "./endings";
import { forgetLaunches, noteArrivals } from "./launches";
import { taskOfJob } from "./model";
import type { Task } from "./model";
import { job } from "./taskFixtures";

const NOW = Date.parse("2026-11-04T12:00:00Z");
const me = { username: "ana", session: null };
const task = (patch: Partial<Task> = {}, jobPatch = {}): Task => ({ ...taskOfJob(job({ actor: "ana", ...jobPatch }), null, NOW), ...patch });

describe("the end of a task", () => {
  it("is the change from running or waiting to ended, and nothing else", () => {
    expect(endedTask(task({ state: "running" }), task({ state: "succeeded" }))?.state).toBe("succeeded");
    expect(endedTask(task({ state: "queued" }), task({ state: "cancelled" }))?.state).toBe("cancelled");
    expect(endedTask(task({ state: "running" }), task({ state: "running" }))).toBeNull();
    expect(endedTask(task({ state: "succeeded" }), task({ state: "succeeded" }))).toBeNull();
    // Heard of only as ended: the console was not there to see it run.
    expect(endedTask(undefined, task({ state: "failed" }))).toBeNull();
  });
});

describe("telling the person", () => {
  it("is a toast for a job of theirs that ends on another page", () => {
    expect(endingOf(task({ state: "succeeded" }), me, "/databases")).toBe("toast");
    expect(endingOf(task({ state: "failed" }), me, "/")).toBe("toast");
  });

  it("is silent on the task's own page and on the page of the application it acts on", () => {
    expect(endingOf(task({ state: "succeeded", id: "ab12" }), me, "/activity/tasks/ab12")).toBe("silent");
    expect(endingOf(task({ state: "succeeded" }), me, "/apps/shop.example.com/deployments")).toBe("silent");
  });

  it("is not silent about a failure on the page of the application it acts on: the failure is announced once, here", () => {
    // The page of an application whose first deploy failed has, by design, no application left to show it (item 141), and the
    // card a page draws for its own job does not announce: a success on the page is seen, a failure is said.
    expect(endingOf(task({ state: "failed" }), me, "/apps/shop.example.com")).toBe("toast");
    expect(endingOf(task({ state: "failed" }), me, "/apps/shop.example.com/deployments")).toBe("toast");
    // Its own page says it with its log, which is what the person is reading.
    expect(endingOf(task({ state: "failed", id: "ab12" }), me, "/activity/tasks/ab12")).toBe("silent");
  });

  it("is silent on the Software page for an install it started there, which the page already says it ended", () => {
    const install = { title: { code: "jobs.title.server_action_software_install", params: { component: "nginx" } }, state: "succeeded" } as const;
    expect(endingOf(task(install), me, "/server/software")).toBe("silent");
    expect(endingOf(task({ ...install, state: "failed" }), me, "/server/software")).toBe("silent");
    // Anywhere else the task centre is the only one that says so.
    expect(endingOf(task(install), me, "/server/webserver")).toBe("toast");
  });

  it("is not the task centre's for what is not the person's, nor for an operation", () => {
    expect(endingOf(task({ state: "succeeded", actor: "ben" }), me, "/databases")).toBe("none");
    expect(endingOf(task({ state: "succeeded", actor: null }), me, "/databases")).toBe("none");
    expect(endingOf(task({ state: "succeeded", kind: "operation" }), me, "/databases")).toBe("none");
  });

  it("names the server's word for how it ended, so its own notice can be told from another's", () => {
    expect(noticeStateOf({ state: "succeeded" })).toBe("active");
    expect(noticeStateOf({ state: "failed" })).toBe("failed");
    expect(noticeStateOf({ state: "cancelled" })).toBe("idle");
  });
});

describe("telling a person who left from one who did not", () => {
  const launch = (key: string, pathname: string) => {
    forgetLaunches();
    noteArrivals([{ key, createdAt: NOW - 1_000, state: "running" }], pathname, NOW);
  };
  afterEach(() => {
    forgetLaunches();
  });

  it("is silent while the person is still on the page that launched the task, whichever page that is", () => {
    // Back up a database, open a port, deploy from the wizard: none of them has the task's page or an application's.
    const backup = task({ state: "succeeded", id: "b1" });
    launch(backup.key, "/databases/postgresql/shop/backups");
    expect(endingOf(backup, me, "/databases/postgresql/shop/backups")).toBe("silent");
    expect(endingOf(task({ state: "failed", id: "b1" }), me, "/databases/postgresql/shop/backups")).toBe("silent");
  });

  it("is a toast once the person has left that page, and silent again if they come back to it", () => {
    const backup = task({ state: "succeeded", id: "b1" });
    launch(backup.key, "/databases/postgresql/shop/backups");
    expect(endingOf(backup, me, "/apps")).toBe("toast");
    expect(endingOf(backup, me, "/databases/postgresql/shop/backups")).toBe("silent");
  });

  it("is a toast, or the browser's notification, when the tab is hidden: that person is elsewhere, whatever page it shows", () => {
    const backup = task({ state: "succeeded", id: "b1" });
    launch(backup.key, "/databases/postgresql/shop/backups");
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    expect(endingOf(backup, me, "/databases/postgresql/shop/backups")).toBe("toast");
    vi.restoreAllMocks();
  });

  it("is what it was when the console did not see the task arrive", () => {
    forgetLaunches();
    expect(endingOf(task({ state: "succeeded", id: "b1" }), me, "/databases/postgresql/shop/backups")).toBe("toast");
  });

  it("does not take a long task for left while its person is on the page that launched it", () => {
    const running = task({ startedAt: NOW - LONG_TASK_MS - 1, id: "b1" });
    launch(running.key, "/network/firewall");
    expect(isLeavingLongTask([running], me, "/network/firewall", NOW)).toBe(false);
    expect(isLeavingLongTask([running], me, "/apps", NOW)).toBe(true);
  });
});

describe("leaving a long task", () => {
  const long = { startedAt: NOW - LONG_TASK_MS - 1 };
  it("is moving to another page while a task of theirs has run for a while", () => {
    expect(isLeavingLongTask([task(long)], me, "/databases", NOW)).toBe(true);
  });

  it("is not while it is still short, nor on its own page, nor for another person's, nor for one that waits", () => {
    expect(isLeavingLongTask([task({ startedAt: NOW - 1000 })], me, "/databases", NOW)).toBe(false);
    expect(isLeavingLongTask([task(long)], me, "/apps/shop.example.com", NOW)).toBe(false);
    expect(isLeavingLongTask([task({ ...long, actor: "ben" })], me, "/databases", NOW)).toBe(false);
    expect(isLeavingLongTask([task({ ...long, state: "queued" })], me, "/databases", NOW)).toBe(false);
    expect(isLeavingLongTask([], me, "/databases", NOW)).toBe(false);
  });
});
