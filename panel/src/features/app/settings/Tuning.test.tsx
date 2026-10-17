import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import { DOMAIN, ELEVATED, NO_TUNING, accepted, jobOf, renderSettings, saveBar } from "./testKit";

const TUNED = {
  ...NO_TUNING,
  configured: true,
  options: { memory_high_mb: 384, cpu_weight: 50, capabilities: ["IPC_LOCK"], devices: [{ path: "char-drm", mode: "rw" }] },
  directives: ["MemoryHigh=384M", "CPUWeight=50", "CapabilityBoundingSet=CAP_IPC_LOCK"],
};

function resources(tuning: object = NO_TUNING, routes: Parameters<typeof renderSettings>[3] = {}) {
  return renderSettings("/resources", "Resources", undefined, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/tuning`]: () => json(200, tuning),
    ...routes,
  });
}

function security(tuning: object = NO_TUNING, routes: Parameters<typeof renderSettings>[3] = {}) {
  return renderSettings("/security", "Unit security", undefined, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/hardening`]: () =>
      json(200, { domain: DOMAIN, profile: "default", configured: true, eligible: true, reason: null, writable_paths: [], directives: [], exposure: null, exposure_label: null, unit: null }),
    [`GET /api/apps/${DOMAIN}/tuning`]: () => json(200, tuning),
    ...routes,
  });
}

