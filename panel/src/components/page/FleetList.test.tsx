import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import type { Column } from "../ui/DataTable";
import { Select } from "../ui/Select";
import { FleetList } from "./FleetList";
import type { FleetServer } from "./fleetServers";

interface Backup {
  app: string;
  server: string;
  last: string;
}

const ROWS: readonly Backup[] = [
  { app: "example.com", server: "web-01", last: "7 h ago" },
  { app: "shop.example.net", server: "web-01", last: "7 h ago" },
  { app: "blog.example.org", server: "web-02", last: "1 d ago" },
  { app: "clientes.example.com", server: "db-02", last: "3 h ago" },
  { app: "lanzamiento.example.org", server: "central-01", last: "Never" },
];

const COLUMNS: readonly Column<Backup>[] = [
  { id: "app", header: "Application", cell: (row) => row.app, sortValue: (row) => row.app },
  { id: "last", header: "Last backup", cell: (row) => row.last },
];

const SERVERS: readonly FleetServer[] = [
  { name: "central-01", central: true },
  { name: "web-01" },
  { name: "web-02", answer: { kind: "stale", since: new Date(Date.now() - 3 * 3_600_000 - 30 * 60_000 - 20_000) } },
  { name: "db-02" },
  { name: "vps-03", answer: { kind: "unsupported", version: "4.2.1" } },
  { name: "nas-01", answer: { kind: "unreachable" } },
];

