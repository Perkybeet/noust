import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, STATIC_APP, accepted, jobOf, part, renderSettings } from "./testKit";

const UNIT = "shop-example-com";

const ALONE = {
  domain: DOMAIN,
  copies: 1,
  maximum: 8,
  ports: [],
  eligible: true,
  reason: null,
  units: [{ unit: UNIT, port: 3000, state: "active", own: true, serving: true }],
};

const THREE = {
  ...ALONE,
  copies: 3,
  ports: [3001, 3002],
  units: [
    ...ALONE.units,
    { unit: `${UNIT}-replica@3001`, port: 3001, state: "active", own: false, serving: true },
    { unit: `${UNIT}-replica@3002`, port: 3002, state: "failed", own: false, serving: true },
  ],
};

const WORKER = {
  name: "worker",
  command: "node worker.js",
  instances: 2,
  memory_max_mb: 256,
  cpu_quota_percent: null,
  tasks_max: null,
  source: "operator",
  unit: `${UNIT}-worker@`,
  installed: true,
  running: 1,
  copies: [
    { unit: `${UNIT}-worker@1`, number: 1, state: "active", sub_state: "running", restarts: 0 },
    { unit: `${UNIT}-worker@2`, number: 2, state: "failed", sub_state: "failed", restarts: 3 },
  ],
};

const LIST = (processes: object[]) => ({ domain: DOMAIN, processes, total: processes.length });

function processes(replicas: object = ALONE, list: object[] = [WORKER], routes: Parameters<typeof renderSettings>[3] = {}, app: object = RELEASE_APP) {
  return renderSettings("/processes", "Processes", app, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/replicas`]: () => json(200, replicas),
    [`GET /api/apps/${DOMAIN}/processes`]: () => json(200, LIST(list)),
    ...routes,
  });
}

describe("the copies of the web process", () => {
  it("lists the unit and each copy with its port, its state and whether it takes traffic", async () => {
    await processes(THREE);
    const card = part("Copies of the web process");

    const list = await within(card).findByRole("list", { name: `Copies of ${DOMAIN}` });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    const row = (index: number): HTMLElement => {
      const found = rows[index];
      if (!found) throw new Error(`no row ${String(index)}`);
      return found;
    };
    expect(within(row(0)).getByText("the application's own")).toBeInTheDocument();
    expect(within(row(1)).getByText("3001")).toBeInTheDocument();
    expect(within(row(2)).getByText("Failed")).toBeInTheDocument();
    expect(within(card).getByRole("textbox", { name: /Copies/ })).toHaveValue("3");
  });

  it("runs the application as the copies asked for, as a job it waits for", async () => {
    const job = jobOf("r1", "replicas");
    const { user, backend } = await processes(ALONE, [], {
      [`PUT /api/apps/${DOMAIN}/replicas`]: () => json(202, accepted(job)),
      "GET /api/jobs/r1": () => json(200, { ...job, status: "completed", progress: 100 }),
    });
    const card = part("Copies of the web process");
    const field = await within(card).findByRole("textbox", { name: /Copies/ });
    await user.clear(field);
    await user.type(field, "3");

    await user.click(within(card).getByRole("button", { name: "Apply" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/replicas`)[0]?.body).toEqual({ copies: 3 });
    });
    expect(await screen.findByText(`${DOMAIN} runs 3 copies`)).toBeInTheDocument();
  });

  it("says where a copy that did not answer failed, in the job's own words", async () => {
    const job = jobOf("r2", "replicas");
    const { user } = await processes(ALONE, [], {
      [`PUT /api/apps/${DOMAIN}/replicas`]: () => json(202, accepted(job)),
      "GET /api/jobs/r2": () => json(200, { ...job, status: "failed", error: "A copy of shop.example.com on port 3001 did not answer its health check; nothing changed" }),
    });
    const card = part("Copies of the web process");
    const field = await within(card).findByRole("textbox", { name: /Copies/ });
    await user.clear(field);
    await user.type(field, "2");
    await user.click(within(card).getByRole("button", { name: "Apply" }));

    expect(await screen.findByText(`${DOMAIN} was not scaled`)).toBeInTheDocument();
  });

  it("checks the number before asking anything", async () => {
    const { user, backend } = await processes();
    const card = part("Copies of the web process");
    const field = await within(card).findByRole("textbox", { name: /Copies/ });
    await user.clear(field);
    await user.type(field, "12");

    await user.click(within(card).getByRole("button", { name: "Apply" }));

    expect(await within(card).findByText("Give a whole number of copies from 1 to 8.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/replicas`)).toHaveLength(0);
  });

  it("says why an application cannot run as several copies, instead of a form", async () => {
    await processes({ ...ALONE, eligible: false, reason: "shop.example.com runs in zero-downtime mode" });
    const card = part("Copies of the web process");

    expect(await within(card).findByText(/It cannot run as several copies: shop.example.com runs in zero-downtime mode/)).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
  });

  it("is not offered for a static site, which has no process", async () => {
    await processes(ALONE, [], {}, STATIC_APP);
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
  });
});

