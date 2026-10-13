import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../test/axe";
import { setLocale } from "./locale";
import { CommandPalette, filterCommands } from "./CommandPalette";
import type { Command } from "./CommandPalette";

function commands(run = vi.fn()): Command[] {
  return [
    { id: "p1", group: "Pages", label: "Overview", kind: "navigate", run },
    { id: "p2", group: "Pages", label: "Databases", keywords: "postgres mysql", kind: "navigate", run },
    { id: "a1", group: "Applications", label: "shop.example.com", status: "running", kind: "navigate", run },
    { id: "a2", group: "Applications", label: "admin.example.com", status: "failed", kind: "navigate", run },
    { id: "x1", group: "Actions", label: "Keyboard shortcuts", shortcut: ["?"], kind: "action", run },
  ];
}

describe("filterCommands", () => {
  it("puts the best match first, across groups", () => {
    const [first] = filterCommands(commands(), "shop");
    expect(first?.group).toBe("Applications");
    expect(first?.items.map((item) => item.label)).toEqual(["shop.example.com"]);
  });

  it("finds a command by its keywords", () => {
    const results = filterCommands(commands(), "postgres");
    expect(results.flatMap((group) => group.items.map((item) => item.label))).toEqual(["Databases"]);
  });

  it("matches scattered letters only when there are several", () => {
    expect(filterCommands(commands(), "ovw").flatMap((group) => group.items.map((item) => item.label))).toEqual(["Overview"]);
    expect(filterCommands(commands(), "zq")).toEqual([]);
  });
});

/** One of each kind of resource, as the console lists them. */
function resources(run = vi.fn()): Command[] {
  return [
    { id: "s1", group: "Settings", label: "API tokens settings", kind: "navigate", run },
    { id: "d1", group: "Databases", label: "shop_prod", detail: "PostgreSQL", kind: "navigate", run },
    { id: "w1", group: "Domains", label: "shop.example.com", detail: "nginx", kind: "navigate", run },
    { id: "c1", group: "Certificates", label: "shop.example.com", detail: "29 days left", kind: "navigate", run },
    { id: "v1", group: "Services", label: "noust-web", status: "running", kind: "navigate", run },
    { id: "r1", group: "Servers", label: "Switch to web-2", kind: "navigate", run },
    { id: "y1", group: "Deployments", label: "shop.example.com: deploy 12", status: "failed", detail: "c07d5e3", kind: "navigate", run },
    { id: "x1", group: "Actions", label: "New application", kind: "navigate", run },
  ];
}

describe("filterCommands over every kind of resource", () => {
  it("shows only what the operator can browse until something is typed", () => {
    const browsed = filterCommands([...commands(), ...resources()], "").map((group) => group.group);
    expect(browsed).toEqual(["Pages", "Settings", "Applications", "Servers", "Actions"]);
  });

  it("finds each kind once something is typed, and the group name finds them all", () => {
    const all = [...commands(), ...resources()];
    const find = (query: string) => filterCommands(all, query).flatMap((group) => group.items.map((item) => item.id));
    expect(find("shop_prod")).toEqual(["d1"]);
    expect(find("noust-web")).toEqual(["v1"]);
    // What is shown beside a name is searched as well.
    expect(find("c07d5e3")).toEqual(["y1"]);
    // The same name in three places: one row per kind, each in its own group.
    expect(find("shop.example.com").sort()).toEqual(["a1", "c1", "w1", "y1"]);
    expect(find("databases")).toContain("d1");
  });

  it("orders the groups as the console does, best match first once something is typed", () => {
    const all = [...commands(), ...resources()];
    expect(filterCommands(all, "shop.example.com").map((group) => group.group)[0]).toBe("Applications");
  });
});

