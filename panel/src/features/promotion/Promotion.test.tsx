import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { APPS_IN_TEAMS, teamsRoutes } from "../teams/testFixtures";

vi.setConfig({ testTimeout: 20_000 });
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

const SHOP = "shop.example.com";
const STAGING = "staging.example.com";

/** The apps of the fixture server, on the release layout so a release can be promoted. */
const RELEASE_APPS = APPS_IN_TEAMS.map((app) => ({ ...app, layout: "releases", tags: [] as string[] }));

function appsList(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...teamsRoutes(),
    "GET /api/apps": () => json(200, { total: RELEASE_APPS.length, apps: RELEASE_APPS }),
    "GET /api/deployments": () => json(200, { items: [], total: 0 }),
    ...extra,
  });
  const harness = renderConsole("/apps");
  return { ...harness, backend };
}

async function chooseFromMenu(user: ReturnType<typeof renderConsole>["user"], domain: string, item: string) {
  await user.click(await screen.findByRole("button", { name: `Actions for ${domain}` }));
  await user.click(await screen.findByRole("menuitem", { name: item }));
}

describe("promoting a release to another environment", () => {
  const PROMOTION = {
    domain: SHOP,
    release: "20261012-101500",
    commit: "a1b2c3d4e5f6",
    targets: [{ domain: STAGING, environment: "Staging", release: "20261001-090000", commit: "0000000" }],
  };

  it("is offered on an app with releases, in a project with more than one environment", async () => {
    const { user } = appsList();
    await user.click(await screen.findByRole("button", { name: `Actions for ${SHOP}` }));
    expect(await screen.findByRole("menuitem", { name: /Promote to another environment/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Clone application/ })).toBeInTheDocument();
  });

  it("is not offered on an app kept in a single folder", async () => {
    const inPlace = APPS_IN_TEAMS.map((app) => ({ ...app, layout: "inplace" }));
    const { user } = appsList({ "GET /api/apps": () => json(200, { total: inPlace.length, apps: inPlace }) });
    await user.click(await screen.findByRole("button", { name: `Actions for ${SHOP}` }));
    await screen.findByRole("menuitem", { name: /Clone application/ });
    expect(screen.queryByRole("menuitem", { name: /Promote to another environment/ })).not.toBeInTheDocument();
  });

  it("says which release goes where, and sends the serving release to the chosen app", async () => {
    const { user, backend } = appsList({
      [`GET /api/apps/${SHOP}/promotion`]: () => json(200, PROMOTION),
      [`POST /api/apps/${SHOP}/promote`]: () =>
        json(202, { job_id: "j1", status: "pending", message: "Promotion queued", job: { id: "j1", status: "pending" } }),
    });
    await chooseFromMenu(user, SHOP, "Promote to another environment");
    const dialog = await screen.findByRole("dialog", { name: `Promote ${SHOP}` });
    expect(await within(dialog).findByText("20261012-101500")).toBeInTheDocument();
    expect(within(dialog).getByText("a1b2c3d")).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "Promote to" })).toHaveTextContent(`${STAGING} (Staging)`);

    await user.click(within(dialog).getByRole("button", { name: "Promote release" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${SHOP}/promote`)[0]?.body).toEqual({
        target: STAGING,
        release: "20261012-101500",
        schema_changed_ok: false,
      });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: `Promote ${SHOP}` })).not.toBeInTheDocument();
    });
  });

  it("allows going back past a schema change only when asked, under more options", async () => {
    const { user, backend } = appsList({
      [`GET /api/apps/${SHOP}/promotion`]: () => json(200, PROMOTION),
      [`POST /api/apps/${SHOP}/promote`]: () => json(202, { job_id: "j2", status: "pending", message: "queued", job: { id: "j2", status: "pending" } }),
    });
    await chooseFromMenu(user, SHOP, "Promote to another environment");
    const dialog = await screen.findByRole("dialog", { name: `Promote ${SHOP}` });
    await within(dialog).findByText("20261012-101500");
    await user.click(within(dialog).getByRole("button", { name: "More options" }));
    await user.click(within(dialog).getByRole("checkbox", { name: "Allow going back past a schema change" }));
    await user.click(within(dialog).getByRole("button", { name: "Promote release" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${SHOP}/promote`)[0]?.body).toMatchObject({ schema_changed_ok: true });
    });
  });

  it("says there is nowhere to promote to, and how to make a place", async () => {
    const { user } = appsList({ [`GET /api/apps/${SHOP}/promotion`]: () => json(200, { ...PROMOTION, targets: [] }) });
    await chooseFromMenu(user, SHOP, "Promote to another environment");
    const dialog = await screen.findByRole("dialog", { name: `Promote ${SHOP}` });
    expect(await within(dialog).findByText("No other environment has this app")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Promote release" })).toBeDisabled();
  });

  it("says the app has no release to promote", async () => {
    const { user } = appsList({ [`GET /api/apps/${SHOP}/promotion`]: () => json(200, { ...PROMOTION, release: null, commit: null }) });
    await chooseFromMenu(user, SHOP, "Promote to another environment");
    const dialog = await screen.findByRole("dialog", { name: `Promote ${SHOP}` });
    expect(await within(dialog).findByText(`${SHOP} has no release to promote`)).toBeInTheDocument();
  });

  it("shows the server's refusal in the dialog, as it worded it", async () => {
    const { user } = appsList({
      [`GET /api/apps/${SHOP}/promotion`]: () => json(200, PROMOTION),
      [`POST /api/apps/${SHOP}/promote`]: () => problem(400, "validation_error", "staging.example.com already runs release 20261012-101500"),
    });
    await chooseFromMenu(user, SHOP, "Promote to another environment");
    const dialog = await screen.findByRole("dialog", { name: `Promote ${SHOP}` });
    await within(dialog).findByText("20261012-101500");
    await user.click(within(dialog).getByRole("button", { name: "Promote release" }));
    expect(await within(dialog).findByText("staging.example.com already runs release 20261012-101500")).toBeInTheDocument();
    expect(within(dialog).getByText("The release was not promoted")).toBeInTheDocument();
  });
});

