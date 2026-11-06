import { cleanup, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { databaseRoutes } from "../testFixtures";

const RESULT = { success: true, output: "", mode: "read", truncated: false, returned_rows: 2, columns: ["id", "email"], rows: [["1", "a@example.com"], ["2", null]], row_count: 2, duration_ms: 3.2, timeout_s: 30 };

describe("the SQL console", () => {
  it("reads by default, draws the rows, and only writes once changes are allowed", async () => {
    screenWidth(1440);
    const backend = fakeBackend(
      databaseRoutes({
        "POST /api/databases/query": (call) => json(200, { ...RESULT, mode: (call.body as { mode: string }).mode }),
        "GET /api/databases/console/history": () => json(200, { entries: [] }),
        "GET /api/databases/console/saved": () => json(200, { queries: [] }),
      }),
    );
    const { user } = renderConsole("/databases/postgresql/example_production/query");
    const editor = await screen.findByRole("textbox", { name: "Statement to run against example_production" });
    await user.clear(editor);
    await user.type(editor, "SELECT id, email FROM customers");
    await user.click(screen.getByRole("button", { name: /^Run/ }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/query")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/query")[0]?.body).toMatchObject({ mode: "read", query: "SELECT id, email FROM customers", timeout_s: 30, row_limit: 1000 });
    const grid = await screen.findByRole("region", { name: "Result rows" });
    expect(within(grid).getByText("a@example.com")).toBeInTheDocument();
    expect(within(grid).getByText("NULL")).toBeInTheDocument();
    expect(screen.getByText("2 rows")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "Allow changes" }));
    expect(screen.getByText(/Runs as the engine's administrator/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Run/ }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/query")).toHaveLength(2));
    expect(backend.callsTo("POST /api/databases/query")[1]?.body).toMatchObject({ mode: "write" });
  });
});

describe("what a plan runs", () => {
  async function consoleAt() {
    screenWidth(1440);
    const backend = fakeBackend(
      databaseRoutes({
        "POST /api/databases/query/explain": () => json(200, { format: "text", plan: null, text: "Seq Scan on customers", analyze: false }),
        "GET /api/databases/console/history": () => json(200, { entries: [] }),
        "GET /api/databases/console/saved": () => json(200, { queries: [] }),
      }),
    );
    const harness = renderConsole("/databases/postgresql/example_production/query");
    const editor = await screen.findByRole("textbox", { name: "Statement to run against example_production" });
    return { ...harness, backend, editor };
  }

  it("shows the plan without running the statement, and measures it only once changes are allowed, saying that it runs", async () => {
    const { user, backend } = await consoleAt();
    await user.click(screen.getByRole("button", { name: "Explain" }));
    expect(await screen.findByRole("menuitem", { name: /Show the plan/ })).toHaveAccessibleName("Show the plan It does not run the statement.");
    const measure = screen.getByRole("menuitem", { name: /Measure it by running it/ });
    // The one that runs it waits for the box that lets a run change data, and says so in its description.
    expect(measure).toHaveAttribute("aria-disabled", "true");
    expect(measure).toHaveAccessibleName("Measure it by running it Turn on Allow changes: it runs the statement for real.");
    await user.click(screen.getByRole("menuitem", { name: /Show the plan/ }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/query/explain")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/query/explain")[0]?.body).toMatchObject({ analyze: false });

    await user.click(screen.getByRole("checkbox", { name: "Allow changes" }));
    await user.click(screen.getByRole("button", { name: "Explain" }));
    const enabled = await screen.findByRole("menuitem", { name: /Measure it by running it/ });
    expect(enabled).toHaveAccessibleName("Measure it by running it It runs the statement for real, and it may change data.");
    await user.click(enabled);
    await waitFor(() => expect(backend.callsTo("POST /api/databases/query/explain")).toHaveLength(2));
    expect(backend.callsTo("POST /api/databases/query/explain")[1]?.body).toMatchObject({ analyze: true });
  });

  it("keeps what is being written when the person goes to look something up and comes back", async () => {
    const { user, editor } = await consoleAt();
    await user.clear(editor);
    await user.type(editor, "SELECT email FROM customers WHERE id = 7");
    await waitFor(() => expect(window.sessionStorage.getItem("noust.db-query.postgresql.example_production")).toBe("SELECT email FROM customers WHERE id = 7"));
    cleanup();
    const again = renderConsole("/databases/postgresql/example_production/query");
    expect(await screen.findByRole("textbox", { name: "Statement to run against example_production" })).toHaveValue("SELECT email FROM customers WHERE id = 7");
    again.unmount();
  });
});
