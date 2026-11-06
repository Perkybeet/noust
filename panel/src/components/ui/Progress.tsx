import { Meter as BaseMeter } from "@base-ui/react/meter";
import { Progress as BaseProgress } from "@base-ui/react/progress";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { StatusGlyph } from "./StatusPill";

export interface ProgressProps {
  /** Percent complete, 0-100; null when the total is not known yet. */
  value: number | null;
  /** What is progressing: "Uploading backup". */
  label: string;
  /** Show the percentage next to the label. */
  showValue?: boolean;
  /**
   * `inline`: the track alone with its figure after it on one line, and the label only for assistive technology,
   * for a row that already says what is progressing (a task in the task centre's panel and its pinned chips).
   */
  layout?: "stacked" | "inline";
  className?: string;
}

/** How far a task with a known end has got: an upload, a restore, a build step. Amber: work in progress. */
export function Progress({ value, label, showValue = true, layout = "stacked", className }: ProgressProps) {
  if (layout === "inline") {
    return (
      <BaseProgress.Root value={value} className={cx("flex min-w-0 items-center gap-2", className)}>
        <BaseProgress.Label className="sr-only">{label}</BaseProgress.Label>
        <BaseProgress.Track className="elev-carved-fine relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-pill bg-bg-sunken">
          <BaseProgress.Indicator
            className={cx(
              "block h-full rounded-pill bg-warn transition-[width] duration-(--duration-move) ease-(--ease-standard)",
              "data-indeterminate:w-2/5 data-indeterminate:animate-indeterminate",
              "motion-reduce:data-indeterminate:w-full motion-reduce:data-indeterminate:opacity-40",
            )}
          />
        </BaseProgress.Track>
        {showValue && value !== null ? <BaseProgress.Value className="mono w-10 shrink-0 text-right text-12 text-fg-muted tabular-nums" /> : null}
      </BaseProgress.Root>
    );
  }
  return (
    <BaseProgress.Root value={value} className={cx("flex min-w-0 flex-col gap-1.5", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <BaseProgress.Label className="truncate text-13 text-fg">{label}</BaseProgress.Label>
        {showValue && value !== null ? (
          <BaseProgress.Value className="mono text-12 text-fg-muted" />
        ) : null}
      </div>
      <BaseProgress.Track className="elev-carved-fine relative h-2 overflow-hidden rounded-pill bg-bg-sunken">
        <BaseProgress.Indicator
          className={cx(
            "block h-full rounded-pill bg-warn transition-[width] duration-(--duration-move) ease-(--ease-standard)",
            "data-indeterminate:w-2/5 data-indeterminate:animate-indeterminate",
            "motion-reduce:data-indeterminate:w-full motion-reduce:data-indeterminate:opacity-40",
          )}
        />
      </BaseProgress.Track>
    </BaseProgress.Root>
  );
}

export interface MeterProps {
  /** The measured amount, in the same unit as `max`. */
  value: number;
  max?: number;
  label: string;
  /** How the value reads aloud and on screen, e.g. "3.1 of 8 GB". Defaults to a percentage. */
  valueText?: string;
  /** Fractions of max at which the fill turns amber, then red. Usage is a state. */
  thresholds?: { warn: number; fail: number };
  size?: "sm" | "md";
  className?: string;
}

/**
 * A level within a known range: CPU, memory, disk. The fill is neutral until the level is a
 * problem, then takes the state colour and, beside the value, the state's glyph and a word
 * ("High", "Critical"): colour is never the only signal (WCAG 1.4.1). The value is always
 * printed. In the small size the word is kept for screen readers only.
 */
export function Meter({
  value,
  max = 100,
  label,
  valueText,
  thresholds = { warn: 0.75, fail: 0.9 },
  size = "md",
  className,
}: MeterProps) {
  const ratio = max > 0 ? value / max : 0;
  const level = ratio >= thresholds.fail ? "fail" : ratio >= thresholds.warn ? "warn" : "normal";
  const fill = { normal: "bg-il-stroke", warn: "bg-warn", fail: "bg-fail" }[level];
  const track = { normal: "bg-bg-sunken", warn: "bg-warn-soft", fail: "bg-fail-soft" }[level];
  const t = useT();
  const text = valueText ?? `${String(Math.round(ratio * 100))}%`;
  const levelWord = level === "fail" ? t("common.meter.critical") : level === "warn" ? t("common.meter.high") : null;
  return (
    <BaseMeter.Root
      value={value}
      max={max}
      aria-valuetext={text}
      className={cx("flex min-w-0 flex-col", size === "sm" ? "gap-1" : "gap-1.5", className)}
    >
      {/* The small meter is 96px wide in the top bar: with its value and its warning glyph beside it, "Memoria" does not fit
          with a 12px gap and loses its last letters exactly when the level is a problem. */}
      <div className={cx("flex items-baseline justify-between", size === "sm" ? "gap-1.5" : "gap-3")}>
        <BaseMeter.Label className={cx("truncate text-fg-muted", size === "sm" ? "text-12" : "text-13")}>
          {label}
        </BaseMeter.Label>
        <span className={cx("flex shrink-0 items-center gap-1", size === "sm" ? "text-12" : "text-13")}>
          {level !== "normal" ? (
            <StatusGlyph state={level === "fail" ? "failed" : "warning"} size={size === "sm" ? 10 : 12} className={level === "fail" ? "text-fail" : "text-warn"} />
          ) : null}
          {levelWord !== null ? (
            <span className={cx(size === "sm" ? "sr-only" : "font-medium", level === "fail" ? "text-fail" : "text-warn")}>{levelWord}</span>
          ) : null}
          <span className="mono text-fg">{text}</span>
        </span>
      </div>
      <BaseMeter.Track
        data-level={level}
        className={cx("elev-carved-fine relative overflow-hidden rounded-pill", track, size === "sm" ? "h-1.5" : "h-2")}
      >
        <BaseMeter.Indicator className={cx("block h-full rounded-pill transition-[width] duration-(--duration-move) ease-(--ease-standard)", fill)} />
      </BaseMeter.Track>
    </BaseMeter.Root>
  );
}
