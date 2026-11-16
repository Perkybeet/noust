import { describe, expect, it } from "vitest";

import { FIRST_MACHINE_PORT, machineUrl, nameProblem, stackPort } from "./machineAddress";
import { initialReview, proposedPort } from "./wizard";
import type { Inspection } from "./wizard";

const NONE: ReadonlySet<string> = new Set();

describe("the name of an application where there is no domain", () => {
  it("takes a name with no dot and no top-level domain, which a domain would not be", () => {
    expect(nameProblem("vault", NONE)).toBeNull();
    expect(nameProblem("vault.casa.example.org", NONE)).toBeNull();
    expect(nameProblem("  Vault-2  ", NONE)).toBeNull();
  });

  it("asks for one when there is none, and says what a name is made of when it is not", () => {
    expect(nameProblem("   ", NONE)).toBe("Give the application a name.");
    expect(nameProblem("my app", NONE)).toMatch(/lowercase letters, digits, dots and dashes/);
    expect(nameProblem("-vault", NONE)).toMatch(/starting and ending with a letter or a digit/);
    expect(nameProblem("https://vault", NONE)).toMatch(/lowercase letters/);
  });

  it("refuses a name another application has, as a domain is refused", () => {
    expect(nameProblem("Vault", new Set(["vault"]))).toMatch(/already/i);
  });
});

describe("where it opens", () => {
  it("writes the address a port makes on the machine the console was opened at", () => {
    expect(machineUrl("nas-casa", "8083")).toBe("http://nas-casa:8083");
    expect(machineUrl("192.168.1.160", " 8083 ")).toBe("http://192.168.1.160:8083");
  });

  it("reads a Compose stack's port from the document, which Noust does not rewrite", () => {
    expect(stackPort({ default_port: 8082 })).toBe("8082");
    expect(stackPort(null)).toBeNull();
  });

  it("proposes the first port from 8081 that no application of Noust has", () => {
    const inspection = { app_type: "container", detected_types: ["container"], default_port: 3000, env_keys: [], branch: "main", commit: "abc1234", install_command: [], build_command: [], start_command: "", package_manager: null } as Inspection;
    const taken = new Map([
      [8081, "a"],
      [8082, "b"],
    ]);

    expect(initialReview(inspection, { webserver: "nginx", taken, containerHost: true }).port).toBe(String(proposedPort(FIRST_MACHINE_PORT, taken)));
    expect(initialReview(inspection, { webserver: "nginx", taken, containerHost: true }).port).toBe("8083");
    // A server is proposed what it always was: the type's own.
    expect(initialReview(inspection, { webserver: "nginx", taken }).port).toBe("3000");
  });
});
