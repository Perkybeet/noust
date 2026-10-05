import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface StatTileProps {
  /** What the number is, in sentence case: "Current release". */
  label: string;
  /** The reading. A string or number is set large; an element (a pill) as it is. */
  value: ReactNode;
  /** One line of context under the value: when, of what, compared with what. */
  detail?: ReactNode;
  /**
   * Lines the context may take. 2 for a row of tiles whose contexts are sentences (a
   * dashboard's): it wraps instead of cutting (as many lines as it needs on a phone), and both
   * lines are kept from the first frame so nothing moves when the words arrive. 1 by default: cut with an ellipsis from 640px, and
   * wrapped on a phone, where two tiles to a row leave a line too little room to be read.
   */
  detailLines?: 1 | 2;
  /** Set the value as a system value (a commit, a port) in mono. */
  mono?: boolean;
  /**
   * The reading is still being read and `value` is a skeleton. The tile says so (`aria-busy`):
   * a screen reader hears its label and that it is not ready, and the route tests find it.
   */
  loading?: boolean;
  className?: string;
}

/**
 * One headline fact with a line of context. Tiles sit side by side in a row that answers "how
 * is this doing" before the details below it do. The value uses proportional figures; mono
 * is for identifiers, not for quantities.
 */
export function StatTile({ label, value, detail, detailLines = 1, mono = false, loading = false, className }: StatTileProps) {
  const primitive = typeof value === "string" || typeof value === "number";
  return (
    <div
      {...(loading ? { "aria-busy": true } : {})}
      className={cx("flex min-w-0 flex-col gap-1.5 rounded-card border border-border bg-surface px-4 py-3.5 shadow-raised", className)}
    >
      <span className="break-words text-12 text-fg-muted sm:truncate">{label}</span>
      <div className="flex min-h-7 min-w-0 items-center">
        {primitive ? (
          <span
            translate={mono ? "no" : undefined}
            className={cx("break-words text-fg sm:truncate", mono ? "mono text-16 font-medium" : "title text-18")}
          >
            {value}
          </span>
        ) : (
          value
        )}
      </div>
      {detail !== undefined ? (
        <div data-slot="detail" className={cx("text-12 text-fg-faint", detailLines === 2 ? "min-h-8 break-words text-pretty sm:line-clamp-2" : "break-words text-pretty sm:truncate")}>
          {detail}
        </div>
      ) : null}
    </div>
  );
}
