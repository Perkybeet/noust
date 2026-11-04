/**
 * What widget bodies are made of: how one waits and fails inside its cell, and the few pieces of
 * type every one of them draws (a figure, a state with its glyph). A body never draws a box of its
 * own: its cell is the slab.
 */
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { LoadingRegion } from "../page/LoadingRegion";
import { Button } from "../ui/Button";
import { ICONS } from "../ui/icons";
import { Skeleton } from "../ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../ui/StatusPill";
import type { Status } from "../ui/StatusPill";

/**
 * A widget that has nothing to draw yet: the shape of what it will show, in a region that says what is
 * being read, or, when the read failed, one line that says so with its own Try again. The cell keeps its
 * size either way, so nothing moves when the answer arrives; the system's own words about a failure are in
 * the page that owns the thing, which the head of the widget leads to.
 */
export function WidgetPending({ title, failed = false, onRetry }: { title: string; failed?: boolean; onRetry?: () => void }) {
  const t = useT();
  if (failed) {
    return (
      <div className="flex h-full min-w-0 flex-col items-start justify-center gap-2">
        <p className="flex items-center gap-2 text-13 text-fg">
          <ICONS.error aria-hidden="true" className="size-icon-md shrink-0 text-fail" />
          {t("widgets.couldNotLoad", { title })}
        </p>
        {onRetry !== undefined ? (
          <Button size="sm" variant="ghost" onClick={onRetry}>
            {t("common.errorBlock.tryAgain")}
          </Button>
        ) : null}
      </div>
    );
  }
  return (
    <LoadingRegion label={t("widgets.loading", { title })} className="flex flex-col gap-2.5">
      <Skeleton className="h-7 w-1/3" />
      <Skeleton className="h-3.5 w-2/3" />
      <Skeleton className="h-3.5 w-1/2" />
    </LoadingRegion>
  );
}

/** A state's glyph in its colour: the shape and the colour say it, a word beside it says it too. */
export function Glyph({ state, size = 12, className }: { state: Status; size?: number; className?: string }) {
  return <StatusGlyph state={state} size={size} className={cx(stateTextClass(state), className)} />;
}

/** One figure that is the point of a widget: counted, in the display face. */
export function Big({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cx("title text-24 leading-8 text-fg tabular-nums", className)}>{children}</p>;
}

/** A line with a state: its glyph, and the words, in the text colour. */
export function StateLine({ state, children, className }: { state: Status; children: ReactNode; className?: string }) {
  return (
    <p className={cx("flex min-w-0 items-center gap-1.5 text-12 text-fg", className)}>
      <Glyph state={state} size={10} />
      <span className="min-w-0 truncate">{children}</span>
    </p>
  );
}
