/**
 * Settings > About, the parts that answer "what do I do about this server" (checklist N-09, N-24,
 * N-33): what changed in each version, what an update will change, whether the installation has
 * what a deployment needs, and the report an issue asks for.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

const VERSION = { current_version: "3.3.1", latest_version: "3.3.1", has_update: false, update_state: "up_to_date", status: "checked" };

const CHANGELOG = {
  current_version: "3.3.1",
  entries: [
    { version: "3.3.1", date: "2026-10-05", action_required: false, items: [{ text: "Sudo mode lasts while you work", action_required: false }] },
    { version: "3.3.0", date: "2026-10-03", action_required: true, items: [{ text: "Databases as they really are", action_required: false }, { text: "run noust db exposure after updating", action_required: true }] },
    { version: "3.2.1", date: "2026-10-03", action_required: false, items: [{ text: "A Compose fix", action_required: false }] },
    { version: "3.2.0", date: "2026-10-02", action_required: false, items: [{ text: "Docker Compose deploys properly", action_required: false }] },
  ],
};

const DOCTOR = {
  ready: false,
  issues: 1,
  warnings: 1,
  sections: [
    { title: "Platform", explain: null, items: [{ status: "ok", text: "Ubuntu 24.04, amd64: supported", fix: null, notes: [] }] },
    {
      title: "Core Dependencies",
      explain: null,
      items: [
        { status: "ok", text: "curl: curl 8.5.0", fix: null, notes: [] },
        { status: "fail", text: "git: not installed", fix: "sudo apt install git", notes: [] },
      ],
    },
    { title: "SSL/TLS (Certbot)", explain: null, items: [{ status: "warn", text: "certbot: not installed", fix: "sudo apt install certbot", notes: [] }] },
  ],
};

const REPORT = {
  version: "3.3.1",
  generated_at: "2026-10-13T10:00:00Z",
  hide_domains: false,
  markdown: "# Noust support report\n\n## Noust\n\n```\nNoust: 3.3.1\n```\n",
  sections: [
    { key: "noust", title: "Noust", body: "Noust: 3.3.1", collected: true },
    { key: "journal", title: "Journal", body: "(left out: it needs secrets.reveal)", collected: false },
  ],
};

function routes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/system/version": () => json(200, VERSION),
    "GET /api/config": () => json(200, { config: {}, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/system/update": () => json(200, { current_version: "3.3.1", method: "apt", supported: true }),
    "GET /api/system/changelog": () => json(200, CHANGELOG),
    "GET /api/system/doctor": () => json(200, DOCTOR),
    "GET /api/system/support-report": () => json(200, REPORT),
    ...extra,
  };
}

describe("Settings > About > What's new", () => {
  it("lists the newest versions first, flags a line that needs action, and shows the rest on request", async () => {
    fakeBackend(routes());
    const { user } = renderConsole("/settings/about");

    const section = await screen.findByRole("region", { name: "What's new" });
    // Folded, so what is read does not move the page; one press shows it.
    await user.click(within(section).getByRole("button", { name: "Show what changed" }));
    expect(await within(section).findByText("Sudo mode lasts while you work")).toBeInTheDocument();
    expect(within(section).getByText("Version 3.3.1")).toBeInTheDocument();
    expect(within(section).getByText(/Installed/)).toBeInTheDocument();
    expect(within(section).getByText("Action required")).toBeInTheDocument();
    expect(within(section).getByText("run noust db exposure after updating")).toBeInTheDocument();
    // Three versions at first.
    expect(within(section).queryByText("Docker Compose deploys properly")).not.toBeInTheDocument();

    await user.click(within(section).getByRole("button", { name: "Show earlier versions" }));

    expect(within(section).getByText("Docker Compose deploys properly")).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Show fewer versions" })).toHaveAttribute("aria-expanded", "true");
  });

  it("says when the package carries no notes, and when it could not read them", async () => {
    fakeBackend(routes({ "GET /api/system/changelog": () => json(200, { current_version: "3.3.1", entries: [] }) }));
    const { user } = renderConsole("/settings/about");
    await user.click(await screen.findByRole("button", { name: "Show what changed" }));
    expect(await screen.findByText("This installation carries no release notes.")).toBeInTheDocument();
  });

  it("reads what an update will change, before it is installed", async () => {
    const backend = fakeBackend(
      routes({
        "GET /api/system/version": () =>
          json(200, { current_version: "3.3.1", latest_version: "3.4.0", has_update: true, update_state: "update_available", update_command: "apt install --only-upgrade noust", status: "checked" }),
        "GET /api/system/release-notes": () =>
          json(200, {
            version: "3.4.0",
            available: true,
            entry: { version: "3.4.0", date: "", action_required: true, items: [{ text: "A new thing", action_required: false }, { text: "run noust setup doctor", action_required: true }] },
            url: "https://github.com/Perkybeet/noust/releases/tag/v3.4.0",
            reason: "",
          }),
      }),
    );
    const { user } = renderConsole("/settings/about");

    const version = await screen.findByRole("region", { name: "Version" });
    await user.click(await within(version).findByRole("button", { name: "What 3.4.0 changes" }));

    expect(await within(version).findByText("A new thing")).toBeInTheDocument();
    expect(within(version).getByText("1 line needs you to act.")).toBeInTheDocument();
    expect(within(version).getByRole("link", { name: /Open the release on GitHub/ })).toHaveAttribute("href", "https://github.com/Perkybeet/noust/releases/tag/v3.4.0");
    expect(backend.callsTo("GET /api/system/release-notes")[0]?.search.get("version")).toBe("3.4.0");
  });

  it("says why the notes of an update cannot be read, and still leads to the release", async () => {
    fakeBackend(
      routes({
        "GET /api/system/version": () => json(200, { current_version: "3.3.1", latest_version: "3.4.0", has_update: true, update_state: "update_available", status: "checked" }),
        "GET /api/system/release-notes": () =>
          json(200, { version: "3.4.0", available: false, entry: null, url: "https://github.com/Perkybeet/noust/releases/tag/v3.4.0", reason: "Update checks are turned off (updates.check)" }),
      }),
    );
    const { user } = renderConsole("/settings/about");

    await user.click(await screen.findByRole("button", { name: "What 3.4.0 changes" }));

    expect(await screen.findByText("Update checks are turned off (updates.check)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open the release on GitHub/ })).toBeInTheDocument();
  });
});

describe("Settings > About > Installation check", () => {
  it("says what is missing with the command that fixes it, in words and shapes", async () => {
    fakeBackend(routes());
    const { user } = renderConsole("/settings/about");

    const section = await screen.findByRole("region", { name: "Installation check" });
    // The verdict is there at once; the checks behind it are one press away.
    expect(await within(section).findByText("1 thing a deployment needs is missing")).toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Show the checks" }));
    const core = within(section).getByRole("list", { name: "Checks of Core Dependencies" });
    expect(within(core).getByText("git: not installed")).toBeInTheDocument();
    expect(within(core).getByText("Missing")).toBeInTheDocument();
    expect(within(core).getByText("sudo apt install git")).toBeInTheDocument();
    expect(within(section).getByText("sudo apt install certbot")).toBeInTheDocument();
    expect(within(section).getAllByText("OK").length).toBeGreaterThan(0);
    expect(within(section).getByText("Warning")).toBeInTheDocument();
  });

  it("is ready when nothing a deployment needs is missing, with its warnings counted", async () => {
    fakeBackend(routes({ "GET /api/system/doctor": () => json(200, { ...DOCTOR, ready: true, issues: 0, warnings: 2 }) }));
    renderConsole("/settings/about");

    expect(await screen.findByText("Ready, with 2 warnings")).toBeInTheDocument();
  });

  it("asks again when pressed", async () => {
    const backend = fakeBackend(routes());
    const { user } = renderConsole("/settings/about");
    const section = await screen.findByRole("region", { name: "Installation check" });
    await within(section).findByText("1 thing a deployment needs is missing");

    await user.click(within(section).getByRole("button", { name: "Run the check again" }));

    await waitFor(() => expect(backend.callsTo("GET /api/system/doctor").length).toBe(2));
  });

  it("shows the failure of the check as an error, not as an empty list", async () => {
    fakeBackend(routes({ "GET /api/system/doctor": () => json(500, { error: "internal", detail: "The doctor failed", hint: null, fields: null, output: null }) }));
    renderConsole("/settings/about");

    expect(await screen.findByText("Could not run the installation check")).toBeInTheDocument();
  });
});

describe("Settings > About > Support report", () => {
  it("creates the report on demand and offers it to copy, read first", async () => {
    const backend = fakeBackend(routes());
    const { user } = renderConsole("/settings/about");
    const section = await screen.findByRole("region", { name: "Support report" });
    expect(backend.callsTo("GET /api/system/support-report")).toHaveLength(0);

    await user.click(within(section).getByRole("button", { name: "Create the report" }));

    expect(await within(section).findByText(/# Noust support report/)).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Copy the report" })).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Download the report" })).toBeInTheDocument();
    expect(within(section).getByText(/Read it before you post it/)).toBeInTheDocument();
    expect(backend.callsTo("GET /api/system/support-report")[0]?.search.get("hide_domains")).toBe("false");
  });

  it("says when the journal was left out because the credential may not read it", async () => {
    fakeBackend(routes());
    const { user } = renderConsole("/settings/about");
    const section = await screen.findByRole("region", { name: "Support report" });

    await user.click(within(section).getByRole("button", { name: "Create the report" }));

    expect(await within(section).findByText("The journal is left out: reading it needs the right to reveal secrets.")).toBeInTheDocument();
  });

  it("asks for the names to be replaced when the switch is on", async () => {
    const backend = fakeBackend(routes());
    const { user } = renderConsole("/settings/about");
    const section = await screen.findByRole("region", { name: "Support report" });

    await user.click(within(section).getByRole("switch", { name: "Replace domains and this server's name" }));
    await user.click(within(section).getByRole("button", { name: "Create the report" }));

    await waitFor(() => expect(backend.callsTo("GET /api/system/support-report")[0]?.search.get("hide_domains")).toBe("true"));
  });

  it("shows a failure and offers the command instead", async () => {
    fakeBackend(routes({ "GET /api/system/support-report": () => json(500, { error: "internal", detail: "No journal", hint: null, fields: null, output: null }) }));
    const { user } = renderConsole("/settings/about");
    const section = await screen.findByRole("region", { name: "Support report" });

    await user.click(within(section).getByRole("button", { name: "Create the report" }));

    expect(await within(section).findByText("Could not create the report")).toBeInTheDocument();
    expect(within(section).getByText("noust support report")).toBeInTheDocument();
  });
});

describe("Settings > About", () => {
  it("is accessible with every part loaded and the report open", { timeout: 30_000 }, async () => {
    fakeBackend(routes());
    const { user, container } = renderConsole("/settings/about");
    const section = await screen.findByRole("region", { name: "Support report" });
    await screen.findByText("git: not installed");
    await user.click(within(section).getByRole("button", { name: "Create the report" }));
    await within(section).findByText(/# Noust support report/);

    await expectNoAxeViolations(container);
  });

  it("speaks Spanish", async () => {
    fakeBackend(routes());
    renderConsole("/settings/about");
    await screen.findByRole("region", { name: "Installation check" });

    await act(() => setLocale("es"));

    expect(await screen.findByRole("region", { name: "Novedades" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Comprobación de la instalación" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Informe de soporte" })).toBeInTheDocument();
  });
});
