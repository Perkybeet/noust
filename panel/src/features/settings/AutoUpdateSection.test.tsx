/**
 * Settings > About > Automatic updates (checklist N-28): off, on, on with a problem, the editor with
 * its own checks, the server's refusal, and sudo mode.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

const OFF = {
  settings: { enabled: false, channel: "patch", window: "03:00-05:00", days: "daily" },
  supported: true,
  reason: null,
  hint: null,
  check_enabled: true,
  window_open: false,
  next_window: null,
  timer: { installed: false, next_run: null, last_run: null },
  last_run: null,
};

const ON = {
  ...OFF,
  settings: { enabled: true, channel: "security", window: "23:00-01:00", days: "mon,thu" },
  next_window: "2026-10-19T23:00:00+02:00",
  timer: { installed: true, next_run: "Mon 2026-10-19 23:00:00 CEST", last_run: null },
  last_run: {
    outcome: "updated",
    reason: "updated",
    detail: "3.4.1 fixes a vulnerability",
    at: "2026-10-15T23:20:00+02:00",
    trigger: "schedule",
    from_version: "3.4.0",
    target: "3.4.1",
    to_version: "3.4.1",
    settled: true,
    output: null,
  },
};

function routes(auto: unknown, extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/system/version": () => json(200, { current_version: "3.4.0", has_update: false, update_state: "up_to_date" }),
    "GET /api/config": () => json(200, { config: {}, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/system/update": () => json(200, { current_version: "3.4.0", method: "apt", supported: true }),
    "GET /api/system/changelog": () => json(200, { entries: [] }),
    "GET /api/system/update/auto": () => json(200, auto),
    ...extra,
  };
}

async function openAbout() {
  const harness = renderConsole("/settings/about");
  const section = await screen.findByRole("region", { name: "Automatic updates" });
  return { ...harness, section };
}

describe("Settings > About > Automatic updates", () => {
  it("says it is off, and how updating works meanwhile, and passes axe", async () => {
    fakeBackend(routes(OFF));
    const { section, container } = await openAbout();
    expect(await within(section).findByText("Automatic updates are off")).toBeInTheDocument();
    expect(within(section).getByText(/A release that fixes a vulnerability waits for you/)).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Set up" })).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says what it takes, between which hours, on which days, and what it did last", async () => {
    fakeBackend(routes(ON));
    const { section, container } = await openAbout();
    expect(await within(section).findByText("Noust installs its security fixes by itself")).toBeInTheDocument();
    expect(within(section).getByText(/From 23:00 to 01:00 the next day on the server's clock, Monday, Thursday\./)).toBeInTheDocument();
    expect(within(section).getByText("Security fixes only")).toBeInTheDocument();
    expect(within(section).getByText("23:00-01:00, into the next day")).toBeInTheDocument();
    expect(within(section).getByText(/Updated from 3\.4\.0 to 3\.4\.1/)).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("is on with a problem when the update check is off, because nothing will be installed", async () => {
    fakeBackend(routes({ ...ON, check_enabled: false }));
    const { section } = await openAbout();
    expect(await within(section).findByText("On, but nothing will be installed")).toBeInTheDocument();
    expect(within(section).getByText(/The update check is off \(updates\.check\)/)).toBeInTheDocument();
  });

  it("is on with a problem when this installation cannot update itself, in the server's own words", async () => {
    fakeBackend(routes({ ...ON, supported: false, reason: "This Noust runs from a container image", hint: "On the Docker host: docker compose pull" }));
    const { section } = await openAbout();
    expect(await within(section).findByText(/This Noust runs from a container image On the Docker host: docker compose pull/)).toBeInTheDocument();
  });

  it("sets it up from the defaults, sends what was chosen, and shows the answer", async () => {
    let saved: unknown;
    let current: unknown = OFF;
    fakeBackend(
      routes(OFF, {
        "GET /api/system/update/auto": () => json(200, current),
        "PUT /api/system/update/auto": (call) => {
          saved = call.body;
          current = ON;
          return json(200, ON);
        },
      }),
    );
    const { user, section } = await openAbout();
    await user.click(await within(section).findByRole("button", { name: "Set up" }));
    const drawer = await screen.findByRole("dialog", { name: "Automatic updates" });

    // Starts on the defaults, switched on: patch releases, 03:00 to 05:00, every day.
    expect(within(drawer).getByRole("checkbox", { name: /Install Noust's new releases by itself/ })).toBeChecked();
    expect(within(drawer).getByRole("radio", { name: "Patch releases" })).toBeChecked();
    expect(within(drawer).getByLabelText("From")).toHaveValue("03:00");

    await user.click(within(drawer).getByRole("radio", { name: "Security fixes only" }));
    await user.clear(within(drawer).getByLabelText("From"));
    await user.type(within(drawer).getByLabelText("From"), "23:00");
    await user.clear(within(drawer).getByLabelText("To"));
    await user.type(within(drawer).getByLabelText("To"), "01:00");
    expect(within(drawer).getByText(/Ends the next day\./)).toBeInTheDocument();
    await user.click(within(drawer).getByRole("checkbox", { name: "Every day" }));
    await user.click(within(drawer).getByRole("checkbox", { name: "Monday" }));
    await user.click(within(drawer).getByRole("checkbox", { name: "Thursday" }));
    await user.click(within(drawer).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(saved).toEqual({ enabled: true, channel: "security", window: "23:00-01:00", days: "mon,thu" }));
    expect(await screen.findByText("Automatic updates saved")).toBeInTheDocument();
    expect(await within(section).findByText("Noust installs its security fixes by itself")).toBeInTheDocument();
  });

  it("catches a wrong time and a window with no days before asking the server", async () => {
    const backend = fakeBackend(routes(OFF));
    const { user, section } = await openAbout();
    await user.click(await within(section).findByRole("button", { name: "Set up" }));
    const drawer = await screen.findByRole("dialog", { name: "Automatic updates" });

    await user.clear(within(drawer).getByLabelText("From"));
    await user.type(within(drawer).getByLabelText("From"), "25:00");
    await user.click(within(drawer).getByRole("checkbox", { name: "Every day" }));
    await user.click(within(drawer).getByRole("button", { name: "Save" }));

    expect(await within(drawer).findByText("Write a time as 03:00.")).toBeInTheDocument();
    expect(within(drawer).getByText("Choose at least one day.")).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/system/update/auto")).toHaveLength(0);
    await expectNoAxeViolations(drawer);
  });

  it("refuses a window that opens and closes at the same minute", async () => {
    fakeBackend(routes(OFF));
    const { user, section } = await openAbout();
    await user.click(await within(section).findByRole("button", { name: "Set up" }));
    const drawer = await screen.findByRole("dialog", { name: "Automatic updates" });
    await user.clear(within(drawer).getByLabelText("To"));
    await user.type(within(drawer).getByLabelText("To"), "03:00");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    expect(await within(drawer).findByText("A window that opens and closes at the same minute never opens.")).toBeInTheDocument();
  });

  it("shows the server's refusal verbatim and keeps the form open", async () => {
    fakeBackend(
      routes(OFF, {
        "PUT /api/system/update/auto": () => problem(409, "container_image", "This Noust runs from a container image", { hint: "On the Docker host: docker compose pull" }),
      }),
    );
    const { user, section } = await openAbout();
    await user.click(await within(section).findByRole("button", { name: "Set up" }));
    const drawer = await screen.findByRole("dialog", { name: "Automatic updates" });
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    expect(await within(drawer).findByText("Could not save the automatic update")).toBeInTheDocument();
    expect(within(drawer).getByText(/This Noust runs from a container image/)).toBeInTheDocument();
  });

  it("confirms it's you before saving, and saves once confirmed", async () => {
    let elevated = false;
    const backend = fakeBackend(
      routes(OFF, {
        "PUT /api/system/update/auto": () => (elevated ? json(200, ON) : problem(403, "elevation_required", "Confirm it's you to continue.")),
        "POST /api/auth/elevate": () => {
          elevated = true;
          return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
        },
      }),
    );
    const { user, section } = await openAbout();
    await user.click(await within(section).findByRole("button", { name: "Set up" }));
    const drawer = await screen.findByRole("dialog", { name: "Automatic updates" });
    await user.click(within(drawer).getByRole("button", { name: "Save" }));

    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
    await user.click(within(confirm).getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("Automatic updates saved")).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/system/update/auto")).toHaveLength(2);
  });

  it("speaks Spanish", async () => {
    fakeBackend(routes(ON));
    await act(() => setLocale("es"));
    renderConsole("/settings/about");
    const section = await screen.findByRole("region", { name: "Actualizaciones automáticas" });
    expect(await within(section).findByText("Noust instala sus correcciones de seguridad por sí mismo")).toBeInTheDocument();
    expect(within(section).getByText("Solo correcciones de seguridad")).toBeInTheDocument();
  });
});
