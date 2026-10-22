import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { EXTENSIONS, OVERVIEW, databaseRoutes } from "../testFixtures";

const BASE = "/api/databases/databases/postgresql/example_production";

async function cardAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(databaseRoutes(extra));
  const harness = renderConsole("/databases/postgresql/example_production");
  await screen.findByRole("heading", { level: 1, name: "example_production" });
  const card = (await screen.findByRole("heading", { level: 2, name: "Extensions" })).closest("section");
  if (card === null) throw new Error("no card");
  await within(card).findByText("pg_trgm");
  return { ...harness, backend, card };
}

describe("the extensions of a PostgreSQL database", { timeout: 60_000 }, () => {
  it("lists what is loaded, with its version and what it is", async () => {
    const { card } = await cardAt();

    expect(within(card).getByText("1.6")).toBeInTheDocument();
    expect(within(card).getByText("text similarity measurement and index searching")).toBeInTheDocument();
    expect(within(card).queryByText("vector")).not.toBeInTheDocument();
    expect(within(card).getByText(/apt install postgresql-17-<extension>/)).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is not shown for an engine that has none", async () => {
    screenWidth(1440);
    fakeBackend(databaseRoutes({ [`GET ${BASE}/overview`]: () => json(200, { ...OVERVIEW, capabilities: ["dump", "sql"] }) }));
    renderConsole("/databases/postgresql/example_production");
    await screen.findByRole("heading", { level: 1, name: "example_production" });
    await screen.findByText("Every day at 02:00");

    expect(screen.queryByRole("heading", { level: 2, name: "Extensions" })).not.toBeInTheDocument();
  });

  it("loads one the server offers", async () => {
    const { backend, user, card } = await cardAt({ [`POST ${BASE}/extensions`]: () => json(200, { ...EXTENSIONS.extensions[1], installed: true, installed_version: "0.8.0" }) });

    await user.click(within(card).getByRole("button", { name: "Add extension" }));
    const dialog = await screen.findByRole("dialog", { name: "Add an extension to example_production" });
    await user.click(within(dialog).getByRole("combobox", { name: "Extension" }));
    await user.click(await screen.findByRole("option", { name: /^vector/ }));
    expect(within(dialog).getByText("vector data type and ivfflat and hnsw access methods")).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Add extension" }));

    await waitFor(() => expect(backend.callsTo(`POST ${BASE}/extensions`)).toHaveLength(1));
    expect(backend.callsTo(`POST ${BASE}/extensions`)[0]?.body).toEqual({ name: "vector", cascade: false });
  });

  it("says an extension that is not trusted is the server's, and asks to load what it needs", async () => {
    const { user, card } = await cardAt();

    await user.click(within(card).getByRole("button", { name: "Add extension" }));
    const dialog = await screen.findByRole("dialog", { name: "Add an extension to example_production" });
    await user.click(within(dialog).getByRole("combobox", { name: "Extension" }));
    await user.click(await screen.findByRole("option", { name: /^postgis_topology/ }));

    expect(within(dialog).getByText("This extension is not trusted")).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: "Also load what it needs: postgis" })).not.toBeChecked();
    // Pressing without it says what is missing, and loads nothing.
    await user.click(within(dialog).getByRole("button", { name: "Add extension" }));
    expect(within(dialog).getByText("Tick the box to load what it needs too.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("checkbox", { name: "Also load what it needs: postgis" }));
    expect(within(dialog).queryByText("Tick the box to load what it needs too.")).not.toBeInTheDocument();
  });

  it("shows what the server refused, as it said it", async () => {
    const { user, card } = await cardAt({
      [`POST ${BASE}/extensions`]: () => problem(403, "instance_scope_required", "This is about the whole server", { hint: "Ask an administrator of this server." }),
    });

    await user.click(within(card).getByRole("button", { name: "Add extension" }));
    const dialog = await screen.findByRole("dialog", { name: "Add an extension to example_production" });
    await user.click(within(dialog).getByRole("combobox", { name: "Extension" }));
    await user.click(await screen.findByRole("option", { name: /^postgis \d/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add extension" }));

    expect(await within(dialog).findByText("This is about the whole server")).toBeInTheDocument();
    expect(within(dialog).getByText("Ask an administrator of this server.")).toBeInTheDocument();
  });

  it("unloads one after asking, and drops what depends on it only when asked", async () => {
    const { backend, user, card } = await cardAt({ [`DELETE ${BASE}/extensions/pg_trgm`]: () => json(200, { ...EXTENSIONS.extensions[0], installed: false, installed_version: null }) });

    await user.click(within(card).getByRole("button", { name: "Unload pg_trgm" }));
    const ask = await screen.findByRole("alertdialog", { name: "Unload pg_trgm from example_production?" });
    expect(within(ask).getByText(/refuses while anything in the database depends on it/)).toBeInTheDocument();
    expect(within(ask).getByRole("checkbox", { name: /Also drop what depends on it/ })).not.toBeChecked();
    await user.click(within(ask).getByRole("button", { name: "Unload extension" }));

    await waitFor(() => expect(backend.callsTo(`DELETE ${BASE}/extensions/pg_trgm`)).toHaveLength(1));
    expect(backend.callsTo(`DELETE ${BASE}/extensions/pg_trgm`)[0]?.search.get("cascade")).toBe("false");
  });

  it("speaks Spanish", async () => {
    await cardAt();

    await act(() => setLocale("es"));

    expect(await screen.findByRole("heading", { level: 2, name: "Extensiones" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Añadir extensión" })).toBeInTheDocument();
  });
});
