/**
 * The diff the server gives of a Compose document (unified text) as the lines the kit draws, and how many lines a draft changed. The
 * headers of the diff (`---`, `+++`, `@@`) say nothing a person needs: what is shown is the lines that were added and removed, with
 * the unchanged ones around them for company.
 */

import type { DiffLine } from "../../components/ui/DiffLines";

/** A unified diff as lines: `+` is added, `-` is removed, a line that begins with a space is the document's own. */
export function diffLinesOf(diff: string): DiffLine[] {
  const lines: DiffLine[] = [];
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("+++") || raw.startsWith("---") || raw.startsWith("@@") || raw.startsWith("\\")) continue;
    if (raw.startsWith("+")) lines.push({ kind: "add", text: raw.slice(1) });
    else if (raw.startsWith("-")) lines.push({ kind: "del", text: raw.slice(1) });
    else if (raw.startsWith(" ")) lines.push({ kind: "same", text: raw.slice(1) });
  }
  return lines;
}

/** How many lines a draft changed against what runs: lines it has that the document lacks plus lines it lacks, each counted once. */
export function changedLines(before: string, after: string): number {
  if (before === after) return 0;
  const left = before.split("\n");
  const right = after.split("\n");
  const count = (from: readonly string[], within: readonly string[]): number => {
    const pool = new Map<string, number>();
    for (const line of within) pool.set(line, (pool.get(line) ?? 0) + 1);
    let missing = 0;
    for (const line of from) {
      const have = pool.get(line) ?? 0;
      if (have > 0) pool.set(line, have - 1);
      else missing += 1;
    }
    return missing;
  };
  return count(right, left) + count(left, right);
}
