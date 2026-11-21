/**
 * Settings > Approval policy as a person reaches it: from the page that lists the requests, in the same tab, without reloading.
 * Two pages asked two different questions of the server under one cache key, so the second read the first's answer and drew a
 * table from a document that has no rows: "This page broke / Cannot read properties of undefined (reading 'filter')".
 */

import { act, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PolicyCellInfo, PolicyRow, PolicyTable } from "../../../api/queries/callerPolicy";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import { findLoadedRegion } from "../../../test/regions";

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
});

const cell = (caller: "token" | "assistant", environment: "production" | "other"): PolicyCellInfo => ({
  caller,
  environment,
  value: caller === "token" ? "alone" : "propose",
  default: caller === "token" ? "alone" : "propose",
  chosen: null,
  offered: ["alone", "propose", "never"],
  fixed: false,
  reason: null,
  legacy: false,
});

const ROW: PolicyRow = {
  permission: "apps.deploy",
  area: "apps",
  description: "Deploy applications",
  key: false,
  changed: false,
  operations: { total: 4, strong: 0, person: 0 },
  cells: [cell("token", "production"), cell("token", "other"), cell("assistant", "production"), cell("assistant", "other")],
};

const TABLE: PolicyTable = {
  values: ["alone", "propose", "never"],
  callers: ["token", "assistant"],
  environments: ["production", "other"],
  profile: { token_production_approval: false },
  floor: { assistant_never: [], assistant_alone: [] },
  rows: [ROW],
  chosen: 0,
};

const ADMIN = { ...SESSION, grant: null, role: "admin", permissions: ["self", "apps.read", "settings.read", "settings.manage", "security.manage", "accounts.read"] };

function serve() {
  return fakeBackend({
    ...signedInRoutes(ADMIN),
    // What the Approvals page asks: whether approvals apply and who decides. No rows, and no cells.
    "GET /api/approvals/policy": () => json(200, { enabled: true, approvers: ["security"], request_hours: 24, execute_minutes: 30, reason_required: false, rules: [] }),
    "GET /api/approvals": () => json(200, { approvals: [], pending: 0 }),
    // What the Approval policy page asks: the table of who calls.
    "GET /api/config/approval-policy": () => json(200, TABLE),
  });
}

describe("Approval policy, reached from Approvals in the same tab", () => {
  it("draws its table and not the page that broke: the two pages do not share an answer", { timeout: 30_000 }, async () => {
    serve();
    const { router } = renderConsole("/settings/approvals");
    await findLoadedRegion("Approval requests");
    // Nothing is reloaded: the first page's answers are in the cache when the second asks for its own.
    await act(() => router.navigate({ to: "/settings/approval-policy" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Approval policy" })).toBeInTheDocument();
    expect((await screen.findAllByText("Update, roll back and rebuild")).length).toBeGreaterThan(0);
    expect(screen.queryByText("This page broke")).not.toBeInTheDocument();
  });

  it("and the other way round: Approvals still reads its own policy after the table was read", { timeout: 30_000 }, async () => {
    serve();
    const { router } = renderConsole("/settings/approval-policy");
    expect((await screen.findAllByText("Update, roll back and rebuild")).length).toBeGreaterThan(0);
    await act(() => router.navigate({ to: "/settings/approvals" }));
    await findLoadedRegion("Approval requests");
    expect(screen.queryByText("This page broke")).not.toBeInTheDocument();
  });
});