describe("CommandPalette", () => {
  it("names each group and shows what tells two resources of one name apart", async () => {
    const user = userEvent.setup();
    render(<CommandPalette open onOpenChange={() => undefined} commands={[...commands(), ...resources()]} />);
    await user.type(await screen.findByRole("combobox"), "shop.example.com");
    const listbox = screen.getByRole("listbox");
    expect(within(listbox).getAllByRole("group").map((group) => group.getAttribute("aria-labelledby"))).toHaveLength(4);
    const options = within(listbox).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["shop.example.comRunning", "shop.example.comnginx", "shop.example.com29 days left", "shop.example.com: deploy 12c07d5e3Failed"]);
  });

  it("opens a resource with the keyboard and with a click", async () => {
    const user = userEvent.setup();
    const run = vi.fn();
    render(<CommandPalette open onOpenChange={() => undefined} commands={resources(run)} />);
    await user.type(await screen.findByRole("combobox"), "noust-web");
    await user.keyboard("{Enter}");
    expect(run).toHaveBeenCalledOnce();
    await user.clear(screen.getByRole("combobox"));
    await user.type(screen.getByRole("combobox"), "shop_prod");
    await user.click(screen.getByRole("option", { name: /shop_prod/ }));
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("does not translate the names of things on the machine", async () => {
    const user = userEvent.setup();
    render(<CommandPalette open onOpenChange={() => undefined} commands={resources()} />);
    await user.type(await screen.findByRole("combobox"), "shop_prod");
    expect(screen.getByText("shop_prod")).toHaveAttribute("translate", "no");
  });

  it("has no accessibility violations with every kind of resource", async () => {
    const user = userEvent.setup();
    render(<CommandPalette open onOpenChange={() => undefined} commands={[...commands(), ...resources()]} />);
    await user.type(await screen.findByRole("combobox"), "shop");
    await expectNoAxeViolations(document.body);
  });

  it("is a combobox whose active option is announced through aria-activedescendant", async () => {
    const user = userEvent.setup();
    render(<CommandPalette open onOpenChange={() => undefined} commands={commands()} />);
    const input = await screen.findByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
    // The dialog hands focus over after it mounts, a frame later than the input being in the document.
    await waitFor(() => {
      expect(input).toHaveFocus();
    });
    const first = screen.getByRole("option", { name: "Overview" });
    expect(input).toHaveAttribute("aria-activedescendant", first.id);
    expect(first).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowDown}");
    const second = screen.getByRole("option", { name: "Databases" });
    expect(input).toHaveAttribute("aria-activedescendant", second.id);
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(input).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: "Keyboard shortcuts" }).id);
  });

  it("filters as the operator types and runs the chosen command with Enter", async () => {
    const user = userEvent.setup();
    const run = vi.fn();
    const onOpenChange = vi.fn();
    render(<CommandPalette open onOpenChange={onOpenChange} commands={commands(run)} />);
    await user.type(await screen.findByRole("combobox"), "admin");
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["admin.example.comFailed"]);
    await user.keyboard("{Enter}");
    expect(run).toHaveBeenCalledOnce();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("says when nothing matches, without an empty listbox", async () => {
    const user = userEvent.setup();
    render(<CommandPalette open onOpenChange={() => undefined} commands={commands()} />);
    const input = await screen.findByRole("combobox");
    await user.type(input, "nothing like this");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText('Nothing matches "nothing like this".')).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    render(<CommandPalette open onOpenChange={() => undefined} commands={commands()} />);
    await screen.findByRole("combobox");
    await expectNoAxeViolations(document.body);
  });

  it("speaks Spanish once the language switches", async () => {
    const user = userEvent.setup();
    await act(async () => {
      await setLocale("es");
    });
    render(<CommandPalette open onOpenChange={() => undefined} commands={commands()} />);
    const input = await screen.findByRole("combobox", { name: "Buscar apps, bases de datos, dominios, servidores, ajustes y acciones" });
    await user.type(input, "algo que no existe");
    expect(screen.getByText('Nada coincide con "algo que no existe".')).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });
});
