import { describe, expect, it } from "vitest";

import type { Job } from "../../api/queries/jobs";
import { loadCatalog } from "../../i18n";
import { jobStep, jobWords, stepWords } from "./useAppJob";

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
});

describe("stepWords", () => {
  it("shows the server's own words in English, whatever the step", () => {
    expect(stepWords("deploy", "Deploying")).toBe("Deploying");
    expect(stepWords("update", "[7/7] 🚀 Activating release...")).toBe("[7/7] 🚀 Activating release...");
    expect(stepWords("deploy", "")).toBeNull();
    expect(stepWords("deploy", null)).toBeNull();
  });

  it("says in Spanish what a deploy is doing: the phases the deployment page draws and the steps around them", async () => {
    await loadCatalog("es");
    expect(stepWords("deploy", "Deploying", "es")).toBe("Desplegando");
    // The deployer's step header, with its counter and its icon, names the phase.
    expect(stepWords("deploy", "[7/7] 🚀 Activating release...", "es")).toBe("Arrancando la versión nueva…");
    expect(stepWords("update", "Activating release 20261030-101500, built from main", "es")).toBe("Arrancando la versión nueva…");
    expect(stepWords("deploy", "[2/7] 📥 Fetching source into a new release...", "es")).toBe("Obteniendo el origen…");
    expect(stepWords("update", "Installing dependencies", "es")).toBe("Instalando dependencias…");
    expect(stepWords("deploy", "[4/7] 🔒 Setting permissions...", "es")).toBe("Ajustando los permisos");
    expect(stepWords("deploy", "[5/7] 🌐 Creating site configuration...", "es")).toBe("Creando la configuración del sitio");
    expect(stepWords("deploy", "Obtaining SSL certificate", "es")).toBe("Obteniendo el certificado");
  });

  it("leaves a step it does not know as the server said it, and every step of a job that is not a deploy", async () => {
    await loadCatalog("es");
    expect(stepWords("deploy", "Copying the data", "es")).toBe("Copying the data");
    // A database engine's installation is not the deploy's install phase.
    expect(stepWords("database_install", "Installing the postgresql engine", "es")).toBe("Installing the postgresql engine");
  });
});
