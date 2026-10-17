import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { json } from "../../test/fakes";
import { DOMAIN, ELEVATED, IN_PLACE_APP, jobOf, accepted, renderSettings } from "./settings/testKit";
import { SETTINGS_SECTIONS } from "./settings/sections";
import { appTabs } from "./tabs";
import { isAdopted } from "./adopted";

/** A site Noust adopted: a unit the operator wrote behind nginx, with no source and no build. */
const ADOPTED_APP = {
  ...IN_PLACE_APP,
  app_type: "adopted",
  source: null,
  branch: null,
  path: "/srv/legacy",
  unit: "legacy-api.service",
};

const ADOPTION = {
  kind: "proxy",
  unit: "legacy-api",
  webserver: "nginx",
  site_file: DOMAIN,
  root: "/srv/legacy",
  upstream: "127.0.0.1:8000",
  adopted_by: "alice",
  adopted_at: "2026-10-16T10:00:00",
};

const routes = {
  [`GET /api/apps/${DOMAIN}/adoption`]: () => json(200, ADOPTION),
  "GET /api/apps/types": () => json(200, { types: [{ type: "nextjs", name: "Next.js", default_port: 3000 }] }),
};

describe("what the console knows of an adopted site", () => {
  it("is told by the type alone", () => {
    expect(isAdopted({ app_type: "adopted" })).toBe(true);
    expect(isAdopted({ app_type: "nextjs" })).toBe(false);
    expect(isAdopted(undefined)).toBe(false);
  });

  it("leaves out the tabs of what Noust does not manage", () => {
    const names = (tabs: ReturnType<typeof appTabs>) => tabs.map((tab) => tab.to);
    expect(names(appTabs(DOMAIN))).toContain("/apps/$domain/deployments");
    expect(names(appTabs(DOMAIN, {}, { adopted: true }))).toEqual(["/apps/$domain", "/apps/$domain/logs", "/apps/$domain/metrics", "/apps/$domain/settings"]);
  });

  it("keeps General and Delete of the settings and nothing about building", () => {
    const adopted = SETTINGS_SECTIONS.filter((section) => section.when?.(ADOPTED_APP as never) !== false).map((section) => section.path);
    expect(adopted).toEqual(["", "/delete"]);
  });
});

describe("an adopted site's page", () => {
  it("says what was adopted, in the operator's own terms", async () => {
    await renderSettings("", "General", ADOPTED_APP, routes);
    const top = screen.getByRole("heading", { level: 1, name: DOMAIN }).closest("header");
    if (top === null) throw new Error("no page header");

    // The daily verbs of a unit stay; what builds, deploys or hands over does not.
    expect(await within(top).findByRole("button", { name: "Restart" })).toBeInTheDocument();
    expect(within(top).queryByRole("button", { name: "Update" })).not.toBeInTheDocument();
    expect(within(top).queryByRole("button", { name: "Roll back" })).not.toBeInTheDocument();
    expect(within(top).getByText("Adopted site")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Application sections" });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual(["Overview", "Logs", "Metrics", "Settings"]);
  });

  it("lists the operator's unit, site and directory on the overview, kept as they are", async () => {
    const { user } = await renderSettings("", "General", ADOPTED_APP, routes);
    await user.click(within(screen.getByRole("navigation", { name: "Application sections" })).getByRole("link", { name: "Overview" }));

    expect(await screen.findByText("It was running before Noust met it, and its unit, its site and its files are yours. Noust shows it, restarts its unit and reads its logs; it does not build, update, roll back or rewrite it.")).toBeInTheDocument();
    const what = (await screen.findByRole("heading", { name: "What Noust adopted" })).closest("section") ?? document.body;
    expect(await within(what).findByText("legacy-api.service")).toBeInTheDocument();
    expect(within(what).getByText("nginx " + DOMAIN)).toBeInTheDocument();
    expect(within(what).getByText("/srv/legacy")).toBeInTheDocument();
    expect(within(what).getByText("127.0.0.1:8000")).toBeInTheDocument();
    expect(within(what).getByText("Started, stopped and restarted from here; its file is never rewritten.")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("offers to stop managing it, not to delete it, and asks for nothing about its files", async () => {
    const { user, backend } = await renderSettings("/delete", "Delete", ADOPTED_APP, {
      ...routes,
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`DELETE /api/apps/${DOMAIN}`]: () => json(202, accepted(jobOf("d1", "delete"))),
    });
    expect(screen.getByText("Stop managing this site")).toBeInTheDocument();
    expect(screen.getByText(/Removes it from Noust and nothing else: the unit, the site, the certificate and the files in/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Stop managing…" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Stop managing ${DOMAIN}` });
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(dialog).getByText(/The unit, the site, the certificate and the files stay where they are and keep running\./)).toBeInTheDocument();
    await expectNoAxeViolations(dialog);

    await user.type(within(dialog).getByRole("textbox"), DOMAIN);
    await user.click(within(dialog).getByRole("button", { name: "Stop managing" }));
    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}`)).toHaveLength(1);
    });
    expect(Object.fromEntries(backend.callsTo(`DELETE /api/apps/${DOMAIN}`)[0]?.search ?? [])).toEqual({ remove_files: "false", remove_ssl: "false" });
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    await renderSettings("/delete", "Eliminar", ADOPTED_APP, routes);
    expect(await screen.findByText("Dejar de gestionar este sitio")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dejar de gestionar…" })).toBeInTheDocument();
  });
});
