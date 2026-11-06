import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";

/** A screen as wide as a desktop's: the header's actions are beside the title. */
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

function config(notifications: Record<string, unknown>) {
  return { config: { notifications, monitor: { disk_threshold: 90 } }, path: "/etc/noust/config.yaml", writable: true };
}

function alerts(notifications: Record<string, unknown>, extra: Record<string, RouteHandler> = {}, permissions?: string[]) {
  let elevated = false;
  return fakeBackend({
    ...signedInRoutes(permissions === undefined ? SESSION : { ...SESSION, permissions }),
    "GET /api/alerts/rules": () => json(200, { items: [], metrics: [], firing: 0 }),
    "GET /api/config": () => json(200, config(notifications)),
    "POST /api/auth/elevate": () => {
      elevated = true;
      return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
    },
    "PATCH /api/config": (call) => {
      if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue.");
      const body = call.body as { path: string; value: unknown };
      return json(200, { message: `Configuration '${body.path}' updated`, path: "/etc/noust/config.yaml", value: body.value });
    },
    ...extra,
  });
}

/** What the page wrote to the configuration, by key: a write that asks "Confirm it's you" is asked twice, and counted once. */
function written(backend: ReturnType<typeof fakeBackend>): string[] {
  return [...new Set(backend.callsTo("PATCH /api/config").map((call) => (call.body as { path: string }).path))];
}

async function confirmItsYou(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
  await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
  await user.click(within(confirm).getByRole("button", { name: "Continue" }));
}

describe("Silence notifications, in the header of Alerts", () => {
  it("offers the question of until when to whoever is looking at what fires, and writes the answer", { timeout: 30_000 }, async () => {
    const backend = alerts({ enabled: true });
    const { user } = renderConsole("/alerts");
    await screen.findByRole("heading", { level: 1, name: "Alerts" });
    await user.click(await screen.findByRole("button", { name: "Silence notifications…" }));
    const dialog = await screen.findByRole("dialog", { name: "Silence the notifications until when?" });
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Silence" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(written(backend)).toEqual(["notifications.muted_until"]);
    });
  });

  it("says until when while they are silenced, and turns them on again from there", { timeout: 30_000 }, async () => {
    const until = new Date(Date.now() + 2 * 3_600_000).toISOString();
    const backend = alerts({ enabled: true, muted_until: until });
    const { user } = renderConsole("/alerts");
    expect(await screen.findByText(/^Silenced until /)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Silence notifications…" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Turn on now" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(written(backend)).toEqual(["notifications.muted_until"]);
    });
    expect(backend.callsTo("PATCH /api/config").at(-1)?.body).toEqual({ path: "notifications.muted_until", value: "" });
  });

  it("draws nothing while notifications are off altogether: there is nothing to silence", { timeout: 30_000 }, async () => {
    alerts({ enabled: false });
    renderConsole("/alerts");
    await screen.findByRole("heading", { level: 1, name: "Alerts" });
    // The configuration is read before the button could be drawn: wait for that, then look.
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Silence notifications…" })).toBeNull();
    });
    expect(screen.queryByText(/^Silenced until /)).toBeNull();
  });

  it("is not drawn for a role that may not change settings", { timeout: 30_000 }, async () => {
    alerts({ enabled: true }, {}, (SESSION.permissions ?? []).filter((permission) => permission !== "settings.manage"));
    renderConsole("/alerts");
    await screen.findByRole("heading", { level: 1, name: "Alerts" });
    expect(screen.queryByRole("button", { name: "Silence notifications…" })).toBeNull();
  });
});
