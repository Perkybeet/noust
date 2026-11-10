import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "./testRoutes";
import { readDraft } from "../new-app/draft";

const DOMAIN = "shop.example.com";

const FAILED = {
  job_id: "aa11",
  domain: DOMAIN,
  origin: "source",
  source: "https://github.com/acme/shop.git",
  branch: "main",
  app_type: "nextjs",
  recipe: null,
  created_at: "2026-11-10T10:00:00+00:00",
  error: "Release r1 did not pass its health check\n  Details: probe GET / : Connection refused",
  error_coded: { code: "deploy_failure.release_unhealthy", params: { release: "r1" } },
  finished_at: "2026-11-10T10:05:00+00:00",
};

async function missingAt(extra: Record<string, RouteHandler> = {}, items: unknown[] = [FAILED]) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    [`GET /api/apps/${DOMAIN}`]: () => problem(404, "not_found", `Application not found: ${DOMAIN}`),
    "GET /api/first-deploys": () => json(200, { items }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...extra,
  });
  const harness = renderConsole(`/apps/${DOMAIN}`);
  await screen.findByRole("heading", { level: 1, name: DOMAIN });
  return { ...harness, backend };
}

afterEach(() => {
  window.sessionStorage.clear();
  setLocale("en");
});

describe("the page of a domain whose first deploy failed", () => {
  it("says so, with the time and the system's own words, and not that there is no application", async () => {
    const { backend } = await missingAt();

    expect(await screen.findByText("The first deploy of shop.example.com failed")).toBeInTheDocument();
    expect(screen.queryByText("No application at this domain")).not.toBeInTheDocument();
    // What the system said, in mono: the first line is Noust's own sentence, in the console's words, and the rest is the gate's, as it came.
    const output = await screen.findByText(/Connection refused/);
    expect(output).toHaveTextContent("Release r1 did not pass its startup check");
    expect(output).toHaveTextContent("probe GET / : Connection refused");
    expect(screen.getByText(/Nothing was created/)).toBeInTheDocument();
    expect(screen.getByText("First deploy failed")).toBeInTheDocument();
    expect(backend.calls.some((call) => call.method === "GET" && call.path === "/api/first-deploys" && call.search.get("domain") === DOMAIN)).toBe(true);
  });

  it("says it in Spanish", async () => {
    setLocale("es");
    await missingAt();

    expect(await screen.findByText("El primer despliegue de shop.example.com falló")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Intentar de nuevo" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Olvidar este fallo" })).toBeInTheDocument();
  });

  it("opens the wizard again with what had been filled in", async () => {
    const { user, location } = await missingAt({ "GET /api/apps/types": () => json(200, { types: [] }) });

    await user.click(await screen.findByRole("button", { name: "Try again" }));

    await waitFor(() => {
      expect(location().pathname).toBe("/apps/new");
    });
    expect(readDraft()).toMatchObject({ mode: "manual", source: "https://github.com/acme/shop.git", branch: "main", domain: DOMAIN });
  });

  it("puts the failure away and goes to the applications", async () => {
    const { backend, user, location } = await missingAt({ "POST /api/first-deploys/aa11/dismiss": () => new Response(null, { status: 204 }) });

    await user.click(await screen.findByRole("button", { name: "Forget this failure" }));

    await waitFor(() => {
      expect(backend.calls.some((call) => call.method === "POST" && call.path === "/api/first-deploys/aa11/dismiss")).toBe(true);
      expect(location().pathname).toBe("/apps");
    });
  });

  it("tells a masked address from one that can be used as it is", async () => {
    await missingAt({}, [{ ...FAILED, source: "https://***@github.com/acme/shop.git" }]);

    expect(await screen.findByText(/had a password or a token in it/)).toBeInTheDocument();
  });

  it("links to the task's log", async () => {
    await missingAt();

    const link = await screen.findByRole("link", { name: "View the log" });
    expect(link).toHaveAttribute("href", "/activity/tasks/aa11");
  });

  it("has nothing for an axe run to find", async () => {
    const { container } = await missingAt();
    await screen.findByText("The first deploy of shop.example.com failed");
    await expectNoAxeViolations(container);
  });
});

describe("the page of a domain with nothing", () => {
  it("is the plain 'no application' when there is no failed first deploy for it", async () => {
    await missingAt({}, []);

    expect(await screen.findByRole("heading", { level: 2, name: "No application at this domain" })).toBeInTheDocument();
  });

  it("is the plain 'no application' on a server that does not know the route", async () => {
    await missingAt({ "GET /api/first-deploys": () => problem(404, "not_found", "Not Found") });

    expect(await screen.findByRole("heading", { level: 2, name: "No application at this domain" })).toBeInTheDocument();
  });

  it("does not show it before it knows", async () => {
    screenWidth(1440);
    fakeBackend({
      ...signedInRoutes(),
      [`GET /api/apps/${DOMAIN}`]: () => problem(404, "not_found", "Application not found"),
      "GET /api/first-deploys": () => new Promise<Response>(() => undefined),
      "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    });
    renderConsole(`/apps/${DOMAIN}`);

    await screen.findByRole("heading", { level: 1, name: DOMAIN });
    expect(within(document.body).queryByText("No application at this domain")).not.toBeInTheDocument();
  });
});
