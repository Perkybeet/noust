import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import { Illustration } from "./Illustration";
import type { IllustrationName } from "./Illustration";

export interface FirstUseProps {
  /** One sentence that says what this gives you once it exists: not what is missing, and never a paragraph. */
  children: ReactNode;
  /** The button that starts it. Left out where the page's own header already holds that button. */
  action?: ReactNode;
  /** A second way in, beside the action (a link to where the thing is made). */
  secondary?: ReactNode;
  /** The drawing: the empty slab, waiting to be filled, unless the thing has a drawing of its own. */
  drawing?: IllustrationName;
  className?: string;
}

/**
 * The first time of a thing, inside a section: a drawing, one sentence that says what it gives you, and the button that
 * starts it. It is the answer to "there is none yet" that is not a muted line with the button somewhere else (owner item
 * 121), and the section-sized sibling of `EmptyState variant="firstUse"`, which is a whole page's first time and is
 * drawn in the middle of it. One per section; a list that a filter emptied is an inline empty state.
 */
export function FirstUse({ children, action, secondary, drawing = "empty-slab", className }: FirstUseProps) {
  return (
    <div data-slot="first-use" className={cx("elev-carved-fine flex flex-col items-center gap-4 rounded-card bg-bg-sunken px-6 py-6 text-center sm:flex-row sm:gap-6 sm:text-left", className)}>
      <Illustration name={drawing} className={drawing === "empty-slab" ? "h-auto w-28 sm:w-32" : "h-auto w-40 sm:w-44"} />
      <p className="max-w-measure-help min-w-0 flex-1 text-14 text-pretty text-fg">{children}</p>
      {action !== undefined || secondary !== undefined ? (
        <div className="flex shrink-0 flex-wrap items-center justify-center gap-2">
          {secondary}
          {action}
        </div>
      ) : null}
    </div>
  );
}
