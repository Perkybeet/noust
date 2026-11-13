import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { screenWidth } from "../../features/app/testRoutes";
import { expectNoAxeViolations } from "../../test/axe";
import { PolicyCell, PolicyMatrix } from "./PolicyMatrix";
import type { PolicyCellModel, PolicyRowModel } from "./PolicyMatrix";

function cell(overrides: Partial<PolicyCellModel> = {}): PolicyCellModel {
  return { caller: "token", environment: "production", value: "alone", default: "alone", offered: ["alone", "propose", "never"], fixed: false, reason: null, legacy: false, ...overrides };
}

function row(permission: string, label: string, key: boolean, overrides: Partial<Record<string, Partial<PolicyCellModel>>> = {}): PolicyRowModel {
  const make = (caller: "token" | "assistant", environment: "production" | "other") =>
    cell({ caller, environment, value: caller === "token" ? "alone" : "propose", default: caller === "token" ? "alone" : "propose", offered: key ? ["propose", "never"] : ["alone", "propose", "never"], ...(overrides[`${caller}.${environment}`] ?? {}) });
  return { permission, label, key, cells: [make("token", "production"), make("token", "other"), make("assistant", "production"), make("assistant", "other")] };
}

beforeEach(() => {
  screenWidth(1440);
});

describe("PolicyCell", () => {
  it("offers exactly the values the server offers: a row that asks for confirmation has no 'Does it' in its menu", async () => {
    render(<PolicyCell rowLabel="Create applications" cell={cell({ value: "propose", default: "propose", offered: ["propose", "never"] })} onChange={() => undefined} />);
    await userEvent.click(screen.getByRole("button", { name: /Create applications, A token, Production: Proposes it/ }));
    const items = (await screen.findAllByRole("menuitem")).map((item) => item.textContent);
    expect(items).toEqual([expect.stringContaining("Proposes it"), expect.stringContaining("Cannot")]);
    expect(screen.queryByRole("menuitem", { name: /Does it/ })).toBeNull();
  });

  it("chooses a value and tells the caller of it", async () => {
    const onChange = vi.fn();
    render(<PolicyCell rowLabel="Back up now" cell={cell({ caller: "assistant", value: "propose", default: "propose" })} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Back up now, The AI assistant/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Does it/ }));
    expect(onChange).toHaveBeenCalledWith("alone");
  });

  it("says the factory's value only where the cell differs from it", () => {
    const { rerender } = render(<PolicyCell rowLabel="Back up now" cell={cell()} onChange={() => undefined} />);
    expect(screen.queryByText(/Factory/)).toBeNull();
    rerender(<PolicyCell rowLabel="Back up now" cell={cell({ value: "never" })} onChange={() => undefined} />);
    expect(screen.getByText("Factory: does it")).toBeInTheDocument();
  });

  it("draws the assistant's floor as a padlock that opens nothing", () => {
    render(<PolicyCell rowLabel="The server's terminal" cell={cell({ caller: "assistant", value: "never", default: "never", fixed: true, reason: "assistant_floor", offered: ["never"] })} onChange={() => undefined} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/fixed, it is never offered/)).toBeInTheDocument();
  });

  it("says that a token issued to act in sudo mode keeps 'Does it' as it has always had it, and does not offer it again", () => {
    render(<PolicyCell rowLabel="Create applications" cell={cell({ value: "alone", default: "alone", offered: ["propose", "never"], legacy: true })} onChange={() => undefined} />);
    expect(screen.getByText("As it has always been: choose another value and it does not come back.")).toBeInTheDocument();
  });

  it("is read-only without a handler", () => {
    render(<PolicyCell rowLabel="Back up now" cell={cell()} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByLabelText(/Back up now, A token, Production: Does it/)).toBeInTheDocument();
  });
});

describe("PolicyMatrix", () => {
  const rows = [row("apps.operate", "Start, stop and restart", false), row("apps.manage", "Create applications", true), row("server.terminal", "The server's terminal", true, { "assistant.production": { value: "never", default: "never", fixed: true, reason: "assistant_floor", offered: ["never"] } })];

  it("is one table with the permission and four cells, the key beside a row that asks for confirmation", async () => {
    const { container } = render(<PolicyMatrix rows={rows} caption="Approval policy, Applications" onChange={() => undefined} />);
    const table = screen.getByRole("table", { name: "Approval policy, Applications" });
    expect(within(table).getAllByRole("row")).toHaveLength(2 + rows.length);
    expect(within(table).getAllByText("It asks you to confirm it is you: it is never done on its own").length).toBeGreaterThan(0);
    expect(within(table).getByText("apps.operate")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("reports which row and which cell changed", async () => {
    const onChange = vi.fn();
    render(<PolicyMatrix rows={rows} caption="Approval policy, Applications" onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Start, stop and restart, The AI assistant, Other environments/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Does it/ }));
    expect(onChange).toHaveBeenCalledTimes(1);
    const [changed, changedCell, value] = onChange.mock.calls[0] as [PolicyRowModel, PolicyCellModel, string];
    expect(changed.permission).toBe("apps.operate");
    expect([changedCell.caller, changedCell.environment, value]).toEqual(["assistant", "other", "alone"]);
  });
});
