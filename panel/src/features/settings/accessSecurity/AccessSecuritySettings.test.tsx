import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { control, planFor, withFrameworksOn } from "../../../test/compliance";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";

/** The controls about getting in, on top of the three the shared fixture has. */
const ACCESS_CONTROLS = [
  control("second_factor"),
  control("password_min_length", { kind: "max", standard: 12, value: 12, operator: 12, floor: 12, unit: "characters", key: "auth.password.min_length" }),
  control("lockout_threshold", { kind: "min", standard: 5, value: 5, operator: 5, floor: null, unit: "attempts", key: "auth.lockout.threshold" }),
  control("lockout_minutes", { kind: "max", standard: 15, value: 15, operator: 15, floor: 15, unit: "minutes", key: "auth.lockout.minutes" }),
  control("session_absolute_hours", { kind: "min", standard: 12, value: 12, operator: 12, floor: null, unit: "hours", key: "auth.session.absolute_hours" }),
  control("master_token", { kind: "choice", standard: "break_glass", value: "break_glass", operator: "break_glass", floor: "break_glass", choices: ["break_glass", "recovery"], key: "security.master_token" }),
];

function stateWith(...frameworks: string[]) {
  const base = withFrameworksOn(...frameworks);
  return { ...base, controls: [...base.controls, ...ACCESS_CONTROLS.filter((entry) => !base.controls.some((existing) => existing.id === entry.id))] };
}

interface World {
  state: ReturnType<typeof stateWith>;
  plans: RecordedCall[];
  changes: RecordedCall[];
}

function routes(world: World, extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/auth/sessions": () => json(200, { active_sessions: 3, current_session: "c0ffee00aa", sessions: [] }),
    "GET /api/config": () => json(200, { config: { web: { ip_whitelist: [] } }, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/compliance": () => json(200, world.state),
    "POST /api/compliance/plan": (call: RecordedCall) => {
      world.plans.push(call);
      return json(200, { ...planFor("ens-media"), effects: [], warnings: [] });
    },
    "PUT /api/compliance": (call: RecordedCall) => {
      world.changes.push(call);
      return json(200, { enabled_before: [], enabled_after: [], settings: {}, state: world.state });
    },
    ...extra,
  };
}

async function pageAt(initial = stateWith(), extra: Record<string, RouteHandler> = {}, title = "Access security") {
  screenWidth(1440);
  const world: World = { state: initial, plans: [], changes: [] };
  fakeBackend(routes(world, extra));
  const harness = renderConsole("/settings/access-security");
  await screen.findByRole("heading", { level: 1, name: title });
  return { ...harness, world };
}

const main = () => screen.getByRole("main");

describe("Settings > Access security", () => {
  it("is three things, each a slab: the door, the sessions and the token for when nothing else works", { timeout: 30_000 }, async () => {
    await pageAt();
    for (const name of ["At the door", "Sessions", "Master access token"]) expect(await screen.findByRole("heading", { level: 2, name })).toBeInTheDocument();
    const door = within(screen.getByRole("region", { name: "At the door" }));
    expect(door.getByRole("switch", { name: "Second factor required" })).not.toBeChecked();
    expect(door.getByText("Shortest password")).toBeInTheDocument();
    // The addresses that may reach the console are the configuration's: said, with where, and not edited here.
    expect(door.getByText("Any address")).toBeInTheDocument();
    expect(door.getByText(/web\.ip_whitelist/)).toBeInTheDocument();
    const sessions = within(screen.getByRole("region", { name: "Sessions" }));
    expect(sessions.getByText("Session idle time")).toBeInTheDocument();
    expect(await sessions.findByText("3 sessions")).toBeInTheDocument();
    expect(sessions.getByRole("link", { name: "Your sessions" })).toHaveAttribute("href", "/account/security");
    const master = within(screen.getByRole("region", { name: "Master access token" }));
    expect(master.getByText("Break-glass")).toBeInTheDocument();
    await expectNoAxeViolations(main());
  });

  it("turns the second factor on at once, as a framework would: the same control, the same write", { timeout: 30_000 }, async () => {
    const { user, world } = await pageAt();
    await user.click(await screen.findByRole("switch", { name: "Second factor required" }));
    await waitFor(() => {
      expect(world.changes[0]?.body).toMatchObject({ controls: { second_factor: true } });
    });
  });

  it("changes how long a session may sit idle in a dialog, and the server judges the number", { timeout: 30_000 }, async () => {
    const { user, world } = await pageAt();
    await user.click(await screen.findByRole("button", { name: "Change… Session idle time" }));
    const dialog = await screen.findByRole("dialog", { name: "Change Session idle time" });
    const field = within(dialog).getByRole("spinbutton");
    await user.clear(field);
    await user.type(field, "20");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(world.changes[0]?.body).toMatchObject({ controls: { session_idle_minutes: 20 } });
    });
  });

  it("shows which framework holds a setting, and keeps it from being loosened here", { timeout: 30_000 }, async () => {
    await pageAt(stateWith("ens-media"));
    const sessions = within(await screen.findByRole("region", { name: "Sessions" }));
    const row = present(sessions.getByText("Session idle time").closest('[data-slot="setting"]'));
    expect(within(row as HTMLElement).getByText("Required by ENS MEDIA")).toBeInTheDocument();
  });

  it("names the addresses the configuration allows", { timeout: 30_000 }, async () => {
    await pageAt(stateWith(), { "GET /api/config": () => json(200, { config: { web: { ip_whitelist: ["203.0.113.0/24", "10.0.0.0/8"] } }, path: "/etc/noust/config.yaml", writable: true }) });
    const door = within(await screen.findByRole("region", { name: "At the door" }));
    expect(await door.findByText("203.0.113.0/24, 10.0.0.0/8")).toBeInTheDocument();
    expect(door.queryByText("Any address")).toBeNull();
  });

  it("says once that it can only be read, and keeps every switch off, for a role that may not change security", { timeout: 30_000 }, async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "security.manage") };
    await pageAt(stateWith(), { ...signedInRoutes(reader) });
    const door = within(await screen.findByRole("region", { name: "At the door" }));
    expect(screen.getByText(/can see this page and not change it/)).toBeInTheDocument();
    expect(door.queryByRole("button", { name: /^Change…/ })).toBeNull();
    const second = door.getByRole("switch", { name: "Second factor required" });
    expect(second.hasAttribute("disabled") || second.getAttribute("aria-disabled") === "true").toBe(true);
  });

  it("does not ask for the open sessions of a role that may not read the accounts", { timeout: 30_000 }, async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "accounts.read") };
    await pageAt(stateWith(), { ...signedInRoutes(reader) });
    const sessions = within(await screen.findByRole("region", { name: "Sessions" }));
    expect(sessions.queryByText("Open now")).toBeNull();
  });

  it("reads in Spanish", { timeout: 30_000 }, async () => {
    await act(() => setLocale("es"));
    await pageAt(stateWith(), {}, "Seguridad del acceso");
    expect(await screen.findByRole("heading", { level: 2, name: "En la puerta" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Token de acceso maestro" })).toBeInTheDocument();
    expect(screen.getByText("Cualquier dirección")).toBeInTheDocument();
    await expectNoAxeViolations(main());
  });
});

/** A value that has to be there, or the test says so. */
function present<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) throw new Error("expected a value");
  return value;
}
