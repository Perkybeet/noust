import { cx } from "../../lib/cx";
import { Card } from "./Card";

export interface SkeletonProps {
  /** Size with utilities: `h-4 w-32`. Defaults to a line of body text. */
  className?: string;
}

/**
 * A placeholder with the shape of content that is loading. It is decorative: the region that
 * contains it carries aria-busy and says what is loading. `data-slot="skeleton"` is what the
 * route tests (`routes/loading.test.tsx`) look for to know a page draws something while it waits.
 */
export function Skeleton({ className }: SkeletonProps) {
  return (
    <span
      aria-hidden="true"
      data-slot="skeleton"
      className={cx("block animate-breathe rounded-[4px] bg-surface-active", sizeDefaults(className), className)}
    />
  );
}

/**
 * The line-of-text size, for whichever dimension the caller did not set. Both utilities in one
 * class list do not "override" each other: Tailwind's stylesheet order decides, so `h-3.5` used
 * to beat a caller's `h-3` and `w-full` every fixed width, and placeholders rendered taller and
 * wider than the content they stand for - a layout shift when it arrived.
 */
function sizeDefaults(className: string | undefined): string {
  const classes = (className ?? "").split(/\s+/);
  const sets = (prefixes: readonly string[]) =>
    classes.some((name) => prefixes.some((prefix) => name.replace(/^[a-z0-9-]+:/, "").startsWith(prefix)));
  const height = sets(["h-", "size-"]) ? "" : "h-3.5";
  const width = sets(["w-", "size-"]) ? "" : "w-full";
  return cx(height, width);
}

/** Widths that differ from row to row, so the placeholder reads as names and not as a stripe. */
const ROW_WIDTHS = ["w-2/5", "w-1/3", "w-1/2", "w-2/5", "w-1/4"] as const;

/**
 * The shape of a `RowList` while its rows are on their way: the same frame, the same hairlines and
 * rows of the same 44px, so nothing moves when they arrive. Put it inside a `LoadingRegion` (or let
 * `QueryState` do): the region says what is loading, this only has its shape.
 */
export function SkeletonRows({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div aria-hidden="true" data-slot="skeleton-rows" className={cx("divide-y divide-border rounded-card border border-border bg-surface", className)}>
      {Array.from({ length: Math.max(1, Math.round(rows)) }, (_, index) => (
        <div key={index} className="flex min-h-11 items-center gap-3 px-4 py-3">
          <Skeleton className={cx("h-4", ROW_WIDTHS[index % ROW_WIDTHS.length])} />
        </div>
      ))}
    </div>
  );
}

/**
 * The shape of a `Card` with a title and a few lines of body, with the card's own padding: the
 * header 16, 20 and 12, the body 20 below. Inside a `LoadingRegion`.
 */
export function SkeletonCard({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <Card padding="none" {...(className !== undefined ? { className } : {})}>
      <div aria-hidden="true" data-slot="skeleton-card">
        <div className="px-5 pt-4 pb-3">
          <Skeleton className="h-5 w-40" />
        </div>
        <div className="px-5 pb-5">
          <SkeletonText lines={lines} />
        </div>
      </div>
    </Card>
  );
}

/**
 * The shape of a page's section: its title at 24px, then rows. For a settings subsection or any
 * part of a page that is a heading and a list; inside a `LoadingRegion`.
 */
export function SkeletonSection({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div aria-hidden="true" data-slot="skeleton-section" className={cx("flex min-w-0 flex-col gap-4", className)}>
      <Skeleton className="h-6 w-48" />
      <SkeletonRows rows={rows} />
    </div>
  );
}

/** Lines of text, the last one shorter, the way a paragraph ends. */
export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <span aria-hidden="true" className={cx("flex flex-col gap-2", className)}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={i === lines - 1 && lines > 1 ? "w-3/5" : "w-full"} />
      ))}
    </span>
  );
}
