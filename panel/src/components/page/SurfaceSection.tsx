import type { LucideIcon } from "lucide-react";
import { useId } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Card } from "../ui/Card";
import { HelpPopover } from "../ui/HelpPopover";

export interface SurfaceSectionProps {
  title: string;
  /** The icon of the object the section is about, before the title: decorative, in the colour of the text. */
  icon?: LucideIcon;
  /** One line on what the section holds: the state of the matter, never the rule. */
  description?: ReactNode;
  /**
   * The long rule (why a thing is kept, what it does to something else), one press away behind a "?" beside the
   * title. The page shows the state and this is the explanation, so a section never carries a paragraph.
   */
  help?: ReactNode;
  /** The state of what the section holds, a `StatusPill size="sm"`, right after the title. */
  status?: ReactNode;
  /** The section's own action, at the right of its head: one button, or a link that opens the thing. */
  actions?: ReactNode;
  /** What the section holds. Without it the section is its head alone (a link to somewhere else, with its line). */
  children?: ReactNode;
  /**
   * The content draws its own edges (a table, a list of rows): it goes from edge to edge under a hairline.
   * Otherwise it sits inside the slab with its padding.
   */
  flush?: boolean;
  /** Heading level: 2 directly under the page's title. */
  level?: 2 | 3;
  /** What it holds is still being read: the section is `aria-busy`. */
  loading?: boolean;
  className?: string;
}

/**
 * A section that is a slab: its head row says what it is (the object's icon, a title, one line, its own action and,
 * when the rule is long, a "?") and its content sits inside. It replaces a heading over loose paragraphs, which read
 * with the same weight as everything around them (owner item 121): a page of several of these says which part is
 * which before a word of it is read. It is the `Card`'s seated slab with a head the `Card` has no room for: the icon
 * spans the title and its line, and the "?" sits beside the title.
 */
export function SurfaceSection({ title, icon: Icon, description, help, status, actions, children, flush = false, level = 2, loading = false, className }: SurfaceSectionProps) {
  const t = useT();
  const headingId = useId();
  const Heading = `h${level}` as const;
  return (
    <Card as="section" padding="none" labelledBy={headingId} loading={loading} className={cx("overflow-hidden", className)}>
      <div data-slot="surface-section">
        <header className={cx("flex flex-wrap items-start justify-between gap-x-4 gap-y-3 px-5 pt-4", children !== undefined ? "pb-3" : "pb-4")}>
          <div className="flex min-w-0 flex-1 basis-48 items-start gap-3">
            {Icon !== undefined ? <Icon aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" /> : null}
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                <Heading id={headingId} className="title text-16 text-fg">
                  {title}
                </Heading>
                {status}
                {help !== undefined ? (
                  <HelpPopover label={t("common.help.about", { name: title })} title={title}>
                    <div className="text-13 text-pretty text-fg-muted">{help}</div>
                  </HelpPopover>
                ) : null}
              </div>
              {description !== undefined ? <p className="mt-0.5 max-w-measure text-13 text-pretty text-fg-muted">{description}</p> : null}
            </div>
          </div>
          {actions !== undefined ? <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
        </header>
        {children !== undefined ? <div className={cx("min-w-0", flush ? "border-t border-border" : "px-5 pb-5")}>{children}</div> : null}
      </div>
    </Card>
  );
}
