/**
 * Pressing Continue after a repository was typed, however long the press lasts. Leaving the field
 * (pressing the button is what leaves it) starts the read of the repository by itself, and a press
 * of 90ms is the ordinary one: the read starts while the button is still down. The press must go on
 * with that read, not be lost to a button that turned busy under the finger.
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
  it("goes on with the read the field's own blur started, when it is pressed while that read runs", { timeout: 30_000 }, async () => {
    const read = slowRead();
    const { backend, harness } = wizard({ "POST /api/apps/inspect": read.handler });
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("What to deploy"), REPOSITORY);

    // The press starts: the button takes the focus, so the field is left and its read begins.
    await user.pointer({ keys: "[MouseLeft>]", target: continueButton() });
    await waitFor(() => {
      expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1);
    });
    await screen.findByText("Loading the repository…");
    // Nothing Continue asked for is under way, so it is a button that can still be pressed.
    expect(continueButton()).not.toHaveAttribute("aria-busy");

    // The press ends with the read still going: it goes on with it, it does not start another.
    await user.pointer({ keys: "[/MouseLeft]" });
    expect(continueButton()).toHaveAttribute("aria-busy", "true");
    read.answer();

    expect(await screen.findByRole("heading", { level: 2, name: "Variables" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1);
  });

  it("goes on at once when the read ended while the button was down", { timeout: 30_000 }, async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("What to deploy"), REPOSITORY);

    await user.pointer({ keys: "[MouseLeft>]", target: continueButton() });
    // What the read found is on screen before the finger lifts.
    await screen.findByText(/Detected: Next\.js/);
    await user.pointer({ keys: "[/MouseLeft]" });

    expect(await screen.findByRole("heading", { level: 2, name: "Variables" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1);
  });
});
