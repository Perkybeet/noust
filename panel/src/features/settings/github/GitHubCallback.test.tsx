import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { GitHubStatus } from "../../../api/queries/github";
import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";

const CREATED: GitHubStatus = {
  configured: true,
  app_id: 424242,
  slug: "wasm-web-01",
  name: "WASM web-01",
  owner: "yago",
  html_url: "https://github.com/apps/wasm-web-01",
  settings_url: "https://github.com/settings/apps/wasm-web-01",
  install_url: "https://github.com/apps/wasm-web-01/installations/new",
  installations: [],
  hooks_url: "https://hooks.example.com/hooks/github",
  hooks_active: true,
};

const INSTALLATION = {
  installation_id: 7001,
  account: "acme",
  account_type: "Organization",
  repository_selection: "selected",
  settings_url: "https://github.com/organizations/acme/settings/installations/7001",
};

function callbackBackend(extra: Record<string, RouteHandler> = {}) {
  let elevated = false;
  const guard = (answer: () => Response): RouteHandler => () =>
    elevated ? answer() : problem(403, "elevation_required", "Confirm it's you to continue.");
  return fakeBackend({
    ...signedInRoutes(),
    "GET /api/integrations/github": () => json(200, CREATED),
    "POST /api/auth/elevate": () => {
      elevated = true;
      return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
    },
    "POST /api/integrations/github/manifest/conversions": guard(() => json(200, CREATED)),
    "POST /api/integrations/github/installations": guard(() => json(200, INSTALLATION)),
    ...extra,
  });
}

async function confirmItsMe(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
  await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
  await user.click(within(confirm).getByRole("button", { name: "Confirm" }));
}

async function expectToast(text: string): Promise<void> {
  await waitFor(() => {
    expect([...document.querySelectorAll(".toast")].some((toast) => toast.textContent.includes(text))).toBe(true);
  });
}

describe("the GitHub callback", () => {
  it("finishes creating the App with GitHub's code, once, and returns to Integrations", { timeout: 20_000 }, async () => {
    const backend = callbackBackend();
    // A state that reads as a number must reach the server exactly as GitHub sent it.
    const { user, location } = renderConsole("/integrations/github/callback?code=a1b2c3d4&state=1e5");
    expect(await screen.findByRole("heading", { level: 1, name: "Connecting GitHub" })).toBeInTheDocument();
    expect(screen.getByText("Finishing the App with GitHub's answer…")).toBeInTheDocument();
    await confirmItsMe(user);
    await expectToast("Created the GitHub App WASM web-01");
    await waitFor(() => {
      expect(location().pathname).toBe("/settings/integrations");
    });
    const conversions = backend.callsTo("POST /api/integrations/github/manifest/conversions");
    // The refused first attempt and its one retry after "Confirm it's you"; never a third.
    expect(conversions.map((call) => call.body)).toEqual([
      { code: "a1b2c3d4", state: "1e5" },
      { code: "a1b2c3d4", state: "1e5" },
    ]);
    // The install step is what Integrations shows next.
    expect(await screen.findByText("Next: install the App")).toBeInTheDocument();
  });

  it("records an installation and returns to Integrations", { timeout: 20_000 }, async () => {
    const backend = callbackBackend();
    const { user, location } = renderConsole("/integrations/github/callback?installation_id=7001&setup_action=install");
    expect(await screen.findByText("Recording the installation…")).toBeInTheDocument();
    await confirmItsMe(user);
    await expectToast("Installed the GitHub App on acme");
    await waitFor(() => {
      expect(location().pathname).toBe("/settings/integrations");
    });
    expect(backend.callsTo("POST /api/integrations/github/installations").at(-1)?.body).toEqual({ installation_id: 7001 });
  });

  it("says an installation was updated when GitHub says so", { timeout: 20_000 }, async () => {
    callbackBackend();
    const { user } = renderConsole("/integrations/github/callback?installation_id=7001&setup_action=update");
    await confirmItsMe(user);
    await expectToast("Updated the installation on acme");
  });

  it("shows the server's refusal verbatim with its fix, and passes axe", { timeout: 20_000 }, async () => {
    const backend = callbackBackend({
      "POST /api/integrations/github/manifest/conversions": () =>
        problem(400, "integrationerror", "This GitHub callback does not belong to an App creation started here", {
          hint: "Start again from Integrations: a creation is valid for ten minutes, and its callback can be used once.",
        }),
    });
    const { container, location } = renderConsole("/integrations/github/callback?code=a1b2&state=forged");
    const title = await screen.findByText("The GitHub App was not created on this server");
    const alert = title.closest("[role=alert]");
    if (!(alert instanceof HTMLElement)) throw new Error("not announced");
    expect(alert).toHaveTextContent("This GitHub callback does not belong to an App creation started here");
    expect(alert).toHaveTextContent("Start again from Integrations: a creation is valid for ten minutes");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to integrations" })).toBeInTheDocument();
    // It stays: the operator reads what went wrong before leaving.
    expect(location().pathname).toBe("/integrations/github/callback");
    expect(backend.callsTo("POST /api/integrations/github/manifest/conversions")).toHaveLength(1);
    await expectNoAxeViolations(container);
  });

  it("offers to confirm again when the operator dismissed the confirmation", { timeout: 20_000 }, async () => {
    const backend = callbackBackend();
    const { user } = renderConsole("/integrations/github/callback?installation_id=7001&setup_action=install");
    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("Nothing was changed because the confirmation was cancelled.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm and continue" }));
    await confirmItsMe(user);
    await expectToast("Installed the GitHub App on acme");
    expect(backend.callsTo("POST /api/integrations/github/installations").at(-1)?.body).toEqual({ installation_id: 7001 });
  });

  it("explains an installation waiting for an organization owner", async () => {
    const backend = callbackBackend();
    renderConsole("/integrations/github/callback?setup_action=request");
    expect(await screen.findByText("Waiting for an organization owner")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to integrations" })).toHaveAttribute("href", "/settings/integrations");
    expect(backend.callsTo("POST /api/integrations/github/installations")).toHaveLength(0);
  });

  it("does nothing when opened without what GitHub sends", async () => {
    const backend = callbackBackend();
    renderConsole("/integrations/github/callback");
    expect(await screen.findByText("Nothing to finish here")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "POST" && call.path.startsWith("/api/integrations"))).toHaveLength(0);
  });
});

describe("the GitHub callback in Spanish", () => {
  it("explains an installation waiting for an organization owner, in Spanish, with no accessibility violations", async () => {
    await act(() => setLocale("es"));
    callbackBackend();
    const { container } = renderConsole("/integrations/github/callback?setup_action=request");
    expect(await screen.findByRole("heading", { level: 1, name: "Conectando GitHub" })).toBeInTheDocument();
    expect(screen.getByText("Esperando a un propietario de la organización")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Volver a integraciones" })).toHaveAttribute("href", "/settings/integrations");
    await expectNoAxeViolations(container);
  });
});
