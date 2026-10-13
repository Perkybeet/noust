import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { catalogueRoutes } from "./testFixtures";

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const DOCUMENT = "name: my-app\ntitle: My app\ndescription: Mine.\nhomepage: https://example.org\nkind: native\n";

const OWN = {
  name: "my-app",
  title: "My app",
  slogan: "",
  description: "Mine.",
  homepage: "https://example.org",
  kind: "native",
  category: "other",
  tags: [],
  architectures: [],
  available: true,
  dependencies: [],
  requires: [],
  runs_in_docker: false,
  revision: 1,
  schema_version: 2,
  origin: "operator",
};

const PROBLEM = {
  name: "broken",
  origin: "operator",
  location: "/etc/noust/recipes/broken.yaml",
  error: "title: a recipe needs a title",
  details: "Fix the file, then reload the catalog.",
};

const PACKAGE = {
  package: { serial: 7, created: "2026-10-01T10:00:00+00:00", signer: "noust-release", min_noust: null, recipes: { vaultwarden: { version: "1.37.4", revision: 1 }, syncthing: { version: "2.1.6", revision: 2 } } },
  trusted_keys: ["noust-release"],
};

function catalog(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/session": () => json(200, ELEVATED),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [] }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    ...catalogueRoutes(),
    ...extra,
  });
  return { backend, harness: renderConsole("/catalog") };
}

describe("recipes of the server's own", { timeout: 40_000 }, () => {
  it("shows a broken file in the notice, with its path and the server's words", async () => {
    catalog({
      "GET /api/recipes": () => json(200, { items: [OWN], total: 1, problems: [PROBLEM] }),
    });

    const notice = await screen.findByText("1 recipe file was left out");
    expect(notice).toBeInTheDocument();
    expect(screen.getByText("/etc/noust/recipes/broken.yaml")).toBeInTheDocument();
    expect(screen.getByText("title: a recipe needs a title")).toBeInTheDocument();
    expect(screen.getByText("Fix the file, then reload the catalog.")).toBeInTheDocument();
    const card = (await screen.findByRole("heading", { level: 2, name: "My app" })).closest("li") as HTMLElement;
    expect(within(card).getByText("Yours")).toBeInTheDocument();
  });

  it("checks a recipe without adding it, and then adds it", async () => {
    const { backend, harness } = catalog({
      "GET /api/recipes": () => json(200, { items: [OWN], total: 1, problems: [] }),
      "POST /api/recipes/private/check": () => json(200, { recipe: OWN, added: false }),
      "POST /api/recipes/private": () => json(201, { recipe: OWN, added: true }),
    });
    const { user } = harness;

    await user.click(await screen.findByRole("button", { name: "Add a recipe" }));
    const dialog = await screen.findByRole("dialog", { name: "A recipe of your own" });
    expect(within(dialog).getByRole("button", { name: "Add recipe" })).toBeDisabled();
    await user.click(within(dialog).getByRole("textbox", { name: /Recipe \(YAML\)/ }));
    await user.paste(DOCUMENT);
    await user.click(within(dialog).getByRole("button", { name: "Check it" }));

    expect(await within(dialog).findByText("My app is a valid recipe")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/recipes/private/check")[0]?.body).toEqual({ document: DOCUMENT, assets: {}, replace: false });
    expect(backend.callsTo("POST /api/recipes/private")).toHaveLength(0);
    await expectNoAxeViolations(dialog);

    await user.click(within(dialog).getByRole("button", { name: "Add recipe" }));
    await waitFor(() => expect(backend.callsTo("POST /api/recipes/private")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "A recipe of your own" })).not.toBeInTheDocument());
  });

  it("shows the server's refusal as it came, naming the field", async () => {
    const { harness } = catalog({
      "POST /api/recipes/private/check": () => problem(400, "recipeerror", "variables[0].name: not a valid variable name", { hint: "Use capital letters and underscores." }),
    });
    const { user } = harness;

    await user.click(await screen.findByRole("button", { name: "Add a recipe" }));
    const dialog = await screen.findByRole("dialog", { name: "A recipe of your own" });
    await user.click(within(dialog).getByRole("textbox", { name: /Recipe \(YAML\)/ }));
    await user.paste("name: x");
    await user.click(within(dialog).getByRole("button", { name: "Check it" }));

    expect(await within(dialog).findByText("variables[0].name: not a valid variable name")).toBeInTheDocument();
    expect(within(dialog).getByText("Use capital letters and underscores.")).toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    const { harness } = catalog();
    await screen.findByRole("button", { name: "Add a recipe" });

    await act(() => setLocale("es"));

    expect(await screen.findByRole("button", { name: "Añadir una receta" })).toBeInTheDocument();
    await harness.user.click(screen.getByRole("button", { name: "Añadir una receta" }));
    expect(await screen.findByRole("dialog", { name: "Una receta propia" })).toBeInTheDocument();
  });
});

