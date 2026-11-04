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
  state: "running",
  cancellable: false,
  cancel_requested: false,
};

const DOMAIN = "shop.example.net";

describe("jobWords", () => {
  it("names each job the way the header says it, in English by default", () => {
    expect(jobWords("update", DOMAIN)).toEqual({ running: "Updating", title: `Updating ${DOMAIN}`, failed: `Update of ${DOMAIN} failed` });
    expect(jobWords("rollback", DOMAIN)).toEqual({ running: "Rolling back", title: `Rolling ${DOMAIN} back`, failed: `Rollback of ${DOMAIN} failed` });
    expect(jobWords("restore", DOMAIN)).toEqual({ running: "Restoring", title: `Restoring ${DOMAIN}`, failed: `Restore of ${DOMAIN} failed` });
    expect(jobWords("migrate", DOMAIN)).toMatchObject({ running: "Migrating", failed: `Migration of ${DOMAIN} failed` });
    expect(jobWords("push", DOMAIN)).toMatchObject({ running: "Copying", failed: `Copy of ${DOMAIN} failed` });
    expect(jobWords("zero_downtime", DOMAIN)).toMatchObject({ running: "Switching", failed: `Zero-downtime mode of ${DOMAIN} failed` });
    expect(jobWords("sandbox_test", DOMAIN)).toEqual({
      running: "Testing a build",
      title: `Testing a sandboxed build of ${DOMAIN}`,
      failed: `The sandboxed test build of ${DOMAIN} failed`,
    });
    expect(jobWords("something_new", DOMAIN)).toEqual({ running: "Working", title: `Working on ${DOMAIN}`, failed: `Job of ${DOMAIN} failed` });
  });

  it("names each job in Spanish when asked", async () => {
    await loadCatalog("es");
    expect(jobWords("update", DOMAIN, "es")).toEqual({
      running: "Actualizando",
      title: `Actualizando ${DOMAIN}`,
      failed: `La actualización de ${DOMAIN} falló`,
    });
    expect(jobWords("sandbox_test", DOMAIN, "es")).toMatchObject({ title: `Probando una compilación aislada de ${DOMAIN}` });
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

  it("is said in the console's language when the line is one of the server's own sentences, and as it came when it is not", async () => {
    await loadCatalog("es");
    const { setLocale } = await import("../../app/locale");
    await setLocale("es");
    const coded = { ...JOB, logs: [{ message: "Creating backup", step: 20, coded: { code: "jobs.step.backup.creating", params: {} } }] };
    // What a tool printed has no code, and is never paraphrased.
    const printed = { ...JOB, logs: [{ message: "pg_dump: dumping contents of table users", step: 20, coded: null }] };

    expect(jobStep(coded)).toBe("Creando la copia de seguridad");
    expect(jobStep(printed)).toBe("pg_dump: dumping contents of table users");
  });
});
