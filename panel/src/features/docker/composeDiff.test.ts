import { describe, expect, it } from "vitest";

import { changedLines, diffLinesOf } from "./composeDiff";

describe("a Compose document's diff", () => {
  it("draws what was added and what was removed, with the lines around them, and none of the diff's own headers", () => {
    const diff = ["--- current", "+++ edited", "@@ -1,3 +1,4 @@", " services:", "   web:", "-    image: nginx:1.26", "+    image: nginx:1.27", "+    restart: always", " volumes:"].join("\n");
    expect(diffLinesOf(diff)).toEqual([
      { kind: "same", text: "services:" },
      { kind: "same", text: "  web:" },
      { kind: "del", text: "    image: nginx:1.26" },
      { kind: "add", text: "    image: nginx:1.27" },
      { kind: "add", text: "    restart: always" },
      { kind: "same", text: "volumes:" },
    ]);
  });

  it("draws nothing for a diff with no change", () => {
    expect(diffLinesOf("")).toEqual([]);
  });
});

describe("how many lines a draft changed", () => {
  it("counts no change as none, so the bar says there is nothing to save", () => {
    expect(changedLines("a\nb\n", "a\nb\n")).toBe(0);
  });

  it("counts a changed line as two (the one that went and the one that came) and an added line as one", () => {
    expect(changedLines("a\nb\nc", "a\nB\nc")).toBe(2);
    expect(changedLines("a\nb", "a\nb\nc")).toBe(1);
  });

  it("counts a repeated line as many times as it was added or removed", () => {
    expect(changedLines("a", "a\na\na")).toBe(2);
    expect(changedLines("a\na\na", "a")).toBe(2);
  });
});
