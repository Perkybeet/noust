import { describe, expect, it } from "vitest";

import { NAS_ON } from "../../../test/nas";
import type { TableKey } from "../../../test/nas";
import { CAN, CANNOT, GROUP_COMMAND, NOT_SHOWN, SOCKET_GROUP, SOCKET_PATH, composeLines, composeText, stateOf } from "./deployHere";

describe("the lines that turn it on", () => {
  it("add Docker's socket, the group that opens it and the setting that says why, each under the key it goes under", () => {
    expect(composeText()).toBe(
      [
        "services:",
        "  noust:",
        "    group_add:",
        `      - "${SOCKET_GROUP}"`,
        "    volumes:",
        `      - ${SOCKET_PATH}:${SOCKET_PATH}`,
        "    environment:",
        "      NOUST_DEPLOY_HERE: containers",
      ].join("\n"),
    );
  });

  it("mark as added only the lines that are, and leave the keys they go under as context", () => {
    const added = composeLines().filter((line) => line.added).map((line) => line.text.trim());
    expect(added).toEqual(["group_add:", `- "${SOCKET_GROUP}"`, `- ${SOCKET_PATH}:${SOCKET_PATH}`, "NOUST_DEPLOY_HERE: containers"]);
    expect(composeLines().filter((line) => !line.added).map((line) => line.text.trim())).toEqual(["services:", "noust:", "volumes:", "environment:"]);
  });

  it("take the group number the person found, and say how to find it", () => {
    expect(composeText("101")).toContain('- "101"');
    expect(GROUP_COMMAND).toBe("stat -c %g /var/run/docker.sock");
  });
});

describe("what Noust does on a machine that has Docker, tied to the server's table", () => {
  const keys = Object.keys(NAS_ON) as TableKey[];
  const said = (list: typeof CAN): Set<string> => new Set(list.flatMap((item) => item.keys));

  it("says everything the table says works there but what the console has no page for, and nothing it says does not", () => {
    for (const key of keys.filter((candidate) => NAS_ON[candidate] && !NOT_SHOWN.includes(candidate))) expect(said(CAN), `${key} works there and is not said`).toContain(key);
    for (const key of said(CAN)) expect(NAS_ON[key as TableKey], `${key} is promised and the table refuses it`).toBe(true);
  });

  it("says everything the table refuses there, and nothing it allows", () => {
    for (const key of keys.filter((candidate) => !NAS_ON[candidate])) expect(said(CANNOT), `${key} is refused there and is not said`).toContain(key);
    for (const key of said(CANNOT)) expect(NAS_ON[key as TableKey], `${key} is said to be refused and the table allows it`).toBe(false);
  });

  it("says each function once, and leaves unsaid only what it names as not shown, which the table does say works", () => {
    const all = [...CAN, ...CANNOT].flatMap((item) => item.keys);
    expect(new Set(all).size).toBe(all.length);
    for (const key of NOT_SHOWN) {
      expect(all, `${key} is said and is also not shown`).not.toContain(key);
      expect(NAS_ON[key], `${key} is not shown and the table does not say it works`).toBe(true);
    }
  });
});

describe("where the page stands", () => {
  const ready = { ready: true };
  const failed = { ready: false };

  it("is off while the central was not told and nothing was checked", () => {
    expect(stateOf(false, null, false)).toBe("off");
  });

  it("is checking while the check runs, and when the central was told and has not answered yet", () => {
    expect(stateOf(false, null, true)).toBe("checking");
    expect(stateOf(true, null, false)).toBe("checking");
  });

  it("is on when it was told and the check found nothing wrong", () => {
    expect(stateOf(true, ready, false)).toBe("on");
  });

  it("is a problem as soon as the check says it is not ready, told or not: the machine's own word", () => {
    expect(stateOf(true, failed, false)).toBe("problem");
    expect(stateOf(false, failed, false)).toBe("problem");
  });

  it("is off when the check is fine and the central was not told: only the compose file sets the setting", () => {
    expect(stateOf(false, ready, false)).toBe("off");
  });
});
