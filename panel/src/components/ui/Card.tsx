import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface CardProps {
  title?: ReactNode;
  /** The icon of the object the card is about, drawn before the title (docs/DESIGN.md, 2.8): decorative, in the colour of the text beside it. */
  icon?: LucideIcon;
  /**
   * The state of the feature this card configures, a `StatusPill size="sm"`, right after the
   * title: "Instant rollback (*) On". It is not an action, so it never goes in `actions`; a
   * tinted block is never what says that something is on or off.
   */
  status?: ReactNode;
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
  /**
   * What the card shows is still being read and its body (and perhaps an action) is a skeleton.
   * The card is `aria-busy`, so a screen reader hears that it is not ready, whichever of its
   * parts hold the placeholders.
   */
  loading?: boolean;
  /**
   * The state of what the card holds, when it is not well: its ground and its edge take the
   * state's (a database server whose support is ending is amber, one whose support ended is red).
   * A card that is well stays stone, so the one that has colour is the one that is found. The
   * state is also said in words and a glyph inside the card, never by the ground alone.
   */
  tone?: "warning" | "failed";
  /**
   * A real logo (`LogoTile`) at the start of the header, outside the text so it spans the title and the
   * line under it. Only a logo goes here: the icon of an object is `icon`, and it stands bare.
   */
  leading?: ReactNode;
  className?: string;
}

const TONES = {
  warning: "border-warn-border bg-warn-soft",
  failed: "border-fail-border bg-fail-soft",
} as const;

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
  icon: Icon,
  status,
  description,
  actions,
  footer,
  children,
  level = 3,
  padding = "md",
  as: Element = "section",
  interactive = false,
  loading = false,
  tone,
  leading,
  className,
}: CardProps) {
  const Heading = `h${level}` as const;
  const hasHeader = title !== undefined || status !== undefined || actions !== undefined;
  const space = PADDING[padding];
  return (
    <Element
      {...(loading ? { "aria-busy": true } : {})}
      {...(tone !== undefined ? { "data-tone": tone } : {})}
      className={cx(
        "elev-seated flex min-w-0 flex-col rounded-card border",
        tone === undefined ? "border-border bg-surface" : TONES[tone],
        // A panel that is read does not move. One that opens something changes its tone, flat.
        interactive && "transition-colors duration-(--duration-press) ease-out hover:bg-surface-hover",
        className,
      )}
    >
      {hasHeader ? (
        <header className={cx("flex items-start justify-between gap-4", space.header)}>
          {leading}
          <div className="min-w-0 flex-1">
            {status !== undefined ? (
              <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                {Icon !== undefined ? <Icon aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" /> : null}
                {title !== undefined ? <Heading className="title text-14 text-fg">{title}</Heading> : null}
                {status}
              </div>
            ) : title !== undefined ? (
              Icon !== undefined ? (
                <div className="flex min-w-0 items-center gap-2">
                  <Icon aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
                  <Heading className="title text-14 text-fg">{title}</Heading>
                </div>
              ) : (
                <Heading className="title text-14 text-fg">{title}</Heading>
              )
            ) : null}
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
