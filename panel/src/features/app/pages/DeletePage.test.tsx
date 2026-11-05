import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import { DOMAIN, IN_PLACE_APP, RELEASE_APP, renderSettings, settingsRoutes } from "../settings/testKit";

const PLAN = { domain: DOMAIN, path: "/var/www/apps/shop-example-com", keeps_directory: false, has_volumes: false, databases: [] };
const STACK = { ...RELEASE_APP, app_type: "docker-compose", path: "/opt/shop", source: null };
const STACK_PLAN = {
  domain: DOMAIN,
  path: "/opt/shop",
  keeps_directory: true,
  has_volumes: true,
  databases: [
    { engine: "postgresql", name: "shop", owned: true, shared_with: [], will_drop: true },
    { engine: "postgresql", name: "reports", owned: false, shared_with: ["analytics.example.com"], will_drop: false },
  ],
};

const queued = () => json(202, { job_id: "j1", status: "pending", message: `Deletion queued for ${DOMAIN}`, job: {} });

/** Opens the page of deleting an application (it was the last subsection of the settings, now it is a page of its own). */
async function open(app: object, plan: object, extra: Parameters<typeof renderSettings>[3] = {}) {
  const harness = await renderSettings("/delete", "What is deleted", app, {
    [`GET /api/apps/${DOMAIN}/deletion`]: () => json(200, plan),
    "POST /api/auth/elevate": () => json(200, { elevated_until: "2999-01-01T00:00:00+00:00" }),
    [`DELETE /api/apps/${DOMAIN}`]: queued,
    ...extra,
  });
  return harness;
}

describe("the page of deleting an application", () => {
  it("offers one box for each thing that holds data, none ticked, and says nothing it does not do", async () => {
    await open(IN_PLACE_APP, PLAN);
    expect(screen.getByRole("checkbox", { name: /Also delete its files/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Also delete its certificate/ })).not.toBeChecked();
    // Nothing runs in Docker and no database is its own: those boxes are not drawn rather than disabled.
    expect(screen.queryByRole("checkbox", { name: /volumes/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /database/ })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("asks for the volumes and the databases of an adopted stack, and says its folder is not Noust's", async () => {
    const { user, backend, location } = await open(STACK, STACK_PLAN);
    expect(screen.getByRole("checkbox", { name: /Also delete its folder/ })).not.toBeChecked();
    expect(screen.getByText(/is not in Noust's apps folder/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Also delete the volumes of its containers/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Also delete its database shop/ })).not.toBeChecked();
    // A database other applications use is said to stay, with who uses it, and has no box.
    expect(screen.getByText(/stays: analytics\.example\.com also use it\./)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /reports/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: /Also delete its folder/ }));
    await user.click(screen.getByRole("checkbox", { name: /Also delete the volumes of its containers/ }));
    await user.click(screen.getByRole("checkbox", { name: /Also delete its database shop/ }));
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    const elevate = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(elevate).getByRole("textbox"), "123456");
    await user.click(within(elevate).getByRole("button", { name: "Continue" }));

    // The question says, from the boxes, what goes and what stays.
    const confirm = await screen.findByRole("alertdialog", { name: `Delete ${DOMAIN}` });
    expect(within(confirm).getByText(/^Removed: its service, its web server site, its folder, the volumes of its containers, and its databases\.$/)).toBeInTheDocument();
    expect(within(confirm).getByText(/^Kept: its certificate and its backups\.$/)).toBeInTheDocument();
    await user.type(within(confirm).getByRole("textbox"), DOMAIN);
    await user.click(within(confirm).getByRole("button", { name: "Delete application" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/apps");
    });
    // The folder is named exactly, which is the one way the server removes a folder that is not its own.
    expect(Object.fromEntries(backend.callsTo(`DELETE /api/apps/${DOMAIN}`)[0]?.search ?? [])).toEqual({
      remove_files: "true",
      remove_ssl: "false",
      remove_volumes: "true",
      remove_databases: "true",
      remove_adopted_directory: "/opt/shop",
    });
  });

  it("deletes nothing but the service and the site when nothing is ticked", async () => {
    const { user, backend, location } = await open(IN_PLACE_APP, PLAN);
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    const elevate = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(elevate).getByRole("textbox"), "123456");
    await user.click(within(elevate).getByRole("button", { name: "Continue" }));
    const confirm = await screen.findByRole("alertdialog", { name: `Delete ${DOMAIN}` });
    expect(within(confirm).getByText(/^Removed: its service and its web server site\.$/)).toBeInTheDocument();
    expect(within(confirm).getByText(/^Kept: its files, its certificate, and its backups\.$/)).toBeInTheDocument();
    const action = within(confirm).getByRole("button", { name: "Delete application" });
    expect(action).toBeDisabled();
    await user.type(within(confirm).getByRole("textbox"), DOMAIN);
    await user.click(action);
    await waitFor(() => {
      expect(location().pathname).toBe("/apps");
    });
    expect(Object.fromEntries(backend.callsTo(`DELETE /api/apps/${DOMAIN}`)[0]?.search ?? [])).toEqual({
      remove_files: "false",
      remove_ssl: "false",
      remove_volumes: "false",
      remove_databases: "false",
    });
  });

  it("does nothing when the operator does not confirm it's them", async () => {
    const { user, backend } = await open(IN_PLACE_APP, PLAN);
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    const elevate = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.click(within(elevate).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Confirm it's you" })).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}`)).toHaveLength(0);
  });

  it("says plainly that it could not read what deleting would touch, and does not guess", async () => {
    fakeBackend(settingsRoutes(IN_PLACE_APP, { [`GET /api/apps/${DOMAIN}/deletion`]: () => json(500, { error: "boom", detail: "The plan could not be read." }) }));
    renderConsole(`/apps/${DOMAIN}/delete`);
    expect(screen.queryByRole("button", { name: "Delete…" })).not.toBeInTheDocument();
  });
});
