import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { DataGrid } from "./DataGrid";

const COLUMNS = [
  { name: "id", type: "bigint", kind: "numeric", primaryKey: 1 },
  { name: "notes", type: "text", kind: "text" },
  { name: "receipt", type: "bytea", kind: "binary" },
  { name: "paid", type: "boolean", kind: "boolean" },
];

describe("the rows of a table", () => {
  it("tells NULL from an empty text, draws binary by its size, and says what the engine cut", async () => {
    render(
      <DataGrid
        label="Rows of public.orders"
        columns={COLUMNS}
        rows={[
          { id: "1", cells: [1001, null, { bytes: 2048, hex: "25504446" }, true] },
          { id: "2", cells: [1002, "", null, false] },
          { id: "3", cells: [1003, "x".repeat(2000), null, false], truncated: [1] },
        ]}
      />,
    );
    const grid = screen.getByRole("region", { name: "Rows of public.orders" });
    const [, first, second, third] = within(grid).getAllByRole("row");
    if (!first || !second || !third) throw new Error("rows");
    expect(within(first).getByText("NULL")).toBeInTheDocument();
    expect(within(first).getByText(/^binary, 2/)).toBeInTheDocument();
    expect(within(first).getByText("\\x25504446…")).toBeInTheDocument();
    expect(within(second).getByText("(empty)")).toBeInTheDocument();
    expect(within(third).getByText("cut")).toBeInTheDocument();
    expect(within(grid).getByLabelText("Primary key")).toBeInTheDocument();
    await expectNoAxeViolations(grid);
  });

  it("asks the server for an order from a header, and says the one in force", async () => {
    const onSort = vi.fn();
    render(<DataGrid label="Rows" columns={COLUMNS} rows={[]} sort={{ column: "id", descending: true }} onSort={onSort} empty="Nothing" />);
    expect(screen.getAllByRole("columnheader")[1]).toHaveAttribute("aria-sort", "descending");
    await userEvent.setup().click(screen.getByRole("button", { name: /notes/ }));
    expect(onSort).toHaveBeenCalledWith("notes");
  });
});
