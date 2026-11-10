import { describe, expect, it } from "vitest";

import { loadCatalog } from "../../i18n";
import type { Job } from "../../api/queries/jobs";
import { appViewOf } from "./appView";

const JOB = { id: "j1", type: "deploy", status: "running", metadata: { domain: "shop.example.com" } } as unknown as Job;

describe("the state an application is drawn in", () => {
  it("is what its status says when nothing is being done to it", () => {
    expect(appViewOf("running", null, "shop.example.com", "en")).toMatchObject({ state: "running", label: "Running" });
    expect(appViewOf("stopped", null, "shop.example.com", "en")).toMatchObject({ state: "stopped", label: "Stopped" });
    expect(appViewOf(undefined, null, "shop.example.com", "en")).toMatchObject({ state: "unknown" });
  });

  it("is the job's while one runs on it, whatever systemd says meanwhile: the sidebar and the header say the same", () => {
    // A first deploy: the unit does not exist yet, so the live state is "stopped".
    expect(appViewOf("stopped", JOB, "shop.example.com", "en")).toMatchObject({ state: "deploying", label: "Deploying" });
    // A redeploy: the old one still runs.
    expect(appViewOf("running", JOB, "shop.example.com", "en")).toMatchObject({ state: "deploying", label: "Deploying" });
    expect(appViewOf("running", { ...JOB, type: "update" } as unknown as Job, "shop.example.com", "en").state).toBe("deploying");
  });

  it("says the job in the console's language", async () => {
    await loadCatalog("es");
    expect(appViewOf("running", JOB, "shop.example.com", "es").label).toBe("Desplegando");
  });
});
