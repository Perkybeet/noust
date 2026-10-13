/**
 * The jobs running now, counted beside Activity from any page (checklist M-28): a deploy, a backup,
 * an update, where each of them can be followed. The count is read once and kept current by the
 * `job` events, so it is true without opening Activity.
 */

import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../test/console";
import { FakeEventSource, fakeBackend, json, signedInRoutes } from "../test/fakes";
import { setLocale } from "./locale";

const job = (id: string, status: "running" | "pending") => ({ id, type: "deploy", status, name: `Job ${id}`, metadata: {}, logs: [] });

async function shellAt(path: string) {
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  const nav = await screen.findByRole("navigation", { name: "Main" });
  return { ...harness, nav };
}

describe("the jobs running now", () => {
  it("are counted beside Activity, in words for a screen reader, from any page", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/jobs/active": () => json(200, { jobs: [job("a", "running"), job("b", "pending")], total: 2, active: 2 }) });
    const { nav } = await shellAt("/cron");

    const activity = await within(nav).findByRole("link", { name: "Activity 2 running" });

    expect(activity).toHaveTextContent("2");
    // Nothing else is marked: the count belongs to the place where the jobs are followed.
    expect(within(nav).queryByRole("link", { name: /Applications .* running/ })).not.toBeInTheDocument();
  });

  it("say nothing when nothing runs", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }) });
    const { nav } = await shellAt("/cron");

    expect(await within(nav).findByRole("link", { name: "Activity" })).toBeInTheDocument();
    expect(within(nav).queryByText(/running/)).not.toBeInTheDocument();
  });

  it("follow the server's events: a job that starts is counted and a job that ends is not", async () => {
    let jobs: unknown[] = [];
    fakeBackend({ ...signedInRoutes(), "GET /api/jobs/active": () => json(200, { jobs, total: jobs.length, active: jobs.length }) });
    const { nav } = await shellAt("/cron");
    await within(nav).findByRole("link", { name: "Activity" });

    jobs = [job("a", "running")];
    await act(async () => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { id: "a", type: "deploy", status: "running", metadata: {} });
      await Promise.resolve();
    });
    expect(await within(nav).findByRole("link", { name: "Activity 1 running" })).toBeInTheDocument();

    jobs = [];
    await act(async () => {
      FakeEventSource.latest().emit("job", { id: "a", type: "deploy", status: "completed", metadata: {} });
      await Promise.resolve();
    });
    expect(await within(nav).findByRole("link", { name: "Activity" })).toBeInTheDocument();
  });

  it("are counted in Spanish", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/jobs/active": () => json(200, { jobs: [job("a", "running")], total: 1, active: 1 }) });
    await shellAt("/cron");

    await act(() => setLocale("es"));

    expect(await screen.findByRole("link", { name: "Actividad 1 en curso" })).toBeInTheDocument();
  });
});
