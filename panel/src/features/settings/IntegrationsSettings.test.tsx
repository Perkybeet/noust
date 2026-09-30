import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { GitHubStatus } from "../../api/queries/github";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

const NOT_CONFIGURED: GitHubStatus = { configured: false, installations: [], hooks_url: null, hooks_active: false };

const CONFIGURED: GitHubStatus = {
  configured: true,
  app_id: 424242,
  slug: "noust-web-01",
  name: "Noust web-01",
  owner: "acme",
  html_url: "https://github.com/apps/noust-web-01",
  settings_url: "https://github.com/organizations/acme/settings/apps/noust-web-01",
  install_url: "https://github.com/apps/noust-web-01/installations/new",
  installations: [
    {
      installation_id: 7001,
      account: "acme",
      account_type: "Organization",
      repository_selection: "selected",
      settings_url: "https://github.com/organizations/acme/settings/installations/7001",
    },
    {
      installation_id: 7002,
      account: "yago",
      account_type: "User",
      repository_selection: "all",
      settings_url: "https://github.com/settings/installations/7002",
    },
  ],
  hooks_url: "https://hooks.example.com/hooks/github",
  hooks_active: true,
};

function integrations(status: GitHubStatus, extra: Record<string, RouteHandler> = {}) {
  let elevated = false;
  const current = { status };
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/integrations/github": () => json(200, current.status),
    "GET /api/config": () =>
      json(200, { config: { web: { hooks_url: status.hooks_url === null ? "" : "https://hooks.example.com" } }, path: "/etc/noust/config.yaml", writable: true }),
    "POST /api/auth/elevate": () => {
      elevated = true;
      return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
    },
    "POST /api/integrations/github/manifest": (call) => {
      if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue.");
      const body = call.body as { origin: string; organization?: string };
      const base = body.organization ? `https://github.com/organizations/${body.organization}/settings/apps/new` : "https://github.com/settings/apps/new";
      return json(200, {
        manifest: { name: "Noust web-01", url: body.origin, redirect_url: `${body.origin}/integrations/github/callback` },
        post_url: `${base}?state=s7a7e`,
        state: "s7a7e",
      });
    },
    "DELETE /api/integrations/github": () => {
      if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue.");
      current.status = { ...NOT_CONFIGURED, hooks_url: status.hooks_url ?? null };
      return json(200, { removed: true, settings_url: CONFIGURED.settings_url });
    },
    ...extra,
  });
  return backend;
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

