import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { CopyButton } from "./CopyButton";

export interface EmptyStateProps {
  /**
   * `firstUse`: the page (or tab) has nothing yet; this takes the content's place, one per
   * page, with the one action that is possible now and its CLI command. `inline`: a list or
   * section inside a page is empty, or a filter matched nothing; one line with a link or small
   * button, no heading, no icon, no frame, 56px at most. Without a variant, the framed block
   * of 3.0, kept for pages not yet moved onto a template.
   */
  variant?: "firstUse" | "inline";
  /** A lucide icon element, drawn at 20px. Not drawn inline. */
  icon?: ReactNode;
  /** What this place is and what is missing ("No applications yet"). Inline: the whole line. */
  title: string;
  /** What this place is for and what to do next. An invitation, not an apology. */
  description?: ReactNode;
  action?: ReactNode;
  /** The CLI command that does the same thing, for operators who live in a terminal. */
  command?: string;
  /** Heading level of the title, to keep the page outline correct: 2 directly under a page h1. */
  level?: 2 | 3 | 4;
  className?: string;
}

function Command({ command }: { command: string }) {
  const t = useT();
  return (
    <div className="mt-5 flex max-w-full items-center gap-1 rounded-control border border-border bg-bg-sunken py-1 pr-1 pl-3">
      <span className="mono truncate text-13 text-fg-muted" translate="no">
        <span aria-hidden="true" className="text-fg-faint select-none">
          ${" "}
        </span>
        {command}
      </span>
      <CopyButton value={command} label={t("common.copyButton.copyCommand")} />
    </div>
  );
}

/** What a list or page shows before it has anything in it. */
export function EmptyState({ variant, icon, title, description, action, command, level, className }: EmptyStateProps) {
  if (variant === "inline") {
    return (
      <div data-variant="inline" className={cx("flex min-h-10 flex-wrap items-center gap-x-3 gap-y-1 py-2 text-13", className)}>
        <p className="text-fg-muted">{title}</p>
        {description !== undefined ? <p className="text-fg-faint">{description}</p> : null}
        {action !== undefined ? <div className="flex items-center gap-2">{action}</div> : null}
      </div>
    );
  }

  const firstUse = variant === "firstUse";
  const Heading = `h${level ?? (firstUse ? 2 : 3)}` as const;
  return (
    <div
      {...(firstUse ? { "data-variant": "firstUse" } : {})}
      className={cx(
        "flex flex-col items-center text-center",
        firstUse ? "px-4 py-12" : "rounded-card border border-dashed border-border px-6 py-12",
        className,
      )}
    >
      {icon !== undefined ? (
        <div className="mb-4 flex size-10 items-center justify-center rounded-control border border-border bg-surface text-fg-muted shadow-raised [&_svg]:size-icon-lg">
          {icon}
        </div>
      ) : null}
      <Heading className="title text-16 text-fg">{title}</Heading>
      {description !== undefined ? (
        <p className={cx("mt-1.5 text-14 text-pretty text-fg-muted", firstUse ? "max-w-measure-help" : "max-w-[46ch]")}>{description}</p>
      ) : null}
      {action !== undefined ? <div className="mt-5 flex flex-wrap justify-center gap-2">{action}</div> : null}
      {command !== undefined ? <Command command={command} /> : null}
    </div>
  );
}
