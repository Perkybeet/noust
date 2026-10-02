/**
 * Counting the lines two versions of a text differ by: what the save bar of a file editor (a
 * site's configuration, a unit file) says is unsaved.
 */

/**
 * How many lines differ between what is saved and a draft. Lines are matched as a diff matches
 * them (the longest common subsequence), so a block added near the top counts its own lines,
 * not every line after it; a replaced line counts once.
 */
export function changedLines(saved: string, draft: string): number {
  if (saved === draft) return 0;
  const before = saved.split("\n");
  const after = draft.split("\n");
  let start = 0;
  while (start < before.length && start < after.length && before[start] === after[start]) start += 1;
  let endBefore = before.length;
  let endAfter = after.length;
  while (endBefore > start && endAfter > start && before[endBefore - 1] === after[endAfter - 1]) {
    endBefore -= 1;
    endAfter -= 1;
  }
  const a = before.slice(start, endBefore);
  const b = after.slice(start, endAfter);
  // Past a few million cells the table costs more than the count is worth: every line of the
  // changed middle counts.
  if (a.length === 0 || b.length === 0 || a.length * b.length > 4_000_000) return Math.max(a.length, b.length);
  const width = b.length + 1;
  const lcs = new Uint32Array((a.length + 1) * width);
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      lcs[i * width + j] = a[i] === b[j] ? (lcs[(i + 1) * width + j + 1] ?? 0) + 1 : Math.max(lcs[(i + 1) * width + j] ?? 0, lcs[i * width + j + 1] ?? 0);
    }
  }
  let changed = 0;
  let removed = 0;
  let added = 0;
  let i = 0;
  let j = 0;
  const flush = (): void => {
    changed += Math.max(removed, added);
    removed = 0;
    added = 0;
  };
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      flush();
      i += 1;
      j += 1;
    } else if (j < b.length && (i === a.length || (lcs[i * width + j + 1] ?? 0) >= (lcs[(i + 1) * width + j] ?? 0))) {
      added += 1;
      j += 1;
    } else {
      removed += 1;
      i += 1;
    }
  }
  flush();
  return changed;
}
