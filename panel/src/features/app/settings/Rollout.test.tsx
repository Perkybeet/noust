import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, renderSettings } from "./testKit";

const ROLLOUT = `/api/apps/${DOMAIN}/rollout`;
const FREEZES = "/api/deploy-freezes";
const SCHEDULE = `/api/apps/${DOMAIN}/update-schedule`;

const RUNNING = {
  domain: DOMAIN,
  history_keep: null,
  history_keep_server: 20,
  history_keep_effective: 20,
  stop_timeout: null,
  stop_signal: null,
  stop_supported: true,
  pause_on_rollback: true,
  paused: null,
  held: 0,
  automatic: ["webhook", "tags"],
  freeze: null,
};

const PAUSED = { ...RUNNING, paused: { at: "2026-10-16T10:00:00+00:00", by: "ana", reason: "Looking into the checkout bug" }, held: 3 };

const ALIASES = { daily: "*-*-* 03:00:00", weekly: "Sun *-*-* 03:00:00", monthly: "*-*-01 03:00:00" };
const SCHEDULE_OFF = {
  domain: DOMAIN,
  scheduled: false,
  calendar: null,
  enabled: true,
  backup_first: true,
  only_if_new: true,
  next_run: null,
  last_run_at: null,
  last_outcome: null,
  last_detail: null,
  last_deployment_id: null,
  aliases: ALIASES,
};
const SCHEDULE_ON = {
  ...SCHEDULE_OFF,
  scheduled: true,
  calendar: ALIASES.weekly,
  next_run: "2999-01-01T03:00:00+00:00",
  last_run_at: "2026-10-12T03:00:00+00:00",
  last_outcome: "backup_failed",
  last_detail: "disk full",
};

const FREEZE = {
  id: 7,
  scope: DOMAIN,
  domain: DOMAIN,
  reason: "Black Friday: nothing ships",
  starts_at: "2026-11-27T00:00:00+00:00",
  ends_at: "2026-11-28T00:00:00+00:00",
  created_by: "ana",
  created_at: "2026-10-16T10:00:00+00:00",
  lifted_at: null,
  lifted_by: null,
  state: "active",
};
const SERVER_FREEZE = { ...FREEZE, id: 8, scope: "server", domain: null, reason: "Datacentre move" };

const SESSION_ROUTES: Record<string, RouteHandler> = { "GET /api/auth/session": () => json(200, ELEVATED) };

function deploys(routes: Record<string, RouteHandler> = {}) {
  return renderSettings("/deploys", "Deploys", RELEASE_APP, { ...SESSION_ROUTES, ...routes });
}