/** A window wide enough for a table: jsdom has no layout, and under 640px a table is cards. */
function stubWidth(wide: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

function list(props: Partial<Parameters<typeof FleetList<Backup>>[0]> = {}) {
  return (
    <FleetList<Backup>
      scope="all"
      servers={SERVERS}
      rows={ROWS}
      serverOf={(row) => row.server}
      columns={COLUMNS}
      getRowId={(row) => row.app}
      caption="Backups"
      filterLabel="Filter backups"
      count={(shown, total) => `${String(shown)} of ${String(total)} applications`}
      {...props}
    />
  );
}

function bodyApps(): string[] {
  const body = screen.getAllByRole("rowgroup")[1];
  if (!body) throw new Error("no body");
  return within(body)
    .getAllByRole("row")
    .map((row) => within(row).getAllByRole("cell")[0]?.textContent ?? "");
}

describe("FleetList", () => {
  beforeEach(() => {
    stubWidth(true);
  });

  it("is the table it always was, with the server as a column right after the thing each row is", () => {
    render(list());
    const headers = screen.getAllByRole("columnheader").map((header) => header.textContent);
    expect(headers).toEqual(["Application", "Server", "Last backup"]);
    expect(bodyApps()).toHaveLength(5);
    const row = screen.getByRole("row", { name: /clientes\.example\.com/ });
    expect(within(row).getByText("db-02")).toHaveAttribute("translate", "no");
  });

  it("marks this central's own rows with the word central", () => {
    render(list());
    const row = screen.getByRole("row", { name: /lanzamiento\.example\.org/ });
    expect(within(row).getByText("central")).toBeInTheDocument();
  });

  it("sorts by server, as a column that can be asked to", async () => {
    const user = userEvent.setup();
    render(list());
    await user.click(screen.getByRole("button", { name: /Server/ }));
    expect(bodyApps()).toEqual(["lanzamiento.example.org", "clientes.example.com", "example.com", "shop.example.net", "blog.example.org"]);
    await user.click(screen.getByRole("button", { name: /Server/ }));
    expect(bodyApps()).toEqual(["blog.example.org", "example.com", "shop.example.net", "clientes.example.com", "lanzamiento.example.org"]);
  });

  it("filters by server, and each option counts what the server has", async () => {
    const user = userEvent.setup();
    render(list());
    await user.click(screen.getByRole("combobox", { name: "Server" }));
    const options = (await screen.findAllByRole("option")).map((option) => option.textContent);
    // A Noust that does not know the view and a server with nothing have no rows to count.
    expect(options).toEqual(["All servers (5)", "central-01 (1)", "web-01 (2)", "web-02 (1)", "db-02 (1)"]);
    await user.click(screen.getByRole("option", { name: "web-01 (2)" }));
    expect(bodyApps()).toEqual(["example.com", "shop.example.net"]);
  });

  it("tells the count of what is shown against what there is", async () => {
    const user = userEvent.setup();
    render(list());
    expect(screen.getByRole("status")).toHaveTextContent("5 of 5 applications");
    await user.click(screen.getByRole("combobox", { name: "Server" }));
    await user.click(await screen.findByRole("option", { name: "db-02 (1)" }));
    expect(screen.getByRole("status")).toHaveTextContent("1 of 5 applications");
  });

  it("counts what the page's own filters left, not what the server has", async () => {
    const user = userEvent.setup();
    render(list({ rows: ROWS.filter((row) => row.last !== "Never") }));
    await user.click(screen.getByRole("combobox", { name: "Server" }));
    const options = (await screen.findAllByRole("option")).map((option) => option.textContent);
    expect(options).toEqual(["All servers (4)", "central-01 (0)", "web-01 (2)", "web-02 (1)", "db-02 (1)"]);
  });

  it("says that the rows of a server that did not answer are the last it gave, and how old they are", () => {
    render(list());
    const stale = screen.getByRole("row", { name: /blog\.example\.org/ });
    expect(within(stale).getByText("Data from 3h 30m ago")).toBeInTheDocument();
    expect(stale.querySelector("[data-glyph='triangle']")).not.toBeNull();
    // Nobody else wears the mark.
    expect(screen.getAllByText(/Data from/)).toHaveLength(1);
  });

  it("draws neither the column nor the filter for the rows of one server", () => {
    render(list({ scope: "server", servers: [{ name: "web-01" }], rows: ROWS.filter((row) => row.server === "web-01") }));
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Application", "Last backup"]);
    expect(screen.queryByRole("combobox", { name: "Server" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("2 of 2 applications");
  });

  it("keeps the search and the filters of the page beside the server's", async () => {
    const user = userEvent.setup();
    const onSearch = vi.fn();
    render(
      list({
        search: { value: "", onChange: onSearch, label: "Search backups" },
        filters: <Select aria-label="State" value="all" options={[{ value: "all", label: "All states" }]} />,
      }),
    );
    expect(screen.getByRole("search", { name: "Filter backups" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "State" })).toBeInTheDocument();
    await user.type(screen.getByRole("searchbox", { name: "Search backups" }), "x");
    expect(onSearch).toHaveBeenCalledWith("x");
  });

  it("lets the page keep the server in the address when it asks to", async () => {
    const user = userEvent.setup();
    const onServerChange = vi.fn();
    function Page() {
      const [server, setServer] = useState<string | null>("web-01");
      return list({
        server,
        onServerChange: (next) => {
          onServerChange(next);
          setServer(next);
        },
      });
    }
    render(<Page />);
    expect(bodyApps()).toEqual(["example.com", "shop.example.net"]);
    await user.click(screen.getByRole("combobox", { name: "Server" }));
    await user.click(await screen.findByRole("option", { name: "All servers (5)" }));
    expect(onServerChange).toHaveBeenLastCalledWith(null);
    expect(bodyApps()).toHaveLength(5);
  });

  it("says that a server has nothing here, and offers every server again", async () => {
    const user = userEvent.setup();
    render(list({ rows: ROWS.filter((row) => row.server !== "db-02") }));
    await user.click(screen.getByRole("combobox", { name: "Server" }));
    await user.click(await screen.findByRole("option", { name: "db-02 (0)" }));
    expect(screen.getByText("Nothing from db-02 here.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show every server" }));
    expect(bodyApps()).toHaveLength(4);
  });

  it("keeps a server chosen in a shared link that is not in the list any more", async () => {
    const user = userEvent.setup();
    render(list({ server: "web-09" }));
    await user.click(screen.getByRole("combobox", { name: "Server" }));
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toContain("web-09 (0)");
  });

  it("draws the skeleton while the rows have not arrived, and the page's empty state when there are none", () => {
    const { rerender } = render(list({ rows: undefined }));
    expect(screen.getByRole("table")).toHaveAttribute("aria-busy", "true");
    rerender(list({ rows: [], empty: <p>No backups yet.</p> }));
    expect(screen.getByText("No backups yet.")).toBeInTheDocument();
  });

  it("opens a row, as the table does", async () => {
    const user = userEvent.setup();
    const onRowActivate = vi.fn();
    render(list({ onRowActivate }));
    await user.click(screen.getByRole("button", { name: "example.com" }));
    expect(onRowActivate).toHaveBeenCalledWith(ROWS[0]);
  });

  it("says the column and the filter in Spanish", async () => {
    await act(() => setLocale("es"));
    const user = userEvent.setup();
    render(list());
    expect(screen.getAllByRole("columnheader")[1]).toHaveTextContent("Servidor");
    await user.click(screen.getByRole("combobox", { name: "Servidor" }));
    expect((await screen.findAllByRole("option"))[0]).toHaveTextContent("Todos los servidores (5)");
    expect(screen.getByText(/Dato de hace 3 h 30 min/)).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(list());
    await expectNoAxeViolations(container);
  });
});
