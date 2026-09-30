import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { screenWidth } from "../testRoutes";
import { downloadText } from "../../../lib/clipboard";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import { DOMAIN, ELEVATED, IN_PLACE_APP, RELEASE_APP, STATIC_APP, renderSettings, saveBar } from "./testKit";

// The real implementation still runs (so the export test exercises the actual blob and anchor
// dance), wrapped so the exact file name and text handed to it can be asserted.
vi.mock("../../../lib/clipboard", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../lib/clipboard")>();
  return { ...actual, downloadText: vi.fn(actual.downloadText) };
});

function nav(name = "Application settings"): HTMLElement {
  // Wider than a phone the list is beside the content; on a phone the index page repeats it.
  const [first] = screen.getAllByRole("navigation", { name });
  if (!first) throw new Error("no settings navigation");
  return first;
}

describe("the settings' navigation", () => {
  it("lists every subsection as a URL of its own, Delete last and apart", async () => {
    screenWidth(1440);
    await renderSettings("", "General");
    const links = within(nav()).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["General", "Deploys", "Deploy on push", "Builds", "Resources", "Previews", "Export", "Delete"]);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      `/apps/${DOMAIN}/settings`,
      `/apps/${DOMAIN}/settings/deploys`,
      `/apps/${DOMAIN}/settings/deploy-on-push`,
      `/apps/${DOMAIN}/settings/builds`,
      `/apps/${DOMAIN}/settings/resources`,
      `/apps/${DOMAIN}/settings/previews`,
      `/apps/${DOMAIN}/settings/export`,
      `/apps/${DOMAIN}/settings/delete`,
    ]);
    expect(within(nav()).getByRole("link", { name: "General" })).toHaveAttribute("aria-current", "page");
  });

  it("goes from one subsection to another, and names the browser tab after it", async () => {
    screenWidth(1440);
    const { user, location } = await renderSettings("", "General");
    await user.click(within(nav()).getByRole("link", { name: "Resources" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Resources" })).toBeInTheDocument();
    expect(location().pathname).toBe(`/apps/${DOMAIN}/settings/resources`);
    await waitFor(() => {
      expect(document.title).toMatch(new RegExp(`^Resources - Settings - ${DOMAIN.replace(/\./g, "\\.")}`));
    });
  });

  it("is an index of its own on a phone, and each subsection leads back to it", async () => {
    screenWidth(390);
    await renderSettings("/export", "Export");
    expect(screen.getByRole("link", { name: "All sections" })).toHaveAttribute("href", `/apps/${DOMAIN}/settings`);
  });
});

describe("General", () => {
  it("states what the app is, where it comes from and how it runs, as facts", async () => {
    await renderSettings("", "General");
    const facts = screen.getByRole("main");
    const repo = within(facts).getByRole("link", { name: /^https:\/\/github\.com\/shop\/storefront\.git/ });
    expect(repo).toHaveAttribute("href", RELEASE_APP.source);
    expect(repo).toHaveAttribute("target", "_blank");
    expect(within(facts).getByText("main")).toBeInTheDocument();
    expect(within(facts).getByText("npm run build")).toBeInTheDocument();
    expect(within(facts).getByText("npm run start")).toBeInTheDocument();
    expect(within(facts).getByText("/var/www/apps/shop-example-com")).toBeInTheDocument();
    expect(within(facts).getByText("Instant rollback")).toBeInTheDocument();
    expect(within(facts).getByText("Each deploy is kept apart; going back takes seconds")).toBeInTheDocument();
    expect(within(facts).getByText("shop-example-com.service")).toBeInTheDocument();
    expect(within(facts).getByText(`noust app branch ${DOMAIN} <branch>`)).toBeInTheDocument();
    // Nothing here is a form: no save bar.
    expect(screen.queryByRole("region", { name: "Unsaved changes" })).not.toBeInTheDocument();
  });

  it("says when no branch is pinned, and that any push then deploys", async () => {
    await renderSettings("", "General", IN_PLACE_APP);
    expect(screen.getByText("None pinned")).toBeInTheDocument();
    expect(screen.getByText("With deploy on push, a push to any branch deploys it")).toBeInTheDocument();
    expect(screen.getByText("Single folder")).toBeInTheDocument();
    expect(screen.getByText("Rebuilt in the same folder on every deploy")).toBeInTheDocument();
  });

  it("pins the branch it deploys from, checked on the remote, and shows it at once", async () => {
    let app: object = { ...RELEASE_APP, branch: null };
    const { user, backend } = await renderSettings("", "General", { ...RELEASE_APP, branch: null }, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`GET /api/apps/${DOMAIN}`]: () => json(200, app),
      [`PATCH /api/apps/${DOMAIN}/branch`]: () => {
        app = { ...RELEASE_APP, branch: "release" };
        return json(200, { domain: DOMAIN, branch: "release", pinned: true, commit: "4f2a9c1", previous: null });
      },
    });
    await user.click(screen.getByRole("button", { name: "Pin the branch" }));
    const dialog = await screen.findByRole("dialog", { name: "Pin the branch that deploys" });
    const field = within(dialog).getByRole("textbox", { name: "Branch" });
    expect(field).toHaveValue("main");
    await user.clear(field);
    await user.type(field, "release");
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Pin branch" }));
    await waitFor(() => {
      expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/branch`)[0]?.body).toEqual({ branch: "release" });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(await screen.findByText("release")).toBeInTheDocument();
    expect(screen.queryByText("None pinned")).not.toBeInTheDocument();
  });

  it("shows git's own words when the branch is not on the remote, and keeps the dialog open", async () => {
    const { user } = await renderSettings("", "General", RELEASE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`PATCH /api/apps/${DOMAIN}/branch`]: () => problem(400, "source_error", "No branch 'relase' on https://github.com/shop/storefront.git", { hint: "Check the name, or push the branch first." }),
    });
    await user.click(screen.getByRole("button", { name: "Change the branch" }));
    const dialog = await screen.findByRole("dialog", { name: "Change the branch that deploys" });
    const field = within(dialog).getByRole("textbox", { name: "Branch" });
    await user.clear(field);
    await user.type(field, "relase");
    await user.click(within(dialog).getByRole("button", { name: "Pin branch" }));
    expect(await within(dialog).findByText("No branch 'relase' on https://github.com/shop/storefront.git")).toBeInTheDocument();
    expect(within(dialog).getByText("Check the name, or push the branch first.")).toBeInTheDocument();
  });

  it("unpins the branch after asking once, since any push then deploys", async () => {
    let app: object = RELEASE_APP;
    const { user, backend } = await renderSettings("", "General", RELEASE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`GET /api/apps/${DOMAIN}`]: () => json(200, app),
      [`PATCH /api/apps/${DOMAIN}/branch`]: () => {
        app = { ...RELEASE_APP, branch: null };
        return json(200, { domain: DOMAIN, branch: null, pinned: false, commit: null, previous: "main" });
      },
    });
    await user.click(screen.getByRole("button", { name: "Unpin" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Unpin the branch of ${DOMAIN}?` });
    await user.click(within(dialog).getByRole("button", { name: "Unpin branch" }));
    await waitFor(() => {
      expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/branch`)[0]?.body).toEqual({ branch: null });
    });
    expect(await screen.findByText("None pinned")).toBeInTheDocument();
  });

  it("offers no branch to pin for an app that does not deploy from git", async () => {
    await renderSettings("", "General", IN_PLACE_APP);
    expect(screen.queryByRole("button", { name: "Pin the branch" })).not.toBeInTheDocument();
  });

  it("has nothing to start on a static site", async () => {
    await renderSettings("", "General", STATIC_APP);
    expect(screen.queryByText("Start command")).not.toBeInTheDocument();
    expect(screen.queryByText("Port")).not.toBeInTheDocument();
    expect(screen.getByText("The web server, no process")).toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    // The subsection alone: on the index route the phone's list of subsections is in the DOM
    // beside the wide one, and jsdom applies no media query to hide either.
    screenWidth(1440);
    await renderSettings("", "General");
    await expectNoAxeViolations(screen.getByRole("region", { name: "General" }));
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { level: 2, name: "General" })).toBeInTheDocument();
    expect(screen.getByText("Modo de despliegue")).toBeInTheDocument();
    expect(screen.getByText("Vuelta atrás instantánea")).toBeInTheDocument();
    expect(within(nav("Ajustes de la aplicación")).getByRole("link", { name: "Desplegar al hacer push" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("region", { name: "General" }));
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
  const DOCUMENT = { format: "wasm-app", version: 1, secrets_included: false, app: { domain: DOMAIN, app_type: "nextjs" }, env: {} };

  it("downloads the export without secrets by default, and with them when asked", async () => {
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:export"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const { user, backend } = await renderSettings("/export", "Export", RELEASE_APP, {
      [`GET /api/apps/${DOMAIN}/export`]: () => json(200, DOCUMENT),
    });
    await user.click(screen.getByRole("button", { name: "Export application" }));
    await waitFor(() => {
      expect(downloadText).toHaveBeenCalledWith(`${DOMAIN}.wasm-app.json`, JSON.stringify(DOCUMENT, null, 2), "application/json");
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

describe("Delete", () => {
  it("asks who it is, then opens the deletion with nothing destructive ticked, and waits for the domain", async () => {
    const { user, backend, location } = await renderSettings("/delete", "Delete", IN_PLACE_APP, {
      "POST /api/auth/elevate": () => json(200, { elevated_until: "2999-01-01T00:00:00+00:00" }),
      [`DELETE /api/apps/${DOMAIN}`]: () => json(202, { job_id: "j1", status: "pending", message: `Deletion queued for ${DOMAIN}`, job: {} }),
    });
    expect(screen.getByText(/Its files in/)).toHaveTextContent("backups are always kept");
    await user.click(screen.getByRole("button", { name: "Delete application…" }));
    const elevate = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(elevate).getByRole("textbox"), "123456");
    await user.click(within(elevate).getByRole("button", { name: "Confirm" }));

    const confirm = await screen.findByRole("alertdialog", { name: `Delete ${DOMAIN}` });
    expect(within(confirm).getByRole("checkbox", { name: /Also delete its files/ })).not.toBeChecked();
    expect(within(confirm).getByRole("checkbox", { name: /Also delete its certificate/ })).not.toBeChecked();
    const action = within(confirm).getByRole("button", { name: "Delete application" });
    expect(action).toBeDisabled();
    await user.type(within(confirm).getByRole("textbox"), DOMAIN);
    await user.click(action);
    await waitFor(() => {
      expect(location().pathname).toBe("/apps");
    });
    expect(Object.fromEntries(backend.callsTo(`DELETE /api/apps/${DOMAIN}`)[0]?.search ?? [])).toEqual({ remove_files: "false", remove_ssl: "false" });
  });

  it("does nothing when the operator does not confirm it's them", async () => {
    const { user, backend } = await renderSettings("/delete", "Delete", IN_PLACE_APP);
    await user.click(screen.getByRole("button", { name: "Delete application…" }));
    const elevate = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.click(within(elevate).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Confirm it's you" })).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}`)).toHaveLength(0);
  });
});
