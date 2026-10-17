import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { FakeEventSource, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import type { AppBuild } from "../../build/model";
import { DOMAIN, ELEVATED, PACKAGES_NONE, RELEASE_APP, accepted, jobOf, part, renderSettings } from "./testKit";

const BUILD: AppBuild = {
  domain: DOMAIN,
  source: "none",
  base_directory: null,
  install: null,
  build: null,
  start: null,
  publish_directory: null,
  runtime: null,
  document: null,
  repository_error: null,
  applied: null,
};

const DECLARED = { ...PACKAGES_NONE, declared: ["libvips-dev", "ffmpeg"], installed: ["ffmpeg"], missing: ["libvips-dev"], updated_at: "2026-10-17T10:00:00", updated_by: "ana" };

function page(state: object | (() => object), routes: Record<string, RouteHandler> = {}) {
  return renderSettings("/build", "Build commands", RELEASE_APP, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/build`]: () => json(200, BUILD),
    [`GET /api/apps/${DOMAIN}/packages`]: () => json(200, typeof state === "function" ? state() : state),
    ...routes,
  });
}

const card = () => part("System packages");

describe("the system packages an application needs", () => {
  it("says when it declares none, and that a repository cannot ask for any", { timeout: 40_000 }, async () => {
    const { container } = await page(PACKAGES_NONE);
    await screen.findByRole("heading", { name: "System packages" });
    const section = within(card());

    expect(await section.findByText("This application declares no system packages.")).toBeInTheDocument();
    expect(section.getByText(/a repository cannot ask for one/)).toBeInTheDocument();
    expect(section.getByRole("button", { name: "Save the list" })).toBeDisabled();
    expect(section.getByRole("button", { name: "Install what is missing" })).toBeDisabled();
    expect(section.getByText("noust app packages show shop.example.com")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("shows each package and whether the host has it", { timeout: 40_000 }, async () => {
    await page(DECLARED);
    await screen.findByRole("heading", { name: "System packages" });
    const list = within(await within(card()).findByRole("list", { name: "Packages this application needs" }));

    expect(within(list.getByText("ffmpeg").closest("li") as HTMLElement).getByText("Installed")).toBeInTheDocument();
    expect(within(list.getByText("libvips-dev").closest("li") as HTMLElement).getByText("Not installed yet")).toBeInTheDocument();
    expect(within(card()).getByRole("textbox", { name: /^Packages/ })).toHaveValue("libvips-dev\nffmpeg");
    expect(within(card()).getByRole("button", { name: "Install what is missing" })).toBeEnabled();
  });

  it("records the list as names, one per line, and nothing else", { timeout: 60_000 }, async () => {
    let state: object = PACKAGES_NONE;
    const { user, backend } = await page(() => state, {
      [`PUT /api/apps/${DOMAIN}/packages`]: () => {
        state = { ...DECLARED, installed: [], missing: ["libvips-dev", "ffmpeg"] };
        return json(200, state);
      },
    });
    await screen.findByRole("heading", { name: "System packages" });
    const section = within(card());

    await user.type(await section.findByRole("textbox", { name: /^Packages/ }), "libvips-dev{enter}ffmpeg libvips-dev");
    await user.click(section.getByRole("button", { name: "Save the list" }));

    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/packages`)).toHaveLength(1));
    // Repeats once, whatever separated them; nothing is installed by saving.
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/packages`)[0]?.body).toEqual({ packages: ["libvips-dev", "ffmpeg"] });
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/packages/install`)).toHaveLength(0);
    expect(await within(card()).findAllByText("Not installed yet", {}, { timeout: 10_000 })).toHaveLength(2);
  });

  it("puts a refusal that names the packages field under it, and records nothing", { timeout: 60_000 }, async () => {
    const { user } = await page(PACKAGES_NONE, {
      [`PUT /api/apps/${DOMAIN}/packages`]: () => problem(400, "validationerror", "'-y' is not a package name", { fields: { packages: "'-y' is not a package name" } }),
    });
    await screen.findByRole("heading", { name: "System packages" });
    const section = within(card());

    await user.type(await section.findByRole("textbox", { name: /^Packages/ }), "-y");
    await user.click(section.getByRole("button", { name: "Save the list" }));

    expect(await section.findByText("'-y' is not a package name")).toBeInTheDocument();
    expect(section.getByRole("button", { name: "Save the list" })).toBeEnabled();
  });

  it("queues the installation and reads the host again when the job ends", { timeout: 60_000 }, async () => {
    let state: object = DECLARED;
    const job = jobOf("pk1", "custom", "running", { metadata: { domain: DOMAIN } });
    const { user, backend } = await page(() => state, {
      [`POST /api/apps/${DOMAIN}/packages/install`]: () => json(202, accepted(job)),
      "GET /api/jobs/pk1": () => json(200, job),
    });
    await screen.findByRole("heading", { name: "System packages" });

    await user.click(await within(card()).findByRole("button", { name: "Install what is missing" }));

    await waitFor(() => expect(backend.callsTo(`POST /api/apps/${DOMAIN}/packages/install`)).toHaveLength(1));
    state = { ...DECLARED, installed: ["ffmpeg", "libvips-dev"], missing: [] };
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...job, status: "completed", progress: 100, result: { installed: ["libvips-dev"] } });
    });
    await waitFor(() => expect(within(card()).getAllByText("Installed")).toHaveLength(2), { timeout: 20_000 });
  });

  it("shows why a package cannot be installed, in the job's words", { timeout: 60_000 }, async () => {
    const running = jobOf("pk2", "custom", "running", { metadata: { domain: DOMAIN } });
    const { user } = await page(DECLARED, {
      [`POST /api/apps/${DOMAIN}/packages/install`]: () => json(202, accepted(running)),
      "GET /api/jobs/pk2": () => json(200, running),
    });
    await screen.findByRole("heading", { name: "System packages" });

    await user.click(await within(card()).findByRole("button", { name: "Install what is missing" }));
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...running, status: "failed", error: "Could not install the system packages shop.example.com needs: nonexistent-lib" });
    });

    expect(await within(card()).findByText(/Could not install the system packages shop\.example\.com needs: nonexistent-lib/, {}, { timeout: 20_000 })).toBeInTheDocument();
    expect(within(card()).getByText("The system packages of shop.example.com were not installed")).toBeInTheDocument();
  });

  it("names the policy, and the packages the server no longer allows", { timeout: 40_000 }, async () => {
    await page({ ...DECLARED, policy: ["libvips*", "fonts-*"], refused: ["ffmpeg"] });
    await screen.findByRole("heading", { name: "System packages" });
    const section = within(card());

    expect(await section.findByText("This server allows: libvips*, fonts-*.", { exact: false })).toBeInTheDocument();
    expect(section.getByText("The server no longer allows these packages")).toBeInTheDocument();
    expect(section.getByText(/ffmpeg fall outside the packages this server lets applications declare/)).toBeInTheDocument();
    expect(section.getByRole("button", { name: "Install what is missing" })).toBeDisabled();
  });

  it("says a server whose packages Noust does not manage keeps the list but installs nothing", { timeout: 40_000 }, async () => {
    await page({ ...DECLARED, supported: false, installed: [], missing: ["libvips-dev", "ffmpeg"], manager: "dnf" });
    await screen.findByRole("heading", { name: "System packages" });
    const section = within(card());

    expect(await section.findByText("This server's packages are not managed by Noust")).toBeInTheDocument();
    expect(section.getAllByText("Not checked")).toHaveLength(2);
    expect(section.getByRole("button", { name: "Install what is missing" })).toBeDisabled();
  });

  it("speaks Spanish", { timeout: 40_000 }, async () => {
    await page(DECLARED);
    await screen.findByRole("heading", { name: "System packages" });
    await act(() => setLocale("es"));

    expect(await screen.findByRole("heading", { name: "Paquetes del sistema" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Instalar lo que falta" })).toBeInTheDocument();
    expect(screen.getByText("Aún sin instalar")).toBeInTheDocument();
  });
});
