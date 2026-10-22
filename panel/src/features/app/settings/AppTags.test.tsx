import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { filterApps, appTagNames, isFiltered, validateAppsSearch } from "../../apps/filters";
import { APPS_IN_TEAMS, teamsRoutes } from "../../teams/testFixtures";
import { screenWidth } from "../testRoutes";
import { MAX_TAGS, tagsOf, tagsProblem, tagsText } from "./appTags";

vi.setConfig({ testTimeout: 20_000 });
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

describe("the tags an app carries", () => {
  it("reads a list written with commas or spaces, each tag once and in order", () => {
    expect(tagsOf("web, client-acme  web\nv1.2")).toEqual(["web", "client-acme", "v1.2"]);
    expect(tagsOf("")).toEqual([]);
    expect(tagsText(["a", "b"])).toBe("a, b");
  });

  it("refuses what the server would refuse", () => {
    expect(tagsProblem("web, client-acme")).toBeNull();
    expect(tagsProblem("Web, -x, ok")).toMatch(/^Not tags: Web, -x\./);
    expect(tagsProblem("Web")).toMatch(/^Not a tag: Web\./);
    expect(tagsProblem(Array.from({ length: MAX_TAGS + 1 }, (_, i) => `t${String(i)}`).join(" "))).toBe(`At most ${String(MAX_TAGS)} tags.`);
  });

  it("filters the list by a tag and lists the tags there are", () => {
    const apps = [
      { ...APPS_IN_TEAMS[0], tags: ["web", "client-acme"] },
      { ...APPS_IN_TEAMS[1], tags: ["web"] },
      { ...APPS_IN_TEAMS[2], tags: [] },
    ] as never as Parameters<typeof filterApps>[0];
    expect(filterApps(apps, { tag: "client-acme" }).map((app) => app.domain)).toEqual(["shop.example.com"]);
    expect(filterApps(apps, { tag: "web" })).toHaveLength(2);
    expect(appTagNames(apps)).toEqual(["client-acme", "web"]);
    expect(isFiltered({ tag: "web" })).toBe(true);
    expect(validateAppsSearch({ tag: "web" })).toEqual({ tag: "web" });
    // A tag the server could not have made is dropped, not trusted.
    expect(validateAppsSearch({ tag: "Not A Tag" })).toEqual({});
  });
});

const SHOP = "shop.example.com";

function rows(tags: Record<string, string[]>) {
  return APPS_IN_TEAMS.map((app) => ({ ...app, layout: "releases", tags: tags[app.domain] ?? [] }));
}

