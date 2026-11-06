import { describe, expect, it } from "vitest";

import { bindT } from "../../../i18n/useT";
import type { Fail2ban } from "../queries";
import { blocksOf, jailName, matchBlocks } from "./BansView";

const t = bindT("en");

type Jail = Fail2ban["jails"][number];

function jail(name: string, banned: string[]): Jail {
  return { name, banned, currently_banned: banned.length, currently_failed: 0, total_banned: banned.length, total_failed: 0, reads: "systemd" };
}

describe("the blocks of fail2ban, as a list of addresses", () => {
  it("says what a jail watches in the words of the thing it protects, and keeps the name of one it has no word for", () => {
    expect(jailName(t, "sshd")).toBe("SSH access");
    expect(jailName(t, "nginx-http-auth")).toBe("Web server");
    expect(jailName(t, "nginx-botsearch")).toBe("Web server");
    expect(jailName(t, "recidive")).toBe("recidive");
  });

  it("lists every address blocked now with the jail that blocked it, once per jail", () => {
    const blocks = blocksOf([jail("sshd", ["203.0.113.45", "192.0.2.77"]), jail("nginx-botsearch", ["203.0.113.45"]), jail("recidive", [])]);
    expect(blocks).toEqual([
      { address: "203.0.113.45", jail: "sshd" },
      { address: "192.0.2.77", jail: "sshd" },
      { address: "203.0.113.45", jail: "nginx-botsearch" },
    ]);
  });

  it("finds an address by a part of it, and by the name of what blocked it", () => {
    const blocks = blocksOf([jail("sshd", ["203.0.113.45", "192.0.2.77"]), jail("nginx-botsearch", ["198.51.100.23"])]);
    expect(matchBlocks(blocks, "", t)).toHaveLength(3);
    expect(matchBlocks(blocks, "  ", t)).toHaveLength(3);
    expect(matchBlocks(blocks, "113.45", t).map((block) => block.address)).toEqual(["203.0.113.45"]);
    expect(matchBlocks(blocks, "web", t).map((block) => block.address)).toEqual(["198.51.100.23"]);
    expect(matchBlocks(blocks, "ssh", t)).toHaveLength(2);
    expect(matchBlocks(blocks, "10.0.0.1", t)).toEqual([]);
  });
});