describe("cloning an application", () => {
  const QUEUED = { job_id: "c1", status: "pending", message: "Clone queued", job: { id: "c1", status: "pending" } };

  it("asks for the new domain and sends it with where it goes", async () => {
    const { user, backend } = appsList({ [`POST /api/apps/${SHOP}/clone`]: () => json(202, QUEUED) });
    await chooseFromMenu(user, SHOP, "Clone application");
    const dialog = await screen.findByRole("dialog", { name: `Clone ${SHOP}` });
    const choice = await within(dialog).findByRole("radiogroup", { name: "Its database" });
    await user.click(within(dialog).getByRole("button", { name: "Clone application" }));
    expect(await within(dialog).findByText("Give the domain the clone answers on.")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${SHOP}/clone`)).toHaveLength(0);

    await user.type(within(dialog).getByLabelText("Domain of the clone"), "copy.example.com");
    await user.click(within(choice).getByRole("radio", { name: "A database of its own" }));
    await user.click(within(dialog).getByRole("button", { name: "Clone application" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${SHOP}/clone`)[0]?.body).toMatchObject({ domain: "copy.example.com", team: "acme", project: "web", environment: "production", database: "empty" });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: `Clone ${SHOP}` })).not.toBeInTheDocument();
    });
  });

  it("makes the operator choose what a clone does with the database, and sends the choice", async () => {
    const { user, backend } = appsList({ [`POST /api/apps/${SHOP}/clone`]: () => json(202, QUEUED) });
    await chooseFromMenu(user, SHOP, "Clone application");
    const dialog = await screen.findByRole("dialog", { name: `Clone ${SHOP}` });
    // The fixture server has a database linked to this app.
    const choice = await within(dialog).findByRole("radiogroup", { name: "Its database" });
    expect(within(choice).getByRole("radio", { name: "A database of its own" })).not.toBeChecked();
    expect(within(choice).getByRole("radio", { name: "This app's database" })).not.toBeChecked();

    await user.type(within(dialog).getByLabelText("Domain of the clone"), "copy.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Clone application" }));
    expect(await within(dialog).findByText("Choose what the clone does with the database.")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${SHOP}/clone`)).toHaveLength(0);

    await user.click(within(choice).getByRole("radio", { name: "A database of its own" }));
    await user.click(within(dialog).getByRole("button", { name: "Clone application" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${SHOP}/clone`)[0]?.body).toMatchObject({ domain: "copy.example.com", database: "empty" });
    });
  });

  it("places the server's refusal by the domain it is about", async () => {
    const { user } = appsList({
      [`POST /api/apps/${SHOP}/clone`]: () => problem(409, "domain_conflict", "An application is already deployed on copy.example.com", { fields: { domain: "An application is already deployed on copy.example.com" } }),
    });
    await chooseFromMenu(user, SHOP, "Clone application");
    const dialog = await screen.findByRole("dialog", { name: `Clone ${SHOP}` });
    await within(dialog).findByRole("radiogroup", { name: "Its database" });
    await user.type(within(dialog).getByLabelText("Domain of the clone"), "copy.example.com");
    await user.click(within(dialog).getByRole("radio", { name: "This app's database" }));
    await user.click(within(dialog).getByRole("button", { name: "Clone application" }));
    expect(await within(dialog).findByText("An application is already deployed on copy.example.com")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Domain of the clone")).toHaveAttribute("aria-invalid", "true");
  });

  it("is in Spanish, with no accessibility violations", async () => {
    const { user } = appsList();
    await chooseFromMenu(user, SHOP, "Clone application");
    const dialog = await screen.findByRole("dialog", { name: `Clone ${SHOP}` });
    await within(dialog).findByRole("radiogroup", { name: "Its database" });
    await act(() => setLocale("es"));
    expect(screen.getByRole("dialog", { name: `Clonar ${SHOP}` })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("dialog", { name: `Clonar ${SHOP}` }));
  });
});