describe("the catalog between releases", { timeout: 40_000 }, () => {
  it("says which signed package is in place and which keys are trusted", async () => {
    const { harness } = catalog({ "GET /api/recipes/catalog": () => json(200, PACKAGE) });
    const { user } = harness;

    await user.click(await screen.findByRole("button", { name: "Catalog package" }));
    const dialog = await screen.findByRole("dialog", { name: "The catalog between releases" });

    expect(await within(dialog).findByText("noust-release")).toBeInTheDocument();
    expect(within(dialog).getByText("7")).toBeInTheDocument();
    expect(within(dialog).getByTestId("trusted-keys")).toHaveTextContent("Keys trusted here: noust-release.");
    await expectNoAxeViolations(dialog);
  });

  it("says the catalog is the release's own when no package is in place", async () => {
    const { harness } = catalog({ "GET /api/recipes/catalog": () => json(200, { package: null, trusted_keys: [] }) });

    await harness.user.click(await screen.findByRole("button", { name: "Catalog package" }));
    const dialog = await screen.findByRole("dialog", { name: "The catalog between releases" });

    expect(await within(dialog).findByText(/the catalog is the one this release ships/)).toBeInTheDocument();
    expect(within(dialog).getByText(/No key is trusted here/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Go back to the release's recipes" })).not.toBeInTheDocument();
  });

  it("fetches a package from an address and says what it brought", async () => {
    const { backend, harness } = catalog({
      "GET /api/recipes/catalog": () => json(200, PACKAGE),
      "POST /api/recipes/catalog/fetch": () =>
        json(200, { package: { ...PACKAGE.package, serial: 8 }, added: ["plane"], updated: ["vaultwarden", "syncthing"], ignored: [], clashes: ["my-app"] }),
    });
    const { user } = harness;

    await user.click(await screen.findByRole("button", { name: "Catalog package" }));
    const dialog = await screen.findByRole("dialog", { name: "The catalog between releases" });
    await user.type(await within(dialog).findByLabelText(/Fetch a package/), "https://example.org/recipes.tar.gz");
    await user.click(within(dialog).getByRole("button", { name: "Fetch and import" }));

    expect(await within(dialog).findByText("Package 8 is in place")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/recipes/catalog/fetch")[0]?.body).toEqual({ url: "https://example.org/recipes.tar.gz", force: false });
    expect(within(dialog).getByText("1 recipe added")).toBeInTheDocument();
    expect(within(dialog).getByText("2 recipes updated")).toBeInTheDocument();
    expect(within(dialog).getByText("Names one of your own recipes also has: my-app")).toBeInTheDocument();
  });

  it("shows why a package was refused, as the server said it", async () => {
    const { harness } = catalog({
      "GET /api/recipes/catalog": () => json(200, PACKAGE),
      "POST /api/recipes/catalog/fetch": () => problem(400, "recipeerror", "The signature does not match any trusted key", { hint: "Check the key file." }),
    });
    const { user } = harness;

    await user.click(await screen.findByRole("button", { name: "Catalog package" }));
    const dialog = await screen.findByRole("dialog", { name: "The catalog between releases" });
    await user.type(await within(dialog).findByLabelText(/Fetch a package/), "https://example.org/bad.tar.gz");
    await user.click(within(dialog).getByRole("button", { name: "Fetch and import" }));

    expect(await within(dialog).findByText("The signature does not match any trusted key")).toBeInTheDocument();
  });

  it("goes back to the release's recipes after asking", async () => {
    const { backend, harness } = catalog({
      "GET /api/recipes/catalog": () => json(200, PACKAGE),
      "DELETE /api/recipes/catalog": () => new Response(null, { status: 204 }),
    });
    const { user } = harness;

    await user.click(await screen.findByRole("button", { name: "Catalog package" }));
    const dialog = await screen.findByRole("dialog", { name: "The catalog between releases" });
    await user.click(await within(dialog).findByRole("button", { name: "Go back to the release's recipes" }));
    const ask = await screen.findByRole("alertdialog", { name: "Go back to the release's recipes?" });
    expect(backend.callsTo("DELETE /api/recipes/catalog")).toHaveLength(0);
    await user.click(within(ask).getByRole("button", { name: "Go back" }));

    await waitFor(() => expect(backend.callsTo("DELETE /api/recipes/catalog")).toHaveLength(1));
  });
});
