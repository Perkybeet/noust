import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import type { Column } from "./DataTable";
import { DataTable } from "./DataTable";
import { IconButton } from "./IconButton";

interface App {
  domain: string;
  port: number;
}

const ROWS: App[] = [
  { domain: "shop.example.com", port: 3004 },
  { domain: "api.example.com", port: 8001 },
  { domain: "blog.example.com", port: 3010 },
];

const COLUMNS: Column<App>[] = [
  { id: "domain", header: "Application", cell: (row) => row.domain, sortValue: (row) => row.domain },
  { id: "port", header: "Port", cell: (row) => row.port, sortValue: (row) => row.port, align: "end", mono: true },
];

function domains(): string[] {
  const body = screen.getAllByRole("rowgroup")[1];
  if (!body) throw new Error("no body");
  return within(body)
    .getAllByRole("row")
    .map((row) => within(row).getAllByRole("cell")[0]?.textContent ?? "");
}

describe("DataTable", () => {
  it("sorts by a column and announces the order on its header", async () => {
    render(<DataTable caption="Applications" columns={COLUMNS} rows={ROWS} getRowId={(r) => r.domain} />);
    expect(domains()).toEqual(["shop.example.com", "api.example.com", "blog.example.com"]);
    const header = screen.getByRole("columnheader", { name: /Port/ });
    expect(header).not.toHaveAttribute("aria-sort");

    await userEvent.click(within(header).getByRole("button", { name: /Port/ }));
    expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(domains()).toEqual(["shop.example.com", "blog.example.com", "api.example.com"]);

    await userEvent.click(within(header).getByRole("button", { name: /Port/ }));
    expect(header).toHaveAttribute("aria-sort", "descending");
    expect(domains()).toEqual(["api.example.com", "blog.example.com", "shop.example.com"]);
  });

  it("names the table with its caption", () => {
    render(<DataTable caption="Applications" columns={COLUMNS} rows={ROWS} getRowId={(r) => r.domain} />);
    expect(screen.getByRole("table", { name: "Applications" })).toBeInTheDocument();
  });

  it("opens a row by click anywhere on it, or by Enter on its primary control", async () => {
    const onRowActivate = vi.fn();
    render(
      <DataTable caption="Applications" columns={COLUMNS} rows={ROWS} getRowId={(r) => r.domain} onRowActivate={onRowActivate} />,
    );
    await userEvent.click(screen.getByText("8001"));
    expect(onRowActivate).toHaveBeenLastCalledWith(ROWS[1]);

    screen.getByRole("button", { name: "blog.example.com" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(onRowActivate).toHaveBeenLastCalledWith(ROWS[2]);
  });

  it("moves between rows with the arrow keys and keeps one row in the tab order", async () => {
    render(
      <DataTable caption="Applications" columns={COLUMNS} rows={ROWS} getRowId={(r) => r.domain} onRowActivate={vi.fn()} />,
    );
    const [first, second, third] = ROWS.map((r) => screen.getByRole("button", { name: r.domain }));
    expect(first).toHaveAttribute("tabindex", "0");
    expect(second).toHaveAttribute("tabindex", "-1");

    first?.focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(second).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(third).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(first).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(first).toHaveFocus();
  });

  it("keeps row actions from opening the row", async () => {
    const onRowActivate = vi.fn();
    const onAction = vi.fn();
    render(
      <DataTable
        caption="Applications"
        columns={COLUMNS}
        rows={ROWS}
        getRowId={(r) => r.domain}
        onRowActivate={onRowActivate}
        rowActions={(row) => <IconButton label={`Restart ${row.domain}`} icon={<span />} tooltip={false} onClick={onAction} />}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Restart api.example.com" }));
    expect(onAction).toHaveBeenCalledOnce();
    expect(onRowActivate).not.toHaveBeenCalled();
    expect(screen.getByRole("columnheader", { name: "Actions" })).toBeInTheDocument();
  });

  it("marks itself busy while loading and shows the empty state when there is nothing", () => {
    const { rerender } = render(
      <DataTable caption="Applications" columns={COLUMNS} rows={[]} getRowId={(r) => r.domain} loading />,
    );
    expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
    rerender(
      <DataTable caption="Applications" columns={COLUMNS} rows={[]} getRowId={(r) => r.domain} empty={<p>No applications yet.</p>} />,
    );
    expect(screen.getByRole("table")).not.toHaveAttribute("aria-busy");
    expect(screen.getByText("No applications yet.")).toBeInTheDocument();
  });

  it("draws as many placeholder rows as it is told to expect, five by default", () => {
    const { rerender } = render(<DataTable caption="Applications" columns={COLUMNS} rows={[]} getRowId={(r) => r.domain} loading />);
    // The header row and the placeholders.
    expect(screen.getAllByRole("row")).toHaveLength(1 + 5);
    rerender(<DataTable caption="Applications" columns={COLUMNS} rows={[]} getRowId={(r) => r.domain} loading skeletonRows={12} />);
    expect(screen.getAllByRole("row")).toHaveLength(1 + 12);
    rerender(<DataTable caption="Applications" columns={COLUMNS} rows={[]} getRowId={(r) => r.domain} loading skeletonRows={0} />);
    expect(screen.getAllByRole("row")).toHaveLength(1 + 1);
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <div>
        <DataTable
          caption="Applications"
          columns={COLUMNS}
          rows={ROWS}
          getRowId={(r) => r.domain}
          defaultSort={{ column: "domain", direction: "ascending" }}
          onRowActivate={vi.fn()}
          rowActions={(row) => <IconButton label={`Actions for ${row.domain}`} icon={<span />} tooltip={false} />}
        />
        <DataTable caption="Loading" columns={COLUMNS} rows={[]} getRowId={(r) => r.domain} loading />
        <DataTable caption="Read only" columns={[{ id: "d", header: "Domain", cell: (r: App) => r.domain }]} rows={ROWS} getRowId={(r) => r.domain} />
      </div>,
    );
    await expectNoAxeViolations(container);
  });

  it("contains its visually hidden labels, so they cannot widen the page past its scroll box", () => {
    render(
      <DataTable
        caption="Applications"
        columns={COLUMNS}
        rows={ROWS}
        getRowId={(r) => r.domain}
        rowActions={(row) => <IconButton label={`Actions for ${row.domain}`} icon={<span />} tooltip={false} />}
      />,
    );
    const region = screen.getByRole("region", { name: "Applications" });
    // The "Actions" header is sr-only (absolutely positioned); its containing block must be
    // the scrolling region itself.
    expect(within(region).getByText("Actions")).toHaveClass("sr-only");
    expect(region).toHaveClass("relative", "overflow-x-auto");
  });

  it("translates its own built-in labels into Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(
      <DataTable
        caption="Applications"
        columns={COLUMNS}
        rows={ROWS}
        getRowId={(r) => r.domain}
        rowActions={(row) => <IconButton label={`Restart ${row.domain}`} icon={<span />} tooltip={false} />}
      />,
    );
    expect(screen.getByRole("columnheader", { name: "Acciones" })).toBeInTheDocument();
  });

  describe("as card rows on a phone", () => {
    interface Row {
      domain: string;
      state: string;
      kind: string;
    }
    const rows: Row[] = [
      { domain: "shop.example.com", state: "Running", kind: "Next.js" },
      { domain: "api.example.com", state: "Failed", kind: "FastAPI" },
    ];
    const columns: Column<Row>[] = [
      { id: "domain", header: "Application", cell: (row) => row.domain },
      { id: "state", header: "State", cell: (row) => row.state, card: "status" },
      { id: "kind", header: "Type", cell: (row) => row.kind },
    ];

    function wide(matches: boolean): void {
      vi.stubGlobal("matchMedia", (query: string) => ({
        matches,
        media: query,
        onchange: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
      }));
    }

    it("turns each row into a card under 640px: state, name, the rest, and its actions always in view", () => {
      wide(false);
      render(
        <DataTable
          mobile="cards"
          caption="Applications"
          columns={columns}
          rows={rows}
          getRowId={(row) => row.domain}
          rowActions={(row) => <IconButton label={`Actions for ${row.domain}`} icon={<span />} />}
        />,
      );
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      const list = screen.getByRole("list", { name: "Applications" });
      const cards = within(list).getAllByRole("listitem");
      expect(cards).toHaveLength(2);
      const first = cards[0];
      if (!first) throw new Error("no card");
      expect(first).toHaveTextContent("Running");
      expect(first).toHaveTextContent("shop.example.com");
      expect(first).toHaveTextContent("Type: Next.js");
      expect(within(first).getByRole("button", { name: "Actions for shop.example.com" })).toBeInTheDocument();
    });

    it("opens a row from its name, as the table does", async () => {
      wide(false);
      const onRowActivate = vi.fn();
      render(
        <DataTable mobile="cards" caption="Applications" columns={columns} rows={rows} getRowId={(row) => row.domain} onRowActivate={onRowActivate} />,
      );
      await userEvent.click(screen.getByRole("button", { name: "api.example.com" }));
      expect(onRowActivate).toHaveBeenCalledWith(rows[1]);
    });

    it("stays a table from 640px", () => {
      wide(true);
      render(<DataTable mobile="cards" caption="Applications" columns={columns} rows={rows} getRowId={(row) => row.domain} />);
      expect(screen.getByRole("table")).toBeInTheDocument();
      expect(screen.queryByRole("list")).not.toBeInTheDocument();
    });

    it("keeps its shape while loading and shows the empty state when there is nothing", () => {
      wide(false);
      const { rerender } = render(
        <DataTable mobile="cards" caption="Applications" columns={columns} rows={[]} getRowId={(row) => row.domain} loading skeletonRows={3} />,
      );
      expect(screen.getByRole("list", { name: "Applications" })).toHaveAttribute("aria-busy", "true");
      rerender(<DataTable mobile="cards" caption="Applications" columns={columns} rows={[]} getRowId={(row) => row.domain} empty={<p>Nothing here</p>} />);
      expect(screen.getByText("Nothing here")).toBeInTheDocument();
    });

    it("has no accessibility violations", async () => {
      wide(false);
      const { container } = render(
        <DataTable
          mobile="cards"
          caption="Applications"
          columns={columns}
          rows={rows}
          getRowId={(row) => row.domain}
          onRowActivate={() => undefined}
          rowActions={(row) => <IconButton label={`Actions for ${row.domain}`} icon={<span />} />}
        />,
      );
      await expectNoAxeViolations(container);
    });
  });
});
