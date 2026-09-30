import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
import { callFromSnapshot, validateApprovalsSearch } from "./data";

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

const NOW = Date.now() / 1000;

function request(id: number, extra: Record<string, unknown> = {}) {
  return {
    id,
    action: "root_equivalent",
    kind: "infrastructure",
    description: "Write a unit file: it runs as root",
    method: "PUT",
    path: "/api/services/worker/config",
    parameters: { method: "PUT", path: "/api/services/worker/config", query: [], body: { content: "[Service]\nExecStart=/usr/bin/worker" } },
    fingerprint: "9f86d081884c7d65",
    reason: "The worker needs a new flag",
    state: "requested",
    requester: { kind: "account", name: "dani", role: "admin" },
    created_at: NOW - 600,
    expires_at: NOW + 86_000,
    mine: false,
    can_decide: true,
    ...extra,
  };
}

const DECIDER = {
  ...SESSION,
  grant: null,
  role: "security",
  account: { id: 2, username: "bea", display_name: "", role: "security", status: "active", mfa_enabled: true, passkeys: 0, backup_codes_remaining: 8, failures_since_login: 0, created_at: NOW },
};

describe("the approvals inbox", () => {
  it("shows what waits, the call exactly as asked, and approves it with a comment", { timeout: 20_000 }, async () => {
    let elevated = false;
    const backend = fakeBackend({
      ...signedInRoutes(DECIDER),
      "GET /api/approvals/policy": () => json(200, { enabled: true, approvers: ["security"], request_hours: 24, execute_minutes: 30, reason_required: true, rules: [] }),
      "GET /api/approvals": () => json(200, { approvals: [request(7), request(6, { state: "rejected", can_decide: false })], pending: 1 }),
      "POST /api/auth/elevate": () => {
        elevated = true;
        return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
      },
      "POST /api/approvals/7/approve": (call: RecordedCall) =>
        elevated ? json(200, request(7, { state: "approved", decision_comment: (call.body as { comment: string }).comment })) : problem(403, "elevation_required", "Confirm it's you"),
    });
    const { user, container } = renderConsole("/settings/approvals");
    const table = await screen.findByRole("region", { name: "Approval requests" });
    expect(await within(table).findByText("Write a unit file: it runs as root")).toBeInTheDocument();
    // Waiting ones by default: the decided one is behind "Decided".
    expect(within(table).queryByText("Rejected")).toBeNull();
    expect(screen.getByText("1 request")).toBeInTheDocument();
    await expectNoAxeViolations(container);

    await user.click(within(table).getByRole("button", { name: "Open request 7" }));
    const drawer = await screen.findByRole("dialog", { name: "Request 7" });
    expect(within(drawer).getByText(/ExecStart=\/usr\/bin\/worker/)).toBeInTheDocument();
    expect(within(drawer).getByText("The worker needs a new flag")).toBeInTheDocument();
    await user.type(within(drawer).getByLabelText(/^Comment/), "Checked with dani");
    await user.click(within(drawer).getByRole("button", { name: "Approve" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(confirm).getByLabelText("Password"), "pw");
    await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
    await user.click(within(confirm).getByRole("button", { name: "Confirm" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/approvals/7/approve").at(-1)?.body).toEqual({ comment: "Checked with dani" });
    });
  });

  it("lets the requester run an approved call from here, sent exactly as it was", { timeout: 20_000 }, async () => {
    const backend = fakeBackend({
      ...signedInRoutes(DECIDER),
      "GET /api/approvals/policy": () => json(200, { enabled: true, approvers: ["security"], request_hours: 24, execute_minutes: 30, reason_required: false, rules: [] }),
      "GET /api/approvals": () => json(200, { approvals: [request(9, { state: "approved", mine: true, can_decide: false, execute_by: NOW + 1200 })], pending: 0 }),
      "PUT /api/services/worker/config": () => json(200, { saved: true }),
    });
    const { user } = renderConsole("/settings/approvals?view=decided");
    await user.click(await screen.findByRole("button", { name: "Open request 9" }));
    const drawer = await screen.findByRole("dialog", { name: "Request 9" });
    await user.click(within(drawer).getByRole("button", { name: "Run it now" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/services/worker/config")).toHaveLength(1);
    });
    const [call] = backend.callsTo("PUT /api/services/worker/config");
    expect(call?.headers.get("X-Noust-Approval")).toBe("9");
    expect(call?.body).toEqual({ content: "[Service]\nExecStart=/usr/bin/worker" });
  });

  it("says when approvals are off here", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/approvals/policy": () => json(200, { enabled: false, approvers: ["security"], request_hours: 24, execute_minutes: 30, reason_required: false, rules: [] }),
      "GET /api/approvals": () => json(200, { approvals: [], pending: 0 }),
    });
    renderConsole("/settings/approvals");
    expect(await screen.findByText("Approvals are off on this server")).toBeInTheDocument();
    expect(await screen.findByText("Nothing waits for approval.")).toBeInTheDocument();
  });
});

describe("a call that needs a second person", () => {
  it("asks why, files the request, and leaves it waiting without running anything", { timeout: 20_000 }, async () => {
    const backend = fakeBackend({
      ...signedInRoutes(DECIDER),
      "GET /api/approvals/policy": () => json(200, { enabled: true, approvers: ["security"], request_hours: 24, execute_minutes: 30, reason_required: true, rules: [] }),
      "GET /api/approvals/12": () => json(200, request(12, { mine: true, can_decide: false })),
      "POST /api/cron": (call: RecordedCall) =>
        call.headers.has("X-Noust-Reason")
          ? json(
              202,
              { error: "approval_required", detail: "Request 12 is waiting.", hint: null, fields: { approval: "12", state: "requested" }, output: null },
              { "X-Noust-Approval-Request": "12" },
            )
          : problem(400, "approval_reason_required", "Say why: this call needs a second person's approval, and a reason"),
    });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    let outcome: unknown = null;
    await act(async () => {
      void api("POST", "/api/cron", { name: "nightly" }).catch((error: unknown) => {
        outcome = error;
      });
      await Promise.resolve();
    });
    const ask = await screen.findByRole("dialog", { name: "This needs a second person's approval" });
    expect(within(ask).getByText(/POST \/api\/cron/)).toBeInTheDocument();
    await user.click(within(ask).getByRole("button", { name: "Ask for approval" }));
    expect(await within(ask).findByText("Say why you need it.")).toBeInTheDocument();
    await user.type(within(ask).getByLabelText(/^Reason/), "Nightly report for finance");
    await user.click(within(ask).getByRole("button", { name: "Ask for approval" }));

    const waiting = await screen.findByRole("dialog", { name: "Waiting for approval" });
    await expectNoAxeViolations(waiting);
    await user.click(within(waiting).getByRole("button", { name: "Leave it waiting" }));
    await waitFor(() => {
      expect(outcome).toMatchObject({ error: "approval_pending", approvalId: "12" });
    });
    expect(decodeURIComponent(backend.callsTo("POST /api/cron")[1]?.headers.get("X-Noust-Reason") ?? "")).toBe("Nightly report for finance");
    expect(backend.callsTo("POST /api/cron")).toHaveLength(2);
  });
});

describe("running a held call again", () => {
  it("rebuilds the call from its snapshot, query and node included", () => {
    expect(
      callFromSnapshot({
        method: "POST",
        path: "/api/nodes/web-2/api/databases/query",
        parameters: { method: "POST", path: "/api/nodes/web-2/api/databases/query", query: [["engine", "postgresql"]], body: { sql: "DELETE FROM carts", mode: "write" } },
      }),
    ).toEqual({ method: "POST", target: "/api/nodes/web-2/api/databases/query?engine=postgresql", body: { sql: "DELETE FROM carts", mode: "write" }, node: "web-2" });
  });

  it("refuses a snapshot a secret was taken out of, or one too large to hold", () => {
    expect(callFromSnapshot({ method: "PUT", path: "/api/apps/x/env", parameters: { body: { variables: { TOKEN: "***" } } } })).toBeNull();
    expect(callFromSnapshot({ method: "PUT", path: "/api/x", parameters: { body: { bytes: 90000, sha256: "ab", shown: false } } })).toBeNull();
    expect(callFromSnapshot({ method: "GET", path: "/api/x", parameters: {} })).toBeNull();
  });

  it("keeps the inbox's filters in the URL, and nothing else", () => {
    expect(validateApprovalsSearch({ view: "decided", mine: "true", other: 1 })).toEqual({ view: "decided", mine: true });
    expect(validateApprovalsSearch({ view: "nonsense" })).toEqual({});
  });
});
