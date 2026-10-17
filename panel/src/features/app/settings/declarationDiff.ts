/**
 * A declaration compared with what the application is now, block by block (checklist M-35).
 *
 * The file is the keys of `noust.yaml` (`app:`, `source:`, `build:`, `env:`...), each a block that
 * starts at the left margin. Applying a file touches only the blocks it has, so a block is the unit
 * of a change that can be reviewed, kept or discarded; one that the file lacks changes nothing and
 * is not a change. The server says what each block would do (its plan); this shows the lines, which
 * the plan does not carry, and lets a block be put back to what runs now. No YAML is parsed: the
 * blocks are cut by indentation, so a comment or a flow-style value is kept as the operator wrote
 * it.
 *
 * Pure, so every rule is tested without rendering.
 */

/** One top-level key of the file with the lines under it. */
export interface Block {
  /** The key: `app`, `env`, `build`... */
  key: string;
  /** Its lines, the key's own first, with no trailing blank lines. */
  lines: readonly string[];
}

export interface Split {
  /** Lines before the first key: comments the operator wrote. */
  preamble: readonly string[];
  blocks: readonly Block[];
}

const TOP_LEVEL_KEY = /^([A-Za-z_][\w-]*):(?:\s|$)/;

/** Cuts a declaration into its top-level blocks. */
export function splitBlocks(text: string): Split {
  const preamble: string[] = [];
  const blocks: { key: string; lines: string[] }[] = [];
  for (const line of text.replace(/\r\n/g, "\n").split("\n")) {
    const key = TOP_LEVEL_KEY.exec(line)?.[1];
    if (key !== undefined) {
      blocks.push({ key, lines: [line] });
    } else if (blocks.length === 0) {
      preamble.push(line);
    } else {
      blocks[blocks.length - 1]?.lines.push(line);
    }
  }
  const trimmed = (lines: readonly string[]): string[] => {
    const kept = [...lines];
    while (kept.length > 0 && (kept[kept.length - 1] ?? "").trim() === "") kept.pop();
    return kept;
  };
  return { preamble: trimmed(preamble), blocks: blocks.map((block) => ({ key: block.key, lines: trimmed(block.lines) })) };
}

/** The blocks written back as a file. */
export function joinBlocks(split: Split): string {
  const parts = [split.preamble.join("\n"), ...split.blocks.map((block) => block.lines.join("\n"))].filter((part) => part.trim() !== "");
  return `${parts.join("\n")}\n`;
}

export type LineKind = "same" | "add" | "del";

export interface DiffLine {
  kind: LineKind;
  text: string;
}

/** The lines of `after` against those of `before`, by the longest common subsequence. */
export function lineDiff(before: readonly string[], after: readonly string[]): DiffLine[] {
  const rows = before.length;
  const cols = after.length;
  // common(i, j): how long the common part of before[i..] and after[j..] is, kept flat so no
  // index can be missing.
  const width = cols + 1;
  const lengths = new Array<number>((rows + 1) * width).fill(0);
  const common = (i: number, j: number): number => lengths[i * width + j] ?? 0;
  for (let i = rows - 1; i >= 0; i -= 1) {
    for (let j = cols - 1; j >= 0; j -= 1) {
      lengths[i * width + j] = before[i] === after[j] ? common(i + 1, j + 1) + 1 : Math.max(common(i + 1, j), common(i, j + 1));
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < rows && j < cols) {
    if (before[i] === after[j]) {
      out.push({ kind: "same", text: before[i] ?? "" });
      i += 1;
      j += 1;
    } else if (common(i + 1, j) >= common(i, j + 1)) {
      out.push({ kind: "del", text: before[i] ?? "" });
      i += 1;
    } else {
      out.push({ kind: "add", text: after[j] ?? "" });
      j += 1;
    }
  }
  for (; i < rows; i += 1) out.push({ kind: "del", text: before[i] ?? "" });
  for (; j < cols; j += 1) out.push({ kind: "add", text: after[j] ?? "" });
  return out;
}

export interface BlockChange {
  key: string;
  /** `added`: the application has no such block now. `changed`: it has, and the lines differ. */
  status: "added" | "changed";
  lines: DiffLine[];
}

const blank = (line: string): boolean => line.trim() === "";
const comment = (line: string): boolean => line.trim().startsWith("#");

/** Whether two blocks say the same: a comment or a blank line is not a change. */
function sameMeaning(a: readonly string[], b: readonly string[]): boolean {
  const meaningful = (lines: readonly string[]): string[] => lines.filter((line) => !blank(line) && !comment(line)).map((line) => line.trimEnd());
  return meaningful(a).join("\n") === meaningful(b).join("\n");
}

/**
 * What a draft changes, block by block.
 *
 * A block the draft does not have is not a change (applying a file never removes by omission), and
 * a block that differs only in comments or blank lines is not one either.
 *
 * @param current - The declaration of the application as it runs now.
 * @param draft - The declaration to be applied.
 */
export function changedBlocks(current: string, draft: string): BlockChange[] {
  const now = new Map(splitBlocks(current).blocks.map((block) => [block.key, block] as const));
  const found: BlockChange[] = [];
  for (const block of splitBlocks(draft).blocks) {
    const existing = now.get(block.key);
    if (existing === undefined) {
      found.push({ key: block.key, status: "added", lines: lineDiff([], block.lines) });
    } else if (!sameMeaning(existing.lines, block.lines)) {
      found.push({ key: block.key, status: "changed", lines: lineDiff(existing.lines, block.lines) });
    }
  }
  return found;
}

/**
 * The draft with one block put back to what runs now: a changed block gets its current lines, an
 * added one is left out. The rest of the draft, comments included, is untouched.
 */
export function discardBlock(current: string, draft: string, key: string): string {
  const existing = splitBlocks(current).blocks.find((block) => block.key === key);
  const split = splitBlocks(draft);
  const blocks = existing === undefined ? split.blocks.filter((block) => block.key !== key) : split.blocks.map((block) => (block.key === key ? existing : block));
  return joinBlocks({ preamble: split.preamble, blocks });
}

/** Which block of the file the server's plan section is part of: the health check, limits and retention live under `app:`. */
export function blockOfSection(section: string): string {
  return section === "health" || section === "limits" || section === "keep_releases" ? "app" : section;
}
