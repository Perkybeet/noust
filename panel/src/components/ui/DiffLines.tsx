import { Minus, Plus } from "lucide-react";

import { cx } from "../../lib/cx";
import { Mono } from "./Mono";

export interface DiffLine {
  kind: "same" | "add" | "del";
  text: string;
}

export interface DiffLinesProps {
  lines: readonly DiffLine[];
  /** Names the group: "Changes to web". */
  label: string;
  /** What a screen reader says of a changed line, since the plus, the minus and the ground are drawn. */
  labels: { added: string; removed: string };
}

/**
 * The lines of a change, the same drawing wherever one is shown (a declaration's review, a Compose document's): a shape
 * (a plus or a minus) as well as a ground, so a change is read without its colour, and a word for a screen reader. It is
 * state in the kit's sense (added is the running green, removed the failure red), never decoration. Lines are
 * system text, in mono and never cut: a long one scrolls inside the box.
 */
export function DiffLines({ lines, label, labels }: DiffLinesProps) {
  return (
    <div role="group" aria-label={label} className="overflow-x-auto rounded-control border border-border bg-bg-sunken py-1 text-12 leading-5 scroll-thin">
      {lines.map((line, index) => (
        <div key={`${String(index)}:${line.kind}`} className={cx("flex w-max min-w-full px-2", line.kind === "add" && "bg-ok-soft", line.kind === "del" && "bg-fail-soft")}>
          <span aria-hidden="true" className="w-4 shrink-0 text-fg-muted select-none">
            {line.kind === "add" ? <Plus className="size-3" /> : line.kind === "del" ? <Minus className="size-3" /> : null}
          </span>
          {line.kind !== "same" ? <span className="sr-only">{line.kind === "add" ? labels.added : labels.removed}</span> : null}
          <Mono className="whitespace-pre">{line.text === "" ? " " : line.text}</Mono>
        </div>
      ))}
    </div>
  );
}
