import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

function aboutRoutes(version: Record<string, unknown>): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/system/version": () => json(200, version),
    "GET /api/config": () => json(200, { config: {}, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/system/update": () => json(200, { current_version: "2.0.0", method: "apt", supported: true }),
    "GET /api/system/update/auto": () =>
      json(200, {
        settings: { enabled: false, channel: "patch", window: "03:00-05:00", days: "daily" },
        supported: true,
        check_enabled: true,
        window_open: false,
        timer: { installed: false },
      }),
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
    expect(screen.getByText(/Noust is the new name of WASM since version 3\.0/)).toBeInTheDocument();
    expect(screen.getByText("AGPL-3.0-or-later")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /github\.com\/Perkybeet\/noust/ })).toHaveAttribute("href", "https://github.com/Perkybeet/noust");
    expect(screen.getByText("noust config show")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("shows the copyright, the licence, that there is no warranty and where the source is (AGPL-3.0, sections 5 and 13)", { timeout: 20_000 }, async () => {
    fakeBackend(aboutRoutes({ current_version: "4.0.0", latest_version: "4.0.0", has_update: false }));
    const { container } = renderConsole("/settings/about");
    const legal = (await screen.findByRole("heading", { name: "Licence and warranty" })).closest("section");
    if (legal === null) throw new Error("no legal notice card");
    expect(within(legal).getByText("© 2024-2026 Yago López Prado")).toBeInTheDocument();
    // The licence is named, its short identifier is shown and its text is one click away.
    expect(within(legal).getByRole("link", { name: /GNU Affero General Public License, version 3 or later/ })).toHaveAttribute(
      "href",
      "https://github.com/Perkybeet/noust/blob/main/LICENSE",
    );
    expect(within(legal).getByText("AGPL-3.0-or-later")).toBeInTheDocument();
    expect(within(legal).getByText(/You may use, study, change and share it/)).toBeInTheDocument();
    expect(within(legal).getByText("There is no warranty for Noust, to the extent permitted by law.")).toBeInTheDocument();
    expect(within(legal).getByRole("link", { name: /github\.com\/Perkybeet\/noust/ })).toHaveAttribute("href", "https://github.com/Perkybeet/noust");
    // It is one place, not a licence link beside another: the page names the licence once.
    expect(screen.getAllByRole("link", { name: /GNU Affero General Public License/ })).toHaveLength(1);
    await expectNoAxeViolations(container);
  });

  it("says a security release is out, apart from the update, with what it fixes", async () => {
    fakeBackend(
      aboutRoutes({
        current_version: "3.3.1",
        latest_version: "3.3.2",
        has_update: true,
        update_state: "update_available",
        update_command: "sudo apt update && sudo apt install --only-upgrade noust",
        release_url: "https://github.com/Perkybeet/noust/releases/tag/v3.3.2",
        security_update: true,
        security_version: "3.3.2",
        advisory_url: "https://github.com/Perkybeet/noust/security/advisories/GHSA-abcd-1234-wxyz",
      }),
    );
    const { container } = renderConsole("/settings/about");
    const version = await screen.findByRole("region", { name: "Version" });
    expect(await within(version).findByText("Version 3.3.2 fixes a security vulnerability")).toBeInTheDocument();
    expect(within(version).getByRole("link", { name: /What 3.3.2 fixes/ })).toHaveAttribute(
      "href",
      "https://github.com/Perkybeet/noust/security/advisories/GHSA-abcd-1234-wxyz",
    );
    // The update itself is still offered, with its command.
    expect(within(version).getByText("Version 3.3.2 can be installed")).toBeInTheDocument();
    expect(within(version).getByText("sudo apt update && sudo apt install --only-upgrade noust")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says nothing about security for an ordinary update", async () => {
    fakeBackend(
      aboutRoutes({
        current_version: "3.3.1",
        latest_version: "3.3.2",
        has_update: true,
        update_command: "pip install --upgrade noust",
        security_update: false,
        advisory_url: "https://github.com/Perkybeet/noust/security/advisories/GHSA-abcd-1234-wxyz",
      }),
    );
    renderConsole("/settings/about");
    expect(await screen.findByText("Version 3.3.2 can be installed")).toBeInTheDocument();
    expect(screen.queryByText(/security vulnerability/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /What 3.3.2 fixes/ })).not.toBeInTheDocument();
  });

  it("does not turn an unsafe advisory address into a link", async () => {
    fakeBackend(
      aboutRoutes({
        current_version: "3.3.1",
        latest_version: "3.3.2",
        has_update: true,
        update_command: "pip install --upgrade noust",
        security_update: true,
        security_version: "3.3.2",
        advisory_url: "javascript:alert(1)",
      }),
    );
    renderConsole("/settings/about");
    expect(await screen.findByText("Version 3.3.2 fixes a security vulnerability")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /What 3.3.2 fixes/ })).not.toBeInTheDocument();
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

  it("draws one skeleton, and none of its sections, until the version and the automatic updates are read", { timeout: 20_000 }, async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const backend = fakeBackend({
      ...aboutRoutes({ current_version: "2.1.0", latest_version: "2.1.0", has_update: false, update_command: null, release_url: null }),
      "GET /api/system/update/auto": async () => {
        await held;
        return json(200, { settings: { enabled: false, channel: "patch", window: "03:00-05:00", days: "daily" }, supported: true, check_enabled: true, window_open: false, timer: { installed: false } });
      },
    });
    renderConsole("/settings/about");
    await screen.findByRole("heading", { level: 1, name: "Settings" });
    await waitFor(() => expect(backend.callsTo("GET /api/system/update/auto")).toHaveLength(1));

    // The version is in, the automatic updates are not: a section drawn now would be pushed down
    // by the one above it when that arrives.
    await waitFor(() => expect(backend.callsTo("GET /api/system/version")).toHaveLength(1));
    expect(screen.getByText("Loading the version and the automatic updates")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Version" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "What's new" })).not.toBeInTheDocument();

    release();
    expect(await screen.findByRole("region", { name: "Version" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Automatic updates" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "What's new" })).toBeInTheDocument();
    expect(screen.queryByText("Loading the version and the automatic updates")).not.toBeInTheDocument();
  });

  it("draws its sections when the automatic updates are refused, and asks for them once more at most", { timeout: 20_000 }, async () => {
    const backend = fakeBackend({
      ...aboutRoutes({ current_version: "2.1.0", latest_version: "2.1.0", has_update: false, update_command: null, release_url: null }),
      "GET /api/system/update/auto": () => problem(403, "forbidden", "Your role cannot read this"),
    });
    renderConsole("/settings/about");

    expect(await screen.findByRole("region", { name: "Version" })).toBeInTheDocument();
    expect(await screen.findByText("Could not load the automatic update")).toBeInTheDocument();
    // A refusal answered, the section mounted and asked again: a page that went back to its
    // skeleton for that asked again and again.
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(backend.callsTo("GET /api/system/update/auto").length).toBeLessThanOrEqual(2);
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

  it("shows the copyright, the licence, that there is no warranty and where the source is, in Spanish", { timeout: 20_000 }, async () => {
    await act(() => setLocale("es"));
    fakeBackend(aboutRoutes({ current_version: "4.0.0", latest_version: "4.0.0", has_update: false }));
    const { container } = renderConsole("/settings/about");
    const legal = (await screen.findByRole("heading", { name: "Licencia y garantía" })).closest("section");
    if (legal === null) throw new Error("no legal notice card");
    // The name of the holder is not translated.
    expect(within(legal).getByText("© 2024-2026 Yago López Prado")).toBeInTheDocument();
    expect(within(legal).getByRole("link", { name: /Licencia Pública General Affero de GNU, versión 3 o posterior/ })).toHaveAttribute(
      "href",
      "https://github.com/Perkybeet/noust/blob/main/LICENSE",
    );
    expect(within(legal).getByText("AGPL-3.0-or-later")).toBeInTheDocument();
    expect(within(legal).getByText("No hay ninguna garantía sobre Noust, en la medida que permita la ley.")).toBeInTheDocument();
    expect(within(legal).getByText(/El código fuente completo está en/)).toBeInTheDocument();
    expect(within(legal).getByRole("link", { name: /github\.com\/Perkybeet\/noust/ })).toHaveAttribute("href", "https://github.com/Perkybeet/noust");
    await expectNoAxeViolations(container);
  });
});