describe("the limits beyond the hard ones", () => {
  it("keeps them folded away when nothing is set", async () => {
    await resources();

    const toggle = await screen.findByRole("button", { name: "More limits" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("opens them when the unit is already tuned, with what it has", async () => {
    await resources(TUNED);

    expect(await screen.findByRole("button", { name: "More limits" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("textbox", { name: /Soft memory limit/ })).toHaveValue("384");
    expect(screen.getByRole("textbox", { name: /CPU share/ })).toHaveValue("50");
  });

  it("sends the whole tuning as a job it waits for: this half as typed, the other as the server has it", async () => {
    const job = jobOf("t1", "tuning");
    const { user, backend } = await resources(TUNED, {
      [`PUT /api/apps/${DOMAIN}/tuning`]: () => json(202, accepted(job)),
      "GET /api/jobs/t1": () => json(200, { ...job, status: "completed", progress: 100 }),
    });
    // Tuned, so open already.
    const share = await screen.findByRole("textbox", { name: /CPU share/ });
    await user.clear(share);
    await user.type(share, "80");
    await user.type(screen.getByRole("textbox", { name: /Open files/ }), "100000");
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/tuning`)[0]?.body).toEqual({
        options: {
          memory_high_mb: 384,
          cpu_weight: 80,
          limit_nofile: 100000,
          capabilities: ["IPC_LOCK"],
          devices: [{ path: "char-drm", mode: "rw" }],
        },
        restart: false,
      });
    });
    expect(await screen.findByText(`The tuning of ${DOMAIN} was saved`)).toBeInTheDocument();
  });

  it("restarts with the hard limits when that is asked", async () => {
    const job = jobOf("t2", "tuning");
    const { user, backend } = await resources(NO_TUNING, {
      [`PUT /api/apps/${DOMAIN}/tuning`]: () => json(202, accepted(job)),
      "GET /api/jobs/t2": () => json(200, { ...job, status: "completed", progress: 100 }),
      [`PATCH /api/apps/${DOMAIN}/limits`]: () => json(200, { domain: DOMAIN, memory_max_mb: 512, cpu_quota_percent: 150, tasks_max: 256, units: ["shop"], restarted: true, restart_required: false }),
    });
    await user.click(await screen.findByRole("button", { name: "More limits" }));
    await user.type(screen.getByRole("textbox", { name: /Soft memory limit/ }), "256");
    await user.click(screen.getByRole("checkbox", { name: /Restart/ }));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/tuning`)[0]?.body).toMatchObject({ restart: true });
    });
  });

  it("checks the values before asking anything", async () => {
    const { user, backend } = await resources();
    await user.click(await screen.findByRole("button", { name: "More limits" }));
    await user.type(screen.getByRole("textbox", { name: /Soft memory limit/ }), "8");
    await user.type(screen.getByRole("textbox", { name: /Pinned to CPUs/ }), "0-9");

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("8 is out of range: use 64 to 16777216.")).toBeInTheDocument();
    expect(screen.getByText("CPU 9 does not exist: this server has 4, numbered from 0.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/tuning`)).toHaveLength(0);
  });

  it("puts the refusal of the server beside the field", async () => {
    const { user } = await resources(NO_TUNING, {
      [`PUT /api/apps/${DOMAIN}/tuning`]: () =>
        problem(422, "validationerror", "Cannot tune", { fields: { memory_high_mb: "The soft memory limit (900 MB) is above the hard one (512 MB)" } }),
    });
    await user.click(await screen.findByRole("button", { name: "More limits" }));
    await user.type(screen.getByRole("textbox", { name: /Soft memory limit/ }), "900");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("The soft memory limit (900 MB) is above the hard one (512 MB)")).toBeInTheDocument();
  });

  it("is only the hard limits for a unit that cannot be tuned", async () => {
    await resources({ ...NO_TUNING, eligible: false, reason: "its processes run in containers" });

    await screen.findByRole("textbox", { name: /Memory/ });
    expect(screen.queryByRole("button", { name: "More limits" })).not.toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    await resources(TUNED);
    await screen.findByRole("textbox", { name: /CPU share/ });
    await expectNoAxeViolations(screen.getByRole("main"));

    await act(() => setLocale("es"));
    expect(await screen.findByRole("textbox", { name: /Límite blando de memoria/ })).toBeInTheDocument();
  });
});

describe("the capabilities and the devices", () => {
  it("lists what can be given, with what each is for, and ticks what the unit keeps", async () => {
    await security(TUNED);

    const lock = await screen.findByRole("checkbox", { name: /CAP_IPC_LOCK/ });
    expect(lock).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /CAP_NET_BIND_SERVICE/ })).not.toBeChecked();
    expect(screen.getByText("Listen on a port below 1024 without being root.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Devices it may open/ })).toHaveValue("char-drm:rw");
  });

  it("adds the devices of a GPU with one button, never twice", async () => {
    const { user } = await security();
    const devices = await screen.findByRole("textbox", { name: /Devices it may open/ });

    await user.click(screen.getByRole("button", { name: "Add the devices of a NVIDIA GPU" }));
    await user.click(screen.getByRole("button", { name: "Add the devices of a NVIDIA GPU" }));

    expect(devices).toHaveValue("char-nvidia:rw\nchar-nvidia-uvm:rw");
  });

  it("sends the capabilities and the devices with the limits as the server has them", async () => {
    const job = jobOf("t3", "tuning");
    const { user, backend } = await security(TUNED, {
      [`PUT /api/apps/${DOMAIN}/tuning`]: () => json(202, accepted(job)),
      "GET /api/jobs/t3": () => json(200, { ...job, status: "completed", progress: 100 }),
    });
    await user.click(await screen.findByRole("checkbox", { name: /CAP_NET_BIND_SERVICE/ }));
    await user.click(screen.getByRole("checkbox", { name: /CAP_IPC_LOCK/ }));

    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/tuning`)[0]?.body).toEqual({
        options: { memory_high_mb: 384, cpu_weight: 50, capabilities: ["NET_BIND_SERVICE"], devices: [{ path: "char-drm", mode: "rw" }] },
        restart: true,
      });
    });
  });

  it("refuses a device that is not under /dev before asking anything", async () => {
    const { user, backend } = await security();
    await user.type(await screen.findByRole("textbox", { name: /Devices it may open/ }), "/etc/shadow");

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/^\/etc\/shadow is not a device Noust can allow/)).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/tuning`)).toHaveLength(0);
  });

  it("is only the sandbox when the tuning cannot be read", async () => {
    await security(NO_TUNING, { [`GET /api/apps/${DOMAIN}/tuning`]: () => problem(500, "internal", "no") });

    await screen.findByRole("radio", { name: "Default" });
    expect(screen.queryByRole("checkbox", { name: /CAP_IPC_LOCK/ })).not.toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    await security(TUNED);
    await screen.findByRole("checkbox", { name: /CAP_IPC_LOCK/ });

    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
