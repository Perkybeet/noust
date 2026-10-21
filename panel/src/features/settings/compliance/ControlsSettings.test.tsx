import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { COMPLIANCE } from "../../../test/compliance";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";

const CONFIG = {
  config: {
    web: {
      max_failed_attempts: 5,
      lockout_duration: 900,
      rate_limit_enabled: true,
      rate_limit_requests: 120,
      rate_limit_window: 60,
      token_expiration_hours: 12,
      ip_whitelist: [],
    },
  },
  path: "/etc/noust/config.yaml",
  writable: true,
};

function controlsRoutes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes({ ...SESSION }),
    "GET /api/config": () => json(200, CONFIG),
    "GET /api/approvals/policy": () => json(200, { enabled: false, approvers: ["security"], request_hours: 24, execute_minutes: 30, reason_required: false, rules: [] }),
    "GET /api/compliance": () => json(200, COMPLIANCE),
    "GET /api/ens/profile": () =>
      json(200, {
        profile: "standard",
        baseline: [{ key: "auth.session.idle_minutes", description: "Idle timeout", standard_value: "30", ens_value: "15", measures: ["mp.eq.2"] }],
      }),
    ...extra,
  };
}

/**
 * Settings > Controls: the compliance frameworks, the controls they are made of and the security
 * profile the server enforces on every sign-in. They were the end of Security; the frameworks' own
 * behaviour is held by ComplianceSection.test.tsx.
 */
describe("Settings > Controls", () => {
  it("states the security profile and the limits the server enforces, read-only", async () => {
    fakeBackend(controlsRoutes());
    renderConsole("/settings/controls");
    const policy = await screen.findByRole("region", { name: "Security profile" });
    expect(await within(policy).findByText("Standard", { selector: "dd *, dd" })).toBeInTheDocument();
    expect(await within(policy).findByText("5 failed attempts lock it for 15 minutes")).toBeInTheDocument();
    expect(within(policy).getByText("120 requests a minute")).toBeInTheDocument();
    expect(within(policy).getByText("Any address")).toBeInTheDocument();
    // Four-eyes approvals off is an off at a glance: grey, a ring, the word (item 56).
    expect(within(policy).getByText("Off")).toHaveAttribute("data-state", "stopped");
    await userEvent.click(within(policy).getByRole("button", { name: "Show what the profile fixes" }));
    expect(await within(policy).findByText("auth.session.idle_minutes")).toBeInTheDocument();
    expect(within(policy).queryByRole("textbox")).toBeNull();
  });

  it("shows four-eyes approvals on in the running green, with who decides under it", async () => {
    fakeBackend(
      controlsRoutes({
        "GET /api/approvals/policy": () =>
          json(200, { enabled: true, approvers: ["security"], request_hours: 24, execute_minutes: 30, reason_required: false, rules: [] }),
      }),
    );
    renderConsole("/settings/controls");
    const policy = await screen.findByRole("region", { name: "Security profile" });
    const on = await within(policy).findByText("On");
    expect(on).toHaveAttribute("data-state", "running");
    expect(on.closest("button, [role='switch']")).toBeNull();
    expect(within(policy).getByText("Decided by: Security officer")).toBeInTheDocument();
  });

  it("is in Spanish, with the limits worded as sentences", async () => {
    await act(() => setLocale("es"));
    fakeBackend(controlsRoutes());
    const { container } = renderConsole("/settings/controls");
    const policy = await screen.findByRole("region", { name: "Perfil de seguridad" });
    expect(await within(policy).findByText("5 intentos fallidos la bloquean durante 15 minutos")).toBeInTheDocument();
    expect(within(policy).getByText("120 solicitudes por minuto")).toBeInTheDocument();
    expect(within(policy).getByText("Cualquier dirección")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });
});
