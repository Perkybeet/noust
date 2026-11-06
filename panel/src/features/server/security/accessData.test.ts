import { describe, expect, it } from "vitest";

import { accessRows, consoleRows, sshRows } from "./accessData";
import type { Recording } from "./accessData";

const SSH = {
  sessions: [{ peer: "203.0.113.7", port: 51234, user: "root", fingerprint: "SHA256:aa" }],
  logins: {
    source: "journal",
    error: "",
    days: 30,
    recent: [
      { at: 1_000, user: "root", method: "publickey", source: "203.0.113.7", fingerprint: "SHA256:aa", line: "Accepted publickey for root" },
      { at: 2_000, user: "deploy", method: "password", source: "198.51.100.9", fingerprint: null, line: "Accepted password for deploy" },
    ],
  },
};

function recording(fields: Partial<Recording>): Recording {
  return { id: "r1", status: "complete", actor: "yago", account: "yago", client_ip: "83.45.12.7", started_at: 1_500, ...fields };
}

describe("who has entered the machine", () => {
  it("lists the SSH sessions that are open as people who are there now, and the logins of the journal after them", () => {
    const rows = sshRows(SSH);
    expect(rows.map((row) => [row.who, row.live, row.method])).toEqual([
      ["root", true, null],
      ["root", false, "publickey"],
      ["deploy", false, "password"],
    ]);
    expect(rows.every((row) => row.way === "ssh" && row.recording === null)).toBe(true);
  });

  it("offers the recording of a terminal session that ended, and none for one that is still being recorded", () => {
    const rows = consoleRows([recording({ id: "done" }), recording({ id: "now", status: "recording" }), recording({ id: "cut", status: "interrupted" })]);
    expect(rows.map((row) => [row.key, row.live, row.recording])).toEqual([
      ["console:done", false, "done"],
      ["console:now", true, null],
      ["console:cut", false, "cut"],
    ]);
  });

  it("puts who is there first, then the newest, whichever way they came", () => {
    const rows = accessRows(SSH, [recording({ id: "mid", started_at: 1_500 })]);
    expect(rows.map((row) => row.key)).toEqual(["ssh-live:203.0.113.7:51234", expect.stringContaining("ssh:2000"), "console:mid", expect.stringContaining("ssh:1000")]);
  });

  it("still lists what it can read when one side could not be", () => {
    expect(accessRows(undefined, [recording({})])).toHaveLength(1);
    expect(accessRows(SSH, undefined)).toHaveLength(3);
    expect(accessRows(undefined, undefined)).toEqual([]);
  });

  it("names a console session by its console account when it has no actor", () => {
    expect(consoleRows([recording({ actor: null, account: "marta" })])[0]?.who).toBe("marta");
  });
});
