import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type { FollowedJob } from "../../api/queries/jobs";
import { fakeBackend, json } from "../../test/fakes";
import { useDeploymentLanding } from "./useDeploymentLanding";

const TARGET = { domain: "landing.example.net", jobId: "job-1" };

function followed(status: string, deploymentId: number | null = null): FollowedJob {
  return {
    id: TARGET.jobId,
    job: { id: TARGET.jobId, type: "deploy", name: "Deploy", description: "", status, progress: 100, total_steps: 100, current_step: "", created_at: "2026-10-30T10:00:00", logs: [], deployment_id: deploymentId } as unknown as FollowedJob["job"],
    follow: () => undefined,
    dismiss: () => undefined,
  };
}

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const ROW = { id: 38, domain: TARGET.domain, status: "success", triggered_by: "panel", git_commit: null, git_branch: null, job_id: TARGET.jobId, started_at: "2026-10-30T10:00:00+00:00" };

describe("where the wizard lands once the deploy is queued", () => {
  it("goes to the deployment whose row was written while the job ended, which the poll had not read yet", async () => {
    // A static site is built in a moment: the read made while it ran is empty, and the job is over before the next one is due.
    let reads = 0;
    fakeBackend({ "GET /api/deployments": () => json(200, { items: reads++ === 0 ? [] : [ROW], total: reads === 1 ? 0 : 1, next_before_id: null }) });
    const { result, rerender } = renderHook(({ job }: { job: FollowedJob }) => useDeploymentLanding(TARGET, job), { wrapper: wrapper(), initialProps: { job: followed("running") } });
    await waitFor(() => expect(reads).toBe(1));
    expect(result.current).toEqual({ kind: "waiting" });
    rerender({ job: followed("completed") });
    await waitFor(() => expect(result.current).toEqual({ kind: "deployment", id: 38 }));
  });

  it("waits for that last read before it says there is no deployment, and then lands on the application", async () => {
    let reads = 0;
    fakeBackend({
      "GET /api/deployments": () => {
        reads += 1;
        return json(200, { items: [], total: 0, next_before_id: null });
      },
    });
    const { result, rerender } = renderHook(({ job }: { job: FollowedJob }) => useDeploymentLanding(TARGET, job), { wrapper: wrapper(), initialProps: { job: followed("running") } });
    await waitFor(() => expect(reads).toBe(1));
    rerender({ job: followed("completed") });
    expect(result.current).toEqual({ kind: "waiting" });
    await waitFor(() => expect(result.current).toEqual({ kind: "app" }));
    expect(reads).toBe(2);
  });

  it("says a job that failed with no deployment failed, after the same read", async () => {
    fakeBackend({ "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }) });
    const { result } = renderHook(() => useDeploymentLanding(TARGET, followed("failed")), { wrapper: wrapper() });
    await waitFor(() => expect(result.current).toEqual({ kind: "failed" }));
  });

  it("takes the deployment the job names when it names one, without waiting", async () => {
    fakeBackend({ "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }) });
    const { result } = renderHook(() => useDeploymentLanding(TARGET, followed("completed", 51)), { wrapper: wrapper() });
    await waitFor(() => expect(result.current).toEqual({ kind: "deployment", id: 51 }));
  });
});
