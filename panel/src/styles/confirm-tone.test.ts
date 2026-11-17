/**
 * The tone of a confirmation (docs/DESIGN.md 6.2): red is a failure or a loss, and `ConfirmDialog` is neutral unless its call says it
 * destroys. The factory being neutral means a call that forgets the word is silently the wrong colour, so the calls where
 * forgetting it would be a lie are held here: a dialog that asks for a name to be typed is asking because data is lost for good,
 * and says `destructive` (or an expression that decides it, as a restore does for "over what is there" and "beside it").
 * A call that spreads a bag of props is read by what the bag says, at the place that builds it.
 */

import { describe, expect, it } from "vitest";

const RAW = import.meta.glob<string>(["../**/*.tsx", "!../**/*.test.tsx", "!../dev/**", "!../test/**"], { eager: true, query: "?raw", import: "default" });

/** The opening tags of every `<ConfirmDialog` in a source, with the line each starts on. */
function dialogs(source: string): { line: number; tag: string }[] {
  const found: { line: number; tag: string }[] = [];
  for (const match of source.matchAll(/<ConfirmDialog\b/g)) {
    let depth = 0;
    let end = match.index + match[0].length;
    for (; end < source.length; end += 1) {
      const char = source[end];
      if (char === "{") depth += 1;
      else if (char === "}") depth -= 1;
      else if (char === ">" && depth === 0 && source[end - 1] !== "=") break;
    }
    found.push({ line: source.slice(0, match.index).split("\n").length, tag: source.slice(match.index, end + 1) });
  }
  return found;
}

describe("the tone of a confirmation", () => {
  it("is stated by every call that asks for a name to be typed", () => {
    const silent: string[] = [];
    for (const [path, source] of Object.entries(RAW)) {
      for (const { line, tag } of dialogs(source)) {
        const types = tag.includes('friction="type"');
        const decided = /\bdestructive\b/.test(tag) || tag.includes("{...");
        if (types && !decided) silent.push(`${path.replace(/^\.\.\//, "")}:${String(line)}`);
      }
    }
    expect(silent, `A dialog that asks for the name to be typed destroys something: say \`destructive\`.\n${silent.join("\n")}`).toEqual([]);
  });

  it("finds the dialogs it reads", () => {
    const all = Object.values(RAW).flatMap((source) => dialogs(source));
    // A reader that found none would pass the test above for nothing.
    expect(all.length).toBeGreaterThan(100);
  });
});
