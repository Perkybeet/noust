import { useId } from "react";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface SubsectionProps {
  title: string;
  /** One sentence on what this part is for, when the title alone does not say it. */
  description?: ReactNode;
  /** Controls for this part alone, aligned with its title. */
  actions?: ReactNode;
  children: ReactNode;
  /** 3 inside a page Section, 4 inside a Card that already has an h3. */
  level?: 3 | 4;
  className?: string;
}

/**
 * A part of a section or a card: the subsection title role (14px title), an optional line
 * of description, and the content 12px below. A group named by its heading, not a landmark:
 * a page has few regions and many subsections.
 */
export function Subsection({ title, description, actions, children, level = 3, className }: SubsectionProps) {
  const headingId = useId();
  const Heading = `h${level}` as const;
  return (
    <div role="group" aria-labelledby={headingId} className={cx("flex min-w-0 flex-col gap-3", className)}>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <Heading id={headingId} className="title text-14 text-fg">
            {title}
          </Heading>
          {description !== undefined ? <p className="mt-0.5 max-w-measure text-13 text-pretty text-fg-muted">{description}</p> : null}
        </div>
        {actions !== undefined ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}
