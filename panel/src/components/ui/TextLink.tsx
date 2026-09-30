import { createLink } from "@tanstack/react-router";
import { forwardRef } from "react";
import type { AnchorHTMLAttributes } from "react";

import { cx } from "../../lib/cx";

/**
 * A link's size: `inline` takes the size of the sentence it is in; `ui` is the dense
 * interface size (13px), for a link that stands on its own (a card's, a row's, a notice's).
 */
export type TextLinkSize = "inline" | "ui";

const TEXT_LINK = "rounded-chip font-medium text-accent-fg hover:underline hover:underline-offset-2";

/**
 * The look of a link in running text, for a link the router does not build (an `<a>` to a
 * download, a `ServerLink`): the accent, underlined on hover, with the global focus ring.
 */
export function textLinkClassName(size: TextLinkSize = "inline", className?: string): string {
  return cx(TEXT_LINK, size === "ui" && "text-13", className);
}

interface AnchorProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  size?: TextLinkSize;
}

const Anchor = forwardRef<HTMLAnchorElement, AnchorProps>(function Anchor({ size = "inline", className, children, ...props }, ref) {
  return (
    <a ref={ref} {...props} className={textLinkClassName(size, className)}>
      {children}
    </a>
  );
});

/**
 * A link to another page of the console inside a sentence, a card or a notice: typed like the
 * router's `Link` (`to`, `params`, `search`), drawn in the accent. Not for an action (a
 * `Button`), nor for a page outside the console (`ExternalLink`), nor for a link that looks
 * like a button (`buttonClassName` on a `Link`).
 */
export const TextLink = createLink(Anchor);
