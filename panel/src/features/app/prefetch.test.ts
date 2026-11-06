import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { activeJobsQuery } from "../../api/queries/jobs";
import { fakeBackend, json } from "../../test/fakes";
import { readAppPage } from "./prefetch";

const columns = vi.hoisted(() => ({ release: (): void => undefined, started: 0 }));

vi.mock("../../app/frame/resourceColumnChunks", () => ({
  preloadResourceColumns: () => {
    columns.started += 1;
    return new Promise<void>((resolve) => {
      columns.release = resolve;
    });
  },
}));

describe("what the hand-over to an application's page reads", () => {
  beforeEach(() => {
    columns.started = 0;
  });

  it("reads the sections of the column too, and waits for them: the column is part of the page that must arrive drawn", async () => {
    fakeBackend({
      "GET /api/apps/shop.example.net": () => json(200, { domain: "shop.example.net", status: "running", app_type: "nextjs" }),
      "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
      "GET /api/apps/shop.example.net/pending": () => json(200, { pending: false, items: [] }),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let settled = false;
    const read = readAppPage(client, "shop.example.net").then(() => {
      settled = true;
    });
    // The three requests answer at once; the column's chunk is the one read still out.
    await vi.waitFor(() => expect(columns.started).toBe(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(settled).toBe(false);
    columns.release();
    await read;
    expect(settled).toBe(true);
  });

  it("reads the jobs that run afresh, even when the list the task centre keeps is recent: the job just queued is not in it yet", async () => {
    const backend = fakeBackend({
      "GET /api/apps/shop.example.net": () => json(200, { domain: "shop.example.net", status: "running", app_type: "nextjs" }),
      "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
      "GET /api/apps/shop.example.net/pending": () => json(200, { pending: false, items: [] }),
      "GET /api/jobs/active": () => json(200, { jobs: [{ id: "j1", type: "deploy", status: "running", metadata: { domain: "shop.example.net" } }], total: 1, active: 1 }),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 10_000 } } });
    // Read a moment ago, before the deploy was queued: well within the ten seconds a read is good for.
    await client.query({ ...activeJobsQuery(), queryFn: () => Promise.resolve({ jobs: [], total: 0, active: 0 }) });
    const read = readAppPage(client, "shop.example.net");
    await vi.waitFor(() => expect(columns.started).toBe(1));
    columns.release();
    await read;
    expect(backend.callsTo("GET /api/jobs/active")).toHaveLength(1);
    expect(client.getQueryData<{ jobs: { id: string }[] }>(activeJobsQuery().queryKey)?.jobs.map((job) => job.id)).toEqual(["j1"]);
  });

  it("never rejects, whatever fails: a read that failed is the page's to say", async () => {
    fakeBackend({
      "GET /api/apps/shop.example.net": () => json(500, { error: "boom" }),
      "GET /api/deployments": () => json(500, { error: "boom" }),
      "GET /api/apps/shop.example.net/pending": () => json(500, { error: "boom" }),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const read = readAppPage(client, "shop.example.net");
    await vi.waitFor(() => expect(columns.started).toBe(1));
    columns.release();
    await expect(read).resolves.toBeDefined();
  });
});
