import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { CopyButton } from "./CopyButton";
import { Illustration } from "./Illustration";
import type { IllustrationName } from "./Illustration";

export interface EmptyStateProps {
  /**
   * `firstUse`: the page (or tab) has nothing yet; this takes the content's place, one per
   * page, with the one action that is possible now and its CLI command. `inline`: a list or
   * section inside a page is empty, or a filter matched nothing; one line with a link or small
   * button, no heading, no icon, no frame, 56px at most.
   */
  variant: "firstUse" | "inline";
  /** A lucide icon element, drawn at 24px with no tile behind it. Not drawn inline. */
  icon?: ReactNode;
  /**
   * A drawing, only the first time of a main function (docs/DESIGN.md 2.11): the console draws
   * one for the first application, the first server, the first backup, and an empty list never
   * does. It takes the place of the icon.
   */
  illustration?: IllustrationName;
  /** Says what the drawing shows, when the title does not already. Without it the drawing is decoration. */
  illustrationLabel?: string;
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
    <div className="elev-carved-fine mt-5 flex max-w-full items-center gap-1 rounded-control bg-bg-sunken py-1 pr-1 pl-3">
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
export function EmptyState({ variant, icon, illustration, illustrationLabel, title, description, action, command, level, className }: EmptyStateProps) {
  if (variant === "inline") {
    return (
      <div data-slot="empty-state" data-variant="inline" className={cx("flex min-h-10 flex-wrap items-center gap-x-3 gap-y-1 py-2 text-13", className)}>
        <p className="text-fg-muted">{title}</p>
        {description !== undefined ? <p className="text-fg-faint">{description}</p> : null}
        {action !== undefined ? <div className="flex items-center gap-2">{action}</div> : null}
      </div>
    );
  }

  const Heading = `h${level ?? 2}` as const;
  return (
    <div data-slot="empty-state" data-variant="firstUse" className={cx("flex flex-col items-center px-4 py-12 text-center", className)}>
      {illustration !== undefined ? (
        <Illustration name={illustration} className="mb-5 max-w-full" {...(illustrationLabel !== undefined ? { label: illustrationLabel } : {})} />
      ) : icon !== undefined ? (
        // Bare: a tile behind an icon is for a real logo, which says whose it is.
        <div aria-hidden="true" className="mb-4 flex text-fg-muted [&_svg]:size-icon-xl">
          {icon}
        </div>
      ) : null}
      <Heading className="title text-18 text-fg">{title}</Heading>
      {description !== undefined ? <p className="mt-1.5 max-w-measure-help text-14 text-pretty text-fg-muted">{description}</p> : null}
      {action !== undefined ? <div className="mt-5 flex flex-wrap justify-center gap-2">{action}</div> : null}
      {command !== undefined ? <Command command={command} /> : null}
    </div>
  );
}
