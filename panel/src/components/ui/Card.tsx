import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface CardProps {
  title?: ReactNode;
  description?: ReactNode;
  /** Controls aligned with the title: a button, a menu, a link. */
  actions?: ReactNode;
  footer?: ReactNode;
  children?: ReactNode;
  /** Heading level of the title, to keep the page outline correct. */
  level?: 2 | 3 | 4;
  /**
   * The body's padding: `md` (20px) for a panel, `sm` (16px) for a compact one in a grid or a
   * list of cards, `none` for content that draws its own rows (a table, a divided list), edge
   * to edge.
   */
  padding?: "none" | "sm" | "md";
  /** The element: `section` for a panel with a title, `div` or `li` for a card in a list. */
  as?: "section" | "div" | "li" | "article";
  /** For a card that is itself a target (a whole-card link): its border answers the pointer. */
  interactive?: boolean;
  className?: string;
}

const PADDING = {
  md: { alone: "p-5", underHeader: "px-5 pb-5", header: "px-5 pt-4 pb-3", footer: "px-5 py-3" },
  sm: { alone: "p-4", underHeader: "px-4 pb-4", header: "px-4 pt-3 pb-2", footer: "px-4 py-2.5" },
  none: { alone: "", underHeader: "", header: "px-5 pt-4 pb-3", footer: "px-5 py-3" },
} as const;

/**
 * A bounded surface that groups one subject. Not a decoration: use one only to group. Every
 * panel of the console is this component; a feature never writes `rounded-card border
 * bg-surface` by hand (docs/DESIGN.md, "Card").
 */
export function Card({
  title,
  description,
  actions,
  footer,
  children,
  level = 3,
  padding = "md",
  as: Element = "section",
  interactive = false,
  className,
}: CardProps) {
  const Heading = `h${level}` as const;
  const hasHeader = title !== undefined || actions !== undefined;
  const space = PADDING[padding];
  return (
    <Element
      className={cx(
        "flex min-w-0 flex-col rounded-card border border-border bg-surface shadow-raised",
        interactive && "transition-colors duration-(--duration-fast) ease-out hover:border-border-strong",
        className,
      )}
    >
      {hasHeader ? (
        <header className={cx("flex items-start justify-between gap-4", space.header)}>
          <div className="min-w-0">
            {title !== undefined ? <Heading className="title text-14 text-fg">{title}</Heading> : null}
            {description !== undefined ? <p className="mt-0.5 text-13 text-pretty text-fg-muted">{description}</p> : null}
          </div>
          {actions !== undefined ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      {children !== undefined ? (
        <div
          className={cx(
            "min-w-0 flex-1",
            hasHeader ? space.underHeader : space.alone,
            padding === "none" && hasHeader && "border-t border-border",
          )}
        >
          {children}
        </div>
      ) : null}
      {footer !== undefined ? (
        <footer className={cx("flex items-center justify-end gap-2 rounded-b-card border-t border-border bg-bg-sunken", space.footer)}>
          {footer}
        </footer>
      ) : null}
    </Element>
  );
}
