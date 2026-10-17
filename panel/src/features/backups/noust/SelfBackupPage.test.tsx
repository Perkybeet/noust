import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Bundle, SelfSchedule } from "../../../api/queries/selfBackup";
import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { frameRoutes } from "../frameRoutes";

const JOB = { id: "self-1", type: "backup", name: "Back up Noust", description: "", status: "running", progress: 10, total_steps: 100, current_step: "", created_at: "2026-10-06T10:00:00", logs: [{ message: "Sealing the bundle" }], metadata: { kind: "self-backup" } };
const ACCEPTED = { job_id: JOB.id, status: "pending", message: "Queued", job: JOB };
const NAME = "noust-self-web-1-20261006-030004.tar.gz.enc";
const PASSPHRASE = "correct horse battery staple";

const OFF: SelfSchedule = {
  configured: false,
  schedule: null,
  retention_count: null,
  retention_days: null,
  destinations: [],
  remote_only: false,
  enabled: false,
  passphrase_stored: false,
  timer: {},
  last_run_at: null,
  last_status: null,
  last_error: null,
  last_file: null,
  last_success_at: null,
  last_verified_at: null,
  last_verify_status: null,
  created_at: null,
  updated_at: null,
};

const ON: SelfSchedule = {
  ...OFF,
  configured: true,
  schedule: "*-*-* 03:00:00",
  retention_count: 7,
  retention_days: 30,
  destinations: [{ name: "nas", retention_count: 14, retention_days: null, exists: true, encrypted: true }],
  enabled: true,
  passphrase_stored: true,
  timer: { installed: true, next_run: "Wed 2026-10-07 03:00:00 UTC" },
  last_run_at: "2026-10-06T03:00:04+00:00",
  last_status: "ok",
  last_verified_at: "2026-10-06T03:00:30+00:00",
  last_verify_status: "ok",
  updated_at: "2026-10-01T00:00:00+00:00",
};

function bundle(name: string, extra: Partial<Bundle> = {}): Bundle {
  return { name, size: 3 * 1024 ** 2, created_at: "2026-10-06T03:00:04+00:00", host: "web-1", kind: "scheduled", scheduled: true, sha256: "ab12", remote_copies: [{ destination: "nas" }], sidecar_mac: "valid", ...extra };
}

async function pageAt(schedule: SelfSchedule, bundles: Bundle[] = [bundle(NAME)], extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    frameRoutes({
      "GET /api/self-backup": () => json(200, { bundles, schedule }),
      [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
      ...extra,
    }),
  );
  const harness = renderConsole("/backups/noust");
  await screen.findByRole("heading", { level: 1, name: "Backups" });
  return { ...harness, backend };
}

