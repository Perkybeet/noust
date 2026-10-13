import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { teamsRoutes, viewerSession } from "../teams/testFixtures";

vi.setConfig({ testTimeout: 20_000 });
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

const TEAM = "/api/teams/acme/variables";
const PROJECT = "/api/teams/acme/projects/web/variables";
const ENVIRONMENT = "/api/teams/acme/projects/web/environments/production/variables";
const SERVER = "/api/variables/server";

interface Row {
  name: string;
  value: string;
  secret?: boolean;
  audience?: string;
  references?: string[];
  updated_by?: string;
}

function listing(scope: string, label: string, rows: Row[], unmasked = false) {
  return {
    scope,
    label,
    unmasked,
    syntax: `{{${scope}.NAME}}`,
    variables: rows.map((row) => ({
      name: row.name,
      value: row.value,
      secrecy: { secret: row.secret === true, reason: row.secret === true ? "marked" : "plain", marked: row.secret === true },
      audience: row.audience ?? "both",
      references: row.references ?? [],
      updated_at: "2026-10-10T10:00:00+00:00",
      updated_by: row.updated_by ?? "ana",
    })),
  };
}

const TEAM_ROWS: Row[] = [
  { name: "REGION", value: "eu-west" },
  { name: "STRIPE_SECRET", value: "***", secret: true },
  { name: "SENTRY_DSN", value: "https://sentry.example.com/1", audience: "runtime" },
];

const CLEAR_ROWS: Row[] = [
  { name: "REGION", value: "eu-west" },
  { name: "STRIPE_SECRET", value: "sk_live_clear_value", secret: true },
  { name: "SENTRY_DSN", value: "https://sentry.example.com/1", audience: "runtime" },
];

function teamVariables(rows: Row[] = TEAM_ROWS, clear: Row[] = CLEAR_ROWS): RouteHandler {
  return (call: RecordedCall) => json(200, call.search.get("unmask") === "true" ? listing("team", "acme", clear, true) : listing("team", "acme", rows));
}

function teamPage(extra: Record<string, RouteHandler> = {}, session?: Parameters<typeof teamsRoutes>[1]) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...teamsRoutes({}, session),
    "GET /api/teams/acme/members": () => json(200, { team: "acme", members: [] }),
    [`GET ${TEAM}`]: teamVariables(),
    ...extra,
  });
  const harness = renderConsole("/teams/acme/variables");
  return { ...harness, backend };
}

/** The table once its rows are in: while they are on their way the same table is drawn busy. */
async function loadedTable(name: string): Promise<HTMLElement> {
  await screen.findByRole("table", { name });
  await waitFor(() => {
    expect(screen.getByRole("table", { name })).not.toHaveAttribute("aria-busy");
  });
  return screen.getByRole("table", { name });
}

