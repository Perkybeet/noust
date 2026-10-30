import { describe, expect, it } from "vitest";

import { LEVELS, accessCommand, isLevel } from "./data";

describe("the command that sets what the central may do", () => {
  // tests/test_cli_fleet.py runs these exact strings through the command line: change both together.
  it("says what will be true after it, host access included", () => {
    expect(accessCommand("read", false)).toBe("noust fleet access --level read --host-access off");
    expect(accessCommand("deploy", false)).toBe("noust fleet access --level deploy --host-access off");
    expect(accessCommand("admin", false)).toBe("noust fleet access --level admin --host-access off");
    expect(accessCommand("admin", true)).toBe("noust fleet access --level admin --host-access on");
  });

  it("never turns host access on below the level that allows everything", () => {
    expect(accessCommand("read", true)).toContain("--host-access off");
    expect(accessCommand("deploy", true)).toContain("--host-access off");
  });

  it("knows the three levels and nothing else a server could publish", () => {
    expect(LEVELS).toEqual(["read", "deploy", "admin"]);
    expect(isLevel("deploy")).toBe(true);
    expect(isLevel("superuser")).toBe(false);
  });
});
