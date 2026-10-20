import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { frameRoutes } from "../frameRoutes";
import { TARGETS, backup, policy } from "./testFixtures";

const SHOP = "shop.example.com";
const JOB = { id: "vol-1", type: "backup", name: "Back up the data", description: "", status: "running", progress: 10, total_steps: 100, current_step: "", created_at: "2026-10-06T10:00:00", logs: [{ message: "Archiving path:uploads" }], metadata: { domain: SHOP, kind: "volumes" } };
const ACCEPTED = { job_id: JOB.id, status: "pending", message: "Queued", job: JOB };

function volumeRoutes(extra: Record<string, RouteHandler> = {}, state: { policies?: unknown[]; backups?: unknown[] } = {}): Record<string, RouteHandler> {
  const policies = state.policies ?? [policy(SHOP, { last_status: "failed", last_error: "tar: uploads: file changed as we read it" })];
  const backups = state.backups ?? [backup("shop-example-com.uploads_20261006_020003", SHOP, { remote_copies: [{ destination: "nas" }], verified_ok: true, last_verified_at: "2026-10-06T02:05:00+00:00" }), backup("shop-example-com.uploads_20261005_020003", SHOP, { created_at: "2026-10-05T02:00:03+00:00", tags: ["safety"] })];
  return frameRoutes({
    "GET /api/volume-backups/policies": () => json(200, { policies, total: policies.length }),
    "GET /api/volume-backups": () => json(200, { backups, total: backups.length }),
    [`GET /api/volume-backups/targets/${SHOP}`]: () => json(200, TARGETS),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    ...extra,
  });
}

async function volumesAt(path = "/backups/volumes", extra: Record<string, RouteHandler> = {}, state: { policies?: unknown[]; backups?: unknown[] } = {}) {
  screenWidth(1440);
  const backend = fakeBackend(volumeRoutes(extra, state));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: "Backups", hidden: true });
  return { ...harness, backend };
}