describe("Settings > Integrations", () => {
  it("is a settings tab", async () => {
    integrations(NOT_CONFIGURED);
    renderConsole("/settings/integrations");
    const tabs = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(within(tabs).getByRole("link", { name: "Integrations" })).toHaveAttribute("aria-current", "page");
    // About is the last section on a lone server, after everything that is configured.
    expect(within(tabs).getAllByRole("link").at(-1)).toHaveAccessibleName("About");
  });

  it("explains what an App gives before there is one, and passes axe", { timeout: 20_000 }, async () => {
    integrations(NOT_CONFIGURED);
    const { container } = renderConsole("/settings/integrations");
    expect(await screen.findByRole("button", { name: "Create GitHub App" })).toBeInTheDocument();
    expect(screen.getByText(/Private repositories, cloned with short-lived tokens/)).toBeInTheDocument();
    expect(screen.getByText(/A preview deployment for every pull request/)).toBeInTheDocument();
    expect(screen.getByText(/private key is kept on this server/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Organization/)).toBeInTheDocument();
    // The state first, then the steps: creating the App is the one to do now.
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: /steps/i })).toHaveTextContent("Create the App");
    // No public hooks address: why code hosts cannot deliver, and the command that fixes it.
    expect(screen.getByText("Not set")).toBeInTheDocument();
    expect(screen.getAllByText("noust web expose-hooks hooks.example.com").length).toBeGreaterThan(0);
    // Nothing to remove yet.
    expect(screen.queryByRole("button", { name: "More actions for GitHub" })).not.toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("posts the manifest to GitHub as a form, after confirming it's you", { timeout: 20_000 }, async () => {
    const backend = integrations(NOT_CONFIGURED);
    const submitted: HTMLFormElement[] = [];
    vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(function (this: HTMLFormElement) {
      submitted.push(this);
    });
    const { user } = renderConsole("/settings/integrations");
    await user.type(await screen.findByLabelText(/^Organization/), "acme");
    await user.click(screen.getByRole("button", { name: "Create GitHub App" }));
    await confirmItsMe(user);

    await waitFor(() => {
      expect(submitted).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/integrations/github/manifest").at(-1)?.body).toEqual({
      origin: window.location.origin,
      organization: "acme",
    });
    const form = submitted[0];
    if (form === undefined) throw new Error("no form");
    expect(form.method).toBe("post");
    expect(form.getAttribute("action")).toBe("https://github.com/organizations/acme/settings/apps/new?state=s7a7e");
    const field = form.querySelector<HTMLInputElement>("input[name=manifest]");
    expect(JSON.parse(field?.value ?? "null")).toEqual({
      name: "Noust web-01",
      url: window.location.origin,
      redirect_url: `${window.location.origin}/integrations/github/callback`,
    });
    expect(screen.getByText("Opening GitHub…")).toHaveAttribute("role", "status");
  });

  it("refuses an organization name GitHub would not have, without asking the server", async () => {
    const backend = integrations(NOT_CONFIGURED);
    const { user } = renderConsole("/settings/integrations");
    await user.type(await screen.findByLabelText(/^Organization/), "acme corp");
    await user.click(screen.getByRole("button", { name: "Create GitHub App" }));
    expect(await screen.findByText(/uses letters, digits and single hyphens/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/integrations/github/manifest")).toHaveLength(0);
  });

  it("shows the server's refusal verbatim", { timeout: 20_000 }, async () => {
    integrations(NOT_CONFIGURED, {
      "POST /api/integrations/github/manifest": () =>
        problem(400, "validationerror", "The console's origin must be https: http://10.0.0.5:8080", {
          hint: "Open the console through its https address and try again.",
        }),
    });
    const { user } = renderConsole("/settings/integrations");
    await user.click(await screen.findByRole("button", { name: "Create GitHub App" }));
    const title = await screen.findByText("Could not start creating the App");
    const alert = title.closest("[role=alert]");
    if (alert === null) throw new Error("not announced");
    expect(alert).toHaveTextContent("Open the console through its https address and try again.");
    expect(alert).toHaveTextContent("The console's origin must be https: http://10.0.0.5:8080");
  });

  it("describes the App, its installations and its webhook, and passes axe", { timeout: 20_000 }, async () => {
    integrations(CONFIGURED);
    const { container } = renderConsole("/settings/integrations");
    const table = await screen.findByRole("region", { name: "GitHub App installations" });
    const [acme, personal] = within(table).getAllByRole("row").slice(1);
    if (!acme || !personal) throw new Error("missing rows");
    expect(within(acme).getByText("acme")).toBeInTheDocument();
    expect(within(acme).getByText("Organization")).toBeInTheDocument();
    expect(within(acme).getByText("Selected repositories")).toBeInTheDocument();
    expect(within(acme).getByRole("link", { name: "Manage acme on GitHub (opens in a new tab)" })).toHaveAttribute(
      "href",
      "https://github.com/organizations/acme/settings/installations/7001",
    );
    expect(within(personal).getByText("Personal account")).toBeInTheDocument();
    expect(within(personal).getByText("All repositories")).toBeInTheDocument();

    expect(screen.getByText("Noust web-01")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Install on another account/ })).toHaveAttribute("href", CONFIGURED.install_url);
    expect(screen.getByText("Receiving pushes")).toBeInTheDocument();
    // Every step is done: no stepper left.
    expect(screen.queryByText("Create the App")).not.toBeInTheDocument();
    expect(screen.getByText("https://hooks.example.com/hooks/github")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("asks to install the App when it has no installation yet", async () => {
    integrations({ ...CONFIGURED, installations: [] });
    renderConsole("/settings/integrations");
    expect(await screen.findByText("Setting up")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Install it on an account" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Install on GitHub/ })).toHaveAttribute("href", CONFIGURED.install_url);
  });

  it("explains an inactive webhook, with where to switch it on", async () => {
    integrations({ ...CONFIGURED, hooks_active: false });
    renderConsole("/settings/integrations");
    expect(await screen.findByText("Not switched on at GitHub")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Let GitHub reach this server" })).toBeInTheDocument();
    expect(screen.getByText(/tick Active/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open the App's settings on GitHub/ })).toHaveAttribute("href", CONFIGURED.settings_url);
  });

  it("explains that GitHub cannot deliver while no public hooks address exists", async () => {
    integrations({ ...CONFIGURED, hooks_url: null, hooks_active: false });
    renderConsole("/settings/integrations");
    expect(await screen.findByText("Not reachable from GitHub")).toBeInTheDocument();
    // Said once for every code host, and again in GitHub's own step, each with the command.
    expect(screen.getAllByText(/does not expose one yet/)).toHaveLength(2);
    expect(screen.getAllByText("noust web expose-hooks hooks.example.com")).toHaveLength(2);
  });

  it("syncs the installations from GitHub", async () => {
    const backend = integrations(CONFIGURED, {
      "POST /api/integrations/github/installations/sync": () => json(200, { items: CONFIGURED.installations, total: 2 }),
    });
    const { user } = renderConsole("/settings/integrations");
    await user.click(await screen.findByRole("button", { name: "Sync" }));
    await expectToast("Synced 2 installations from GitHub");
    expect(backend.callsTo("POST /api/integrations/github/installations/sync")).toHaveLength(1);
    await waitFor(() => {
      expect(backend.callsTo("GET /api/integrations/github").length).toBeGreaterThan(1);
    });
  });

  it("removes the App once its name is typed, and says where to delete it on GitHub", { timeout: 20_000 }, async () => {
    const backend = integrations(CONFIGURED);
    const { user } = renderConsole("/settings/integrations");
    await user.click(await screen.findByRole("button", { name: "More actions for GitHub" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove GitHub App" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove Noust web-01" });
    expect(dialog).toHaveTextContent("Deletes the App's private key, its webhook secret and the list of installations");
    const action = within(dialog).getByRole("button", { name: "Remove GitHub App" });
    expect(action).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "Noust web-01");
    await user.click(action);
    await confirmItsMe(user);
    await expectToast("Removed Noust web-01 from this server");
    expect(backend.callsTo("DELETE /api/integrations/github").length).toBeGreaterThan(0);
    expect(await screen.findByText(/It still exists on GitHub/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Delete the App on GitHub/ })).toHaveAttribute("href", CONFIGURED.settings_url);
    expect(await screen.findByRole("button", { name: "Create GitHub App" })).toBeInTheDocument();
  });
});

describe("Settings > Integrations in Spanish", () => {
  it("explains the App and the webhook state in Spanish, with no accessibility violations", { timeout: 20_000 }, async () => {
    await act(() => setLocale("es"));
    integrations(NOT_CONFIGURED);
    const { container } = renderConsole("/settings/integrations");
    expect(await screen.findByRole("button", { name: "Crear GitHub App" })).toBeInTheDocument();
    expect(screen.getByText(/Repositorios privados, clonados con tokens de corta duración/)).toBeInTheDocument();
    expect(screen.getByText("Sin conectar")).toBeInTheDocument();
    expect(screen.getByText("Sin definir")).toBeInTheDocument();
    expect(screen.getAllByText("noust web expose-hooks hooks.example.com").length).toBeGreaterThan(0);
    await expectNoAxeViolations(container);
  });

  it("describes the App's installations in Spanish", { timeout: 20_000 }, async () => {
    await act(() => setLocale("es"));
    integrations(CONFIGURED);
    renderConsole("/settings/integrations");
    const table = await screen.findByRole("region", { name: "Instalaciones de la GitHub App" });
    const [acme] = within(table).getAllByRole("row").slice(1);
    if (!acme) throw new Error("missing row");
    expect(within(acme).getByText("Organización")).toBeInTheDocument();
    expect(within(acme).getByText("Repositorios seleccionados")).toBeInTheDocument();
    expect(screen.getByText("Recibiendo push")).toBeInTheDocument();
  });
});
