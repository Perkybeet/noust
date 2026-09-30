import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface TabToolbarProps {
  /** How the tab's subject is, in one line: "23 updates, 4 of them security · checked 3 h ago". */
  summary: ReactNode;
  /** The tab's actions, its primary last: the Server header has none of its own. */
  actions?: ReactNode;
  className?: string;
}

/**
 * The first line of a Server tab: its state on the left, its actions on the right. The page's
 * header is the area's, the same on every tab; what one tab does lives here, above its content,
 * so the view still has its state and its one primary action in the first screen.
 */
export function TabToolbar({ summary, actions, className }: TabToolbarProps) {
  return (
    <div className={cx("flex min-h-control-md min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2", className)}>
      <div className="min-w-0 text-14 text-pretty text-fg-muted">{summary}</div>
      {/* Wraps rather than widening the page: a phone gets the buttons on two lines. */}
      {actions !== undefined ? <div className="flex min-w-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
