/**
 * Pressing Continue after a repository was typed, however long the press lasts. Leaving the field
 * (pressing the button is what leaves it) used to start the read of the repository by itself 80ms
 * later, and a press of 90ms is the ordinary one: the read put its block above the button, the button
 * moved from under the finger and the release landed on something else, so the press was lost. The
 * press is the ask: while it is down, leaving the field reads nothing, and the click reads and goes
 * on. A read the field starts when it is left some other way is joined by a press that comes while it
 * runs, and never started twice.
 */

import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import type { Inspection } from "./wizard";

const INSPECTION: Inspection = {
  app_type: "nextjs",
  detected_types: ["nextjs"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: ["npm", "run", "build"],
  start_command: "npm run start",
  default_port: 3000,
  env_keys: [{ name: "DATABASE_URL", default: null, secret: false, required: true }],
  branch: "main",
  commit: "a1b2c3d",
  compatible: true,
};

const APP_TYPES = {
  types: [
    { type: "nextjs", name: "Next.js", default_port: 3000 },
    { type: "nodejs", name: "Node.js", default_port: 3000 },
  ],
};

const REPOSITORY = "https://github.com/acme/api.git";

/** A read the test answers when it says so, as a clone that takes its time. */
function slowRead() {
  let answer: (() => void) | null = null;
  const handler: RouteHandler = () =>
    new Promise<Response>((resolve) => {
      answer = () => {
        resolve(json(200, INSPECTION));
      };
    });
  return {
    handler,
    answer: () => {
      if (answer === null) throw new Error("the read was never asked");
      answer();
    },
  };
}

function wizard(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/apps": () => json(200, { total: 0, apps: [] }),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/temporary": () => json(200, { domain: "api.203-0-113-10.sslip.io", address: "203.0.113.10" }),
    "POST /api/apps/inspect": () => json(200, INSPECTION),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

const continueButton = (): HTMLElement => screen.getByRole("button", { name: "Continue" });

describe("Continue after a typed repository", () => {
  it("reads nothing while the press is down, however long it is held, and goes on when it lifts", { timeout: 30_000 }, async () => {
    const read = slowRead();
    const { backend, harness } = wizard({ "POST /api/apps/inspect": read.handler });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("What to deploy"), REPOSITORY);

    // The press starts: the button takes the focus, so the field is left.
    await user.pointer({ keys: "[MouseLeft>]", target: continueButton() });
    // An absence is proved after the window in which it would have happened: the field settles 80ms after it is left.
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(0);
    expect(screen.queryByText("Loading the repository…")).not.toBeInTheDocument();

    // The press ends on the button, which has not moved: Continue reads, and goes on when it has.
    await user.pointer({ keys: "[/MouseLeft]" });
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1);
    });
    expect(continueButton()).toHaveAttribute("aria-busy", "true");
    read.answer();

    expect(await screen.findByRole("heading", { level: 2, name: "Variables" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1);
  });

  it("joins the read the field started when it was left another way, and does not start a second one", { timeout: 30_000 }, async () => {
    const read = slowRead();
    const { backend, harness } = wizard({ "POST /api/apps/inspect": read.handler });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("What to deploy"), REPOSITORY);

    // Leaving the field for anything but the button: it reads by itself, in the open.
    await user.click(screen.getByRole("heading", { level: 1, name: "New application" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1);
    });
    await screen.findByText("Loading the repository…");
    // Nothing Continue asked for is under way, so it is a button that can still be pressed.
    expect(continueButton()).not.toHaveAttribute("aria-busy");

    await user.click(continueButton());
    expect(continueButton()).toHaveAttribute("aria-busy", "true");
    read.answer();

    expect(await screen.findByRole("heading", { level: 2, name: "Variables" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1);
  });

  it("goes on at once when what the field read is already on screen", { timeout: 30_000 }, async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("What to deploy"), REPOSITORY);
    await user.click(screen.getByRole("heading", { level: 1, name: "New application" }));
    await screen.findByText(/Detected: Next\.js/);

    await user.pointer({ keys: "[MouseLeft>]", target: continueButton() });
    await user.pointer({ keys: "[/MouseLeft]" });

    expect(await screen.findByRole("heading", { level: 2, name: "Variables" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1);
  });
});
