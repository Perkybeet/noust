import { ExternalLink as ExternalIcon } from "lucide-react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { isHttpUrl } from "../../lib/url";
import { noteParts } from "./recipe";

const LINK =
  "inline-flex max-w-full items-center gap-1 rounded-[4px] font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";

/**
 * A page outside the console (a project's website, an address a recipe's note names), opened
 * in a new tab without handing it this window (`noopener`). Anything but an http(s) URL the
 * server sent is shown as text, never linked.
 */
export function ExternalLink({ href, children, className }: { href: string; children?: ReactNode; className?: string }) {
  const t = useT();
  if (!isHttpUrl(href)) return <>{children ?? href}</>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={cx(LINK, className)}>
      <span translate={children === undefined ? "no" : undefined} className={cx("min-w-0 break-all", children === undefined && "mono text-12")}>
        {children ?? href}
      </span>
      <ExternalIcon aria-hidden="true" className="size-3.5 shrink-0" />
      <span className="sr-only">{` ${t("newApp.external.newTab")}`}</span>
    </a>
  );
}

/**
 * A sentence the server wrote (a recipe's note), with every address in it a link: plain text
 * and anchors built as elements, never an HTML string.
 */
export function NoteText({ note }: { note: string }) {
  return (
    <>
      {noteParts(note).map((part, index) =>
        part.kind === "link" ? <ExternalLink key={index} href={part.href} /> : <span key={index}>{part.text}</span>,
      )}
    </>
  );
}
