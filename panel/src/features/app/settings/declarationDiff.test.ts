import { describe, expect, it } from "vitest";

import { blockOfSection, changedBlocks, discardBlock, joinBlocks, lineDiff, splitBlocks } from "./declarationDiff";

const CURRENT = `# noust.yaml of shop.example.com
app:
  health:
    path: /
    expect: 200-399
  limits:
    memory_max_mb: 512
env:
  LOG_LEVEL: info
`;

describe("a declaration cut into blocks", () => {
  it("keeps what comes before the first key apart, and each key with its lines", () => {
    const split = splitBlocks(CURRENT);
    expect(split.preamble).toEqual(["# noust.yaml of shop.example.com"]);
    expect(split.blocks.map((block) => block.key)).toEqual(["app", "env"]);
    expect(split.blocks[0]?.lines).toEqual(["app:", "  health:", "    path: /", "    expect: 200-399", "  limits:", "    memory_max_mb: 512"]);
  });

  it("reads a key with a value on its own line, and ignores indented keys and Windows line endings", () => {
    const split = splitBlocks("build: {install: npm ci}\r\nhooks:\r\n  pre_deploy: []\r\n");
    expect(split.blocks.map((block) => [block.key, block.lines.length])).toEqual([
      ["build", 1],
      ["hooks", 2],
    ]);
  });

  it("writes the blocks back as a file, with the comment first", () => {
    expect(joinBlocks(splitBlocks(CURRENT))).toBe(CURRENT);
  });
});

describe("the lines that differ", () => {
  it("marks what is removed and what is added, and keeps what is common between", () => {
    expect(lineDiff(["a", "b", "c"], ["a", "x", "c"])).toEqual([
      { kind: "same", text: "a" },
      { kind: "del", text: "b" },
      { kind: "add", text: "x" },
      { kind: "same", text: "c" },
    ]);
  });

  it("says a block that is new as all added, and one that is gone as all removed", () => {
    expect(lineDiff([], ["x", "y"]).map((line) => line.kind)).toEqual(["add", "add"]);
    expect(lineDiff(["x"], []).map((line) => line.kind)).toEqual(["del"]);
  });
});

describe("what a draft changes", () => {
  it("is nothing for a draft that says what runs", () => {
    expect(changedBlocks(CURRENT, CURRENT)).toEqual([]);
  });

  it("is only the blocks that differ, with the lines that do", () => {
    const draft = CURRENT.replace("memory_max_mb: 512", "memory_max_mb: 1024").replace("LOG_LEVEL: info", "LOG_LEVEL: debug");
    const changes = changedBlocks(CURRENT, draft);
    expect(changes.map((change) => [change.key, change.status])).toEqual([
      ["app", "changed"],
      ["env", "changed"],
    ]);
    expect(changes[0]?.lines.filter((line) => line.kind !== "same").map((line) => `${line.kind}:${line.text.trim()}`)).toEqual(["del:memory_max_mb: 512", "add:memory_max_mb: 1024"]);
  });

  it("is a block the application has no such thing as, as added", () => {
    const changes = changedBlocks(CURRENT, `${CURRENT}domains:\n  aliases: [www.example.com]\n`);
    expect(changes).toHaveLength(1);
    expect(changes[0]).toMatchObject({ key: "domains", status: "added" });
  });

  it("is not a block the draft leaves out: applying a file never removes by omission", () => {
    expect(changedBlocks(CURRENT, "app:\n  health:\n    path: /\n    expect: 200-399\n  limits:\n    memory_max_mb: 512\n")).toEqual([]);
  });

  it("is not a comment or a blank line", () => {
    const draft = CURRENT.replace("env:", "# how noisy\n\nenv:").replace("path: /", "path: /   ");
    expect(changedBlocks(CURRENT, draft)).toEqual([]);
  });
});

describe("putting a block back", () => {
  const draft = CURRENT.replace("memory_max_mb: 512", "memory_max_mb: 1024").replace("LOG_LEVEL: info", "LOG_LEVEL: debug");

  it("restores a changed block and leaves the others as drafted", () => {
    const back = discardBlock(CURRENT, draft, "app");
    expect(back).toContain("memory_max_mb: 512");
    expect(back).toContain("LOG_LEVEL: debug");
    expect(changedBlocks(CURRENT, back).map((change) => change.key)).toEqual(["env"]);
  });

  it("leaves out a block that was new", () => {
    const withDomains = `${CURRENT}domains:\n  aliases: [www.example.com]\n`;
    expect(discardBlock(CURRENT, withDomains, "domains")).not.toContain("domains:");
  });

  it("keeps the operator's comments", () => {
    expect(discardBlock(CURRENT, draft, "env")).toContain("# noust.yaml of shop.example.com");
  });

  it("ends with nothing to review once every block is put back", () => {
    const back = discardBlock(CURRENT, discardBlock(CURRENT, draft, "app"), "env");
    expect(changedBlocks(CURRENT, back)).toEqual([]);
  });
});

describe("the plan's sections and the file's blocks", () => {
  it("puts the health check, limits and retention under app and the rest under their own name", () => {
    expect(["health", "limits", "keep_releases"].map(blockOfSection)).toEqual(["app", "app", "app"]);
    expect(["source", "compose", "build", "hooks", "env", "domains", "tasks"].map(blockOfSection)).toEqual(["source", "compose", "build", "hooks", "env", "domains", "tasks"]);
  });
});