describe("automatic deploys", { timeout: 20_000 }, () => {
  it("says they run and what could deploy the app by itself", async () => {
    await deploys({ [`GET ${ROLLOUT}`]: () => json(200, RUNNING) });

    const heading = await screen.findByRole("heading", { name: "Automatic deploys" });
    expect(await screen.findByText("Automatic deploys are running")).toBeInTheDocument();
    expect(screen.getByTestId("automatic-sources")).toHaveTextContent("Held while paused: push webhook, new tags");
    const card = heading.closest<HTMLElement>("section, div");
    if (card === null) throw new Error("the heading is in no card");
    await expectNoAxeViolations(card);
  });

  it("says when nothing can deploy it by itself", async () => {
    await deploys({ [`GET ${ROLLOUT}`]: () => json(200, { ...RUNNING, automatic: [] }) });

    expect(await screen.findByTestId("automatic-sources")).toHaveTextContent("Nothing deploys this app by itself yet");
  });

  it("pauses with the reason given and then says who paused it", async () => {
    let state: object = RUNNING;
    const { user, backend } = await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, state),
      [`POST ${ROLLOUT}/pause`]: () => {
        state = PAUSED;
        return json(200, PAUSED);
      },
    });

    await user.click(await screen.findByRole("button", { name: "Pause" }));
    const dialog = await screen.findByRole("dialog", { name: `Pause automatic deploys of ${DOMAIN}?` });
    await user.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Looking into the checkout bug");
    await user.click(within(dialog).getByRole("button", { name: "Pause" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${ROLLOUT}/pause`)[0]?.body).toEqual({ reason: "Looking into the checkout bug" });
    });
    expect(await screen.findByText("Automatic deploys are paused")).toBeInTheDocument();
    expect(screen.getByText("Paused by ana.")).toBeInTheDocument();
    expect(screen.getByText("Reason: Looking into the checkout bug")).toBeInTheDocument();
    expect(screen.getByText("3 pushes or tags have been held since.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("pauses without a reason when none is given", async () => {
    const { user, backend } = await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`POST ${ROLLOUT}/pause`]: () => json(200, PAUSED),
    });

    await user.click(await screen.findByRole("button", { name: "Pause" }));
    const dialog = await screen.findByRole("dialog", { name: `Pause automatic deploys of ${DOMAIN}?` });
    await user.click(within(dialog).getByRole("button", { name: "Pause" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${ROLLOUT}/pause`)[0]?.body).toEqual({ reason: null });
    });
  });

  it("resumes, and reads the app again", async () => {
    let state: object = PAUSED;
    const { user, backend } = await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, state),
      [`POST ${ROLLOUT}/resume`]: () => {
        state = RUNNING;
        return json(200, { domain: DOMAIN, was_paused: true, held: 3 });
      },
    });

    await user.click(await screen.findByRole("button", { name: "Resume" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${ROLLOUT}/resume`)).toHaveLength(1);
    });
    expect(await screen.findByText("Automatic deploys are running")).toBeInTheDocument();
  });

  it("turns off pausing after a rollback with PUT", async () => {
    const { user, backend } = await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`PUT ${ROLLOUT}`]: () => json(200, { ...RUNNING, pause_on_rollback: false }),
    });

    const toggle = await screen.findByRole("switch", { name: "Pause automatic deploys after a rollback" });
    expect(toggle).toBeChecked();
    await user.click(toggle);

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROLLOUT}`)[0]?.body).toEqual({ pause_on_rollback: false });
    });
    await waitFor(() => {
      expect(screen.getByRole("switch", { name: "Pause automatic deploys after a rollback" })).not.toBeChecked();
    });
  });

  it("is not shown by a server that has no such setting", async () => {
    await deploys({});

    await screen.findByRole("heading", { name: "A backup before each update" });
    expect(screen.queryByRole("heading", { name: "Automatic deploys" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "History and stopping" })).not.toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await deploys({ [`GET ${ROLLOUT}`]: () => json(200, PAUSED) });
    await screen.findByText("Automatic deploys are paused");

    await act(() => setLocale("es"));

    expect(await screen.findByText("Los despliegues automáticos están en pausa")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reanudar" })).toBeInTheDocument();
    expect(screen.getByText("Motivo: Looking into the checkout bug")).toBeInTheDocument();
  });
});

