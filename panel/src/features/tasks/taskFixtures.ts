/** Jobs and operations as the API says them, for the task centre's tests. */

import type { Job } from "../../api/queries/jobs";
import type { Operation } from "../../api/queries/operations";

/** A running deploy of shop.example.com, as `GET /api/jobs/active` lists it; `patch` changes what a test is about. */
export function job(patch: Partial<Job> = {}): Job {
  return {
    id: "ab12cd34",
    type: "deploy",
    name: "Deploy shop.example.com",
    description: "",
    status: "running",
    state: "running",
    progress: 40,
    total_steps: 100,
    current_step: "Building",
    created_at: "2026-11-04T11:58:00+00:00",
    started_at: "2026-11-04T11:58:05+00:00",
    logs: [],
    metadata: { domain: "shop.example.com" },
    title: { code: "jobs.title.deploy", params: { domain: "shop.example.com" } },
    actor: "ana",
    ...patch,
  } as Job;
}

/** A restart of api.example.com that is running. */
export function operation(patch: Partial<Operation> = {}): Operation {
  return {
    id: "op-1f2e3d4c",
    title: { code: "operations.app_restart", params: { target: "api.example.com" } },
    target: "api.example.com",
    domain: "api.example.com",
    actor: "ben",
    started_at: "2026-11-04T11:59:00+00:00",
    ...patch,
  };
}
