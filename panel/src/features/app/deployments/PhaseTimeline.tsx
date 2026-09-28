import { Check, Minus } from "lucide-react";
import type { ReactNode } from "react";

import { StatusGlyph } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { formatDateTime, formatDuration } from "../../../lib/format";
import type { Outcome, PhaseKey, PhaseState, PhaseView } from "./phases";

/** A phase's label, translated, keyed by its `PhaseKey` (phases.ts stays in English). */
export function phaseLabel(t: T, key: PhaseKey): string {
  switch (key) {
    case "fetch":
      return t("appPages.deployments.phases.fetch.label");
    case "install":
      return t("appPages.deployments.phases.install.label");
    case "build":
      return t("appPages.deployments.phases.build.label");
    case "activate":
      return t("appPages.deployments.phases.activate.label");
    case "health":
      return t("appPages.deployments.phases.health.label");
  }
}

/** What a phase is doing while it runs, translated, keyed by its `PhaseKey`. */
export function phaseDoing(t: T, key: PhaseKey): string {
  switch (key) {
    case "fetch":
      return t("appPages.deployments.phases.fetch.doing");
    case "install":
      return t("appPages.deployments.phases.install.doing");
    case "build":
      return t("appPages.deployments.phases.build.doing");
    case "activate":
      return t("appPages.deployments.phases.activate.doing");
    case "health":
      return t("appPages.deployments.phases.health.doing");
  }
}

interface NodeLook {
  node: string;
  glyph: ReactNode;
  word: string;
}

function look(t: T, state: PhaseState, afterFailure: boolean): NodeLook {
  switch (state) {
    case "done":
      return { node: "border-transparent bg-ok-soft text-ok", glyph: <Check aria-hidden="true" className="size-4" strokeWidth={2.5} />, word: t("appPages.deployments.phaseTimeline.done") };
    case "running":
      return { node: "border-transparent bg-warn-soft text-warn", glyph: <StatusGlyph state="deploying" size={16} />, word: t("appPages.deployments.phaseTimeline.inProgress") };
    case "failed":
      return { node: "border-transparent bg-fail-soft text-fail", glyph: <StatusGlyph state="failed" size={16} />, word: t("appPages.deployments.phaseTimeline.failed") };
    case "pending":
      return { node: "border-border bg-surface text-idle", glyph: <StatusGlyph state="stopped" size={14} />, word: t("appPages.deployments.phaseTimeline.waiting") };
    case "unrecorded":
      return {
        node: "border-dashed border-border-strong bg-surface text-fg-faint",
        glyph: <Minus aria-hidden="true" className="size-3.5" />,
        word: afterFailure ? t("appPages.deployments.phaseTimeline.notReached") : t("appPages.deployments.phaseTimeline.notInLog"),
      };
    case "not_applicable":
      return {
        node: "border-dashed border-border bg-surface text-fg-faint",
        glyph: <Minus aria-hidden="true" className="size-3.5" />,
        word: t("appPages.deployments.phaseTimeline.notApplicable"),
      };
  }
}

const REACHED: ReadonlySet<PhaseState> = new Set(["done", "running", "failed"]);

export interface PhaseTimelineProps {
  phases: readonly PhaseView[];
  outcome: Outcome;
  className?: string;
}

/**
 * Fetch, install, build, activate, health: where a deploy is, or where it stopped, with how
 * long each phase took. Not a live region; the page announces phase changes once each.
 */
export function PhaseTimeline({ phases, outcome, className }: PhaseTimelineProps) {
  const t = useT();
  const failedAt = phases.findIndex((phase) => phase.state === "failed");
  return (
    <ol aria-label={t("appPages.deployments.phaseTimeline.ariaLabel")} className={cx("grid grid-cols-5", className)}>
      {phases.map((phase, index) => {
        const afterFailure = outcome === "failed" && failedAt !== -1 && index > failedAt;
        const { node, glyph, word } = look(t, phase.state, afterFailure);
        const next = phases[index + 1];
        // The logs stamp whole seconds, so anything shorter reads as under one.
        const duration = phase.seconds !== null && REACHED.has(phase.state) ? (phase.seconds < 1 ? "<1s" : formatDuration(phase.seconds, t.locale)) : null;
        return (
          <li key={phase.key} data-phase={phase.key} data-state={phase.state} className="relative flex min-w-0 flex-col items-center gap-2 text-center">
            {next ? (
              <span
                aria-hidden="true"
                className={cx(
                  "absolute top-[15px] left-[calc(50%+1.25rem)] w-[calc(100%-2.5rem)]",
                  REACHED.has(next.state) ? "h-0.5 rounded-pill bg-border-strong" : "border-t-2 border-dashed border-border",
                )}
              />
            ) : null}
            <span
              className={cx("relative flex size-8 shrink-0 items-center justify-center rounded-pill border", node)}
              title={phase.startedAt ? t("appPages.deployments.phaseTimeline.startedAt", { time: formatDateTime(phase.startedAt, t.locale) }) : undefined}
            >
              {glyph}
            </span>
            <span className="flex min-w-0 flex-col items-center gap-0.5">
              <span className={cx("text-13 font-medium", phase.state === "unrecorded" || phase.state === "pending" || phase.state === "not_applicable" ? "text-fg-muted" : "text-fg")}>
                {phaseLabel(t, phase.key)}
              </span>
              {duration !== null ? (
                <span className="mono text-12 text-fg-muted">
                  <span className="sr-only">{`${word}, `}</span>
                  {duration}
                </span>
              ) : (
                <span className="text-12 text-fg-faint">{word}</span>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
