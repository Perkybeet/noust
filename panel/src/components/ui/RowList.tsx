import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface RowListProps {
  /** Names the list for assistive technology: "Environment variables". Required: a list is a landmark to jump to. */
  "aria-label": string;
  /** `RowList.Item`s. */
  children: ReactNode;
  className?: string;
}

export interface RowListItemProps {
  /** A glyph, an avatar or a state before the content: what the row is a row of. */
  leading?: ReactNode;
  /** At the end of the row: a `RowMenu`, or one small button. Never two. */
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

function RowListItem({ leading, actions, children, className }: RowListItemProps) {
  return (
    <li className={cx("flex min-h-11 items-center gap-3 px-4 py-3", className)}>
      {leading !== undefined ? <div className="flex shrink-0 items-center">{leading}</div> : null}
      <div className="min-w-0 flex-1">{children}</div>
      {/* A small button is 28px tall; the row stays at the height of its text. */}
      {actions !== undefined ? <div className="-my-1 flex shrink-0 items-center gap-2">{actions}</div> : null}
    </li>
  );
}

/**
 * Rows of like things that are not a table: a variable, a mount, a process, a person in a dialog.
 * A bordered list on a surface whose rows are told apart by a hairline, 44px at least, with one
 * radius (the card's) and one way to hold an icon at the start and a menu at the end. The three
 * states are not its own: a loading list is `SkeletonRows` inside a `LoadingRegion`, an empty one
 * an `EmptyState variant="inline"`, a failed one a `QueryState`.
 *
 * Rows that carry columns of facts are a `DataTable`. A list written by hand with `divide-y` and
 * a border is this.
 */
export function RowList({ "aria-label": label, children, className }: RowListProps) {
  return (
    <ul aria-label={label} className={cx("m-0 list-none rounded-card border border-border divide-y divide-border bg-surface p-0", className)}>
      {children}
    </ul>
  );
}

RowList.Item = RowListItem;
