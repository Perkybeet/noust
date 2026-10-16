import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { APPS, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../testRoutes";

const DOMAIN = "shop.example.com";

const REPORT = {
  domain: DOMAIN,
  file: "docker-compose.yml",
  env_file: "/var/www/apps/shop/.env",
  variables: [
    { name: "MODE", status: "default", required: false, default: "production", message: "", source: null, used_in: ["web"] },
    { name: "DB_PASSWORD", status: "set", required: true, default: null, message: "set the database password", source: "env", used_in: ["web"] },
    { name: "SECRET_TOKEN", status: "missing", required: true, default: null, message: "needed to sign sessions", source: null, used_in: ["web", "worker"] },
    { name: "LEFT_ALONE", status: "unset", required: false, default: null, message: "", source: null, used_in: ["x-extra"] },
  ],
  missing: ["SECRET_TOKEN"],
  unset: ["LEFT_ALONE"],
};

async function stackEnvironment(extra: Record<string, RouteHandler> = {}, appType = "docker-compose") {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET /api/apps/${DOMAIN}`]: () => json(200, { ...APPS[0], app_type: appType }),
    [`GET /api/apps/${DOMAIN}/env`]: () =>
      json(200, {
        domain: DOMAIN,
        variables: { DB_PASSWORD: "***" },
        unmasked: false,
        secrets: { DB_PASSWORD: { secret: true, reason: "name", marked: false } },
      }),
    [`GET /api/apps/${DOMAIN}/compose/variables`]: () => json(200, REPORT),
    ...extra,
  });
  const harness = renderConsole(`/apps/${DOMAIN}/environment`);
  await screen.findByRole("table", { name: `Environment variables of ${DOMAIN}` });
  return { ...harness, backend };
}

describe("the variables the compose file asks for", () => {
  it("lists each one with whether the stack has it, the required one without a value first", async () => {
    await stackEnvironment();

    const list = await screen.findByRole("list", { name: "Variables docker-compose.yml asks for" });
    const names = within(list)
      .getAllByRole("listitem")
      .map((item) => within(item).getAllByText(/^[A-Z_]+$/)[0]?.textContent);
    expect(names).toEqual(["SECRET_TOKEN", "MODE", "DB_PASSWORD", "LEFT_ALONE"]);
    const missing = within(list).getAllByRole("listitem")[0];
    if (missing === undefined) throw new Error("no first item");
    expect(within(missing).getByText("Required, no value")).toBeInTheDocument();
    expect(within(missing).getByText("needed to sign sessions")).toBeInTheDocument();
    expect(within(missing).getByText("Used by web, worker")).toBeInTheDocument();
    expect(within(list).getByText("Default: production")).toBeInTheDocument();
    expect(within(list).getByText("Has a value")).toBeInTheDocument();
    expect(within(list).getByText("No value, no default")).toBeInTheDocument();
  });

  it("never shows a value", async () => {
    await stackEnvironment();
    await screen.findByRole("list", { name: "Variables docker-compose.yml asks for" });

    expect(screen.queryByText("hunter2")).not.toBeInTheDocument();
  });

  it("starts the add dialog from the name of the variable the file needs", async () => {
    const { user } = await stackEnvironment();

    await user.click(await screen.findByRole("button", { name: "Add SECRET_TOKEN" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("textbox", { name: /Name/ })).toHaveValue("SECRET_TOKEN");
  });

  it("offers no add for a variable that already has a value", async () => {
    await stackEnvironment();
    await screen.findByRole("list", { name: "Variables docker-compose.yml asks for" });

    expect(screen.queryByRole("button", { name: "Add DB_PASSWORD" })).not.toBeInTheDocument();
  });

  it("is only for a Compose stack", async () => {
    const { backend } = await stackEnvironment({}, "nodejs");

    expect(screen.queryByRole("list", { name: /asks for/ })).not.toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/compose/variables`)).toHaveLength(0);
  });

  it("says why the file could not be read, in the system's own words", async () => {
    await stackEnvironment({
      [`GET /api/apps/${DOMAIN}/compose/variables`]: () =>
        problem(500, "deploymenterror", "Specified compose file not found: deploy/compose.yml", { hint: "Looked for /srv/shop/deploy/compose.yml." }),
    });

    expect(await screen.findByText("Could not read the compose file")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText(/Specified compose file not found/)).toBeInTheDocument();
    });
  });

  it("says a file that asks for nothing asks for nothing", async () => {
    await stackEnvironment({ [`GET /api/apps/${DOMAIN}/compose/variables`]: () => json(200, { ...REPORT, variables: [], missing: [], unset: [] }) });

    expect(await screen.findByText("docker-compose.yml asks for no variable.")).toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    await stackEnvironment();
    await screen.findByRole("list", { name: "Variables docker-compose.yml asks for" });
    await expectNoAxeViolations(screen.getByRole("main"));

    await act(() => setLocale("es"));
    expect(await screen.findByRole("list", { name: "Variables que pide docker-compose.yml" })).toBeInTheDocument();
    expect(screen.getByText("Obligatoria, sin valor")).toBeInTheDocument();
  });
});
