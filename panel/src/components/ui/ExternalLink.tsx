import { ExternalLink as ExternalIcon } from "lucide-react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { isHttpUrl } from "../../lib/url";
import { buttonClassName } from "./Button";
import type { ButtonSize, ButtonVariant } from "./Button";

export interface ExternalLinkProps {
  /** A URL the server sent: anything but http(s) is never linked (see `unlinked`). */
  href: string | null | undefined;
  /** The link's text; omitted, the address itself in mono. */
  children?: ReactNode;
  /** Draw it as a button of this variant; omitted, it is a link in the accent. */
  button?: ButtonVariant;
  size?: ButtonSize;
  /**
   * Flows inside a sentence or a card: takes the surrounding text size and breaks a long
   * address anywhere, where a standalone link keeps its own size and truncates.
   */
  inline?: boolean;
  /** What a non-http(s) `href` shows: nothing, or its text unlinked. */
  unlinked?: "hide" | "text";
  /** A fuller name for assistive technology when the visible text needs its context. */
  label?: string;
  className?: string;
}

const LINK =
  "inline-flex max-w-full items-center gap-1 rounded-[4px] font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";

/**
 * A page outside the console (GitHub, a project's website, an address a recipe's note names),
 * opened in a new tab without handing it this window, so the console stays where it was.
 * Marked as leaving the console with the external glyph and in words for assistive
 * technology.
 */
export function ExternalLink({
  href,
  children,
  button,
  size = "md",
  inline = false,
  unlinked = "hide",
  label,
  className,
}: ExternalLinkProps) {
  const t = useT();
  if (href === null || href === undefined || !isHttpUrl(href)) {
    return unlinked === "text" && href ? <>{children ?? href}</> : null;
  }
  const opensInNewTab = t("common.externalLink.opensInNewTab");
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      {...(label !== undefined ? { "aria-label": `${label} ${opensInNewTab}` } : {})}
      className={button !== undefined ? buttonClassName(button, size, className) : cx(LINK, !inline && "w-fit text-13", className)}
    >
      <span
        translate={children === undefined ? "no" : undefined}
        className={cx("min-w-0", inline ? "break-all" : "truncate", children === undefined && "mono text-12")}
      >
        {children ?? href}
      </span>
      <ExternalIcon aria-hidden="true" className="size-3.5 shrink-0" />
      {label === undefined ? <span className="sr-only">{` ${opensInNewTab}`}</span> : null}
    </a>
  );
}
