import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { APPS, FakeEventSource, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { instanceName, parseDrain } from "./zeroDowntime";

const DOMAIN = "shop.example.com";
const APP = { ...APPS[0], path: "/var/www/apps/shop-example-com", layout: "releases", keep_releases: 5 };
const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const OFF = {
  domain: DOMAIN,
  enabled: false,
  active_color: null,
  drain_seconds: 10,
  instances: [],
  upstream_port: null,
  eligible: true,
  reason: null,
  hint: null,
};

const ON = {
  domain: DOMAIN,
  enabled: true,
  active_color: "blue",
  drain_seconds: 10,
  instances: [
    { color: "blue", unit: "shop-example-com-blue", port: 3001, release: "20260925-184247-2a8b7c4", serving: true, state: "active" },
    { color: "green", unit: "shop-example-com-green", port: 3002, release: "20260924-194023-19d3f6e", serving: false, state: "inactive" },
  ],
  upstream_port: 3001,
  eligible: true,
  reason: null,
  hint: null,
};

const JOB = {
  id: "zd1",
  type: "zero_downtime",
  name: `Zero downtime on for ${DOMAIN}`,
  description: `Turning blue/green activation on for ${DOMAIN}`,
  status: "pending",
  progress: 0,
  total_steps: 100,
  current_step: "",
  created_at: "2026-09-28T10:00:00",
  logs: [],
  metadata: { domain: DOMAIN },
};

const ACCEPTED = { job_id: JOB.id, status: "pending", message: `Zero downtime on for ${DOMAIN} queued`, job: JOB };

async function settingsWith(state: () => object, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(ELEVATED),
    [`GET /api/apps/${DOMAIN}`]: () => json(200, APP),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET /api/apps/${DOMAIN}/releases`]: () => json(200, { domain: DOMAIN, items: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/webhook/deliveries`]: () => json(200, { items: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/previews`]: () => json(200, { domain: DOMAIN, enabled: false, settings: null, previews: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/zero-downtime`]: () => json(200, state()),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    ...extra,
  });
  const harness = renderConsole(`/apps/${DOMAIN}/settings`);
  const panel = await screen.findByRole("region", { name: "Zero downtime" });
  return { ...harness, backend, panel };
}

