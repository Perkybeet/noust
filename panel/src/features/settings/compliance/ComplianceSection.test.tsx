import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { COMPLIANCE, planFor, withFrameworksOn } from "../../../test/compliance";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";

/** A value that has to be there, or the test says so. */
function present<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) throw new Error("expected a value");
  return value;
}

interface World {
  state: ReturnType<typeof withFrameworksOn>;
  plans: RecordedCall[];
  changes: RecordedCall[];
}

function routes(world: World, extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/auth/2fa": () => json(200, { enabled: true, pending: false, backup_codes_remaining: 8 }),
    "GET /api/auth/sessions": () => json(200, { active_sessions: 1, current_session: "c0ffee00aa", sessions: [] }),
    "GET /api/config": () => json(200, { config: { web: { max_failed_attempts: 5, lockout_duration: 900 } }, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/auth/passkeys": () => json(200, { passkeys: [], availability: { supported: false, reason: "ip_address", detail: "", hint: "" }, allow_synced: true }),
    "GET /api/approvals/policy": () => json(200, { enabled: false, approvers: ["security"], request_hours: 24, execute_minutes: 30, reason_required: false, rules: [] }),
    "GET /api/ens/profile": () => json(200, { profile: "standard", baseline: [] }),
    "GET /api/compliance": () => json(200, world.state),
    "POST /api/compliance/plan": (call: RecordedCall) => {
      world.plans.push(call);
      const body = call.body as { enable?: string[]; disable?: string[] };
      return json(200, planFor(body.enable?.[0] ?? body.disable?.[0] ?? "ens-media"));
    },
    "PUT /api/compliance": (call: RecordedCall) => {
      world.changes.push(call);
      const body = call.body as { enable?: string[]; disable?: string[] };
      const on = (body.enable ?? []).concat(world.state.frameworks.filter((f) => f.enabled).map((f) => f.id)).filter((id) => !(body.disable ?? []).includes(id));
      world.state = withFrameworksOn(...on);
      return json(200, { enabled_before: [], enabled_after: on, settings: {}, state: world.state });
    },
    ...extra,
  };
}

async function pageAt(initial?: ReturnType<typeof withFrameworksOn>, extra: Record<string, RouteHandler> = {}, title = "Compliance", frameworks = "Frameworks") {
  screenWidth(1440);
  const world: World = { state: initial ?? { ...COMPLIANCE, frameworks: COMPLIANCE.frameworks, controls: COMPLIANCE.controls }, plans: [], changes: [] };
  fakeBackend(routes(world, extra));
  const harness = renderConsole("/settings/security");
  const heading = await screen.findByRole("heading", { level: 2, name: title });
  const section = heading.closest("section");
  if (!section) throw new Error("no compliance section");
  await within(section).findByRole("heading", { level: 3, name: frameworks });
  return { ...harness, world, section };
}

describe("Settings > Security > Compliance", () => {
  it("lists the frameworks that can be switched on, off by default, with one line on what each is, and passes axe", { timeout: 30_000 }, async () => {
    const { section } = await pageAt();

    for (const name of ["ENS MEDIA", "ISO/IEC 27001:2022", "NIS2"]) {
      expect(within(section).getByRole("heading", { level: 3, name })).toBeInTheDocument();
    }
    expect(within(section).getAllByText("Off").length).toBeGreaterThanOrEqual(3);
    expect(within(section).getByText(/Spain's National Security Scheme, category MEDIA/)).toBeInTheDocument();
    expect(within(section).getByText(/It does not make a server compliant/)).toBeInTheDocument();
    await expectNoAxeViolations(section);
  });

  it("shows the controls a framework will turn on, with the clause, before anything is confirmed", { timeout: 30_000 }, async () => {
    const { section, user, world } = await pageAt();

    await user.click(present(within(section).getAllByRole("button", { name: "What it turns on" })[1]));
    // The control is named twice on the page: in what the framework turns on, and as a row of the controls.
    const region = screen
      .getAllByText("Separation of duties", { selector: "span" })
      .map((span) => span.closest("li"))
      .find((item) => item !== null);
    expect(region).not.toBeNull();
    expect(within(region as HTMLElement).getByText("A.5.3")).toBeInTheDocument();
    expect(within(section).getAllByText(/Stays organisational/).length).toBeGreaterThan(0);
    expect(world.changes).toEqual([]);
  });

  it("turns a framework on after listing what changes and the clause each control serves", { timeout: 30_000 }, async () => {
    const { section, user, world } = await pageAt();

    await user.click(present(within(section).getAllByRole("button", { name: "Turn on…" })[1]));
    const dialog = await screen.findByRole("alertdialog", { name: "Turn on ISO/IEC 27001:2022" });
    await within(dialog).findByText("Separation of duties");
    expect(within(dialog).getByText("Session idle time")).toBeInTheDocument();
    expect(within(dialog).getByText("30 minutes")).toBeInTheDocument();
    expect(within(dialog).getByText("15 minutes")).toBeInTheDocument();
    expect(within(dialog).getByText("A.5.3")).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    expect(world.changes).toEqual([]);

    await user.click(within(dialog).getByRole("button", { name: "Turn on" }));

    await waitFor(() => {
      expect(world.changes).toHaveLength(1);
    });
    expect(world.changes[0]?.body).toMatchObject({ enable: ["iso-27001"], acknowledge: false });
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog", { name: "Turn on ISO/IEC 27001:2022" })).toBeNull();
    });
    await within(section).findByText("Forces 2 of the controls below");
  });

  it("makes the person acknowledge a warning before going ahead", { timeout: 30_000 }, async () => {
    const { section, user, world } = await pageAt(undefined, {
      "POST /api/compliance/plan": () => json(200, planFor("ens-media", [{ id: "no_security_officer" }])),
    });

    await user.click(present(within(section).getAllByRole("button", { name: "Turn on…" })[0]));
    const dialog = await screen.findByRole("alertdialog", { name: "Turn on ENS MEDIA" });
    await within(dialog).findByText(/No active account has the security role/);
    const go = within(dialog).getByRole("button", { name: "Turn on" });
    expect(go).toBeDisabled();

    await user.click(within(dialog).getByRole("checkbox", { name: "I have read this and want to go ahead" }));
    await user.click(go);

    await waitFor(() => {
      expect(world.changes[0]?.body).toMatchObject({ enable: ["ens-media"], acknowledge: true });
    });
  });

  it("turns a framework off through a dangerous confirmation, and says protection goes down", { timeout: 30_000 }, async () => {
    const { section, user, world } = await pageAt(withFrameworksOn("nis2"));

    await within(section).findByText("Forces 1 of the controls below");
    await user.click(within(section).getByRole("button", { name: "Turn off…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Turn off NIS2" });
    expect(within(dialog).getByText(/Protection goes down/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Turn off" }));

    await waitFor(() => {
      expect(world.changes[0]?.body).toMatchObject({ disable: ["nis2"] });
    });
  });

  it("shows which framework locks a control, and does not let a locked switch be turned off", { timeout: 30_000 }, async () => {
    const { section } = await pageAt(withFrameworksOn("ens-media"));

    const lock = await within(section).findByRole("switch", { name: "Approval by a second person" });
    const approvals = lock.closest('[data-slot="setting"]');
    if (!(approvals instanceof HTMLElement)) throw new Error("the switch is not in a setting row");
    expect(within(approvals).getByText("Required by ENS MEDIA")).toBeInTheDocument();
    expect(lock.hasAttribute("disabled") || lock.getAttribute("aria-disabled") === "true").toBe(true);
    const free = within(section).getByRole("switch", { name: "A reason for every change" });
    expect(free.hasAttribute("disabled") || free.getAttribute("aria-disabled") === "true").toBe(false);
    expect(within(section).getAllByText("15 minutes").length).toBeGreaterThan(0);
  });

  it("applies a switch that tightens at once, and asks first only when the server has something to say", { timeout: 30_000 }, async () => {
    const { section, user, world } = await pageAt(undefined, {
      "POST /api/compliance/plan": () => json(200, { ...planFor("ens-media"), effects: [], warnings: [] }),
    });

    await user.click(await within(section).findByRole("switch", { name: "A reason for every change" }));

    await waitFor(() => {
      expect(world.changes[0]?.body).toMatchObject({ controls: { change_reasons: true } });
    });
    expect(screen.queryByRole("alertdialog", { name: /^(Change|Turn)/ })).toBeNull();
  });

  it("changes a number or a choice in a dialog and shows the server's refusal as it was worded", { timeout: 30_000 }, async () => {
    const { section, user } = await pageAt(withFrameworksOn("ens-media"), {
      "POST /api/compliance/plan": () => problem(400, "validationerror", "session_idle_minutes cannot be set looser than 15 minutes: ens-media asks for it"),
    });

    await user.click(await within(section).findByRole("button", { name: "Change… Session idle time" }));
    const dialog = await screen.findByRole("dialog", { name: "Change Session idle time" });
    expect(within(dialog).getByText("A framework asks for 15 minutes or stricter.")).toBeInTheDocument();
    const field = within(dialog).getByRole("spinbutton");
    await user.clear(field);
    await user.type(field, "60");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await within(dialog).findByText("session_idle_minutes cannot be set looser than 15 minutes: ens-media asks for it")).toBeInTheDocument();
  });

  it("offers no buttons to somebody who may not change security settings", { timeout: 30_000 }, async () => {
    screenWidth(1440);
    const world: World = { state: withFrameworksOn("nis2"), plans: [], changes: [] };
    fakeBackend({
      ...routes(world),
      ...signedInRoutes({ ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "security.manage") }),
      "GET /api/compliance": () => json(200, world.state),
    });
    renderConsole("/settings/security");
    const heading = await screen.findByRole("heading", { level: 2, name: "Compliance" });
    const section = present(heading.closest("section"));
    await within(section).findByRole("heading", { level: 3, name: "Frameworks" });

    expect(within(section).queryByRole("button", { name: "Turn on…" })).toBeNull();
    expect(within(section).queryByRole("button", { name: "Turn off…" })).toBeNull();
    expect(within(section).getAllByRole("switch").every((control) => control.hasAttribute("disabled") || control.getAttribute("aria-disabled") === "true")).toBe(true);
  });

  it("is in Spanish, with the same care about what a framework is", { timeout: 30_000 }, async () => {
    await act(() => setLocale("es"));
    await pageAt(undefined, {}, "Cumplimiento", "Marcos");

    expect(await screen.findByRole("heading", { level: 2, name: "Cumplimiento" })).toBeInTheDocument();
    expect(screen.getByText(/No hace que un servidor cumpla/)).toBeInTheDocument();
    expect(screen.getAllByText("Activar…").length).toBeGreaterThan(0);
    await expectNoAxeViolations(present(screen.getByRole("heading", { level: 2, name: "Cumplimiento" }).closest("section")));
  });
});
