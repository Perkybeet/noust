import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { IconButton } from "./IconButton";
import { ICONS } from "./icons";

export type NoticeTone = "info" | "success" | "warning" | "error";

export interface NoticeProps {
  /**
   * The severity of the message. `info` is neutral and achromatic: information is not a state.
   * Defaults to `info`.
   */
  tone?: NoticeTone;
  /**
   * `inline` sits beside what it is about (a form, a section, a dialog); `banner` states the
   * condition of a whole page or server, between the header and the content. Defaults to
   * `inline`.
   */
  variant?: "inline" | "banner";
  /** One line: what is the case. */
  title?: ReactNode;
  /** What it means and what to do, in a sentence or two. */
  children?: ReactNode;
  /** At most one action, a small button or a link: "Renew", "Review the plan". */
  action?: ReactNode;
  /** Makes the notice dismissible. Leave it off for a condition that is still true. */
  onDismiss?: () => void;
  /**
   * Announces the notice: for the outcome of something the operator just did. An `error`
   * becomes an alert (assertive), anything else a status (polite). Off for notices that are
   * there when a page loads, or every page would talk over itself.
   */
  live?: boolean;
  className?: string;
}

const FRAME: Record<NoticeTone, string> = {
  info: "border-border bg-surface-raised",
  success: "border-ok-border bg-ok-soft",
  warning: "border-warn-border bg-warn-soft",
  error: "border-fail-border bg-fail-soft",
};

const GLYPH: Record<NoticeTone, string> = {
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
 * A persistent message next to what it concerns: a warning before an action, a saved result,
 * why something is locked. The severity is said three ways, a colour, the icon of that
 * severity and a word, and the content is in the text colours, so it reads on every ground.
 *
 * Not for the result of an action that is visible anyway (nothing), one that is not on screen
 * (a toast), a field's validation (Field), or a failure with the system's own output
 * (ErrorBlock).
 */
export function Notice({ tone = "info", variant = "inline", title, children, action, onDismiss, live = false, className }: NoticeProps) {
  const t = useT();
  const Icon = ICONS[tone];
  const role = live ? (tone === "error" ? "alert" : "status") : undefined;
  return (
    <div
      {...(role !== undefined ? { role } : {})}
      data-tone={tone}
      className={cx(
        "flex min-w-0 items-start gap-2.5 border text-13",
        variant === "banner" ? "rounded-card px-4 py-3" : "rounded-control px-3 py-2.5",
        FRAME[tone],
        className,
      )}
    >
      <Icon aria-hidden="true" className={cx("mt-0.5 size-icon-md shrink-0", GLYPH[tone])} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="sr-only">{`${t(WORD[tone])}:`} </span>
        {title !== undefined ? <p className="font-medium text-pretty text-fg">{title}</p> : null}
        {children !== undefined ? (
          <div className={cx("text-pretty", title !== undefined ? "text-fg-muted" : "text-fg")}>{children}</div>
        ) : null}
      </div>
      {action !== undefined ? <div className="flex shrink-0 items-center self-center">{action}</div> : null}
      {onDismiss !== undefined ? (
        <IconButton label={t("common.notice.dismiss")} icon={<ICONS.dismiss />} size="sm" onClick={onDismiss} className="-my-1 -mr-1.5" />
      ) : null}
    </div>
  );
}
