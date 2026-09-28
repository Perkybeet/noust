import { useMemo } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatDateTime, formatRelative, parseTimestamp, relativeRefreshMs } from "../../lib/format";
import { Tooltip } from "../ui/Tooltip";
import { useNow } from "./clock";

export interface RelativeTimeProps {
  /** An ISO string, a systemd timestamp, Unix seconds or milliseconds, or a Date. */
  value: string | number | Date | null | undefined;
  /** Shown when there is no value at all; "Never" in the active language by default. */
  fallback?: ReactNode;
  className?: string;
}

/**
 * When something happened, as "3m ago", kept current while it is on screen, with the exact
 * moment in a tooltip. A value the console cannot place in time (a systemd timestamp in a
 * named zone) is shown verbatim rather than guessed.
 */
export function RelativeTime({ value, fallback, className }: RelativeTimeProps) {
  // Subscribes to the language too: "3m ago" becomes "hace 3 min" on a switch.
  const t = useT();
  const date = useMemo(() => parseTimestamp(value), [value]);
  const now = useNow((current) => (date === null ? 3_600_000 : relativeRefreshMs(date, new Date(current))));

  if (date === null) {
    if (typeof value === "string" && value.trim() !== "") {
      return (
        <span translate="no" className={cx("mono text-[0.92em]", className)}>
          {value}
        </span>
      );
    }
    return <span className={cx("text-fg-faint", className)}>{fallback ?? t("time.never")}</span>;
  }

  return (
    <Tooltip content={formatDateTime(date, t.locale)}>
      <time dateTime={date.toISOString()} className={cx("whitespace-nowrap tabular-nums", className)}>
        {formatRelative(date, new Date(now), t.locale)}
      </time>
    </Tooltip>
  );
}
