import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface SaidLine {
  id: string;
  /**
   * The sentence, already translated: who it is about and what they said, with the system's own words as a `Mono`
   * inside it ("worker-01: the provider said server limit reached"). Never the system's words paraphrased.
   */
  said: ReactNode;
  /** What it means for the operator, on a line under it ("It is billed until you delete it."). */
  note?: ReactNode;
}

export interface SaidLinesProps {
  /** Names the list for assistive technology: "What the provider said about the servers that did not make it". */
  label: string;
  lines: readonly SaidLine[];
  className?: string;
}

/**
 * What a system answered about the things of a table that did not work, as it said it, under that table and inside
 * its slab: one line for each, the system's words in mono (docs/DESIGN.md, principle 5), and what it costs or what
 * to do under it. The row says that it failed (a state: colour, shape, word); this says why, where the rows end,
 * so a table of twenty keeps its one height for every row. Nothing is drawn when there is nothing to say.
 */
export function SaidLines({ label, lines, className }: SaidLinesProps) {
  if (lines.length === 0) return null;
  return (
    <ul aria-label={label} className={cx("divide-y divide-border border-t border-border", className)}>
      {lines.map((line) => (
        <li key={line.id} className="flex flex-col gap-0.5 px-5 py-3 text-13 text-pretty text-fg">
          <span>{line.said}</span>
          {line.note !== undefined ? <span className="text-fg-muted">{line.note}</span> : null}
        </li>
      ))}
    </ul>
  );
}