describe("zero downtime", () => {
  it("warns before turning it on, sends the drain, and follows the job to its end", async () => {
    let state: object = OFF;
    const { user, backend, panel } = await settingsWith(() => state, {
      [`PUT /api/apps/${DOMAIN}/zero-downtime`]: () => json(202, ACCEPTED),
    });
    expect(await within(panel).findByText("Off")).toBeInTheDocument();
    const drain = within(panel).getByRole("textbox", { name: "Drain" });
    expect(drain).toHaveValue("10");
    await user.clear(drain);
    await user.type(drain, "20");
    await user.click(within(panel).getByRole("button", { name: "Turn on zero downtime" }));

    const dialog = await screen.findByRole("dialog", { name: `Turn on zero downtime for ${DOMAIN}?` });
    expect(within(dialog).getByText("Two copies of the app run at once for a few seconds.")).toBeInTheDocument();
    expect(within(dialog).getByText(/a SQLite database both copies write to/)).toBeInTheDocument();
    expect(within(dialog).getByText(/stop the old one 20 seconds later/)).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: "Turn on zero downtime" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)[0]?.body).toEqual({ enabled: true, drain_seconds: 20 });
    });
    expect(await within(dialog).findByText(/Starting the second instance and switching nginx/)).toBeInTheDocument();

    state = { ...ON, drain_seconds: 20 };
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...JOB, status: "completed", progress: 100, result: { domain: DOMAIN, enabled: true } });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: `Turn on zero downtime for ${DOMAIN}?` })).not.toBeInTheDocument();
    });
    expect(await within(panel).findByText("On")).toBeInTheDocument();
    expect(within(panel).getByRole("list", { name: "Instances" })).toBeInTheDocument();
  });

  it("shows both instances by name, which one serves, and each unit's state", async () => {
    const { panel } = await settingsWith(() => ON);
    const instances = await within(panel).findByRole("list", { name: "Instances" });
    const [blue, green] = within(instances).getAllByRole("listitem");
    if (!blue || !green) throw new Error("two instances expected");
    expect(within(blue).getByText("Blue")).toBeInTheDocument();
    expect(within(blue).getByText("Serving")).toBeInTheDocument();
    expect(within(blue).getByText("Running")).toBeInTheDocument();
    expect(within(blue).getByText(/Port 3001 · Release 20260925-184247-2a8b7c4 · shop-example-com-blue\.service/)).toBeInTheDocument();
    expect(within(green).getByText("Green")).toBeInTheDocument();
    expect(within(green).getByText("Idle")).toBeInTheDocument();
    expect(within(green).getByText("Stopped")).toBeInTheDocument();
    expect(within(panel).getByText("3001", { selector: "span" })).toBeInTheDocument();
  });

  it("saves a new drain without asking to confirm, since nothing starts or stops", async () => {
    const { user, backend, panel } = await settingsWith(() => ON, {
      [`PUT /api/apps/${DOMAIN}/zero-downtime`]: () => json(202, ACCEPTED),
    });
    const drain = await within(panel).findByRole("textbox", { name: "Drain" });
    await user.clear(drain);
    await user.type(drain, "45");
    await user.click(within(panel).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)[0]?.body).toEqual({ enabled: true, drain_seconds: 45 });
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("refuses a drain out of range before asking the backend", async () => {
    const { user, backend, panel } = await settingsWith(() => ON);
    const drain = await within(panel).findByRole("textbox", { name: "Drain" });
    await user.clear(drain);
    await user.type(drain, "400");
    await user.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await within(panel).findByText("Drain from 0 to 300 seconds, not 400.")).toBeInTheDocument();
    expect(drain).toHaveAttribute("aria-invalid", "true");
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)).toHaveLength(0);
  });

  it("puts the backend's refusal of the drain under the field", async () => {
    const { user, panel } = await settingsWith(() => ON, {
      [`PUT /api/apps/${DOMAIN}/zero-downtime`]: () =>
        problem(400, "validation_error", "Cannot drain for 250 seconds", { fields: { drain_seconds: "Cannot drain for 250 seconds" } }),
    });
    const drain = await within(panel).findByRole("textbox", { name: "Drain" });
    await user.clear(drain);
    await user.type(drain, "250");
    await user.click(within(panel).getByRole("button", { name: "Save" }));
    expect(await within(panel).findByText("Cannot drain for 250 seconds")).toBeInTheDocument();
    expect(drain).toHaveAttribute("aria-invalid", "true");
  });

  it("confirms before turning it off", async () => {
    const { user, backend, panel } = await settingsWith(() => ON, {
      [`PUT /api/apps/${DOMAIN}/zero-downtime`]: () => json(202, ACCEPTED),
    });
    await user.click(await within(panel).findByRole("button", { name: "Turn off zero downtime" }));
    const dialog = await screen.findByRole("dialog", { name: `Turn off zero downtime for ${DOMAIN}?` });
    expect(within(dialog).getByText(/Activations go back to restarting a single unit/)).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Turn off zero downtime" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/zero-downtime`)[0]?.body).toEqual({ enabled: false });
    });
  });

  it("shows why the job failed, and that what served before still serves", async () => {
    const { user, panel } = await settingsWith(() => OFF, {
      [`PUT /api/apps/${DOMAIN}/zero-downtime`]: () => json(202, ACCEPTED),
    });
    await user.click(await within(panel).findByRole("button", { name: "Turn on zero downtime" }));
    const dialog = await screen.findByRole("dialog", { name: `Turn on zero downtime for ${DOMAIN}?` });
    await user.click(within(dialog).getByRole("button", { name: "Turn on zero downtime" }));
    await within(dialog).findByText(/Starting the second instance/);
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...JOB, status: "failed", error: "shop-example-com-green did not answer on 127.0.0.1:3002" });
    });
    expect(await within(dialog).findByText("shop-example-com-green did not answer on 127.0.0.1:3002")).toBeInTheDocument();
    expect(within(dialog).getByText(/WASM put back what served before/)).toBeInTheDocument();
  });

  it("says why an app cannot use it, with the fix, and offers no switch", async () => {
    const { panel } = await settingsWith(() => ({
      ...OFF,
      eligible: false,
      reason: "shop.example.com is served by apache; blue/green is nginx-only in 2.2",
      hint: "Zero-downtime activation switches an nginx upstream. Apache applications keep activating with a restart.",
    }));
    expect(await within(panel).findByText("shop.example.com is served by apache; blue/green is nginx-only in 2.2")).toBeInTheDocument();
    expect(within(panel).getByText(/Apache applications keep activating with a restart/)).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: /Turn on/ })).not.toBeInTheDocument();
    expect(within(panel).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("has no accessibility violations, on and in the warning", async () => {
    const { user, panel } = await settingsWith(() => ON);
    await within(panel).findByRole("list", { name: "Instances" });
    await expectNoAxeViolations(panel);
    await user.click(within(panel).getByRole("button", { name: "Turn off zero downtime" }));
    await expectNoAxeViolations(await screen.findByRole("dialog"));
  });

  it("has no accessibility violations in the warning before turning it on", async () => {
    const { user, panel } = await settingsWith(() => OFF);
    await user.click(await within(panel).findByRole("button", { name: "Turn on zero downtime" }));
    await expectNoAxeViolations(await screen.findByRole("dialog"));
  });
});

describe("the drain and the instances' names", () => {
  it("reads the drain as the backend will", () => {
    expect(parseDrain("0")).toEqual({ seconds: 0, error: null });
    expect(parseDrain(" 300 ")).toEqual({ seconds: 300, error: null });
    expect(parseDrain("301").error).toBe("Drain from 0 to 300 seconds, not 301.");
    expect(parseDrain("1.5").error).toBe("Give a whole number of seconds from 0 to 300.");
    expect(parseDrain("").error).toBe("Give a whole number of seconds from 0 to 300.");
  });

  it("names an instance by its colour, as a word", () => {
    expect(instanceName("blue")).toBe("Blue");
    expect(instanceName("green")).toBe("Green");
  });
});
