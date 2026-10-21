import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import { DOMAIN, ELEVATED, accepted, jobOf, renderSettings, saveBar } from "./testKit";

const HARDENED = {
  domain: DOMAIN,
  profile: "default",
  configured: true,
  eligible: true,
  reason: null,
  writable_paths: ["/var/lib/shop-uploads"],
  directives: ["ProtectSystem=strict", "ReadWritePaths=-/var/lib/shop-uploads", "NoNewPrivileges=true"],
  exposure: 4.2,
  exposure_label: "MEDIUM",
  unit: "shop-example-com",
};

const OFF = { ...HARDENED, profile: "off", configured: false, writable_paths: [], directives: [], exposure: 9.6, exposure_label: "UNSAFE" };

function security(hardening: object = HARDENED, routes: Parameters<typeof renderSettings>[3] = {}) {
  return renderSettings("/security", "Service security", undefined, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/hardening`]: () => json(200, hardening),
    ...routes,
  });
}

describe("the sandbox of an application's unit", () => {
  it("shows the profile it has, the folders it may write to and how exposed systemd finds it", async () => {
    await security();

    expect(await screen.findByRole("radio", { name: "Default" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: /Folders it may write to/ })).toHaveValue("/var/lib/shop-uploads");
    expect(screen.getByText("4.2 of 10: MEDIUM")).toBeInTheDocument();
  });

  it("says an application that predates the sandbox is as it always was", async () => {
    await security(OFF);

    expect(await screen.findByText(/predates the sandbox/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Off" })).toBeChecked();
  });

  it("puts the directives of the unit away until they are asked for", async () => {
    const { user } = await security();

    const toggle = await screen.findByRole("button", { name: "Directives of the unit" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(await screen.findByText(/ProtectSystem=strict/)).toBeInTheDocument();
  });

  it("changes the profile and the folders as a job it waits for, and says what it is now", async () => {
    const job = jobOf("h1", "hardening");
    const { user, backend } = await security(HARDENED, {
      [`PUT /api/apps/${DOMAIN}/hardening`]: () => json(202, accepted(job)),
      "GET /api/jobs/h1": () => json(200, { ...job, status: "completed", progress: 100 }),
    });
    await user.click(await screen.findByRole("radio", { name: "Strict" }));
    const folders = screen.getByRole("textbox", { name: /Folders it may write to/ });
    await user.clear(folders);
    await user.type(folders, "/var/lib/a{Enter}/var/lib/b");
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/hardening`)[0]?.body).toEqual({
        profile: "strict",
        writable_paths: ["/var/lib/a", "/var/lib/b"],
        restart: true,
      });
    });
    expect(await screen.findByText(`The sandbox of ${DOMAIN} is now Strict`)).toBeInTheDocument();
  });

  it("can leave the restart for later", async () => {
    const job = jobOf("h2", "hardening");
    const { user, backend } = await security(HARDENED, {
      [`PUT /api/apps/${DOMAIN}/hardening`]: () => json(202, accepted(job)),
      "GET /api/jobs/h2": () => json(200, { ...job, status: "completed", progress: 100 }),
    });
    await user.click(await screen.findByRole("radio", { name: "Strict" }));
    await user.click(screen.getByRole("checkbox", { name: /Restart it now/ }));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/hardening`)[0]?.body).toMatchObject({ restart: false });
    });
  });

  it("checks a folder is absolute before asking anything", async () => {
    const { user, backend } = await security();
    const folders = await screen.findByRole("textbox", { name: /Folders it may write to/ });
    await user.clear(folders);
    await user.type(folders, "uploads");

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText(/^uploads: One absolute path per line/)).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/hardening`)).toHaveLength(0);
  });

  it("puts the refusal of the server under the folders", async () => {
    const { user } = await security(HARDENED, {
      [`PUT /api/apps/${DOMAIN}/hardening`]: () =>
        problem(422, "validationerror", "Cannot write there", { fields: { writable_paths: "/etc/shadow is not a folder the unit may write to" } }),
    });
    await user.click(await screen.findByRole("radio", { name: "Strict" }));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("/etc/shadow is not a folder the unit may write to")).toBeInTheDocument();
  });

  it("says why a unit cannot be sandboxed, instead of a form", async () => {
    await security({ ...HARDENED, eligible: false, reason: "a Compose stack's processes are Docker's" });

    expect(await screen.findByText(/cannot be sandboxed: a Compose stack's processes are Docker's/)).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Strict" })).not.toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    await security();
    await screen.findByRole("radio", { name: "Default" });
    await expectNoAxeViolations(screen.getByRole("main"));

    await act(() => setLocale("es"));
    expect(await screen.findByRole("radio", { name: "Por defecto" })).toBeInTheDocument();
  });
});