describe("history and stopping", { timeout: 20_000 }, () => {
  it("shows the server's number and sends only what changed", async () => {
    const { user, backend } = await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`PUT ${ROLLOUT}`]: () => json(200, { ...RUNNING, history_keep: 50 }),
    });

    const history = await screen.findByRole("textbox", { name: "Deploys kept in history" });
    expect(history).toHaveValue("");
    expect(history).toHaveAttribute("placeholder", "Server default");
    expect(screen.getByText(/Empty uses the server's 20\./)).toBeInTheDocument();
    await user.type(history, "50");
    await user.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROLLOUT}`)[0]?.body).toEqual({ history_keep: 50 });
    });
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "Deploys kept in history" })).toHaveValue("50");
    });
  });

  it("goes back to the server's number with an empty field", async () => {
    const { user, backend } = await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, { ...RUNNING, history_keep: 50 }),
      [`PUT ${ROLLOUT}`]: () => json(200, RUNNING),
    });

    const history = await screen.findByRole("textbox", { name: "Deploys kept in history" });
    await waitFor(() => {
      expect(history).toHaveValue("50");
    });
    await user.clear(history);
    await user.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROLLOUT}`)[0]?.body).toEqual({ history_keep: null });
    });
  });

  it("refuses a number out of range before asking the backend", async () => {
    const { user, backend } = await deploys({ [`GET ${ROLLOUT}`]: () => json(200, RUNNING) });

    await user.type(await screen.findByRole("textbox", { name: "Deploys kept in history" }), "0");
    await user.click(await screen.findByRole("button", { name: "Save" }));

    expect(await screen.findByText("A number from 1 to 1000, or empty for the server's.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT ${ROLLOUT}`)).toHaveLength(0);
  });

  it("sets the time and the signal a unit stops with", async () => {
    const { user, backend } = await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`PUT ${ROLLOUT}`]: () => json(200, { ...RUNNING, stop_timeout: 30, stop_signal: "SIGINT" }),
    });

    await user.type(await screen.findByRole("textbox", { name: "Time to stop" }), "30");
    await user.click(screen.getByRole("combobox", { name: "Stop signal" }));
    await user.click(await screen.findByRole("option", { name: "SIGINT" }));
    await user.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ROLLOUT}`)[0]?.body).toEqual({ stop_timeout: 30, stop_signal: "SIGINT" });
    });
  });

  it("says a unit that Noust does not write has no stop policy here", async () => {
    await deploys({ [`GET ${ROLLOUT}`]: () => json(200, { ...RUNNING, stop_supported: false }) });

    expect(await screen.findByText(/does not run in a unit Noust writes/)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Time to stop" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Deploys kept in history" })).toBeInTheDocument();
  });
});

describe("freezing the deploys of an app", { timeout: 20_000 }, () => {
  it("lists the app's freeze with its reason, and not the server's", async () => {
    await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`GET ${FREEZES}`]: () => json(200, { freezes: [FREEZE, SERVER_FREEZE] }),
    });

    const list = await screen.findByRole("list", { name: "Freeze deploys" });
    expect(within(list).getByText("Black Friday: nothing ships")).toBeInTheDocument();
    expect(within(list).getByText("Frozen now")).toBeInTheDocument();
    expect(within(list).getByText(/Set by ana/)).toBeInTheDocument();
    expect(screen.queryByText("Datacentre move")).not.toBeInTheDocument();
  });

  it("freezes with a reason and a length", async () => {
    const { user, backend } = await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`GET ${FREEZES}`]: () => json(200, { freezes: [] }),
      [`POST ${FREEZES}`]: () => json(201, FREEZE),
    });

    expect(await screen.findByText("No freeze is set for this app.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Freeze deploys" }));
    const dialog = await screen.findByRole("dialog", { name: `Freeze deploys of ${DOMAIN}` });
    await user.click(within(dialog).getByRole("button", { name: "Freeze" }));
    // A person who is refused is told why, so the reason is not optional.
    expect(await within(dialog).findByText("Say why: a person who is refused is told.")).toBeInTheDocument();
    expect(backend.callsTo(`POST ${FREEZES}`)).toHaveLength(0);

    await user.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Black Friday: nothing ships");
    await user.click(within(dialog).getByRole("combobox", { name: "For how long" }));
    await user.click(await screen.findByRole("option", { name: "1 day" }));
    await user.click(within(dialog).getByRole("button", { name: "Freeze" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${FREEZES}`)[0]?.body).toEqual({ reason: "Black Friday: nothing ships", domain: DOMAIN, hours: 24 });
    });
  });

  it("freezes until somebody lifts it, from a time given", async () => {
    const { user, backend } = await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`GET ${FREEZES}`]: () => json(200, { freezes: [] }),
      [`POST ${FREEZES}`]: () => json(201, FREEZE),
    });

    await user.click(await screen.findByRole("button", { name: "Freeze deploys" }));
    const dialog = await screen.findByRole("dialog", { name: `Freeze deploys of ${DOMAIN}` });
    await user.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Holiday");
    await user.click(within(dialog).getByRole("combobox", { name: "Starts" }));
    await user.click(await screen.findByRole("option", { name: "At a time" }));
    await user.click(within(dialog).getByRole("combobox", { name: "For how long" }));
    await user.click(await screen.findByRole("option", { name: "Until I lift it" }));
    const at = within(dialog).getByLabelText("Starts at");
    // A date-time control takes its whole value at once, as the browser's picker hands it over.
    fireEvent.change(at, { target: { value: "2026-12-24T18:00" } });
    await user.click(within(dialog).getByRole("button", { name: "Freeze" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST ${FREEZES}`)[0]?.body).toEqual({ reason: "Holiday", domain: DOMAIN, starts_at: "2026-12-24T18:00" });
    });
  });

  it("lifts a freeze after asking", async () => {
    let freezes = [FREEZE];
    const { user, backend } = await deploys({
      [`GET ${ROLLOUT}`]: () => json(200, RUNNING),
      [`GET ${FREEZES}`]: () => json(200, { freezes }),
      [`DELETE ${FREEZES}/7`]: () => {
        freezes = [];
        return json(200, { lifted: [{ ...FREEZE, state: "lifted" }] });
      },
    });

    await user.click(await screen.findByRole("button", { name: "Lift the freeze: Black Friday: nothing ships" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Lift the freeze?" });
    expect(backend.callsTo(`DELETE ${FREEZES}/7`)).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Lift the freeze" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE ${FREEZES}/7`)).toHaveLength(1);
    });
    expect(await screen.findByText("No freeze is set for this app.")).toBeInTheDocument();
  });
});