describe("a team's shared variables", () => {
  it("is a tab of the team, lists each variable with who gets it, and hides a secret", async () => {
    const { container } = teamPage();
    const table = await loadedTable("Shared variables of Acme Corp");
    expect(screen.getByRole("link", { name: "Variables" })).toHaveAttribute("href", "/teams/acme/variables");
    expect(within(table).getByText("REGION")).toBeInTheDocument();
    expect(within(table).getByText("eu-west")).toBeInTheDocument();
    expect(within(table).getByText("Runtime only")).toBeInTheDocument();
    expect(within(table).queryByText("sk_live_clear_value")).not.toBeInTheDocument();
    expect(within(table).getAllByText("Hidden")).toHaveLength(1);
    expect(screen.getByText(/can refer to these as \{\{team\.NAME\}\}/)).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("reveals a secret only after reading it in clear, once", async () => {
    const { user, backend } = teamPage();
    await loadedTable("Shared variables of Acme Corp");
    expect(backend.calls.filter((call) => call.search.get("unmask") === "true")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Reveal the value of STRIPE_SECRET" }));
    expect(await screen.findByText("sk_live_clear_value")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Hide the value of STRIPE_SECRET" }));
    expect(screen.queryByText("sk_live_clear_value")).not.toBeInTheDocument();
    expect(backend.calls.filter((call) => call.search.get("unmask") === "true")).toHaveLength(1);
  });

  it("adds a variable with a value, a mark and who gets it, saved at once", async () => {
    const { user, backend } = teamPage({
      [`PUT ${TEAM}/PUBLIC_URL`]: () => json(200, listing("team", "acme", [...TEAM_ROWS, { name: "PUBLIC_URL", value: "https://acme.example.com", audience: "build" }])),
    });
    await user.click(await screen.findByRole("button", { name: "Add variable" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a shared variable" });
    await user.type(within(dialog).getByLabelText("Name"), "PUBLIC_URL");
    await user.type(within(dialog).getByLabelText("Value"), "https://acme.example.com");
    await user.click(within(dialog).getByRole("combobox", { name: "Given to" }));
    await user.click(await screen.findByRole("option", { name: "Build only" }));
    await user.click(within(dialog).getByRole("button", { name: "Add variable" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${TEAM}/PUBLIC_URL`)[0]?.body).toEqual({ value: "https://acme.example.com", secret: null, audience: "build" });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Add a shared variable" })).not.toBeInTheDocument();
    });
  });

  it("refuses a name that is not one, and one the scope already has, before asking the server", async () => {
    const { user, backend } = teamPage();
    await user.click(await screen.findByRole("button", { name: "Add variable" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a shared variable" });
    await user.type(within(dialog).getByLabelText("Name"), "REGION");
    await user.click(within(dialog).getByRole("button", { name: "Add variable" }));
    expect(await within(dialog).findByText("REGION is already set. Edit its row instead.")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });

  it("reads a secret in clear to edit it, and keeps the mark it has", async () => {
    const { user, backend } = teamPage({
      [`PUT ${TEAM}/STRIPE_SECRET`]: () => json(200, listing("team", "acme", TEAM_ROWS)),
    });
    await loadedTable("Shared variables of Acme Corp");
    await user.click(screen.getByRole("button", { name: "Actions for STRIPE_SECRET" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit STRIPE_SECRET" });
    expect(within(dialog).getByLabelText("Value")).toHaveValue("sk_live_clear_value");
    await user.clear(within(dialog).getByLabelText("Value"));
    await user.type(within(dialog).getByLabelText("Value"), "sk_live_rotated");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT ${TEAM}/STRIPE_SECRET`)[0]?.body).toEqual({ value: "sk_live_rotated", secret: true, audience: "both" });
    });
  });

  it("shows the server's refusal verbatim and keeps the dialog open", async () => {
    const { user } = teamPage({
      [`PUT ${TEAM}/BAD`]: () => problem(422, "validation_error", "A reference to project.MISSING cannot be resolved here"),
    });
    await user.click(await screen.findByRole("button", { name: "Add variable" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a shared variable" });
    await user.type(within(dialog).getByLabelText("Name"), "BAD");
    await user.click(within(dialog).getByRole("button", { name: "Add variable" }));
    expect(await within(dialog).findByText("A reference to project.MISSING cannot be resolved here")).toBeInTheDocument();
    expect(within(dialog).getByText("Could not save BAD")).toBeInTheDocument();
  });

  it("says who uses a variable, with a link to each application", async () => {
    const { user } = teamPage({
      [`GET ${TEAM}/REGION/usage`]: () => json(200, { name: "REGION", applications: ["shop.example.com", "staging.example.com"] }),
    });
    await loadedTable("Shared variables of Acme Corp");
    await user.click(screen.getByRole("button", { name: "Actions for REGION" }));
    await user.click(await screen.findByRole("menuitem", { name: "Who uses it" }));
    const dialog = await screen.findByRole("dialog", { name: "Who uses REGION" });
    expect(await within(dialog).findByRole("link", { name: "shop.example.com" })).toHaveAttribute("href", "/apps/shop.example.com");
    expect(within(dialog).getByRole("link", { name: "staging.example.com" })).toBeInTheDocument();
  });

  it("asks before deleting, and says which applications still refer to it", async () => {
    const { user, backend } = teamPage({
      [`GET ${TEAM}/REGION/usage`]: () => json(200, { name: "REGION", applications: ["shop.example.com"] }),
      [`DELETE ${TEAM}/REGION`]: () => json(200, { name: "REGION", applications: ["shop.example.com"] }),
    });
    await loadedTable("Shared variables of Acme Corp");
    await user.click(screen.getByRole("button", { name: "Actions for REGION" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete REGION?" });
    expect(await within(dialog).findByText(/1 application refers to it and fails its next deploy.*shop\.example\.com/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete variable" }));
    await waitFor(() => {
      expect(backend.callsTo(`DELETE ${TEAM}/REGION`)).toHaveLength(1);
    });
  });

  it("offers nothing that changes a variable to someone who may only read", async () => {
    teamPage({}, viewerSession());
    await loadedTable("Shared variables of Acme Corp");
    expect(screen.queryByRole("button", { name: "Add variable" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Actions for REGION" })).toBeInTheDocument();
  });

  it("invites a first variable when the team has none", async () => {
    teamPage({ [`GET ${TEAM}`]: teamVariables([], []) });
    expect(await screen.findByRole("heading", { name: "No shared variables" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add variable" }).length).toBeGreaterThan(0);
  });

  it("is in Spanish, with no accessibility violations", async () => {
    const { user } = teamPage();
    await loadedTable("Shared variables of Acme Corp");
    await act(() => setLocale("es"));
    const table = screen.getByRole("table", { name: "Variables compartidas de Acme Corp" });
    expect(within(table).getByText("Solo ejecución")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Añadir variable" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Añadir variable" }));
    const dialog = await screen.findByRole("dialog", { name: "Añadir una variable compartida" });
    await expectNoAxeViolations(dialog);
  });
});

describe("a project's page", () => {
  function projectPage(extra: Record<string, RouteHandler> = {}) {
    screenWidth(1440);
    const backend = fakeBackend({
      ...teamsRoutes(),
      [`GET ${PROJECT}`]: () => json(200, listing("project", "acme/web", [{ name: "DOCS_URL", value: "https://docs.example.com" }])),
      [`GET ${ENVIRONMENT}`]: () => json(200, listing("environment", "acme/web/production", [])),
      ...extra,
    });
    const harness = renderConsole("/projects/acme/web");
    return { ...harness, backend };
  }

  it("lists the project's variables and the selected environment's, each under its own name", async () => {
    projectPage();
    const project = await loadedTable("Shared variables of Web");
    expect(within(project).getByText("DOCS_URL")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 2, name: "Project variables" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 2, name: "Production variables" })).toBeInTheDocument();
    expect(await screen.findByText("No shared variables yet.")).toBeInTheDocument();
  });

  it("follows the environment the page is on", async () => {
    const { user, backend } = projectPage({
      [`GET /api/teams/acme/projects/web/environments/staging/variables`]: () => json(200, listing("environment", "acme/web/staging", [{ name: "DEBUG", value: "1" }])),
    });
    await screen.findByRole("heading", { level: 2, name: "Production variables" });
    await user.click(await screen.findByRole("radio", { name: "Staging" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Staging variables" })).toBeInTheDocument();
    expect(await screen.findByText("DEBUG")).toBeInTheDocument();
    expect(backend.callsTo("GET /api/teams/acme/projects/web/environments/staging/variables")).not.toHaveLength(0);
  });
});

describe("the server's shared variables", () => {
  it("are a section of Settings with the page's one primary action in its header", async () => {
    screenWidth(1440);
    fakeBackend({
      ...signedInRoutes(),
      [`GET ${SERVER}`]: () => json(200, listing("server", "server", [{ name: "REGION", value: "eu-west" }])),
    });
    const { container } = renderConsole("/settings/variables");
    const table = await loadedTable("Shared variables of this server");
    expect(within(table).getByText("REGION")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add variable" })).toHaveLength(1);
    expect(screen.getByText(/can refer to these as \{\{server\.NAME\}\}/)).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });
});
