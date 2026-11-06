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
  checks: RecordedCall[];
}

/** What the check says of ENS MEDIA: one control not met, one needing attention, and thirty-six in force. */
function check(ids: readonly string[]) {
  const finding = (control: string, status: string, summary: string) => ({
    framework: "ens-media",
    control,
    clauses: [control === "second_factor" ? "op.acc.5: second factor on every account" : "op.exp.2: security updates within the day"],
    status,
    required: true,
    actual: false,
    summary,
    evidence: [],
    remediation: status === "ok" ? "" : "Set up the second factor of dani.",
  });
  return {
    verdict: "fail",
    frameworks: ids.map((id) => ({
      framework: id,
      name: COMPLIANCE.frameworks.find((entry) => entry.id === id)?.name ?? id,
      enabled: true,
      verdict: "fail",
      counts: { ok: 36, warning: 1, fail: 1 },
      findings: [
        finding("second_factor", "fail", "1 of 3 accounts has no second factor."),
        finding("backup_verify_days", "warning", "The last verified backup is 9 days old."),
        ...Array.from({ length: 36 }, (_, index) => finding(`met_${String(index)}`, "ok", "In force.")),
      ],
      organisational: [],
      not_covered: [],
    })),
    errors: {},
    disclaimer: "A pass means the control is in force.",
    enabled: [...ids],
    checked_at: "2026-11-05T10:00:00+00:00",
    host: "web-01",
    version: "4.2.0",
  };
}

