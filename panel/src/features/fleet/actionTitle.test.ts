import { describe, expect, it } from "vitest";

import { actionTitle } from "./data";

/** A translator that says which key it was asked for, so a test sees whose words were used. */
const say = (key: string): string => `<${key}>`;

describe("what a fleet job is called", () => {
  it("uses the console's words for an action it knows", () => {
    expect(actionTitle(say, "certs_renew", "Renew certificates")).toBe("<fleet.actions.certs_renew.label>");
  });

  it("uses the console's words for applying the fleet's settings, which the server titles in English", () => {
    expect(actionTitle(say, "settings_apply", "Apply the fleet's settings")).toBe("<fleetSettings.applyDialog.title>");
  });

  it("keeps the server's title for an action nobody here has words for", () => {
    expect(actionTitle(say, "something_new", "Something new")).toBe("Something new");
  });
});
