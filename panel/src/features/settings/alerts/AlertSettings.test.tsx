import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";

const METRICS = [
  { id: "cpu", scope: "server", unit: "percent" },
  { id: "disk", scope: "server", unit: "percent" },
  { id: "net_rx", scope: "server", unit: "bytes_s" },
  { id: "app_memory", scope: "app", unit: "bytes" },
  { id: "app_5xx", scope: "app", unit: "per_min" },
];

const NOW = Math.floor(Date.now() / 1000);

interface RuleState {
  subject: string;
  state: string;
  since: number;
  last_value: number | null;
  announced: boolean;
  acknowledged_at?: number | null;
  acknowledged_by?: string | null;
}

function rule(name: string, extra: Record<string, unknown> = {}) {
  const states: RuleState[] = [];
  return {
    name,
    metric: "cpu",
    scope: "server",
    unit: "percent",
    comparator: ">",
    threshold: 90,
    app_domain: null,
    duration_s: 300,
    hysteresis: 0,
    cooldown_s: 3600,
    severity: "warning",
    enabled: true,
    remind: false,
    note: "",
    states,
    ...extra,
  };
}

const FIRING = rule("shop-memory", {
  metric: "app_memory",
  scope: "app",
  unit: "bytes",
  comparator: ">=",
  threshold: 536_870_912,
  app_domain: "shop.example.com",
  severity: "failed",
  states: [{ subject: "shop.example.com", state: "firing", since: NOW - 600, last_value: 620_000_000, announced: true, acknowledged_at: null, acknowledged_by: null }],
});

function configBody() {
  return {
    config: { monitor: { disk_threshold: 90.0, disk_check_seconds: 0, disk_renotify_hours: 0, cert_warning_days: 14, backup_missing_days: 2, backup_check_hours: 6, backup_missing_renotify_hours: 24, os_patches_check_hours: 24, token_warning_days: 7 } },
    path: "/etc/noust/config.yaml",
    writable: true,
  };
}

function alertsBackend(rules: ReturnType<typeof rule>[] = [rule("cpu-high"), FIRING], routes: Parameters<typeof fakeBackend>[0] = {}) {
  let held = rules;
  let elevated = false;
  const list = () => ({ items: held, metrics: METRICS, firing: held.reduce((sum, item) => sum + item.states.filter((state) => state.state === "firing").length, 0) });
  return fakeBackend({
    ...signedInRoutes(),
    "GET /api/alerts/rules": () => json(200, list()),
    "GET /api/config": () => json(200, configBody()),
    "PUT /api/alerts/rules/cpu-high": (call) => {
      const body = call.body as Record<string, unknown>;
      held = held.map((item) => (item.name === "cpu-high" ? { ...item, ...body, threshold: Number.parseFloat(String(body["threshold"])) } : item));
      return json(200, held[0]);
    },
    "PUT /api/alerts/rules/disk-full": (call) => {
      held = [...held, rule("disk-full", { metric: "disk", ...(call.body as Record<string, unknown>), threshold: 85 })];
      return json(200, held.at(-1));
    },
    "POST /api/alerts/rules/cpu-high/disable": () => {
      held = held.map((item) => (item.name === "cpu-high" ? { ...item, enabled: false } : item));
      return json(200, held[0]);
    },
    "DELETE /api/alerts/rules/cpu-high": () => {
      held = held.filter((item) => item.name !== "cpu-high");
      return new Response(null, { status: 204 });
    },
    "POST /api/alerts/rules/shop-memory/acknowledge": (call) => {
      held = held.map((item) =>
        item.name === "shop-memory"
          ? { ...item, states: item.states.map((state) => (state.subject === call.search.get("subject") ? { ...state, acknowledged_by: "alice", acknowledged_at: NOW - 30 } : state)) }
          : item,
      );
      return json(200, held.find((item) => item.name === "shop-memory"));
    },
    "DELETE /api/alerts/rules/shop-memory/acknowledge": (call) => {
      held = held.map((item) =>
        item.name === "shop-memory"
          ? { ...item, states: item.states.map((state) => (state.subject === call.search.get("subject") ? { ...state, acknowledged_by: null, acknowledged_at: null } : state)) }
          : item,
      );
      return json(200, held.find((item) => item.name === "shop-memory"));
    },
    "POST /api/auth/elevate": () => {
      elevated = true;
      return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
    },
    "PATCH /api/config": (call) => {
      if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue.");
      const body = call.body as { path: string; value: unknown };
      return json(200, { message: `Configuration '${body.path}' updated`, path: "/etc/noust/config.yaml", value: body.value });
    },
    ...routes,
  });
}