function routes(world: World, extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/ens/incident": () => json(200, { locked: false }),
    "GET /api/compliance": () => json(200, world.state),
    "GET /api/compliance/check": (call: RecordedCall) => {
      world.checks.push(call);
      return json(200, check(world.state.frameworks.filter((framework) => framework.enabled).map((framework) => framework.id)));
    },
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

async function pageAt(initial?: ReturnType<typeof withFrameworksOn>, extra: Record<string, RouteHandler> = {}, title = "Compliance") {
  screenWidth(1440);
  const world: World = { state: initial ?? { ...COMPLIANCE, frameworks: COMPLIANCE.frameworks, controls: COMPLIANCE.controls }, plans: [], changes: [], checks: [] };
  fakeBackend(routes(world, extra));
  const harness = renderConsole("/settings/compliance");
  await screen.findByRole("heading", { level: 1, name: title });
  return { ...harness, world };
}

const main = () => screen.getByRole("main");

describe("Settings > Compliance, with no framework on", () => {
  it("says so as a fact and not as a warning, lists the server's frameworks to turn on, and passes axe", { timeout: 30_000 }, async () => {
    const { world } = await pageAt();

    expect(await screen.findByRole("heading", { level: 2, name: "No framework is on" })).toBeInTheDocument();
    expect(screen.getByText(/That is not a warning: it is normal when nothing obliges you/)).toBeInTheDocument();
    // The cards are the server's list (three here), each off, with one line on what it is and how many controls it asks of.
    for (const name of ["ENS MEDIA", "ISO/IEC 27001:2022", "NIS2"]) expect(await screen.findByRole("heading", { level: 3, name })).toBeInTheDocument();
    expect(screen.getAllByText("Off")).toHaveLength(3);
    expect(screen.getByText(/Spain's National Security Scheme, category MEDIA/)).toBeInTheDocument();
    // Nothing is judged and nothing is red: no state of failure or warning is drawn, and no report is asked for.
    expect(main().querySelector('[data-state="failed"], [data-state="warning"]')).toBeNull();
    expect(world.checks).toEqual([]);
    await expectNoAxeViolations(main());
  });

  it("says in the \"?\" what a framework is and is not, and never that a server complies", { timeout: 30_000 }, async () => {
    const { user } = await pageAt();
    await user.click(await screen.findByRole("button", { name: "About compliance" }));
    expect(await screen.findByText(/It does not make a server compliant/)).toBeInTheDocument();
    expect(screen.queryByText(/is compliant with/i)).toBeNull();
  });

  it("turns a framework on after listing what changes, the clause each control serves, and what stays the organisation's", { timeout: 30_000 }, async () => {
    const { user, world } = await pageAt();

    const card = present((await screen.findByRole("heading", { level: 3, name: "ISO/IEC 27001:2022" })).closest("article"));
    await user.click(within(card).getByRole("button", { name: "Turn on…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Turn on ISO/IEC 27001:2022" });
    await within(dialog).findByText("Separation of duties");
    expect(within(dialog).getByText("30 minutes")).toBeInTheDocument();
    expect(within(dialog).getByText("15 minutes")).toBeInTheDocument();
    expect(within(dialog).getByText("A.5.3")).toBeInTheDocument();
    // What stays the organisation's is one press away, in the same question.
    await user.click(within(dialog).getByRole("button", { name: "What stays yours" }));
    expect(within(dialog).getByText("The management system itself.")).toBeInTheDocument();
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
    // Once it is on, the page is its report.
    expect(await screen.findByRole("heading", { level: 2, name: "ISO/IEC 27001:2022" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "No framework is on" })).toBeNull();
  });

  it("makes the person acknowledge a warning before going ahead", { timeout: 30_000 }, async () => {
    const { user, world } = await pageAt(undefined, {
      "POST /api/compliance/plan": () => json(200, planFor("ens-media", [{ id: "no_security_officer" }])),
    });

    const card = present((await screen.findByRole("heading", { level: 3, name: "ENS MEDIA" })).closest("article"));
    await user.click(within(card).getByRole("button", { name: "Turn on…" }));
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
});

describe("Settings > Compliance, with a framework on", () => {
  it("is its report: how many controls are in force, which are not, and where each is mended", { timeout: 30_000 }, async () => {
    const { world } = await pageAt(withFrameworksOn("ens-media"));

    const report = present((await screen.findByRole("heading", { level: 2, name: "ENS MEDIA" })).closest("section"));
    expect(await within(report).findByText("36 of 38 controls in force")).toBeInTheDocument();
    expect(world.checks).toHaveLength(1);
    // The two that need somebody, worst first, each with the link to the page where it is mended.
    const table = await within(report).findByRole("region", { name: "What ENS MEDIA asks that is not in force yet" });
    const rows = within(table).getAllByRole("row");
    const [, worst, next] = rows;
    if (worst === undefined || next === undefined) throw new Error("The report lists the two controls that need somebody");
    expect(worst).toHaveTextContent("Second factor required");
    expect(worst).toHaveTextContent("Not met");
    expect(within(worst).getByRole("link", { name: "See the accounts" })).toHaveAttribute("href", "/settings/accounts");
    expect(next).toHaveTextContent("Partly met");
    expect(within(next).getByRole("link", { name: "See the backups" })).toHaveAttribute("href", "/backups");
    // What is met is a count, never a list of thirty-six.
    expect(within(report).getByText(/The other 36 controls are in force or do not apply/)).toBeInTheDocument();
    expect(within(report).queryByText("met_3")).toBeNull();
    // The document for an auditor is the one thing in the header.
    expect(within(main()).getByRole("link", { name: "Download the report" })).toHaveAttribute("href", "/api/compliance/check?format=markdown");
    await expectNoAxeViolations(main());
  });

  it("says what the server found, as it wrote it, one press away", { timeout: 30_000 }, async () => {
    const { user } = await pageAt(withFrameworksOn("ens-media"));
    await user.click(await screen.findByRole("button", { name: "What was found for Second factor required" }));
    expect(await screen.findByText("1 of 3 accounts has no second factor.")).toBeInTheDocument();
    expect(screen.getByText("Set up the second factor of dani.")).toBeInTheDocument();
  });

  it("turns a framework off through a dangerous confirmation, and says protection goes down", { timeout: 30_000 }, async () => {
    const { user, world } = await pageAt(withFrameworksOn("nis2"));

    const report = present((await screen.findByRole("heading", { level: 2, name: "NIS2" })).closest("section"));
    await user.click(within(report).getByRole("button", { name: "Turn off…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Turn off NIS2" });
    expect(within(dialog).getByText(/Protection goes down/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Turn off" }));

    await waitFor(() => {
      expect(world.changes[0]?.body).toMatchObject({ disable: ["nis2"] });
    });
  });

  it("says the role cannot read the report, instead of drawing a head with nothing under it", { timeout: 30_000 }, async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "compliance.read") };
    const { world } = await pageAt(withFrameworksOn("nis2"), { ...signedInRoutes(reader) });
    expect(await screen.findByText(/Your role cannot read the report/)).toBeInTheDocument();
    expect(world.checks).toEqual([]);
    expect(within(main()).queryByRole("link", { name: "Download the report" })).toBeNull();
  });

  it("shows which framework locks a control, and does not let a locked switch be turned off", { timeout: 30_000 }, async () => {
    const { user } = await pageAt(withFrameworksOn("ens-media"));
    await user.click(await screen.findByRole("button", { name: /^Access and accounts/ }));

    const lock = await screen.findByRole("switch", { name: "Approval by a second person" });
    const approvals = lock.closest('[data-slot="setting"]');
    if (!(approvals instanceof HTMLElement)) throw new Error("the switch is not in a setting row");
    expect(within(approvals).getByText("Required by ENS MEDIA")).toBeInTheDocument();
    expect(lock.hasAttribute("disabled") || lock.getAttribute("aria-disabled") === "true").toBe(true);
  });
});

describe("Settings > Compliance, for the roles that cannot change it", () => {
  it("says once that it can only be read, offers no button and keeps the switches off", { timeout: 30_000 }, async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "security.manage") };
    const { user } = await pageAt(undefined, { ...signedInRoutes(reader) });
    await screen.findByRole("heading", { level: 3, name: "ENS MEDIA" });
    expect(screen.getByText(/can see this page and not change it/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Turn on…" })).toBeNull();
    await user.click(await screen.findByRole("button", { name: /^Accountability and the audit trail/ }));
    const switches = within(main()).getAllByRole("switch");
    expect(switches.length).toBeGreaterThan(0);
    expect(switches.every((control) => control.hasAttribute("disabled") || control.getAttribute("aria-disabled") === "true")).toBe(true);
  });

  it("shows the server's refusal as it was worded when a control change is refused", { timeout: 30_000 }, async () => {
    const { user } = await pageAt(undefined, {
      "POST /api/compliance/plan": () => problem(400, "validationerror", "change_reasons cannot be changed now"),
    });
    await user.click(await screen.findByRole("button", { name: /^Accountability and the audit trail/ }));
    await user.click(await screen.findByRole("switch", { name: "A reason for every change" }));
    expect(await screen.findByText("change_reasons cannot be changed now")).toBeInTheDocument();
  });
});

describe("Settings > Compliance in Spanish", () => {
  it("reads as Spanish, with the same care about what a framework is", { timeout: 30_000 }, async () => {
    await act(() => setLocale("es"));
    await pageAt(undefined, {}, "Cumplimiento");
    expect(await screen.findByRole("heading", { level: 2, name: "Ninguna normativa activa" })).toBeInTheDocument();
    expect(screen.getByText(/No es un aviso: es lo normal si no te obliga ninguna/)).toBeInTheDocument();
    expect(screen.getAllByText("Activar…").length).toBeGreaterThan(0);
    await expectNoAxeViolations(main());
  });
});