describe("the Volumes tab", () => {
  it("lists each application whose data is backed up, with the state of its policy and what it weighs", async () => {
    await volumesAt();
    const table = await screen.findByRole("region", { name: "Application data and its backups" });
    const row = within(table).getByRole("row", { name: new RegExp(SHOP.replace(/\./g, "\\.")) });
    expect(within(row).getByText("Last run failed")).toBeInTheDocument();
    expect(within(row).getByText("Every day at 02:00")).toBeInTheDocument();
    expect(within(row).getByText("Everything it declares")).toBeInTheDocument();
    expect(within(row).getByText("2.0 MB")).toBeInTheDocument();
    const tabs = screen.getByRole("navigation", { name: "Backup sections" });
    expect(within(tabs).getByRole("link", { name: /^Volumes/ })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: /^Noust/ })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says what it is for when no application has data backups yet", async () => {
    await volumesAt("/backups/volumes", {}, { policies: [], backups: [] });
    expect(await screen.findByRole("heading", { level: 2, name: "No data backups yet" })).toBeInTheDocument();
    expect(screen.getByText(/noust backup volumes schedule set/)).toBeInTheDocument();
  });

  it("sets a policy: the application, the pieces it declares, the schedule, what is kept and how still the data is", async () => {
    const { user, backend } = await volumesAt("/backups/volumes", {
      [`PUT /api/volume-backups/policies/${SHOP}`]: (call) => json(200, { ...policy(SHOP), ...(call.body as object) }),
    }, { policies: [], backups: [] });
    await user.click(await screen.findByRole("button", { name: "New data policy" }));
    const drawer = await screen.findByRole("dialog", { name: "New data policy" });
    await user.click(within(drawer).getByRole("combobox", { name: "Application" }));
    await user.click(await screen.findByRole("option", { name: SHOP }));
    // Everything it declares is the default; choosing pieces lists what it does declare.
    await user.click(within(drawer).getByRole("radio", { name: /^Only the pieces I choose/ }));
    expect(await within(drawer).findByText("uploads")).toBeInTheDocument();
    expect(within(drawer).getByText("Not there yet")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("checkbox", { name: /uploads/ }));
    await user.click(within(drawer).getByRole("checkbox", { name: /shop_pgdata/ }));
    await user.type(within(drawer).getByLabelText(/^More folders/), "media/images");
    await user.click(within(drawer).getByRole("radio", { name: /^Pause it/ }));
    await user.click(within(drawer).getByRole("checkbox", { name: "nas" }));
    await expectNoAxeViolations(drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/volume-backups/policies/${SHOP}`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/volume-backups/policies/${SHOP}`)[0]?.body).toEqual({
      schedule: "*-*-* 02:00:00",
      targets: ["path:uploads", "volume:shop_pgdata"],
      extra_paths: ["media/images"],
      retention_count: 7,
      retention_days: 30,
      retention_gb: null,
      destinations: [{ name: "nas", retention_count: null, retention_days: null }],
      remote_only: false,
      consistency: "pause",
      enabled: true,
    });
  });

  it("asks for a piece when only some are to be backed up, and for a folder that stays inside the data", async () => {
    const { user, backend } = await volumesAt("/backups/volumes", {}, { policies: [], backups: [] });
    await user.click(await screen.findByRole("button", { name: "New data policy" }));
    const drawer = await screen.findByRole("dialog", { name: "New data policy" });
    await user.click(within(drawer).getByRole("button", { name: "Save policy" }));
    expect(await within(drawer).findByText("Choose the application whose data is backed up.")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("combobox", { name: "Application" }));
    await user.click(await screen.findByRole("option", { name: SHOP }));
    await user.click(within(drawer).getByRole("radio", { name: /^Only the pieces I choose/ }));
    await user.click(within(drawer).getByRole("button", { name: "Save policy" }));
    expect(await within(drawer).findByText("Choose at least one piece, or back up everything it declares.")).toBeInTheDocument();
    await user.type(within(drawer).getByLabelText(/^More folders/), "../etc");
    await user.click(within(drawer).getByRole("button", { name: "Save policy" }));
    expect(await within(drawer).findByText(/A folder is a relative path inside the application's data/)).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/volume-backups/policies/${SHOP}`)).toHaveLength(0);
  });

  it("backs up now from the row, as a job followed above the table", async () => {
    const { user, backend } = await volumesAt("/backups/volumes", { [`POST /api/volume-backups/policies/${SHOP}/run`]: () => json(202, ACCEPTED) });
    await user.click(await screen.findByRole("button", { name: `Actions for ${SHOP}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Back up now" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/volume-backups/policies/${SHOP}/run`)).toHaveLength(1));
    expect(await screen.findByText(`Backing up the data of ${SHOP}`)).toBeInTheDocument();
    expect(screen.getByText("Archiving path:uploads")).toBeInTheDocument();
  });

  it("opens an application's data: its policy, the failure verbatim, and every backup with its check and copies", async () => {
    const { user } = await volumesAt();
    const table = await screen.findByRole("region", { name: "Application data and its backups" });
    await user.click(within(table).getByRole("button", { name: new RegExp(`^${SHOP.replace(/\./g, "\\.")}`) }));
    const drawer = await screen.findByRole("dialog", { name: SHOP });
    expect(within(drawer).getByText("tar: uploads: file changed as we read it")).toBeInTheDocument();
    const backups = within(drawer).getByRole("region", { name: `Data backups of ${SHOP}` });
    expect(within(backups).getByText("Checked")).toBeInTheDocument();
    expect(within(backups).getByText("nas")).toBeInTheDocument();
    expect(within(backups).getByText("Safety copy")).toBeInTheDocument();
    await expectNoAxeViolations(drawer);
  });

  it("puts the data back only after the application's name is typed, with a copy of what is there first", async () => {
    const { user, backend } = await volumesAt(`/backups/volumes?domain=${SHOP}`, {
      "POST /api/volume-backups/shop-example-com.uploads_20261006_020003/restore": () => json(202, ACCEPTED),
    });
    const drawer = await screen.findByRole("dialog", { name: SHOP });
    await user.click(await within(drawer).findByRole("button", { name: "Actions for shop-example-com.uploads_20261006_020003" }));
    await user.click(await screen.findByRole("menuitem", { name: "Restore…" }));
    const confirm = await screen.findByRole("alertdialog", { name: `Restore the data of ${SHOP}` });
    expect(within(confirm).getByText(/replaces what is there: the data is not merged/)).toBeInTheDocument();
    expect(within(confirm).getByRole("checkbox", { name: /Take a copy of what is there first/ })).toBeChecked();
    const action = within(confirm).getByRole("button", { name: "Restore data" });
    expect(action).toBeDisabled();
    await user.type(within(confirm).getByLabelText(new RegExp(`Type ${SHOP.replace(/\./g, "\\.")}`)), SHOP);
    await user.click(action);
    await waitFor(() => expect(backend.callsTo("POST /api/volume-backups/shop-example-com.uploads_20261006_020003/restore")).toHaveLength(1));
    expect(backend.callsTo("POST /api/volume-backups/shop-example-com.uploads_20261006_020003/restore")[0]?.body).toEqual({ safety_backup: true });
    expect(await screen.findByText(`Putting the data of ${SHOP} back`)).toBeInTheDocument();
  });

  it("checks a backup as a job", async () => {
    const id = "shop-example-com.uploads_20261006_020003";
    const { user, backend } = await volumesAt(`/backups/volumes?domain=${SHOP}`, { [`POST /api/volume-backups/${id}/verify`]: () => json(202, ACCEPTED) });
    const drawer = await screen.findByRole("dialog", { name: SHOP });
    await user.click(await within(drawer).findByRole("button", { name: `Actions for ${id}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Check it" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/volume-backups/${id}/verify`)).toHaveLength(1));
    expect(await screen.findByText(`Checking ${id}`)).toBeInTheDocument();
  });

  it("sends a backup to a destination as a job", async () => {
    const id = "shop-example-com.uploads_20261006_020003";
    const { user, backend } = await volumesAt(`/backups/volumes?domain=${SHOP}`, { [`POST /api/volume-backups/${id}/push`]: () => json(202, ACCEPTED) });
    const drawer = await screen.findByRole("dialog", { name: SHOP });
    await user.click(await within(drawer).findByRole("button", { name: `Actions for ${id}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Send to…" }));
    const dialog = await screen.findByRole("dialog", { name: `Send ${id} to a destination` });
    await user.click(within(dialog).getByRole("combobox", { name: "Destination" }));
    await user.click(await screen.findByRole("option", { name: "nas" }));
    await user.click(within(dialog).getByRole("button", { name: "Send" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/volume-backups/${id}/push`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/volume-backups/${id}/push`)[0]?.body).toEqual({ destination: "nas" });
  });

  it("deletes a backup only after its id is typed", async () => {
    const id = "shop-example-com.uploads_20261005_020003";
    const { user, backend } = await volumesAt(`/backups/volumes?domain=${SHOP}`, { [`DELETE /api/volume-backups/${id}`]: () => json(200, { success: true, message: "Deleted" }) });
    const drawer = await screen.findByRole("dialog", { name: SHOP });
    await user.click(await within(drawer).findByRole("button", { name: `Actions for ${id}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    const confirm = await screen.findByRole("alertdialog", { name: `Delete ${id}` });
    expect(within(confirm).getByRole("button", { name: "Delete backup" })).toBeDisabled();
    await user.type(within(confirm).getByRole("textbox"), id);
    await user.click(within(confirm).getByRole("button", { name: "Delete backup" }));
    await waitFor(() => expect(backend.callsTo(`DELETE /api/volume-backups/${id}`)).toHaveLength(1));
  });

  it("gives back a policy that keeps nothing on this server, as the server reported it", async () => {
    const stored = policy(SHOP, { remote_only: true });
    const { user, backend } = await volumesAt("/backups/volumes", { [`PUT /api/volume-backups/policies/${SHOP}`]: (call) => json(200, { ...stored, ...(call.body as object) }) }, { policies: [stored] });
    await user.click(await screen.findByRole("button", { name: `Actions for ${SHOP}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit the policy" }));
    const drawer = await screen.findByRole("dialog", { name: `Edit the data policy of ${SHOP}` });
    expect(within(drawer).getByRole("checkbox", { name: "Keep nothing on this server" })).toBeChecked();
    await user.click(within(drawer).getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/volume-backups/policies/${SHOP}`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/volume-backups/policies/${SHOP}`)[0]?.body).toMatchObject({ remote_only: true, destinations: [{ name: "nas" }] });
  });

  it("sends true once the box of a policy that keeps its backups here is ticked", async () => {
    const { user, backend } = await volumesAt("/backups/volumes", { [`PUT /api/volume-backups/policies/${SHOP}`]: (call) => json(200, { ...policy(SHOP), ...(call.body as object) }) });
    await user.click(await screen.findByRole("button", { name: `Actions for ${SHOP}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit the policy" }));
    const drawer = await screen.findByRole("dialog", { name: `Edit the data policy of ${SHOP}` });
    const box = within(drawer).getByRole("checkbox", { name: "Keep nothing on this server" });
    expect(box).not.toBeChecked();
    await user.click(box);
    await expectNoAxeViolations(drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/volume-backups/policies/${SHOP}`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/volume-backups/policies/${SHOP}`)[0]?.body).toMatchObject({ remote_only: true });
  });

  it("shows the box off and disabled, with the reason, while there is no destination", async () => {
    const stored = policy(SHOP, { remote_only: true });
    const { user, backend } = await volumesAt("/backups/volumes", { [`PUT /api/volume-backups/policies/${SHOP}`]: (call) => json(200, { ...stored, ...(call.body as object) }) }, { policies: [stored] });
    await user.click(await screen.findByRole("button", { name: `Actions for ${SHOP}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit the policy" }));
    const drawer = await screen.findByRole("dialog", { name: `Edit the data policy of ${SHOP}` });
    const box = within(drawer).getByRole("checkbox", { name: "Keep nothing on this server" });
    expect(box).toBeChecked();
    await user.click(within(drawer).getByRole("checkbox", { name: "nas" }));
    expect(box).not.toBeChecked();
    expect(box).toHaveAttribute("aria-disabled", "true");
    expect(within(drawer).getByText("Add at least one destination to turn this on; with none, the backups stay on this server.")).toBeInTheDocument();
    await expectNoAxeViolations(drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/volume-backups/policies/${SHOP}`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/volume-backups/policies/${SHOP}`)[0]?.body).toMatchObject({ remote_only: false, destinations: [] });
  });

  it("shows the server's refusal of a policy under the schedule it is about", async () => {
    const { user } = await volumesAt("/backups/volumes", {
      [`PUT /api/volume-backups/policies/${SHOP}`]: () => problem(422, "validation_error", "bad", { fields: { schedule: "Invalid calendar expression 'whenever'" } }),
    });
    await user.click(await screen.findByRole("button", { name: `Actions for ${SHOP}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit the policy" }));
    const drawer = await screen.findByRole("dialog", { name: `Edit the data policy of ${SHOP}` });
    await user.click(within(drawer).getByRole("button", { name: "Save policy" }));
    expect(await within(drawer).findByText("Invalid calendar expression 'whenever'")).toBeInTheDocument();
  });

  it("is read in Spanish", async () => {
    await volumesAt();
    await act(() => setLocale("es"));
    expect(await screen.findByRole("region", { name: "Datos de las aplicaciones y sus copias" })).toBeInTheDocument();
    expect(screen.getByText("Falló la última ejecución")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
