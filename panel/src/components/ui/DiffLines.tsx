import { Minus, Plus } from "lucide-react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Mono } from "./Mono";

export interface DiffLine {
  kind: "same" | "add" | "del";
  text: string;
}

export interface DiffLinesProps {
  lines: readonly DiffLine[];
  /** Names the group: "Changes to web", "Changes to /etc/app.conf". */
  label: string;
  /**
   * What a screen reader says of a changed line, since the plus, the minus and the ground are drawn. Absent, the words of a plan's
   * file ("Added line", "Removed line"); a page that is not about a plan says its own.
   */
  labels?: { added: string; removed: string };
}

/**
 * The lines of a change, the same drawing wherever one is shown (a declaration's review, a Compose document's, the file a plan
 * would change): a shape (a plus or a minus) as well as a ground, so a change is read without its colour, and a word for a screen
 * reader. It is state in the kit's sense (added is the running green, removed the failure red), never decoration. Lines are system
 * text, in mono and never cut: a long one scrolls inside the box.
 */
export function DiffLines({ lines, label, labels }: DiffLinesProps) {
  const t = useT();
  const words = labels ?? { added: t("plans.preview.added"), removed: t("plans.preview.removed") };
  return (
    <div role="group" aria-label={label} className="elev-carved-fine overflow-x-auto rounded-control bg-bg-sunken py-1 text-12 leading-5 scroll-thin">
      {lines.map((line, index) => (
        <div key={`${String(index)}:${line.kind}`} className={cx("flex w-max min-w-full px-2", line.kind === "add" && "bg-ok-soft", line.kind === "del" && "bg-fail-soft")}>
          <span aria-hidden="true" className="w-4 shrink-0 text-fg-muted select-none">
            {line.kind === "add" ? <Plus className="size-3" /> : line.kind === "del" ? <Minus className="size-3" /> : null}
          </span>
          {line.kind !== "same" ? <span className="sr-only">{line.kind === "add" ? words.added : words.removed}</span> : null}
          <Mono className="whitespace-pre">{line.text === "" ? " " : line.text}</Mono>
        </div>
      ))}
    </div>
  );
}