describe("the Noust tab", () => {
  it("says Noust's own state is not backed up when there is no schedule, and what that costs", async () => {
    await pageAt(OFF, []);
    expect(await screen.findByText("Not backed up on a schedule")).toBeInTheDocument();
    expect(screen.getByText(/a lost server loses its accounts/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set a schedule" })).toBeInTheDocument();
    expect(screen.getByText("No bundles yet")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("shows a working schedule: when, what it keeps, where it sends, whether the passphrase is kept, and when it was last checked", async () => {
    await pageAt(ON);
    expect(await screen.findByText("Backed up on a schedule")).toBeInTheDocument();
    expect(screen.getByText("Kept on this server, for the timer")).toBeInTheDocument();
    expect(screen.getByText("The last 7 backups, for up to 30 days")).toBeInTheDocument();
    expect(screen.getAllByText("Every day at 03:00").length).toBeGreaterThan(0);
    const table = screen.getByRole("region", { name: "Bundles of Noust's own state" });
    expect(within(table).getByText(NAME)).toBeInTheDocument();
    expect(within(table).getByText("Scheduled")).toBeInTheDocument();
    expect(within(table).getByText("nas")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is amber, with what is wrong said, when the schedule is on but cannot work", async () => {
    await pageAt({ ...ON, passphrase_stored: false, last_status: "failed", last_error: "openssl: bad decrypt" });
    expect(await screen.findByText("On, with a problem")).toBeInTheDocument();
    expect(screen.getByText(/Its passphrase is not kept on this server/)).toBeInTheDocument();
    expect(screen.getByText("openssl: bad decrypt")).toBeInTheDocument();
  });

  it("backs up now with the passphrase the server keeps, without asking for it", async () => {
    const { user, backend } = await pageAt(ON, [bundle(NAME)], { "POST /api/self-backup": () => json(202, ACCEPTED) });
    await user.click(await screen.findByRole("button", { name: "Back up Noust now" }));
    await waitFor(() => expect(backend.callsTo("POST /api/self-backup")).toHaveLength(1));
    expect(backend.callsTo("POST /api/self-backup")[0]?.body).toEqual({ passphrase: null });
    expect(await screen.findByText("Backing up Noust's own state")).toBeInTheDocument();
  });

  it("asks for a passphrase, twice, when the server keeps none, and says what a mistake costs", async () => {
    const { user, backend } = await pageAt(OFF, [], { "POST /api/self-backup": () => json(202, ACCEPTED) });
    await user.click(await screen.findByRole("button", { name: "Back up Noust now" }));
    const dialog = await screen.findByRole("dialog", { name: "Seal a bundle" });
    expect(within(dialog).getByLabelText("Passphrase")).toHaveAttribute("type", "password");
    await user.type(within(dialog).getByLabelText("Passphrase"), "short");
    await user.click(within(dialog).getByRole("button", { name: "Back up now" }));
    expect(await within(dialog).findByText("Use at least 12 characters.")).toBeInTheDocument();
    await user.clear(within(dialog).getByLabelText("Passphrase"));
    await user.type(within(dialog).getByLabelText("Passphrase"), PASSPHRASE);
    await user.type(within(dialog).getByLabelText("Repeat the passphrase"), "correct horse battery stapl");
    await user.click(within(dialog).getByRole("button", { name: "Back up now" }));
    expect(await within(dialog).findByText("The two passphrases are not the same.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/self-backup")).toHaveLength(0);
    await user.clear(within(dialog).getByLabelText("Repeat the passphrase"));
    await user.type(within(dialog).getByLabelText("Repeat the passphrase"), PASSPHRASE);
    await user.click(within(dialog).getByRole("button", { name: "Back up now" }));
    await waitFor(() => expect(backend.callsTo("POST /api/self-backup")).toHaveLength(1));
    expect(backend.callsTo("POST /api/self-backup")[0]?.body).toEqual({ passphrase: PASSPHRASE });
    // The passphrase is not left on the page.
    await waitFor(() => expect(screen.queryByDisplayValue(PASSPHRASE)).not.toBeInTheDocument());
  });

  it("checks a bundle with the stored passphrase, or asks for the one it was sealed with", async () => {
    const { user, backend } = await pageAt(ON, [bundle(NAME)], { [`POST /api/self-backup/${NAME}/verify`]: () => json(202, ACCEPTED) });
    await user.click(await screen.findByRole("button", { name: `Actions for ${NAME}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Check it" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/self-backup/${NAME}/verify`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/self-backup/${NAME}/verify`)[0]?.body).toEqual({ passphrase: null });
  });

  it("asks for the passphrase to check a bundle when the server keeps none", async () => {
    const { user, backend } = await pageAt({ ...ON, passphrase_stored: false }, [bundle(NAME)], { [`POST /api/self-backup/${NAME}/verify`]: () => json(202, ACCEPTED) });
    await user.click(await screen.findByRole("button", { name: `Actions for ${NAME}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Check it" }));
    const dialog = await screen.findByRole("dialog", { name: `Check ${NAME}` });
    expect(within(dialog).queryByLabelText("Repeat the passphrase")).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Passphrase"), PASSPHRASE);
    await user.click(within(dialog).getByRole("button", { name: "Check bundle" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/self-backup/${NAME}/verify`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/self-backup/${NAME}/verify`)[0]?.body).toEqual({ passphrase: PASSPHRASE });
  });

  it("never restores from here: it says the command to run at a terminal", async () => {
    const { user, backend } = await pageAt(ON);
    await user.click(await screen.findByRole("button", { name: `Actions for ${NAME}` }));
    expect(screen.queryByRole("menuitem", { name: /^Restore$/ })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("menuitem", { name: "How to restore…" }));
    const dialog = await screen.findByRole("dialog", { name: `Restore ${NAME}` });
    expect(within(dialog).getByText(`noust self-backup restore ${NAME}`)).toBeInTheDocument();
    expect(within(dialog).getByText(`noust restore-server --from ${NAME}`)).toBeInTheDocument();
    expect(within(dialog).getByText(/stops the console, the monitor and the timers/)).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    expect(backend.calls.filter((call) => call.method !== "GET")).toHaveLength(0);
  });

  it("sends a bundle to a destination, and deletes one only after its name is typed", async () => {
    const { user, backend } = await pageAt(ON, [bundle(NAME)], {
      [`POST /api/self-backup/${NAME}/push`]: () => json(202, ACCEPTED),
      [`DELETE /api/self-backup/${NAME}`]: () => json(200, { success: true, message: "Deleted" }),
    });
    await user.click(await screen.findByRole("button", { name: `Actions for ${NAME}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Send to…" }));
    const push = await screen.findByRole("dialog", { name: `Send ${NAME} to a destination` });
    await user.click(within(push).getByRole("combobox", { name: "Destination" }));
    await user.click(await screen.findByRole("option", { name: "nas" }));
    await user.click(within(push).getByRole("button", { name: "Send" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/self-backup/${NAME}/push`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/self-backup/${NAME}/push`)[0]?.body).toEqual({ destination: "nas" });
  });

  it("deletes a bundle only after its name is typed", async () => {
    const { user, backend } = await pageAt(ON, [bundle(NAME)], { [`DELETE /api/self-backup/${NAME}`]: () => json(200, { success: true, message: "Deleted" }) });
    await user.click(await screen.findByRole("button", { name: `Actions for ${NAME}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    const confirm = await screen.findByRole("alertdialog", { name: `Delete ${NAME}` });
    expect(within(confirm).getByRole("button", { name: "Delete bundle" })).toBeDisabled();
    await user.type(within(confirm).getByRole("textbox"), NAME);
    await user.click(within(confirm).getByRole("button", { name: "Delete bundle" }));
    await waitFor(() => expect(backend.callsTo(`DELETE /api/self-backup/${NAME}`)).toHaveLength(1));
  });

  it("sets the schedule: it needs a passphrase the first time, and keeps the stored one after", async () => {
    const { user, backend } = await pageAt(OFF, [], { "PUT /api/self-backup/schedule": (call) => json(200, { ...ON, ...(call.body as object) }) });
    await user.click(await screen.findByRole("button", { name: "Set a schedule" }));
    const drawer = await screen.findByRole("dialog", { name: "Backups of Noust's own state" });
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    expect(await within(drawer).findByText("Type the passphrase bundles are sealed with.")).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/self-backup/schedule")).toHaveLength(0);
    await user.type(within(drawer).getByLabelText("Passphrase"), PASSPHRASE);
    await user.type(within(drawer).getByLabelText("Repeat the passphrase"), PASSPHRASE);
    await user.click(within(drawer).getByRole("checkbox", { name: "nas" }));
    await expectNoAxeViolations(drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/self-backup/schedule")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/self-backup/schedule")[0]?.body).toEqual({
      schedule: "*-*-* 02:00:00",
      retention_count: 7,
      retention_days: 30,
      retention_gb: null,
      destinations: [{ name: "nas", retention_count: null, retention_days: null }],
      remote_only: false,
      passphrase: PASSPHRASE,
      enabled: true,
    });
  });

  it("leaves the stored passphrase alone when the box is left empty", async () => {
    const { user, backend } = await pageAt(ON, [bundle(NAME)], { "PUT /api/self-backup/schedule": (call) => json(200, { ...ON, ...(call.body as object) }) });
    await user.click(await screen.findByRole("button", { name: "Edit the schedule" }));
    const drawer = await screen.findByRole("dialog", { name: "Backups of Noust's own state" });
    expect(within(drawer).getByText(/This server keeps one/)).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/self-backup/schedule")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/self-backup/schedule")[0]?.body).toMatchObject({ passphrase: null, schedule: "*-*-* 03:00:00" });
  });

  it("gives back a schedule that keeps nothing on this server, as the server reported it", async () => {
    const stored = { ...ON, remote_only: true };
    const { user, backend } = await pageAt(stored, [bundle(NAME)], { "PUT /api/self-backup/schedule": (call) => json(200, { ...stored, ...(call.body as object) }) });
    await user.click(await screen.findByRole("button", { name: "Edit the schedule" }));
    const drawer = await screen.findByRole("dialog", { name: "Backups of Noust's own state" });
    expect(within(drawer).getByRole("checkbox", { name: "Keep nothing on this server" })).toBeChecked();
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/self-backup/schedule")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/self-backup/schedule")[0]?.body).toMatchObject({ remote_only: true, destinations: [{ name: "nas" }] });
  });

  it("sends false for a schedule that keeps its bundles here, and true once the box is ticked", async () => {
    const { user, backend } = await pageAt(ON, [bundle(NAME)], { "PUT /api/self-backup/schedule": (call) => json(200, { ...ON, ...(call.body as object) }) });
    await user.click(await screen.findByRole("button", { name: "Edit the schedule" }));
    const drawer = await screen.findByRole("dialog", { name: "Backups of Noust's own state" });
    const box = within(drawer).getByRole("checkbox", { name: "Keep nothing on this server" });
    expect(box).not.toBeChecked();
    expect(box).not.toHaveAttribute("aria-disabled", "true");
    await user.click(box);
    expect(box).toBeChecked();
    await expectNoAxeViolations(drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/self-backup/schedule")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/self-backup/schedule")[0]?.body).toMatchObject({ remote_only: true });
  });

  it("sends false when the schedule keeps its bundles here and the box is left alone", async () => {
    const { user, backend } = await pageAt(ON, [bundle(NAME)], { "PUT /api/self-backup/schedule": (call) => json(200, { ...ON, ...(call.body as object) }) });
    await user.click(await screen.findByRole("button", { name: "Edit the schedule" }));
    const drawer = await screen.findByRole("dialog", { name: "Backups of Noust's own state" });
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/self-backup/schedule")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/self-backup/schedule")[0]?.body).toMatchObject({ remote_only: false });
  });

  it("shows the box off and disabled, with the reason, while there is no destination", async () => {
    const stored = { ...ON, remote_only: true };
    const { user, backend } = await pageAt(stored, [bundle(NAME)], { "PUT /api/self-backup/schedule": (call) => json(200, { ...stored, ...(call.body as object) }) });
    await user.click(await screen.findByRole("button", { name: "Edit the schedule" }));
    const drawer = await screen.findByRole("dialog", { name: "Backups of Noust's own state" });
    const box = within(drawer).getByRole("checkbox", { name: "Keep nothing on this server" });
    expect(box).toBeChecked();
    // The server refuses it without a destination: taking the last one off puts the box off.
    await user.click(within(drawer).getByRole("checkbox", { name: "nas" }));
    expect(box).not.toBeChecked();
    expect(box).toHaveAttribute("aria-disabled", "true");
    expect(within(drawer).getByText("Add at least one destination to turn this on; with none, the bundles stay on this server.")).toBeInTheDocument();
    await expectNoAxeViolations(drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/self-backup/schedule")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/self-backup/schedule")[0]?.body).toMatchObject({ remote_only: false, destinations: [] });
  });

  it("is read in Spanish", async () => {
    await pageAt(ON);
    await act(() => setLocale("es"));
    expect(await screen.findByText("Con copia programada")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Paquetes del estado propio de Noust" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

const MOVE_PLAN = {
  host: "old-host",
  destination: "nas",
  applications: [
    { domain: "shop.example.com", type: "nextjs", names: ["shop.example.com", "www.shop.example.com"], databases: ["postgresql/shop_db"], data: 2, last_backup: "2026-10-12T02:00:00" },
  ],
  databases: ["mysql/orphan"],
  engines: ["mysql", "postgresql"],
  instances: [],
  passphrase_stored: false,
  blockers: [],
  warnings: ["A backup of a running application is as consistent as its own dump allows: for a final copy, stop what writes and run the preparation again"],
};

const MOVE_JOB = { ...JOB, id: "move-1", name: "Prepare the move", metadata: { kind: "server-migration" } };
// No snapshot of the job: the page reads it by its id at once, instead of waiting for the next poll.
const MOVE_ACCEPTED = { job_id: MOVE_JOB.id, status: "pending", message: "Queued" };

describe("moving to another server", () => {
  async function openDialog(plan: Record<string, unknown> = MOVE_PLAN, extra: Record<string, RouteHandler> = {}) {
    const found = await pageAt(ON, [bundle(NAME)], {
      "GET /api/server/migration/plan": () => json(200, plan),
      ...extra,
    });
    await found.user.click(await screen.findByRole("button", { name: "Move to another server…" }));
    const dialog = await screen.findByRole("dialog", { name: "Move to another server" });
    await found.user.click(within(dialog).getByRole("combobox", { name: "Destination" }));
    await found.user.click(await screen.findByRole("option", { name: "nas" }));
    return { ...found, dialog };
  }

  it("shows what would be copied and what the new server needs before anything is queued", async () => {
    const { dialog, backend } = await openDialog();

    expect(await within(dialog).findByText("shop.example.com")).toBeInTheDocument();
    expect(within(dialog).getByText(/shop\.example\.com, www\.shop\.example\.com · 2 pieces of data/)).toBeInTheDocument();
    expect(within(dialog).getByText("Databases no application owns: mysql/orphan")).toBeInTheDocument();
    expect(within(dialog).getByText("The new server needs: mysql, postgresql")).toBeInTheDocument();
    expect(within(dialog).getByText(/for a final copy, stop what writes/)).toBeInTheDocument();
    expect(backend.callsTo("GET /api/server/migration/plan")[0]?.search.get("destination")).toBe("nas");
    await expectNoAxeViolations(dialog);
  });

  it("needs a passphrase when the server keeps none, and sends it only in the request that starts the copy", async () => {
    const { user, dialog, backend } = await openDialog(MOVE_PLAN, { "POST /api/server/migration/prepare": () => json(202, MOVE_ACCEPTED), [`GET /api/jobs/${MOVE_JOB.id}`]: () => json(200, MOVE_JOB) });
    await within(dialog).findByText("shop.example.com");

    expect(within(dialog).getByRole("button", { name: "Copy everything now" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Passphrase for Noust's state"), PASSPHRASE);
    await user.click(within(dialog).getByRole("button", { name: "Copy everything now" }));

    await waitFor(() => expect(backend.callsTo("POST /api/server/migration/prepare")).toHaveLength(1));
    expect(backend.callsTo("POST /api/server/migration/prepare")[0]?.body).toEqual({ destination: "nas", passphrase: PASSPHRASE, apps: [], data: true });
    expect(await screen.findByText("Copying this server to the destination")).toBeInTheDocument();
  });

  it("uses the stored passphrase when it is left empty", async () => {
    const { user, dialog, backend } = await openDialog({ ...MOVE_PLAN, passphrase_stored: true }, { "POST /api/server/migration/prepare": () => json(202, MOVE_ACCEPTED), [`GET /api/jobs/${MOVE_JOB.id}`]: () => json(200, MOVE_JOB) });
    await within(dialog).findByText("shop.example.com");

    expect(within(dialog).getByText("Leave it empty to use the one the schedule keeps.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Copy everything now" }));

    await waitFor(() => expect(backend.callsTo("POST /api/server/migration/prepare")).toHaveLength(1));
    expect(backend.callsTo("POST /api/server/migration/prepare")[0]?.body).toMatchObject({ destination: "nas", passphrase: null });
  });

  it("does not offer to go on when the destination cannot be used, and says why", async () => {
    const { dialog } = await openDialog({ ...MOVE_PLAN, passphrase_stored: true, blockers: ["nas cannot be reached: Could not reach backup destination 'nas'"] });

    expect(await within(dialog).findByText("It cannot start")).toBeInTheDocument();
    expect(within(dialog).getByText(/nas cannot be reached/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Copy everything now" })).toBeDisabled();
  });

  it("shows, when the job ends, each piece and the commands for the new server", async () => {
    const done = {
      ...MOVE_JOB,
      status: "completed",
      state: "succeeded",
      progress: 100,
      result: {
        host: "old-host",
        destination: "nas",
        ok: false,
        bundle: "noust-self-old-host-20261013.tar.gz.enc",
        steps: [
          { kind: "application", name: "shop.example.com", state: "done", detail: "", backup: "shop-b1" },
          { kind: "data", name: "shop.example.com/uploads", state: "failed", detail: "the destination refused it", backup: null },
          { kind: "noust", name: "old-host", state: "done", detail: "", backup: "noust-self-old-host-20261013.tar.gz.enc" },
        ],
        next_steps: ["Install Noust on the new server", "noust restore-server --from-destination nas --name noust-self-old-host-20261013.tar.gz.enc --host old-host", "noust server migrate check"],
        warnings: [],
      },
    };
    const { user, dialog } = await openDialog({ ...MOVE_PLAN, passphrase_stored: true }, { "POST /api/server/migration/prepare": () => json(202, MOVE_ACCEPTED), [`GET /api/jobs/${MOVE_JOB.id}`]: () => json(200, done) });
    await within(dialog).findByText("shop.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Copy everything now" }));

    expect(await screen.findByText("Not everything was copied")).toBeInTheDocument();
    expect(screen.getByText("the destination refused it")).toBeInTheDocument();
    expect(screen.getByText("noust restore-server --from-destination nas --name noust-self-old-host-20261013.tar.gz.enc --host old-host")).toBeInTheDocument();
    expect(screen.getByText("noust server migrate check", { selector: "li *" })).toBeInTheDocument();
  });
});
