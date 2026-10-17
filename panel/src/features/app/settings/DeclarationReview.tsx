/**
 * What a declaration would change, block by block, with the lines and a way to put a block back
 * (checklist M-35). The server's plan says what each change does and which ones run commands;
 * this shows the lines it changes against what the application runs now, and lets one block be
 * discarded from the draft without touching the others. Nothing here applies anything.
 */

import { Minus, Plus } from "lucide-react";

import type { ResponseOf } from "../../../api/client";
import { Button } from "../../../components/ui/Button";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { blockOfSection, changedBlocks } from "./declarationDiff";
import type { BlockChange, DiffLine } from "./declarationDiff";

type Plan = ResponseOf<"/api/apps/{domain}/apply", "post">["plan"];

/** The name of a block of the file, in words; one this console does not know is shown as written. */
export function blockName(t: T, key: string): string {
  switch (key) {
    case "app":
      return t("declaration.blocks.app");
    case "source":
      return t("declaration.blocks.source");
    case "compose":
      return t("declaration.blocks.compose");
    case "build":
      return t("declaration.blocks.build");
    case "hooks":
      return t("declaration.blocks.hooks");
    case "env":
      return t("declaration.blocks.env");
    case "domains":
      return t("declaration.blocks.domains");
    case "tasks":
      return t("declaration.blocks.tasks");
    default:
      return key;
  }
}

function Lines({ lines, label }: { lines: readonly DiffLine[]; label: string }) {
  const t = useT();
  return (
    <div role="group" aria-label={label} className="overflow-x-auto rounded-control border border-border bg-bg-sunken py-1 text-12 leading-5 scroll-thin">
      {lines.map((line, index) => (
        <div
          key={`${String(index)}:${line.kind}`}
          className={cx("flex w-max min-w-full px-2", line.kind === "add" && "bg-ok-soft", line.kind === "del" && "bg-fail-soft")}
        >
          {/* A shape as well as a ground: the change is read without its colour. */}
          <span aria-hidden="true" className="w-4 shrink-0 text-fg-muted select-none">
            {line.kind === "add" ? <Plus className="size-3" /> : line.kind === "del" ? <Minus className="size-3" /> : null}
          </span>
          {line.kind !== "same" ? <span className="sr-only">{line.kind === "add" ? t("declaration.review.addedLine") : t("declaration.review.removedLine")}</span> : null}
          <Mono className="whitespace-pre">{line.text === "" ? " " : line.text}</Mono>
        </div>
      ))}
    </div>
  );
}

function BlockCard({ change, plan, onDiscard, disabled }: { change: BlockChange; plan: Plan | null; onDiscard: (key: string) => void; disabled: boolean }) {
  const t = useT();
  const name = blockName(t, change.key);
  // What each change does is the plan above, said once; here only whether this block runs commands.
  const runsCommands = (plan?.changes ?? []).some((entry) => entry.root_equivalent && blockOfSection(entry.section) === change.key);
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-card border border-border p-3">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <p className="title flex min-w-0 items-baseline gap-2 text-13 text-fg">
          {name}
          <span className="text-12 font-normal text-fg-muted">{change.status === "added" ? t("declaration.review.added") : t("declaration.review.changed")}</span>
          {runsCommands ? <span className="text-12 font-normal text-warn">{t("declaration.plan.runsCommands")}</span> : null}
        </p>
        <Button size="sm" variant="ghost" disabled={disabled} onClick={() => onDiscard(change.key)}>
          {t("declaration.review.discard", { block: name })}
        </Button>
      </div>
      <Lines lines={change.lines} label={t("declaration.review.linesOf", { block: name })} />
    </li>
  );
}

export interface DeclarationReviewProps {
  /** The declaration of the application as it runs now. */
  current: string;
  /** The draft being reviewed. */
  text: string;
  plan: Plan | null;
  /** Puts one block back to what runs now. */
  onDiscard: (key: string) => void;
  /** Applying is under way, or done: nothing can be discarded then. */
  locked: boolean;
}

/** The blocks of the draft that differ from what runs, each with its lines and a way back. */
export function DeclarationReview({ current, text, plan, onDiscard, locked }: DeclarationReviewProps) {
  const t = useT();
  const blocks = changedBlocks(current, text);
  if (blocks.length === 0) return null;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="text-13 font-medium text-fg">{t("declaration.review.title", { count: blocks.length })}</p>
      <ul aria-label={t("declaration.review.listLabel")} className="flex flex-col gap-2">
        {blocks.map((change) => (
          <BlockCard key={change.key} change={change} plan={plan} onDiscard={onDiscard} disabled={locked} />
        ))}
      </ul>
    </div>
  );
}