describe("the tag filter and updating by tag", () => {
  function list(tags: Record<string, string[]>, extra: Record<string, RouteHandler> = {}, path = "/apps") {
    screenWidth(1440);
    const apps = rows(tags);
    const backend = fakeBackend({
      ...teamsRoutes(),
      "GET /api/apps": () => json(200, { total: apps.length, apps }),
      "GET /api/deployments": () => json(200, { items: [], total: 0 }),
      ...extra,
    });
    const harness = renderConsole(path);
    return { ...harness, backend };
  }

  it("draws no tag filter while no app has a tag", async () => {
    list({});
    await screen.findByRole("table", { name: /Applications/ });
    expect(screen.queryByRole("combobox", { name: "Tag" })).not.toBeInTheDocument();
  });

  it("narrows the list to a tag from the filter bar", async () => {
    const { user } = list({ [SHOP]: ["web"], "staging.example.com": ["web", "client-acme"] });
    const filter = await screen.findByRole("combobox", { name: "Tag" });
    await user.click(filter);
    await user.click(await screen.findByRole("option", { name: "client-acme" }));
    const table = await screen.findByRole("table", { name: /Applications/ });
    await waitFor(() => {
      expect(within(table).queryByText(SHOP)).not.toBeInTheDocument();
    });
    expect(within(table).getByText("staging.example.com")).toBeInTheDocument();
  });

  it("offers to update every app with the tag only once the list is narrowed to it", async () => {
    const { user, backend } = list(
      { [SHOP]: ["web"], "staging.example.com": ["web"] },
      {
        "POST /api/tags/web/deploy": () =>
          json(202, {
            tag: "web",
            queued: 1,
            results: [
              { domain: SHOP, outcome: "queued", detail: "", job_id: "j1" },
              { domain: "staging.example.com", outcome: "skipped", detail: "Nothing new", job_id: null },
            ],
          }),
      },
      "/apps?tag=web",
    );
    const action = await screen.findByRole("button", { name: "Update tagged apps" });
    await user.click(action);
    const dialog = await screen.findByRole("alertdialog", { name: "Update the apps tagged web?" });
    expect(within(dialog).getByText(/2 apps carry web/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("checkbox", { name: "Rebuild even when there is nothing new" }));
    await user.click(within(dialog).getByRole("button", { name: "Update apps" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/tags/web/deploy")[0]?.body).toEqual({ force: true });
    });
    expect(await screen.findByText("Updating 1 app tagged web")).toBeInTheDocument();
    expect(screen.getByText("1 app had nothing new and was skipped")).toBeInTheDocument();
  });

  it("says which apps could not be queued, in the server's words", async () => {
    const { user } = list(
      { [SHOP]: ["web"] },
      {
        "POST /api/tags/web/deploy": () =>
          json(202, { tag: "web", queued: 0, results: [{ domain: SHOP, outcome: "failed", detail: "shop.example.com is busy", job_id: null }] }),
      },
      "/apps?tag=web",
    );
    await user.click(await screen.findByRole("button", { name: "Update tagged apps" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Update apps" }));
    expect(await screen.findByText("1 app could not be updated")).toBeInTheDocument();
    expect(screen.getByText(`${SHOP}: shop.example.com is busy`)).toBeInTheDocument();
  });

  it("offers no bulk update on the whole list", async () => {
    list({ [SHOP]: ["web"] });
    await screen.findByRole("combobox", { name: "Tag" });
    expect(screen.queryByRole("button", { name: "Update tagged apps" })).not.toBeInTheDocument();
  });
});

describe("an app's tags in its settings", () => {
  const APP = {
    name: SHOP,
    domain: SHOP,
    status: "running",
    active: true,
    enabled: true,
    app_type: "nextjs",
    port: 3000,
    path: "/var/www/apps/shop-example-com",
    layout: "inplace",
    source: "https://github.com/shop/storefront.git",
    branch: "main",
    tags: ["web"],
  };

  function settings(extra: Record<string, RouteHandler> = {}) {
    screenWidth(1440);
    const backend = fakeBackend({
      ...teamsRoutes(),
      [`GET /api/apps/${SHOP}`]: () => json(200, APP),
      "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
      "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
      ...extra,
    });
    const harness = renderConsole(`/apps/${SHOP}/settings`);
    return { ...harness, backend };
  }

  it("shows the tags beside the facts, apart from the git tags it deploys", async () => {
    settings();
    const tags = await screen.findByRole("list", { name: "App tags" });
    expect(within(tags).getByText("web")).toBeInTheDocument();
    expect(screen.getByText("Tags it deploys")).toBeInTheDocument();
  });

  it("saves the tags typed, at once", async () => {
    const { user, backend } = settings({
      [`PUT /api/apps/${SHOP}/tags`]: () => json(200, { domain: SHOP, tags: ["client-acme", "web"], max_tags: 16 }),
    });
    await user.click(await screen.findByRole("button", { name: "Edit tags" }));
    const dialog = await screen.findByRole("dialog", { name: `Tags of ${SHOP}` });
    const field = within(dialog).getByLabelText(/Tags/);
    expect(field).toHaveValue("web");
    await user.clear(field);
    await user.type(field, "web, client-acme");
    await user.click(within(dialog).getByRole("button", { name: "Save tags" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${SHOP}/tags`)[0]?.body).toEqual({ tags: ["web", "client-acme"] });
    });
    const list = await screen.findByRole("list", { name: "App tags" });
    await waitFor(() => {
      expect(within(list).getByText("client-acme")).toBeInTheDocument();
    });
  });

  it("refuses a tag the server would refuse before sending anything", async () => {
    const { user, backend } = settings();
    await user.click(await screen.findByRole("button", { name: "Edit tags" }));
    const dialog = await screen.findByRole("dialog", { name: `Tags of ${SHOP}` });
    await user.type(within(dialog).getByLabelText(/Tags/), ", Bad Tag");
    await user.click(within(dialog).getByRole("button", { name: "Save tags" }));
    expect(await within(dialog).findByText(/Not tags: Bad, Tag\./)).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${SHOP}/tags`)).toHaveLength(0);
  });

  it("shows the server's refusal verbatim", async () => {
    const { user } = settings({
      [`PUT /api/apps/${SHOP}/tags`]: () => problem(400, "validation_error", "An application carries at most 16 tags"),
    });
    await user.click(await screen.findByRole("button", { name: "Edit tags" }));
    const dialog = await screen.findByRole("dialog", { name: `Tags of ${SHOP}` });
    await user.click(within(dialog).getByRole("button", { name: "Save tags" }));
    expect(await within(dialog).findByText("An application carries at most 16 tags")).toBeInTheDocument();
  });

  it("is in Spanish, with no accessibility violations", async () => {
    const { user } = settings();
    await screen.findByRole("list", { name: "App tags" });
    await act(() => setLocale("es"));
    await user.click(await screen.findByRole("button", { name: "Editar etiquetas" }));
    const dialog = await screen.findByRole("dialog", { name: `Etiquetas de ${SHOP}` });
    await expectNoAxeViolations(dialog);
  });
});
