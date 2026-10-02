import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { FakeEventSource, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, accepted, jobOf, part, renderSettings } from "./testKit";

const LEGACY = {
  domain: DOMAIN,
  mode: "legacy",
  enabled: false,
  network: "full",
  pty: false,
  reason: null,
  changed_by: null,
  changed_at: null,
  trial: null,
  warning: `${DOMAIN} still builds as root, as applications created before 3.1 do.`,
  compose_exception: null,
};

const PASSED = { tested_at: "2026-09-29T09:00:00+00:00", commit: "2a8b7c4f00", passed: true, detail: null };

const ON = { ...LEGACY, mode: "on", enabled: true, warning: null, changed_by: "master", changed_at: "2026-09-29T09:05:00+00:00", trial: PASSED };

const TEST_JOB = jobOf("s1", "sandbox_test");

function builds(state: () => object, routes: Record<string, RouteHandler> = {}, app: object = RELEASE_APP) {
  return renderSettings("/builds", "Builds", app, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/sandbox`]: () => json(200, state()),
    [`GET /api/jobs/${TEST_JOB.id}`]: () => json(200, TEST_JOB),
    ...routes,
  });
}

describe("sandboxed builds", () => {
  it("says what it protects, that this app still builds as root, and tests a build as a job before turning it on", async () => {
    let state: object = LEGACY;
    const { user, backend } = await builds(() => state, {
      [`POST /api/apps/${DOMAIN}/sandbox/test`]: () => json(202, accepted(TEST_JOB)),
      [`POST /api/apps/${DOMAIN}/sandbox/enable`]: () => json(200, ON),
    });
    expect(screen.getByText(/as an account that cannot read this server's secrets or change the system/)).toBeInTheDocument();
    await screen.findByText("Its builds still run as root");
    const card = part("Sandboxed builds");
    // Off, at a glance: the grey state with a switch drawn off, not a notice to read.
    expect(card.querySelector("[data-feature-state]")).toHaveAttribute("data-state", "off");
    expect(within(card).getByText("No test build yet.")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Turn on sandboxed builds" })).not.toBeInTheDocument();
    expect(within(card).getByText("It can be turned on once a test build passes.")).toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "Test a sandboxed build" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/sandbox/test`)).toHaveLength(1);
    });
    expect(await within(card).findByText(/Building the current commit in the sandbox/)).toBeInTheDocument();

    state = { ...LEGACY, trial: PASSED };
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...TEST_JOB, status: "completed", progress: 100, result: { domain: DOMAIN, passed: true, commit: PASSED.commit, detail: null } });
    });
    expect(await within(card).findByText(/The test build passed\. Turn on sandboxed builds/)).toBeInTheDocument();
    expect(await within(card).findByText(/The last test build passed/)).toHaveTextContent("2a8b7c4");

    await user.click(within(card).getByRole("button", { name: "Turn on sandboxed builds" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/sandbox/enable`)[0]?.body).toEqual({ force: false });
    });
    expect(await within(card).findByText("On")).toBeInTheDocument();
    expect(within(card).getByText("noust-build")).toBeInTheDocument();
  });

  it("shows a failed test build's own output", async () => {
    const output = "npm ERR! code EACCES\nnpm ERR! path /root/.npm";
    await builds(() => ({ ...LEGACY, trial: { ...PASSED, passed: false, detail: output } }));
    await screen.findByText("The last test build failed");
    const card = part("Sandboxed builds");
    expect(within(card).getByText(/npm ERR! code EACCES/)).toBeInTheDocument();
    expect(within(card).getByText("Fix what the build needs, or keep building as root and say why.")).toBeInTheDocument();
  });

  it("builds as root only with a reason, recorded", async () => {
    let state: object = ON;
    const { user, backend } = await builds(() => state, {
      [`POST /api/apps/${DOMAIN}/sandbox/disable`]: () => {
        state = { ...ON, mode: "off", enabled: false, reason: "Needs Docker to build its images", changed_by: "master" };
        return json(200, state);
      },
    });
    await screen.findByRole("button", { name: "Build as root…" });
    const card = part("Sandboxed builds");
    await user.click(within(card).getByRole("button", { name: "Build as root…" }));
    const dialog = await screen.findByRole("dialog", { name: `Build ${DOMAIN} as root?` });
    await user.click(within(dialog).getByRole("button", { name: "Build as root" }));
    expect(await within(dialog).findByText("Say why this app cannot build in the sandbox.")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/sandbox/disable`)).toHaveLength(0);

    await user.type(within(dialog).getByRole("textbox", { name: "Why" }), "Needs Docker to build its images");
    await user.click(within(dialog).getByRole("button", { name: "Build as root" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/sandbox/disable`)[0]?.body).toEqual({ reason: "Needs Docker to build its images" });
    });
    expect(await within(card).findByText("Its builds run as root, by decision of master")).toBeInTheDocument();
    expect(within(card).getByText("Needs Docker to build its images")).toBeInTheDocument();
  });

  it("reads a monorepo's regime from the sandbox like any app's, and says when it already builds there", async () => {
    const { backend } = await builds(() => ON, {}, { ...RELEASE_APP, app_type: "monorepo" });
    await screen.findByText("noust-build");
    const card = part("Sandboxed builds");
    expect(within(card).getByText("On")).toBeInTheDocument();
    expect(screen.queryByText("This monorepo still builds as root")).not.toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/sandbox`).length).toBeGreaterThan(0);
  });

  it("lets a compose stack be allowed what is root on the server only with a reason, and stops allowing it", async () => {
    let state: object = { ...LEGACY, warning: null };
    const compose = { ...RELEASE_APP, app_type: "docker-compose" };
    const { user, backend } = await builds(() => state, {
      [`PUT /api/apps/${DOMAIN}/sandbox/compose-exception`]: () => {
        state = { ...LEGACY, warning: null, compose_exception: { reason: "Runs a CI runner", allowed_by: "master", allowed_at: "2026-09-29T09:00:00+00:00" } };
        return json(200, state);
      },
      [`DELETE /api/apps/${DOMAIN}/sandbox/compose-exception`]: () => json(200, { ...LEGACY, warning: null }),
    }, compose);
    await screen.findByText("This stack is refused both.");
    const card = part("Containers that are root on the server");
    await user.click(within(card).getByRole("button", { name: "Allow it…" }));
    const dialog = await screen.findByRole("dialog", { name: `Allow ${DOMAIN} privileged containers and the Docker socket?` });
    await user.type(within(dialog).getByRole("textbox", { name: "Why" }), "Runs a CI runner");
    await user.click(within(dialog).getByRole("button", { name: "Allow" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/sandbox/compose-exception`)[0]?.body).toEqual({ reason: "Runs a CI runner" });
    });
    expect(await within(card).findByText("Runs a CI runner")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Stop allowing it" }));
    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/sandbox/compose-exception`)).toHaveLength(1);
    });
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    await builds(() => ({ ...LEGACY, trial: PASSED }));
    await screen.findByText("Its builds still run as root");
    await expectNoAxeViolations(screen.getByRole("main"));
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { level: 2, name: "Compilaciones" })).toBeInTheDocument();
    expect(screen.getByText("Sus compilaciones todavía se ejecutan como root")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activar las compilaciones aisladas" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
