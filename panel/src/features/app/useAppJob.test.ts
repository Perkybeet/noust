import { describe, expect, it } from "vitest";

import type { Job } from "../../api/queries/jobs";
import { loadCatalog } from "../../i18n";
import { jobStep, jobWords } from "./useAppJob";

const JOB: Job = {
  id: "96bad296",
  type: "update",
  name: "Update shop.example.net",
  description: "Updating the application at shop.example.net",
  status: "running",
  progress: 0,
  total_steps: 100,
  current_step: "",
  created_at: "2026-09-25T19:21:13",
  logs: [{ timestamp: "2026-09-25T19:21:13", level: "info", message: "Installing dependencies", step: 1 }],
  metadata: { domain: "shop.example.net" },
};

const DOMAIN = "shop.example.net";

describe("jobWords", () => {
  it("names each job the way the header says it, in English by default", () => {
    expect(jobWords("update", DOMAIN)).toEqual({ running: "Updating", failed: `Update of ${DOMAIN} failed` });
    expect(jobWords("rollback", DOMAIN)).toEqual({ running: "Rolling back", failed: `Rollback of ${DOMAIN} failed` });
    expect(jobWords("restore", DOMAIN)).toEqual({ running: "Restoring", failed: `Restore of ${DOMAIN} failed` });
    expect(jobWords("migrate", DOMAIN)).toEqual({ running: "Migrating", failed: `Migration of ${DOMAIN} failed` });
    expect(jobWords("push", DOMAIN)).toEqual({ running: "Copying", failed: `Copy of ${DOMAIN} failed` });
    expect(jobWords("zero_downtime", DOMAIN)).toEqual({ running: "Switching", failed: `Zero-downtime mode of ${DOMAIN} failed` });
    expect(jobWords("something_new", DOMAIN)).toEqual({ running: "Working", failed: `Job of ${DOMAIN} failed` });
  });

  it("names each job in Spanish when asked", async () => {
    await loadCatalog("es");
    expect(jobWords("update", DOMAIN, "es")).toEqual({ running: "Actualizando", failed: `La actualización de ${DOMAIN} falló` });
  });
});

describe("jobStep", () => {
  it("is the newest line the job logged", () => {
    expect(jobStep(JOB)).toBe("Installing dependencies");
  });

  it("is nothing when the job has not logged", () => {
    expect(jobStep({ ...JOB, logs: [] })).toBeNull();
    expect(jobStep({ ...JOB, logs: [{ message: "  " }] })).toBeNull();
  });
});
