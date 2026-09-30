import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { IconButton } from "../ui/IconButton";
import { ICONS } from "../ui/icons";
import { Notice } from "../ui/Notice";
import { Spinner } from "../ui/Spinner";
import { StatusGlyph } from "../ui/StatusPill";
import { ErrorBlock } from "./QueryState";

export type JobState = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface JobProgressProps {
  state: JobState;
  /**
   * The sentence for the state: while it runs, the verb and the object ("Deploying
   * shop.example.com"); once it ends, the outcome ("The certificate covers every domain",
   * "The deploy failed").
   */
  title: string;
  /** The job's current step in the system's own words, shown verbatim in mono. */
  step?: string | null;
  /** A line of context under the title, such as where the log will appear. */
  description?: ReactNode;
  /** A failure's cause, anything describeError reads; shown verbatim. */
  error?: unknown;
  /** The fix for a failure when the backend sent none. */
  hint?: string;
  /** One follow-up: "View log", "Open the application". */
  action?: ReactNode;
  /** Lets a finished job be put away. A running one cannot be. */
  onDismiss?: () => void;
  /**
   * Announces the job: its start and success politely, its failure assertively. Turn it off
   * where another channel (a toast) already says the outcome: an event is said once.
   */
  announce?: boolean;
  className?: string;
}

/**
 * The one way the console shows a job the operator just started, where they started it (an
 * application's header, a domain's tab, the wizard's last step): waiting, running with its
 * current step, done, or failed with the system's words verbatim. The history of every job is
 * the Activity page's; this is the one in hand.
 */
export function JobProgress({
  state,
  title,
  step,
  description,
  error,
  hint,
  action,
  onDismiss,
  announce = true,
  className,
}: JobProgressProps) {
  const t = useT();

  if (state === "failed") {
    return (
      <div className={cx("relative min-w-0", className)}>
        <ErrorBlock
          live={announce}
          error={error ?? { detail: t("common.jobProgress.noReason") }}
          title={title}
          {...(hint !== undefined ? { hint } : {})}
          {...(action !== undefined ? { action } : {})}
          {...(onDismiss !== undefined ? { className: "pr-12" } : {})}
        />
        {onDismiss !== undefined ? (
          <IconButton
            label={t("common.jobProgress.dismiss")}
            icon={<ICONS.dismiss />}
            size="sm"
            onClick={onDismiss}
            className="absolute top-2.5 right-2.5"
          />
        ) : null}
      </div>
    );
  }

  if (state === "succeeded" || state === "cancelled") {
    return (
      <Notice
        tone={state === "succeeded" ? "success" : "info"}
        title={title}
        live={announce}
        {...(action !== undefined ? { action } : {})}
        {...(onDismiss !== undefined ? { onDismiss } : {})}
        {...(className !== undefined ? { className } : {})}
      >
        {state === "cancelled" ? t("common.jobProgress.cancelled") : description}
      </Notice>
    );
  }

  const running = state === "running";
  return (
    <div
      {...(announce ? { role: "status" } : {})}
      className={cx(
        "flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 rounded-control border border-border bg-surface px-3 py-2 text-13 shadow-raised",
        className,
      )}
    >
      {running ? <Spinner size={14} className="text-warn" /> : <StatusGlyph state="queued" size={14} className="text-warn" />}
      <span className="font-medium text-fg">{title}</span>
      {running && step !== undefined && step !== null && step !== "" ? (
        <code translate="no" className="min-w-0 truncate text-12 text-fg-muted" title={step}>
          {step}
        </code>
      ) : null}
      {!running ? <span className="text-fg-muted">{t("common.jobProgress.queued")}</span> : null}
      {description !== undefined ? <span className="basis-full text-12 text-fg-muted">{description}</span> : null}
      {action !== undefined ? <span className="ml-auto flex items-center">{action}</span> : null}
    </div>
  );
}
