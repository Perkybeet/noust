import { screen, waitFor, within } from "@testing-library/react";
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

    await user.click(screen.getByRole("switch", { name: "Allow changes" }));
    expect(screen.getByText("Statements can change data")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Run/ }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/query")).toHaveLength(2));
    expect(backend.callsTo("POST /api/databases/query")[1]?.body).toMatchObject({ mode: "write" });
  });
});
