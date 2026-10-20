import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import type { NoticeTone } from "./Notice";
import { ICONS } from "./icons";

export type InlineMessageTone = NoticeTone;

export interface InlineMessageProps {
  /**
   * The severity, said by the glyph's colour and shape and by a word for screen readers. A risk
   * that exists while an option is on is `warning`, the default.
   */
  tone?: InlineMessageTone;
  /** One sentence of 14 words or fewer: it is a line, not a paragraph. */
  children: ReactNode;
  /** After the sentence, on the same line: a link such as "What changes". */
  trailing?: ReactNode;
  /** Announces it, for a result the operator has just caused. Off for what is there on arrival. */
  live?: boolean;
  className?: string;
}

const GLYPH: Record<InlineMessageTone, string> = {
  info: "text-fg-muted",
  success: "text-ok",
  warning: "text-warn",
  error: "text-fail",
};

const WORD = {
  info: "common.notice.info",
  success: "common.notice.success",
  warning: "common.notice.warning",
  error: "common.notice.error",
} as const;

/**
 * One line with no frame and no ground: the risk of an option, the state of a field or a row, a
 * result that needs no more. The severity is the glyph's colour (12px) and the sentence is in the
 * text colour, so it reads on any surface (DESIGN C-8). A framed condition with a fix is a
 * `Notice`; a field's own validation stays `Field error`; the state of a page is a banner.
 */
export function InlineMessage({ tone = "warning", children, trailing, live = false, className }: InlineMessageProps) {
  const t = useT();
  const Icon = ICONS[tone];
  const role = live ? (tone === "error" ? "alert" : "status") : undefined;
  return (
    <p
      {...(role !== undefined ? { role } : {})}
      data-tone={tone}
      data-slot="inline-message"
      className={cx("flex min-w-0 items-start gap-1.5 text-12 text-fg", className)}
    >
      <Icon aria-hidden="true" className={cx("mt-0.5 size-icon-xs shrink-0", GLYPH[tone])} />
      <span className="min-w-0 flex-1 text-pretty">
        <span className="sr-only">{`${t(WORD[tone])}:`} </span>
        {children}
        {trailing !== undefined ? <> {trailing}</> : null}
      </span>
    </p>
  );
}
