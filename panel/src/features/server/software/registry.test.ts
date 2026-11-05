import { describe, expect, it } from "vitest";

import { SERVER_TEXT } from "../../../api/serverTextCodes.gen";
import { KNOWN, comesFromEpel, dependentPage, groupOf, installPermissions, isKnown, nameKey, removePermissions, whyKey } from "./registry";

// What the server asks of each permission is held by tests/test_console_software_registry.py, which reads this
// module beside the server's own table; here is what the console says with them.

describe("the components the console knows", () => {
  it("are the ones the server's registry says what they are for", () => {
    // The generated list of the server's sentences has one `software.<id>.purpose` per component of the registry.
    const registered = Object.keys(SERVER_TEXT)
      .map((code) => /^software\.(\w+)\.purpose$/.exec(code)?.[1])
      .filter((id): id is string => id !== undefined)
      .sort();
    expect([...KNOWN].sort()).toEqual(registered);
  });

  it("have a name, a group and, for the ones a page of the console is built on, what is lost without them", () => {
    for (const id of KNOWN) {
      expect(nameKey(id), id).not.toBeNull();
      expect(groupOf(id), id).not.toBe("other");
    }
    expect(whyKey("nginx_stream_module")).not.toBeNull();
    expect(whyKey("cloudflared")).not.toBeNull();
    expect(whyKey("git")).toBeNull();
  });

  it("list one that is not known as Other, by the id the server gave", () => {
    expect(isKnown("brand_new")).toBe(false);
    expect(groupOf("brand_new")).toBe("other");
    expect(nameKey("brand_new")).toBeNull();
  });

  it("have a reason from the server for every one that is never removed", () => {
    const never = Object.keys(SERVER_TEXT).filter((code) => /^software\.(?!refused\.)\w+\.never$/.test(code));
    expect(never.length).toBeGreaterThan(0);
    for (const code of never) expect(isKnown(code.split(".")[1] ?? ""), code).toBe(true);
  });
});

describe("what installing and removing take", () => {
  it("is the host's access, besides managing, for what no function of the console installed before", () => {
    expect(installPermissions("git")).toEqual(["server.manage", "server.host_access"]);
    expect(installPermissions("cloudflared")).toEqual(["server.manage"]);
    expect(installPermissions("postgresql")).toEqual(["server.manage", "databases.manage"]);
  });

  it("is the databases' permission as well, to remove an engine", () => {
    expect(removePermissions("fail2ban")).toEqual(["server.host_access"]);
    expect(removePermissions("postgresql")).toEqual(["server.host_access", "databases.manage"]);
  });

  it("asks for EPEL only for what comes from it on a RHEL rebuild", () => {
    expect(comesFromEpel("fail2ban", "dnf")).toBe(true);
    expect(comesFromEpel("fail2ban", "apt")).toBe(false);
    expect(comesFromEpel("nginx", "dnf")).toBe(false);
  });
});

describe("where what depends on a component is looked after", () => {
  it("has a page for each kind of thing that can depend, and none for the console itself", () => {
    expect(dependentPage("tunnel")).toBe("/network/tunnels");
    expect(dependentPage("site")).toBe("/domains/sites");
    expect(dependentPage("destination")).toBe("/backups/destinations");
    expect(dependentPage("console")).toBeNull();
    expect(dependentPage("unreadable")).toBeNull();
  });
});