describe("scheduled updates", { timeout: 20_000 }, () => {
  it("offers to schedule an app that does not update itself", async () => {
    const { user, backend } = await deploys({
      [`GET ${SCHEDULE}`]: () => json(200, SCHEDULE_OFF),
      [`PUT ${SCHEDULE}`]: () => json(200, SCHEDULE_ON),
    });

    expect(await screen.findByText("This app does not update itself")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Schedule updates" }));
    expect(await screen.findByRole("combobox", { name: "When" })).toHaveTextContent("Every Sunday at 03:00");
    expect(screen.getByRole("switch", { name: "Refuse the update when its backup does not happen" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Do nothing when there is nothing new" })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Save the schedule" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${SCHEDULE}`)[0]?.body).toEqual({ calendar: "weekly", enabled: true, backup_first: true, only_if_new: true });
    });
    expect(await screen.findByText("This app updates itself")).toBeInTheDocument();
  });

  it("says when it runs next and how the last run ended", async () => {
    await deploys({ [`GET ${SCHEDULE}`]: () => json(200, SCHEDULE_ON) });

    expect(await screen.findByText("This app updates itself")).toBeInTheDocument();
    expect(screen.getByTestId("schedule-last-run")).toHaveTextContent("refused, its backup failed");
    expect(screen.getByText("disk full")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "When" })).toHaveTextContent("Every Sunday at 03:00");
  });

  it("writes its own calendar expression and keeps what was written", async () => {
    const { user, backend } = await deploys({
      [`GET ${SCHEDULE}`]: () => json(200, SCHEDULE_ON),
      [`PUT ${SCHEDULE}`]: () => json(200, { ...SCHEDULE_ON, calendar: "Sun *-*-* 04:30:00" }),
    });

    await user.click(await screen.findByRole("combobox", { name: "When" }));
    await user.click(await screen.findByRole("option", { name: "At a time I write" }));
    await user.click(screen.getByRole("button", { name: "Save the schedule" }));
    expect(await screen.findByText("Write when it should run.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT ${SCHEDULE}`)).toHaveLength(0);

    await user.type(screen.getByRole("textbox", { name: /Calendar expression/ }), "Sun *-*-* 04:30");
    await user.click(screen.getByRole("button", { name: "Save the schedule" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${SCHEDULE}`)[0]?.body).toEqual({ calendar: "Sun *-*-* 04:30", enabled: true, backup_first: true, only_if_new: true });
    });
  });

  it("puts the backend's refusal of an expression beside the field", async () => {
    const { user } = await deploys({
      [`GET ${SCHEDULE}`]: () => json(200, SCHEDULE_ON),
      [`PUT ${SCHEDULE}`]: () => json(400, { error: "validation_error", detail: "Not a calendar expression: 'whenever'", hint: "Write a systemd calendar expression.", fields: { calendar: "Not a calendar expression: 'whenever'" }, output: null }),
    });

    await user.click(await screen.findByRole("combobox", { name: "When" }));
    await user.click(await screen.findByRole("option", { name: "At a time I write" }));
    await user.type(screen.getByRole("textbox", { name: /Calendar expression/ }), "whenever");
    await user.click(screen.getByRole("button", { name: "Save the schedule" }));

    expect(await screen.findByText("Not a calendar expression: 'whenever'")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Calendar expression/ })).toHaveAttribute("aria-invalid", "true");
  });

  it("stops the app updating itself after asking", async () => {
    const { user, backend } = await deploys({
      [`GET ${SCHEDULE}`]: () => json(200, SCHEDULE_ON),
      [`DELETE ${SCHEDULE}`]: () => json(200, SCHEDULE_OFF),
    });

    await user.click(await screen.findByRole("button", { name: "Stop updating by itself" }));
    const confirm = await screen.findByRole("alertdialog", { name: `Stop ${DOMAIN} updating itself?` });
    expect(backend.callsTo(`DELETE ${SCHEDULE}`)).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Stop" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE ${SCHEDULE}`)).toHaveLength(1);
    });
    expect(await screen.findByText("This app does not update itself")).toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await deploys({ [`GET ${SCHEDULE}`]: () => json(200, SCHEDULE_ON) });
    await screen.findByText("This app updates itself");

    await act(() => setLocale("es"));

    expect(await screen.findByText("Esta aplicación se actualiza sola")).toBeInTheDocument();
    expect(screen.getByTestId("schedule-last-run")).toHaveTextContent("rechazada, falló su copia de seguridad");
  });
});
