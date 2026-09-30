import { describe, expect, it } from "vitest";

import { bindT } from "../../../i18n/useT";
import { CHECKS } from "../testing";
import { checkState, checkTitle, isOpen, sortChecks, sshFixOf, typedFriction, validateSecuritySearch } from "./data";

const t = bindT("en");

describe("the Security tab's data", () => {
  it("keeps the view in the URL, the checks being no parameter", () => {
    expect(validateSecuritySearch({ view: "firewall" })).toEqual({ view: "firewall" });
    expect(validateSecuritySearch({ view: "checks" })).toEqual({});
    expect(validateSecuritySearch({ view: "shell" })).toEqual({});
  });

  it("names a check by its id, and keeps the API's words for one it does not know", () => {
    expect(checkTitle(t, { id: "ssh.password_auth", title: "whatever" })).toBe("SSH accepts passwords");
    expect(checkTitle(t, { id: "new.check", title: "A check from a newer Noust" })).toBe("A check from a newer Noust");
  });

  it("says a finding's state by its severity, and the rest in words", () => {
    expect(checkState(t, { status: "fail", severity: "critical" })).toEqual({ state: "failed", label: "Critical" });
    expect(checkState(t, { status: "warn", severity: "warning" })).toEqual({ state: "warning", label: "Warning" });
    expect(checkState(t, { status: "pass", severity: "critical" })).toEqual({ state: "running", label: "Passed" });
    expect(checkState(t, { status: "accepted", severity: "critical" })).toEqual({ state: "stopped", label: "Accepted" });
    expect(checkState(t, { status: "unknown", severity: "info" })).toEqual({ state: "unknown", label: "Unknown" });
  });

  it("puts open findings first, the worst first", () => {
    expect(sortChecks(CHECKS.checks).map((check) => check.id)).toEqual(["fw.docker_bypass", "upd.security_pending", "ssh.password_auth", "os.eol", "ssh.empty_passwords"]);
    expect(CHECKS.checks.filter(isOpen)).toHaveLength(3);
  });

  it("asks for the host name before a fix that can lock people out", () => {
    expect(sshFixOf("ssh:disable-passwords")).toBe("disable-passwords");
    expect(sshFixOf("firewall:enable")).toBeNull();
    expect(typedFriction("ssh:disable-passwords")).toBe(true);
    expect(typedFriction("firewall:enable")).toBe(true);
    expect(typedFriction("ssh:verbose-logging")).toBe(false);
  });
});
