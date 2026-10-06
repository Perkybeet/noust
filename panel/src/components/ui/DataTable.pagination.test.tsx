import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import type { Column } from "./DataTable";
import { DataTable } from "./DataTable";
import { readPageSize } from "./pageSize";

interface Row {
  name: string;
  weight: number;
}

/** `count` rows named app-001 ... in order. */
function rowsOf(count: number): Row[] {
  return Array.from({ length: count }, (_, index) => ({ name: `app-${String(index + 1).padStart(3, "0")}`, weight: index + 1 }));
}

const COLUMNS: Column<Row>[] = [
  { id: "name", header: "Application", cell: (row) => row.name, sortValue: (row) => row.name },
  { id: "weight", header: "Weight", cell: (row) => row.weight, sortValue: (row) => row.weight, align: "end", mono: true },
];

function names(): string[] {
  const body = screen.getAllByRole("rowgroup")[1];
  if (!body) throw new Error("no body");
  return within(body)
    .getAllByRole("row")
    .map((row) => within(row).getAllByRole("cell")[0]?.textContent ?? "");
}

function table(rows: Row[], pagination: string | null = "apps") {
  return <DataTable caption="Applications" columns={COLUMNS} rows={rows} getRowId={(row) => row.name} {...(pagination !== null ? { pagination } : {})} />;
}

async function choose(size: string): Promise<void> {
  await userEvent.click(screen.getByRole("combobox", { name: "Rows per page" }));
  await userEvent.click(await screen.findByRole("option", { name: size }));
}

describe("DataTable paging (checklist M-51)", () => {
  it("shows 20 rows to a page and says which, out of how many", () => {
    render(table(rowsOf(57)));
    expect(names()).toHaveLength(20);
    expect(names()[0]).toBe("app-001");
    expect(names()[19]).toBe("app-020");
    expect(screen.getByText("Showing 1–20 of 57")).toBeInTheDocument();
  });

  it("goes to the next page and back", async () => {
    render(table(rowsOf(57)));
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(names()[0]).toBe("app-021");
    expect(screen.getByText("Showing 21–40 of 57")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(names()).toHaveLength(17);
    expect(screen.getByText("Showing 41–57 of 57")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(names()[0]).toBe("app-021");
  });

  it("lets the operator choose 50, 100 or all, and starts again at the first page", async () => {
    render(table(rowsOf(130)));
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    await choose("50");
    expect(names()).toHaveLength(50);
    expect(names()[0]).toBe("app-001");
    expect(screen.getByText("Showing 1–50 of 130")).toBeInTheDocument();
    await choose("100");
    expect(names()).toHaveLength(100);
    await choose("All");
    expect(names()).toHaveLength(130);
    expect(screen.getByText("Showing 1–130 of 130")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
  });

  it("remembers the choice for that list, and for no other", async () => {
    const { unmount } = render(table(rowsOf(130), "apps"));
    await choose("100");
    expect(readPageSize("apps")).toBe(100);
    expect(readPageSize("databases")).toBe(20);
    unmount();
    // Back on the page another day: the list comes up as it was left.
    render(table(rowsOf(130), "apps"));
    expect(names()).toHaveLength(100);
    expect(screen.getByRole("combobox", { name: "Rows per page" })).toHaveTextContent("100");
  });

  it("is the same choice for every table of one list", async () => {
    render(
      <>
        <div data-testid="a">{table(rowsOf(60), "apps")}</div>
        <div data-testid="b">{table(rowsOf(60), "apps")}</div>
      </>,
    );
    const [first] = screen.getAllByRole("combobox", { name: "Rows per page" });
    if (!first) throw new Error("no control");
    await userEvent.click(first);
    await userEvent.click(await screen.findByRole("option", { name: "50" }));
    for (const combobox of screen.getAllByRole("combobox", { name: "Rows per page" })) expect(combobox).toHaveTextContent("50");
  });

  it("pages the sorted list, not the order it arrived in, and starts a new order at its beginning", async () => {
    render(table(rowsOf(45)));
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(names()[0]).toBe("app-021");
    await userEvent.click(within(screen.getByRole("columnheader", { name: /Weight/ })).getByRole("button", { name: /Weight/ }));
    await userEvent.click(within(screen.getByRole("columnheader", { name: /Weight/ })).getByRole("button", { name: /Weight/ }));
    // Descending: the heaviest first, on the first page again.
    expect(names()[0]).toBe("app-045");
    expect(screen.getByText("Showing 1–20 of 45")).toBeInTheDocument();
  });

  it("draws nothing for a list of 20 or fewer, whatever size was chosen", () => {
    window.localStorage.setItem("noust.pageSize.apps", "50");
    render(table(rowsOf(20)));
    expect(names()).toHaveLength(20);
    expect(screen.queryByRole("group", { name: "Pagination" })).not.toBeInTheDocument();
  });

  it("brings a page that no longer exists back to the last one when the list shrinks", async () => {
    const { rerender } = render(table(rowsOf(57)));
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("Showing 41–57 of 57")).toBeInTheDocument();
    rerender(table(rowsOf(25)));
    expect(screen.getByText("Showing 21–25 of 25")).toBeInTheDocument();
    expect(names()).toEqual(["app-021", "app-022", "app-023", "app-024", "app-025"]);
    rerender(table(rowsOf(12)));
    expect(names()).toHaveLength(12);
    expect(screen.queryByRole("group", { name: "Pagination" })).not.toBeInTheDocument();
  });

  it("does nothing to a table that is not given a list to page", () => {
    render(table(rowsOf(57), null));
    expect(names()).toHaveLength(57);
    expect(screen.queryByRole("group", { name: "Pagination" })).not.toBeInTheDocument();
  });

  it("draws no paging while the rows are on their way", () => {
    render(<DataTable caption="Applications" columns={COLUMNS} rows={[]} getRowId={(row) => row.name} pagination="apps" loading />);
    expect(screen.queryByRole("group", { name: "Pagination" })).not.toBeInTheDocument();
  });

  it("keeps the arrow keys inside the page that is shown", async () => {
    const onRowActivate = vi.fn();
    render(<DataTable caption="Applications" columns={COLUMNS} rows={rowsOf(45)} getRowId={(row) => row.name} onRowActivate={onRowActivate} pagination="apps" />);
    screen.getByRole("button", { name: "app-020" }).focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("button", { name: "app-020" })).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("button", { name: "app-001" })).toHaveFocus();
  });

  it("pages the cards of a phone the same way", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }));
    render(<DataTable mobile="cards" caption="Applications" columns={COLUMNS} rows={rowsOf(45)} getRowId={(row) => row.name} pagination="apps" />);
    expect(within(screen.getByRole("list", { name: "Applications" })).getAllByRole("listitem")).toHaveLength(20);
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("Showing 21–40 of 45")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("has no accessibility violations", async () => {
    const { container } = render(table(rowsOf(57)));
    await expectNoAxeViolations(container);
  });
});
