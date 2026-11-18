import { describe, expect, it } from "vitest";

import type { PolicyCellInfo, PolicyRow, PolicyTable } from "../../../api/queries/callerPolicy";
import { cellKey, changeBody, effectiveEdits, rowChanged, rowModel } from "./policyModel";

function cell(caller: "token" | "assistant", environment: "production" | "other", over: Partial<PolicyCellInfo> = {}): PolicyCellInfo {
  return { caller, environment, value: caller === "token" ? "alone" : "propose", default: caller === "token" ? "alone" : "propose", chosen: null, offered: ["alone", "propose", "never"], fixed: false, reason: null, legacy: false, ...over };
}

function row(permission: string, over: Partial<PolicyRow> = {}): PolicyRow {
  return {
    permission,
    area: permission.split(".")[0] ?? permission,
    description: permission,
    key: false,
    changed: false,
    operations: { total: 10, strong: 1, person: 0 },
    cells: [cell("token", "production"), cell("token", "other"), cell("assistant", "production"), cell("assistant", "other")],
    ...over,
  };
}

function table(rows: PolicyRow[]): PolicyTable {
  return { values: ["alone", "propose", "never"], callers: ["token", "assistant"], environments: ["production", "other"], profile: { token_production_approval: false }, floor: { assistant_never: [], assistant_alone: [] }, rows, chosen: 0 };
}

/** The one row of a table a test builds. */
function only(policy: PolicyTable): PolicyRow {
  const [first] = policy.rows;
  if (first === undefined) throw new Error("the table has no row");
  return first;
}

describe("the edits of the policy", () => {
  const saved = table([row("apps.operate", { cells: [cell("token", "production"), cell("token", "other"), cell("assistant", "production", { chosen: "propose" }), cell("assistant", "other")] })]);

  it("count only what differs from what is saved: putting a cell back where it was is not a change", () => {
    const key = cellKey("apps.operate", "assistant", "other");
    expect(effectiveEdits(saved, { [key]: "propose" })).toEqual([]);
    expect(effectiveEdits(saved, { [key]: "alone" })).toEqual([key]);
  });

  it("say a cell put back to the factory's value is a reset and any other is a value", () => {
    const body = changeBody(saved, {
      [cellKey("apps.operate", "assistant", "production")]: "propose",
      [cellKey("apps.operate", "assistant", "other")]: "never",
      [cellKey("apps.operate", "token", "production")]: "propose",
    });
    // The assistant's production cell was chosen as "propose", which is also its factory value: nothing differs, nothing is sent.
    expect(body.cells).toEqual([
      { caller: "assistant", environment: "other", permission: "apps.operate", value: "never" },
      { caller: "token", environment: "production", permission: "apps.operate", value: "propose" },
    ]);
    expect(body.reset).toEqual([]);
  });

  it("reset a choice that is moved back to the factory's value", () => {
    const chosen = table([row("apps.operate", { cells: [cell("token", "production", { value: "never", default: "alone", chosen: "never" }), cell("token", "other"), cell("assistant", "production"), cell("assistant", "other")] })]);
    const body = changeBody(chosen, { [cellKey("apps.operate", "token", "production")]: "alone" });
    expect(body.reset).toEqual([{ caller: "token", environment: "production", permission: "apps.operate" }]);
    expect(body.cells).toEqual([]);
  });

  it("show the edited value on top of the saved one, and keep the offer the server made", () => {
    const model = rowModel(only(saved), { [cellKey("apps.operate", "assistant", "other")]: "alone" }, "Start and stop", null);
    const edited = model.cells.find((c) => c.caller === "assistant" && c.environment === "other");
    expect(edited?.value).toBe("alone");
    expect(edited?.default).toBe("propose");
    expect(edited?.offered).toEqual(["alone", "propose", "never"]);
  });

  it("say, in the assistant's cells only, how many of the row's operations a person decides whatever the cell says", () => {
    const mixed = table([row("apps.deploy", { operations: { total: 5, strong: 1, person: 2 } })]);
    const model = rowModel(only(mixed), {}, "Deploy", null);
    expect(model.cells.filter((c) => c.caller === "assistant").map((c) => [c.person, c.personAll])).toEqual([
      [2, false],
      [2, false],
    ]);
    // A token is not the assistant: it does what its cell says, so the line is not drawn for it.
    expect(model.cells.filter((c) => c.caller === "token").map((c) => c.person)).toEqual([0, 0]);
    // The server offered "Does it" for the rest of the row, and it is still offered.
    expect(model.cells.find((c) => c.caller === "assistant")?.offered).toEqual(["alone", "propose", "never"]);
  });

  it("do not offer the assistant 'Does it' in a row where every operation is a person's decision, and keep what the server offered otherwise", () => {
    const whole = table([row("apps.freeze", { operations: { total: 2, strong: 0, person: 2 } })]);
    const cells = rowModel(only(whole), {}, "Freeze deploys", null).cells;
    expect(cells.filter((c) => c.caller === "assistant").map((c) => [c.personAll, c.offered])).toEqual([
      [true, ["propose", "never"]],
      [true, ["propose", "never"]],
    ]);
    expect(cells.filter((c) => c.caller === "token").every((c) => c.offered.includes("alone"))).toBe(true);
    // Nothing for a row with no such operation: the same offer as ever.
    const none = rowModel(only(table([row("apps.operate")])), {}, "x", null).cells.find((c) => c.caller === "assistant");
    expect(none?.person).toBe(0);
    expect(none?.personAll).toBe(false);
  });

  it("keep a row in the 'Changed' view while it has a choice or an edit", () => {
    expect(rowChanged(row("a.b"), {})).toBe(false);
    expect(rowChanged(row("a.b", { changed: true }), {})).toBe(true);
    expect(rowChanged(row("a.b"), { [cellKey("a.b", "token", "other")]: "never" })).toBe(true);
  });

  it("treat a value the console does not know as the strictest, never as one that lets more through", () => {
    const strange = table([row("a.b", { cells: [cell("token", "production", { value: "sometimes" })] })]);
    expect(rowModel(only(strange), {}, "x", null).cells[0]?.value).toBe("never");
  });
});
