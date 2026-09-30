import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

function aboutRoutes(version: Record<string, unknown>): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/system/version": () => json(200, version),
    "GET /api/config": () => json(200, { config: {}, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/system/update": () => json(200, { current_version: "2.0.0", method: "apt", supported: true }),
  };
}

describe("Settings > About", () => {
  it("offers an update with the command that installs it, and passes axe", { timeout: 20_000 }, async () => {
    fakeBackend(
      aboutRoutes({
        current_version: "2.0.0",
        latest_version: "2.1.0",
        has_update: true,
        update_command: "pip install --upgrade wasm-cli",
        release_url: "https://github.com/Perkybeet/wasm/releases/tag/v2.1.0",
      }),
    );
    const { container } = renderConsole("/settings/about");
    const version = await screen.findByRole("region", { name: "Version" });
    expect(await within(version).findByText("Version 2.1.0 can be installed")).toBeInTheDocument();
    expect(within(version).getByText("2.0.0")).toBeInTheDocument();
    expect(within(version).getByText("pip install --upgrade wasm-cli")).toBeInTheDocument();
    expect(within(version).getByRole("link", { name: /What is new in 2.1.0/ })).toHaveAttribute(
      "href",
      "https://github.com/Perkybeet/wasm/releases/tag/v2.1.0",
    );
    // How this server updates, the rename, and the licence.
    expect(await screen.findByText("the apt repository")).toBeInTheDocument();
    expect(screen.getByText("Noust was called WASM")).toBeInTheDocument();
    expect(screen.getByText("AGPL-3.0-or-later")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Source code/ })).toHaveAttribute("href", "https://github.com/Perkybeet/noust");
    expect(screen.getByText("noust config show")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says when checking for new versions is off, and where to turn it on", async () => {
    fakeBackend(aboutRoutes({ current_version: "3.1.0", has_update: false, status: "disabled" }));
    renderConsole("/settings/about");
    expect(await screen.findByText("Checking for new versions is off")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "General settings" })).toHaveAttribute("href", "/settings");
  });

  it("does not turn an unsafe release_url into a link", async () => {
    fakeBackend(
      aboutRoutes({
        current_version: "2.0.0",
        latest_version: "2.1.0",
        has_update: true,
        update_command: "pip install --upgrade wasm-cli",
        release_url: "javascript:alert(1)",
      }),
    );
    renderConsole("/settings/about");
    expect(await screen.findByText("Version 2.1.0 can be installed")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /What is new in/ })).not.toBeInTheDocument();
  });

  it("says a published release is on the way without offering a command that installs nothing", async () => {
    fakeBackend(
      aboutRoutes({
        current_version: "2.2.0",
        latest_version: "2.2.0",
        has_update: false,
        published_version: "2.3.0",
        update_state: "on_the_way",
        update_command: "sudo apt update && sudo apt install --only-upgrade wasm",
        release_url: "https://github.com/Perkybeet/wasm/releases/tag/v2.3.0",
      }),
    );
    renderConsole("/settings/about");
    const version = await screen.findByRole("region", { name: "Version" });
    expect(await within(version).findByText("Version 2.3.0 is on the way")).toBeInTheDocument();
    expect(within(version).getByText(/the package for this server is not available yet/)).toBeInTheDocument();
    expect(within(version).queryByText(/sudo apt update/)).not.toBeInTheDocument();
    expect(within(version).getByRole("link", { name: /What is new in 2.3.0/ })).toBeInTheDocument();
  });

  it("says the package index has not seen a release, refreshes it, then offers the upgrade", { timeout: 20_000 }, async () => {
    const JOB = { id: "j9", type: "os_refresh", name: "Refresh the package lists", description: "", status: "running", progress: 1, total_steps: 100, current_step: "", created_at: "2026-09-30T10:00:00Z" };
    const backend = fakeBackend({
      ...aboutRoutes({
        current_version: "3.1.2",
        latest_version: "3.1.3",
        indexed_version: "3.1.2",
        announced_version: "3.1.3",
        has_update: false,
        published_version: "3.1.3",
        update_state: "index_behind",
        update_command: null,
        refresh_command: "noust server updates refresh",
        release_url: "https://github.com/Perkybeet/noust/releases/tag/v3.1.3",
      }),
      "POST /api/server/updates/refresh": () => json(202, { job_id: "j9", status: "pending", message: "Refreshing" }),
      "GET /api/jobs/j9": () => json(200, JOB),
    });
    const { user, container } = renderConsole("/settings/about");
    const version = await screen.findByRole("region", { name: "Version" });
    expect(await within(version).findByText("3.1.3 is published; this server's package index has not seen it yet")).toBeInTheDocument();
    expect(within(version).getByText("noust server updates refresh")).toBeInTheDocument();
    expect(within(version).queryByText(/sudo apt/)).not.toBeInTheDocument();
    await expectNoAxeViolations(container);

    await user.click(within(version).getByRole("button", { name: "Refresh the package index" }));
    expect(await within(version).findByRole("button", { name: "Refreshing the package index" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/server/updates/refresh")).toHaveLength(1);

    // The index now lists it: once the job ends the version is asked again, and the upgrade offered.
    backend.on("GET /api/system/version", () =>
      json(200, {
        current_version: "3.1.2",
        latest_version: "3.1.3",
        indexed_version: "3.1.3",
        announced_version: "3.1.3",
        has_update: true,
        update_state: "update_available",
        update_command: "sudo apt update && sudo apt install noust",
        release_url: "https://github.com/Perkybeet/noust/releases/tag/v3.1.3",
      }),
    );
    backend.on("GET /api/jobs/j9", () => json(200, { ...JOB, status: "completed", progress: 100 }));
    expect(await within(version).findByText("Version 3.1.3 can be installed", {}, { timeout: 10_000 })).toBeInTheDocument();
    expect(within(version).getByText("sudo apt update && sudo apt install noust")).toBeInTheDocument();
  });

  it("shows the package manager's own words when the refresh fails", { timeout: 20_000 }, async () => {
    fakeBackend({
      ...aboutRoutes({
        current_version: "3.1.2",
        latest_version: "3.1.3",
        announced_version: "3.1.3",
        has_update: false,
        update_state: "index_behind",
        refresh_command: "noust server updates refresh",
      }),
      "POST /api/server/updates/refresh": () => json(202, { job_id: "j9", status: "pending", message: "Refreshing" }),
      "GET /api/jobs/j9": () =>
        json(200, { id: "j9", type: "os_refresh", name: "Refresh", description: "", status: "failed", progress: 100, total_steps: 100, current_step: "", created_at: "2026-09-30T10:00:00Z", error: "E: Could not resolve 'download.opensuse.org'" }),
    });
    const { user } = renderConsole("/settings/about");
    const version = await screen.findByRole("region", { name: "Version" });
    await user.click(await within(version).findByRole("button", { name: "Refresh the package index" }));
    expect(await within(version).findByText("Could not refresh the package index")).toBeInTheDocument();
    expect(within(version).getByText("E: Could not resolve 'download.opensuse.org'")).toBeInTheDocument();
  });

  it("says when it is up to date, and when it could not tell", async () => {
    const backend = fakeBackend(
      aboutRoutes({ current_version: "2.1.0", latest_version: "2.1.0", has_update: false, update_command: null, release_url: null }),
    );
    const { user } = renderConsole("/settings/about");
    expect(await screen.findByText("Up to date: 2.1.0 is the newest version.")).toBeInTheDocument();

    backend.on("GET /api/system/version", () =>
      json(200, { current_version: "2.1.0", latest_version: null, has_update: false, update_command: null, release_url: null }),
    );
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByText("Could not find out whether a newer version exists.")).toBeInTheDocument();
    expect(backend.callsTo("GET /api/system/version")).toHaveLength(2);
  });
});

describe("Settings > About in Spanish", () => {
  it("offers an update and describes the installation in Spanish, with no accessibility violations", { timeout: 20_000 }, async () => {
    await act(() => setLocale("es"));
    fakeBackend(
      aboutRoutes({
        current_version: "2.0.0",
        latest_version: "2.1.0",
        has_update: true,
        update_command: "pip install --upgrade wasm-cli",
        release_url: "https://github.com/Perkybeet/wasm/releases/tag/v2.1.0",
      }),
    );
    const { container } = renderConsole("/settings/about");
    const version = await screen.findByRole("region", { name: "Versión" });
    expect(await within(version).findByText("Se puede instalar la versión 2.1.0")).toBeInTheDocument();
    expect(within(version).getByRole("link", { name: /Novedades de 2.1.0/ })).toHaveAttribute(
      "href",
      "https://github.com/Perkybeet/wasm/releases/tag/v2.1.0",
    );
    expect(screen.getByRole("region", { name: "Esta instalación" })).toBeInTheDocument();
    expect(screen.getByText("noust config show")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });
});
