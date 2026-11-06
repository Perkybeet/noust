import { describe, expect, it } from "vitest";

import { serverSection } from "./sections";

describe("what the Server layout says above a section", () => {
  it("names Status after the machine, so it has no title of its own and no line of facts", () => {
    expect(serverSection("/server")).toEqual({ title: null, facts: false });
    expect(serverSection("/server/")).toEqual({ title: null, facts: false });
  });

  it("titles every other section with the register's own name, followed by the machine's facts", () => {
    expect(serverSection("/server/machine")).toMatchObject({ title: "server.system.views.machine", facts: true });
    expect(serverSection("/server/updates")).toMatchObject({ title: "server.system.views.updates", facts: true });
    expect(serverSection("/server/processes")).toMatchObject({ title: "server.system.views.processes", facts: true });
    expect(serverSection("/server/files")).toMatchObject({ title: "server.tabs.files", facts: true });
    expect(serverSection("/server/software")).toMatchObject({ title: "server.tabs.software", facts: true });
    expect(serverSection("/server/central")).toMatchObject({ title: "server.tabs.central", facts: true });
  });

  it("gives Terminal its own name though it is a screen reached from elsewhere", () => {
    expect(serverSection("/server/terminal")).toMatchObject({ title: "terminal.page.title" });
  });

  it("puts a sentence under Services, which is what that list is read for, and no facts line next to it", () => {
    expect(serverSection("/server/services")).toEqual({ title: "server.tabs.services", description: "server.sections.services", facts: false });
  });
});
