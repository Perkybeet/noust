import { useId } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { cx } from "../../lib/cx";

/**
 * What each group of the main bar is called. Home is alone and has no title, and Settings is pinned
 * at the foot: neither is a group of the wall. Titles are sentence case and quiet (never capitals:
 * the caps-label rule), and a group of one area is still a group, so the space between two of them
 * is never the only thing that says they are apart.
 */
export const GROUP_TITLES: Readonly<Record<string, PlainKey>> = {
  runs: "nav.groups.runs",
  reach: "nav.groups.reach",
  happens: "nav.groups.happens",
  safe: "nav.groups.safe",
  where: "nav.groups.where",
};

/**
 * A joint between two courses of the wall: a dark line and the lit edge under it. It is what sets
 * the groups of the main bar apart, beside their title when the bar is unfolded; folded to icons it
 * is all there is.
 */
export function Joint({ className }: { className?: string }) {
  return (
    <div aria-hidden="true" className={cx("flex flex-col", className)}>
      <div className="h-px bg-joint" />
      <div className="h-px bg-edge" />
    </div>
  );
}

/**
 * One titled group of areas: the title and its joint, then the list. Folded to icons the title is
 * for a screen reader and the joint is what is drawn. The list is named by the title, so a group is
 * something a screen reader can jump to.
 */
export function AreaGroup({ title, folded = false, className, children }: { title: PlainKey | undefined; folded?: boolean; className?: string; children: ReactNode }) {
  const t = useT();
  const id = useId();
  return (
    <section className={cx("flex flex-col", className)}>
      {title === undefined ? null : folded ? (
        <div className="mt-2.5 mb-1.5 w-8 self-center">
          <Joint />
          <p id={id} className="sr-only">
            {t(title)}
          </p>
        </div>
      ) : (
        <div className="mt-3 mb-1 flex w-full items-center gap-2 pl-3">
          <p id={id} className="shrink-0 text-12 text-fg-muted">
            {t(title)}
          </p>
          <Joint className="min-w-4 flex-1" />
        </div>
      )}
      <ul aria-labelledby={title === undefined ? undefined : id} className="flex flex-col gap-0.5">
        {children}
      </ul>
    </section>
  );
}
