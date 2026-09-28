import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";

import { buttonClassName } from "../../../components/ui/Button";
import type { ButtonSize, ButtonVariant } from "../../../components/ui/Button";
import { cx } from "../../../lib/cx";
import { isHttpUrl } from "../../../lib/url";

export interface ExternalAnchorProps {
  /** A URL the server sent: anything but http(s) is shown as text, never linked. */
  href: string | null | undefined;
  children: ReactNode;
  /** Draw it as a button of this variant; omitted, it is an inline link in the accent. */
  button?: ButtonVariant;
  size?: ButtonSize;
  /** A fuller name for assistive technology when the visible text needs its context. */
  label?: string;
  className?: string;
}

const LINK =
  "inline-flex w-fit max-w-full items-center gap-1 rounded-[4px] text-13 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";

/**
 * A page on GitHub, opened in a new tab so the console stays where it was. Marked as leaving
 * the console with the external glyph and in words for assistive technology.
 */
export function ExternalAnchor({ href, children, button, size = "md", label, className }: ExternalAnchorProps) {
  if (href === null || href === undefined || !isHttpUrl(href)) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      {...(label !== undefined ? { "aria-label": `${label} (opens in a new tab)` } : {})}
      className={button !== undefined ? buttonClassName(button, size, className) : cx(LINK, className)}
    >
      <span className="min-w-0 truncate">{children}</span>
      <ExternalLink aria-hidden="true" className="size-3.5 shrink-0" />
      {label === undefined ? <span className="sr-only"> (opens in a new tab)</span> : null}
    </a>
  );
}
