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
    expect(screen.getByText("The last 7 dumps, for up to 30 days")).toBeInTheDocument();
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
      destinations: [{ name: "nas", retention_count: null, retention_days: null }],
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

  it("is read in Spanish", async () => {
    await pageAt(ON);
    await act(() => setLocale("es"));
    expect(await screen.findByText("Con copia programada")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Paquetes del estado propio de Noust" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
