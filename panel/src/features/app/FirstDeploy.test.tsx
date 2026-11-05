import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { APPS, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { giveElementsASize } from "../../test/layout";

const DOMAIN = "reservas.example.com";

const DEPLOY = {
  id: "3f9a1c2e",
  type: "deploy",
  name: `Deploy ${DOMAIN}`,
  description: `Deploying ${DOMAIN}`,
  status: "running",
  progress: 20,
  total_steps: 100,
  current_step: "Building and activating a new release",
  created_at: "2026-11-05T10:00:00",
  logs: [{ timestamp: "2026-11-05T10:00:01", level: "info", message: "Job started", step: 0 }],
  metadata: { domain: DOMAIN },
};

const BUILD = { id: 21, domain: DOMAIN, status: "running", triggered_by: "panel", job_id: DEPLOY.id, has_log: true, started_at: "2026-11-05T10:00:02", finished_at: null };

const LOG = "[10:00:02] $ git clone --depth 1 github.com/example/reservas\n[10:00:08] $ npm ci\n[10:00:17] added 412 packages in 9s\n[10:00:18] $ npm run build\n";

/** An application that has just been created: the deploy that made it is the only thing it has done. */
async function firstDeploy(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    [`GET /api/apps/${DOMAIN}`]: () => json(200, { ...APPS[0], domain: DOMAIN, status: "deploying", layout: "releases" }),
    "GET /api/jobs/active": () => json(200, { jobs: [DEPLOY], total: 1, active: 1 }),
    [`GET /api/jobs/${DEPLOY.id}`]: () => json(200, DEPLOY),
    "GET /api/deployments": () => json(200, { items: [BUILD], total: 1, next_before_id: null }),
    [`GET /api/deployments/${String(BUILD.id)}/log`]: () => json(200, { content: LOG, truncated: false, missing_reason: null }),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
    [`GET /api/apps/${DOMAIN}/domains`]: () => json(200, { app: DOMAIN, domains: [{ domain: DOMAIN, kind: "primary", created_at: "2026-11-05T10:00:00+00:00" }] }),
    ...extra,
  });
  const harness = renderConsole(`/apps/${DOMAIN}`);
  await screen.findByRole("heading", { level: 1, name: DOMAIN });
  return { ...harness, backend };
}

describe("the first deploy of an application, on its own page", () => {
  it("says it is deploying for the first time, with its steps and the build's own output", async () => {
    // jsdom has no layout: a virtualized list's window is as big as the size it is given.
    giveElementsASize();
    await firstDeploy();
    const surface = (await screen.findByRole("heading", { level: 2, name: "Deploying for the first time" })).closest("section");
    if (surface === null) throw new Error("no surface");
    expect(within(surface).getByText(/You can leave this page: the task centre follows it/)).toBeInTheDocument();
    // The steps of the deploy are the deployment page's own, drawn here.
    expect(await within(surface).findByText("Fetch")).toBeInTheDocument();
    expect(within(surface).getByText("Install")).toBeInTheDocument();
    // What the build prints, as it prints it.
    expect(await screen.findByText(/added 412 packages in 9s/)).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("draws no figures, no versions and no recent logs: it has none yet", async () => {
    await firstDeploy();
    await screen.findByRole("heading", { level: 2, name: "Deploying for the first time" });
    for (const name of ["Last deployments", "Recent logs", "Health"]) expect(screen.queryByRole("heading", { name })).not.toBeInTheDocument();
  });

  it("is an update, not a first deploy, when the job is one", async () => {
    await firstDeploy({ "GET /api/jobs/active": () => json(200, { jobs: [{ ...DEPLOY, type: "update" }], total: 1, active: 1 }) });
    expect(await screen.findByRole("heading", { level: 2, name: "Last deployments" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Deploying for the first time" })).not.toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    await firstDeploy();
    expect(await screen.findByRole("heading", { level: 2, name: "Desplegando por primera vez" })).toBeInTheDocument();
    expect(screen.getByText(/Puedes irte a otra página: el centro de tareas lo sigue/)).toBeInTheDocument();
  });
});