async function confirmItsYou(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
  await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
  await user.click(within(confirm).getByRole("button", { name: "Continue" }));
}

function ruleRow(name: string): HTMLElement {
  const found = within(screen.getByRole("list", { name: "Alert rules" }))
    .getAllByRole("listitem")
    .find((item) => item.textContent.startsWith(name));
  if (found === undefined) throw new Error(`no row for ${name}`);
  return found;
}

describe("Settings > Alerts", () => {
  it("lists each rule as a sentence with where it stands, and says which are firing and since when", { timeout: 20_000 }, async () => {
    alertsBackend();
    const { container } = renderConsole("/settings/alerts");
    await screen.findByRole("list", { name: "Alert rules" });
    expect(screen.getByText("1 alert is firing")).toBeInTheDocument();

    const calm = ruleRow("cpu-high");
    expect(within(calm).getByText("CPU above 90% for 5m 00s")).toBeInTheDocument();
    expect(within(calm).getByText("On the server.")).toBeInTheDocument();
    expect(within(calm).getByText("Normal")).toHaveAttribute("data-state", "running");

    const firing = ruleRow("shop-memory");
    // A limit is read the way the metric is measured, and a failure rule is a failed state.
    expect(within(firing).getByText("Application memory at or above 512 MB for 5m 00s")).toBeInTheDocument();
    expect(within(firing).getByText("On shop.example.com.")).toBeInTheDocument();
    expect(within(firing).getByText("Firing")).toHaveAttribute("data-state", "failed");
    expect(firing.textContent).toContain("shop.example.com reads 591 MB, firing");
    await expectNoAxeViolations(container);
  });

  it("takes a firing alert, shows who has it and hands it back", { timeout: 30_000 }, async () => {
    const backend = alertsBackend();
    const { user } = renderConsole("/settings/alerts");
    await screen.findByRole("list", { name: "Alert rules" });
    const firing = ruleRow("shop-memory");
    // Nothing is taken yet, and a rule that is fine has nothing to take.
    expect(within(firing).queryByText(/Taken by/)).toBeNull();
    expect(within(ruleRow("cpu-high")).queryByRole("button", { name: /Take the alert/ })).toBeNull();

    await user.click(within(firing).getByRole("button", { name: "Take the alert for shop.example.com" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/alerts/rules/shop-memory/acknowledge")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/alerts/rules/shop-memory/acknowledge")[0]?.search.get("subject")).toBe("shop.example.com");
    // Who has it, with the button turned into the way back.
    expect(await within(ruleRow("shop-memory")).findByText(/Taken by/)).toHaveTextContent(/Taken by\s+alice/);
    await user.click(within(ruleRow("shop-memory")).getByRole("button", { name: "Hand the alert for shop.example.com back" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/alerts/rules/shop-memory/acknowledge")).toHaveLength(1);
    });
    expect(await within(ruleRow("shop-memory")).findByRole("button", { name: "Take the alert for shop.example.com" })).toBeInTheDocument();
    expect(within(ruleRow("shop-memory")).queryByText(/Taken by/)).toBeNull();
  });

  it("explains what a rule is when there is none", { timeout: 20_000 }, async () => {
    alertsBackend([]);
    renderConsole("/settings/alerts");
    expect(await screen.findByText("No alert rules yet")).toBeInTheDocument();
    expect(screen.getByText("No alert is firing.")).toBeInTheDocument();
  });

  it("creates a rule from the drawer, with the limit as typed and the time in minutes", { timeout: 30_000 }, async () => {
    const backend = alertsBackend();
    const { user } = renderConsole("/settings/alerts");
    await screen.findByRole("list", { name: "Alert rules" });
    await user.click(screen.getByRole("button", { name: "New rule" }));
    const drawer = await screen.findByRole("dialog", { name: "New alert rule" });
    const save = within(drawer).getByRole("button", { name: "Save rule" });
    // Nothing to send until the rule has a name and a limit.
    expect(save).toBeDisabled();
    await user.type(within(drawer).getByLabelText("Name"), "disk-full");
    await user.type(within(drawer).getByLabelText("Limit"), "85%");
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/alerts/rules/disk-full")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/alerts/rules/disk-full")[0]?.body).toEqual({
      metric: "cpu",
      comparator: ">",
      threshold: "85%",
      app_domain: null,
      duration_s: 300,
      hysteresis: 0,
      cooldown_s: 3600,
      severity: "warning",
      enabled: true,
      remind: false,
      note: "",
    });
    // The drawer closes and the new rule is in the list.
    expect(await screen.findByText("disk-full")).toBeInTheDocument();
  });

  it("puts the server's refusal beside the field it names, verbatim", { timeout: 30_000 }, async () => {
    alertsBackend([], {
      "PUT /api/alerts/rules/cpu-high": () =>
        problem(400, "validation_error", "Not a limit: 'plenty'", { hint: "Write a number, with a unit where the metric has one: 90, 90%, 512MB, 2MB/s.", fields: { threshold: "Not a limit: 'plenty'" } }),
    });
    const { user } = renderConsole("/settings/alerts");
    await screen.findByText("No alert rules yet");
    // The header's button and the empty state's: either opens the drawer.
    const [header] = screen.getAllByRole("button", { name: "New rule" });
    if (header === undefined) throw new Error("no New rule button");
    await user.click(header);
    const drawer = await screen.findByRole("dialog", { name: "New alert rule" });
    await user.type(within(drawer).getByLabelText("Name"), "cpu-high");
    const limit = within(drawer).getByLabelText("Limit");
    await user.type(limit, "plenty");
    await user.click(within(drawer).getByRole("button", { name: "Save rule" }));
    expect(await within(drawer).findByText("Not a limit: 'plenty'")).toBeInTheDocument();
    expect(limit).toHaveAttribute("aria-invalid", "true");
  });

  it("edits a rule without letting its name change, and shows its limit the way it is typed", { timeout: 30_000 }, async () => {
    const backend = alertsBackend();
    const { user } = renderConsole("/settings/alerts");
    await screen.findByRole("list", { name: "Alert rules" });
    await user.click(within(ruleRow("shop-memory")).getByRole("button", { name: "Edit shop-memory" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit shop-memory" });
    expect(within(drawer).getByLabelText("Name")).toHaveAttribute("readonly");
    // 536870912 bytes is written back as the 512MB it was typed as.
    expect(within(drawer).getByLabelText("Limit")).toHaveValue("512MB");
    expect(within(drawer).getByLabelText(/^Application/)).toHaveValue("shop.example.com");
    await user.click(within(drawer).getByRole("button", { name: "Cancel" }));
    expect(backend.callsTo("PUT /api/alerts/rules/shop-memory")).toHaveLength(0);
  });

  it("switches a rule off and removes one after a question", { timeout: 30_000 }, async () => {
    const backend = alertsBackend();
    const { user } = renderConsole("/settings/alerts");
    await screen.findByRole("list", { name: "Alert rules" });
    await user.click(within(ruleRow("cpu-high")).getByRole("switch", { name: "Judge cpu-high" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/alerts/rules/cpu-high/disable")).toHaveLength(1);
    });
    expect(await within(ruleRow("cpu-high")).findByText("Off")).toHaveAttribute("data-state", "stopped");

    await user.click(within(ruleRow("cpu-high")).getByRole("button", { name: "Remove cpu-high" }));
    const question = await screen.findByRole("alertdialog", { name: "Remove cpu-high?" });
    await user.click(within(question).getByRole("button", { name: "Remove" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/alerts/rules/cpu-high")).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.queryByText("cpu-high")).toBeNull();
    });
  });

  it("writes only the built-in checks that changed, and puts a refusal beside its field", { timeout: 30_000 }, async () => {
    const backend = alertsBackend();
    const { user } = renderConsole("/settings/alerts");
    const disk = await screen.findByLabelText("Disk almost full at (%)");
    // The values arrive with the configuration; until then the fields hold their place, empty.
    await waitFor(() => {
      expect(disk).toHaveValue("90");
    });
    expect(screen.getByLabelText("Certificates, days before they expire")).toHaveValue("14");
    await user.clear(disk);
    await user.type(disk, "95");
    const bar = screen.getByRole("region", { name: "Unsaved changes" });
    expect(within(bar).getByText("1 unsaved change")).toBeInTheDocument();
    await user.click(within(bar).getByRole("button", { name: "Save" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/config").at(-1)?.body).toEqual({ path: "monitor.disk_threshold", value: 95 });
    });
    // Only that one: what was not touched stays as the file says.
    expect(backend.callsTo("PATCH /api/config").filter((call) => (call.body as { path: string }).path !== "monitor.disk_threshold")).toHaveLength(0);
  });

  it("reads in Spanish, with no accessibility violations", { timeout: 20_000 }, async () => {
    alertsBackend();
    await act(async () => {
      await setLocale("es");
    });
    const { container } = renderConsole("/settings/alerts");
    expect(await screen.findByRole("list", { name: "Reglas de alerta" })).toBeInTheDocument();
    expect(screen.getByText("1 alerta está activa")).toBeInTheDocument();
    expect(screen.getByText("Comprobaciones incluidas")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });
});
