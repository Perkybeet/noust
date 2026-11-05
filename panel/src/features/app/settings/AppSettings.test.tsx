import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { downloadText } from "../../../lib/clipboard";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import { DOMAIN, ELEVATED, IN_PLACE_APP, RELEASE_APP, STATIC_APP, renderSettings, saveBar, settingsRoutes } from "./testKit";

// The real implementation still runs (so the export test exercises the actual blob and anchor
// dance), wrapped so the exact file name and text handed to it can be asserted.
vi.mock("../../../lib/clipboard", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../lib/clipboard")>();
  return { ...actual, downloadText: vi.fn(actual.downloadText) };
});

describe("the settings' old addresses", () => {
  // 4.2 (item 116): an application's settings are not a list of their own. Each subsection is on a page of the
  // column, and its old address goes there, with nothing lost on the way.
  const WHERE: readonly (readonly [string, string])[] = [
    ["", "resources"],
    ["/source", "code"],
    ["/build", "code"],
    ["/builds", "code"],
    ["/container", "code"],
    ["/deploys", "triggers"],
    ["/automation", "triggers"],
    ["/deploy-on-push", "triggers"],
    ["/hooks", "triggers"],
    ["/previews", "triggers"],
    ["/resources", "resources"],
    ["/processes", "resources"],
    ["/mounts", "resources"],
    ["/disk", "resources"],
    ["/security", "resources"],
    ["/access", "access"],
    ["/protection", "access"],
    ["/export", "export"],
    ["/delete", "delete"],
  ];

  it.each(WHERE)("takes %s to its page", async (old, page) => {
    fakeBackend({ ...signedInRoutes(), [`GET /api/apps/${DOMAIN}`]: () => json(200, RELEASE_APP), "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }) });
    const { location } = renderConsole(`/apps/${DOMAIN}/settings${old}`);
    await waitFor(() => {
      expect(location().pathname).toBe(`/apps/${DOMAIN}/${page}`);
    });
  });

  it("names the browser tab after the page and the application", async () => {
    await renderSettings("/resources", "Resources");
    await waitFor(() => {
      expect(document.title).toMatch(new RegExp(`^Resources and running - ${DOMAIN.replace(/\./g, "\\.")}`));
    });
  });

  it("opens the fold a link names, and the old address of a folded part names its own", async () => {
    // A link to `#deploy-on-push` (the overview's "Set it up") and the old `/settings/deploy-on-push` both lead to an open fold.
    fakeBackend(settingsRoutes(RELEASE_APP));
    const { router } = renderConsole(`/apps/${DOMAIN}/settings/deploy-on-push`);
    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/apps/${DOMAIN}/triggers`);
      expect(router.state.location.hash).toBe("deploy-on-push");
    });
    expect(await screen.findByRole("button", { name: "Deploy on push", expanded: true })).toBeInTheDocument();
    // The other folds stay as they were.
    expect(screen.getByRole("button", { name: "Deploy hooks", expanded: false })).toBeInTheDocument();
  });

  it("draws the parts of a page under one save bar, and none of its own", async () => {
    await renderSettings("/resources", "Resources");
    // The page holds Resources, Disk and the rest, and a lone bar: a page is the form.
    expect(screen.getAllByRole("region", { name: "Unsaved changes" })).toHaveLength(1);
  });
});

describe("How it runs", () => {
  it("states where the app lives and how it runs, as facts", async () => {
    await renderSettings("", "How it runs");
    const facts = screen.getByRole("region", { name: "How it runs" });
    expect(within(facts).getByText("/var/www/apps/shop-example-com")).toBeInTheDocument();
    expect(within(facts).getByText("Instant rollback")).toBeInTheDocument();
    expect(within(facts).getByText("Each deploy is kept apart; going back takes seconds")).toBeInTheDocument();
    expect(within(facts).getByText("shop-example-com.service")).toBeInTheDocument();
    // Starting at boot is an on/off at a glance, not a bare "Yes" (item 56).
    const boot = within(facts).getByText("Starts at boot").closest("div");
    if (boot === null) throw new Error("no boot row");
    expect(within(boot).getByText(/^(Yes|No)$/)).toHaveAttribute("data-state", expect.stringMatching(/^(running|stopped)$/));
    // What the type, the source and the commands are is Code and build's, not repeated here.
    expect(within(facts).queryByText("npm run build")).not.toBeInTheDocument();
    expect(within(facts).queryByText("Type")).not.toBeInTheDocument();
  });

  it("says a single folder is rebuilt on every deploy", async () => {
    await renderSettings("", "How it runs", IN_PLACE_APP);
    expect(screen.getByText("Single folder")).toBeInTheDocument();
    expect(screen.getByText("Rebuilt in the same folder on every deploy")).toBeInTheDocument();
  });

  it("has nothing to start on a static site", async () => {
    await renderSettings("", "How it runs", STATIC_APP);
    expect(screen.queryByText("Starts at boot")).not.toBeInTheDocument();
    expect(screen.getByText("The web server, no process")).toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    await renderSettings("", "How it runs");
    await expectNoAxeViolations(screen.getByRole("region", { name: "How it runs" }));
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { level: 2, name: "Cómo corre" })).toBeInTheDocument();
    expect(screen.getByText("Modo de despliegue")).toBeInTheDocument();
    expect(screen.getByText("Vuelta atrás instantánea")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("region", { name: "Cómo corre" }));
  });
});

describe("Resources", () => {
  it("starts from the app's limits and saves exactly what the form says from the save bar", async () => {
    const { user, backend } = await renderSettings("/resources", "Resources", RELEASE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`PATCH /api/apps/${DOMAIN}/limits`]: () =>
        json(200, { domain: DOMAIN, memory_max_mb: 768, cpu_quota_percent: null, tasks_max: 256, units: ["shop-example-com"], restarted: true, restart_required: false }),
    });
    const memory = screen.getByRole("textbox", { name: "Memory" });
    const cpu = screen.getByRole("textbox", { name: "CPU" });
    expect(memory).toHaveValue("512");
    expect(cpu).toHaveValue("150");
    expect(screen.getByText("MemoryMax=512M CPUQuota=150% TasksMax=256")).toBeInTheDocument();
    expect(await screen.findByText(/up to 400 here/)).toBeInTheDocument();

    await user.clear(memory);
    await user.type(memory, "768");
    await user.clear(cpu);
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /Restart now so the limits apply/ }));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/limits`)[0]?.body).toEqual({ memory_max_mb: 768, cpu_quota_percent: null, tasks_max: 256, restart: true });
    });
    expect(await screen.findByText("Saved. shop-example-com restarted under the new limits.")).toBeInTheDocument();
  });

  it("refuses what the backend would refuse before asking, and shows its own refusal verbatim", async () => {
    const { user, backend } = await renderSettings("/resources", "Resources", RELEASE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`PATCH /api/apps/${DOMAIN}/limits`]: () => problem(400, "validationerror", "A CPU quota of 350% is not possible here", { hint: "This machine has 2 CPU(s): use 1% to 200%." }),
    });
    const memory = screen.getByRole("textbox", { name: "Memory" });
    await user.clear(memory);
    await user.type(memory, "32");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("A memory limit of 32M is too small. Allow at least 64M, or no limit.")).toBeInTheDocument();
    expect(memory).toHaveAttribute("aria-invalid", "true");
    expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/limits`)).toHaveLength(0);

    await user.clear(memory);
    await user.type(memory, "512");
    const cpu = screen.getByRole("textbox", { name: "CPU" });
    await user.clear(cpu);
    await user.type(cpu, "350");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("A CPU quota of 350% is not possible here")).toBeInTheDocument();
  });

  it("explains that a static site has nothing to limit", async () => {
    await renderSettings("/resources", "Resources", STATIC_APP);
    expect(screen.getByText(/there is no process of its own to limit/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
});


describe("Export", () => {
  const DOCUMENT = { format: "noust-app", version: 1, secrets_included: false, app: { domain: DOMAIN, app_type: "nextjs" }, env: {} };

  it("downloads the export without secrets by default, and with them when asked", async () => {
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:export"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const { user, backend } = await renderSettings("/export", "Export", RELEASE_APP, {
      [`GET /api/apps/${DOMAIN}/export`]: () => json(200, DOCUMENT),
    });
    await user.click(screen.getByRole("button", { name: "Export application" }));
    await waitFor(() => {
      expect(downloadText).toHaveBeenCalledWith(`${DOMAIN}.noust-app.json`, JSON.stringify(DOCUMENT, null, 2), "application/json");
    });
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/export`)[0]?.search.get("with_secrets")).toBeNull();

    await user.click(screen.getByRole("checkbox", { name: "Include secret values" }));
    await user.click(screen.getByRole("button", { name: "Export application" }));
    await waitFor(() => {
      expect(backend.callsTo(`GET /api/apps/${DOMAIN}/export`)).toHaveLength(2);
    });
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/export`)[1]?.search.get("with_secrets")).toBe("true");
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