describe("the background processes", () => {
  it("lists each with its command, its copies and how each copy is", async () => {
    await processes();
    const card = part("Background processes");

    const list = await within(card).findByRole("list", { name: `Background processes of ${DOMAIN}` });
    expect(within(list).getByText("worker")).toBeInTheDocument();
    expect(within(list).getByText("node worker.js")).toBeInTheDocument();
    expect(within(list).getByText("1 of 2 running")).toBeInTheDocument();
    expect(within(list).getByText("Failed")).toBeInTheDocument();
  });

  it("invites the first one", async () => {
    await processes(ALONE, []);
    expect(await within(part("Background processes")).findByText("No background processes")).toBeInTheDocument();
  });

  it("adds a process with its command, copies and limits", async () => {
    const { user, backend } = await processes(ALONE, [], {
      [`PUT /api/apps/${DOMAIN}/processes/queue`]: () => json(200, { domain: DOMAIN, name: "queue", instances: 2, started: [], stopped: [], removed: false }),
    });
    await user.click(await within(part("Background processes")).findByRole("button", { name: "Add a process" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a process" });
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "queue");
    await user.type(within(dialog).getByRole("textbox", { name: "Command" }), "node queue.js --concurrency 4");
    const copies = within(dialog).getByRole("textbox", { name: "Copies" });
    await user.clear(copies);
    await user.type(copies, "2");
    await user.type(within(dialog).getByRole("textbox", { name: /Memory/ }), "512");

    await user.click(within(dialog).getByRole("button", { name: "Save process" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/processes/queue`)[0]?.body).toEqual({
        command: "node queue.js --concurrency 4",
        instances: 2,
        memory_max_mb: 512,
        cpu_quota_percent: null,
        tasks_max: null,
      });
    });
    expect(await screen.findByText("Saved queue")).toBeInTheDocument();
  });

  it("says what is missing where it is missing, and sends nothing", async () => {
    const { user, backend } = await processes(ALONE, []);
    await user.click(await within(part("Background processes")).findByRole("button", { name: "Add a process" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a process" });

    await user.click(within(dialog).getByRole("button", { name: "Save process" }));

    expect(await within(dialog).findByText("Give the process a name.")).toBeInTheDocument();
    expect(within(dialog).getByText("Give the command to run.")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });

  it("puts a refusal under the field it names", async () => {
    const { user } = await processes(ALONE, [], {
      [`PUT /api/apps/${DOMAIN}/processes/queue`]: () =>
        problem(422, "validationerror", "Cannot read the command", { fields: { command: "Check the quoting: No closing quotation" } }),
    });
    await user.click(await within(part("Background processes")).findByRole("button", { name: "Add a process" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a process" });
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "queue");
    await user.type(within(dialog).getByRole("textbox", { name: "Command" }), "node 'queue.js");
    await user.click(within(dialog).getByRole("button", { name: "Save process" }));

    expect(await within(dialog).findByText("Check the quoting: No closing quotation")).toBeInTheDocument();
  });

  it("changes the copies of a process from its menu", async () => {
    const { user, backend } = await processes(ALONE, [WORKER], {
      [`POST /api/apps/${DOMAIN}/processes/worker/scale`]: () => json(200, { domain: DOMAIN, name: "worker", instances: 4, started: [], stopped: [], removed: false }),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for worker" }));
    await user.click(await screen.findByRole("menuitem", { name: "Change the copies" }));
    const dialog = await screen.findByRole("dialog", { name: "Change the copies of worker" });
    const field = within(dialog).getByRole("textbox", { name: "Copies" });
    await user.clear(field);
    await user.type(field, "4");
    await user.click(within(dialog).getByRole("button", { name: "Change copies" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/processes/worker/scale`)[0]?.body).toEqual({ instances: 4 });
    });
    expect(await screen.findByText("worker runs 4 copies")).toBeInTheDocument();
  });

  it("removes a process after one question", async () => {
    const { user, backend } = await processes(ALONE, [WORKER], {
      [`DELETE /api/apps/${DOMAIN}/processes/worker`]: () => json(200, { domain: DOMAIN, name: "worker", removed: true, started: [], stopped: [], instances: 0 }),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for worker" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove worker?" });
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/processes/worker`)).toHaveLength(1);
    });
  });
});

describe("the processes subsection", () => {
  it("has no accessibility violations, and speaks Spanish", async () => {
    await processes(THREE);
    await within(part("Background processes")).findByText("node worker.js");
    await expectNoAxeViolations(screen.getByRole("main"));

    await act(() => setLocale("es"));
    expect(await screen.findByText("Copias del proceso web")).toBeInTheDocument();
    expect(screen.getByText("Procesos en segundo plano")).toBeInTheDocument();
  });
});
