import { ToggleLeft, ToggleRight } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { cx } from "../../lib/cx";
import { ICONS } from "./icons";
import { REDUCED_MOTION, useMediaQuery } from "./useMediaQuery";

/** On, off, or on but not doing what it says ("enabled, but no channel"). */
export type FeatureStateValue = "on" | "off" | "problem";

export interface FeatureStateProps {
  state: FeatureStateValue;
  /**
   * The state in words, shown strong and in the state's colour. Defaults to "Enabled",
   * "Disabled" or "Enabled, with a problem"; pass the feature's own when the language needs
   * agreement ("Activadas") or the problem has a name ("Enabled, but no channel").
   */
  title?: ReactNode;
  /** What the state means for the operator, in a sentence or two. */
  children?: ReactNode;
  /** One action beside the title: the way to turn it on, or to fix the problem. */
  action?: ReactNode;
  className?: string;
}

interface Spec {
  frame: string;
  tone: string;
  glyph: "on" | "off" | "problem";
  word: PlainKey;
}

// The tinted-block recipe of DESIGN C-2 plus a state rail, so the three read apart at a glance
// and in greyscale: a switch drawn on, a switch drawn off, a warning triangle.
const SPEC: Record<FeatureStateValue, Spec> = {
  on: { frame: "border-ok-border bg-ok-soft state-rail-ok", tone: "text-ok", glyph: "on", word: "common.featureState.on" },
  off: { frame: "border-idle-border bg-idle-soft state-rail-idle", tone: "text-idle", glyph: "off", word: "common.featureState.off" },
  // Amber, not green: a feature that is on but cannot work is not running.
  problem: {
    frame: "border-warn-border bg-warn-soft state-rail-warn",
    tone: "text-warn",
    glyph: "problem",
    word: "common.featureState.problem",
  },
};

const GLYPH: Record<Spec["glyph"], typeof ToggleRight> = {
  on: ToggleRight,
  off: ToggleLeft,
  problem: ICONS.warning,
};

/**
 * Whether a feature is on, at the top of the place that configures it: notifications,
 * instant rollback, automatic updates, the build sandbox, scheduled backups. The state is a
 * colour, a glyph and a word at once, so it is read without reading the sentence under it.
 * Not a `Notice`: a notice is a message, this is a state.
 */
export function FeatureState({ state, title, children, action, className }: FeatureStateProps) {
  const t = useT();
  const reduced = useMediaQuery(REDUCED_MOTION);
  const spec = SPEC[state];
  const Glyph = GLYPH[spec.glyph];

  // A change of state pulses once, like a StatusPill; the first render is not a change.
  const [shown, setShown] = useState(state);
  const [changes, setChanges] = useState(0);
  if (shown !== state) {
    setShown(state);
    setChanges((n) => n + 1);
  }

  return (
    <div
      key={changes}
      data-feature-state=""
      data-state={state}
      className={cx(
        "flex min-w-0 items-start gap-3 rounded-card border px-4 py-3",
        spec.frame,
        changes > 0 && !reduced && "animate-pulse-once",
        className,
      )}
    >
      <Glyph aria-hidden="true" data-glyph={spec.glyph} className={cx("mt-0.5 size-icon-lg shrink-0", spec.tone)} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-h-6 flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <p className={cx("title text-14 text-pretty", spec.tone)}>{title ?? t(spec.word)}</p>
          {action !== undefined ? <div className="flex shrink-0 items-center">{action}</div> : null}
        </div>
        {children !== undefined ? <div className="max-w-measure text-13 text-pretty text-fg">{children}</div> : null}
      </div>
    </div>
  );
}
