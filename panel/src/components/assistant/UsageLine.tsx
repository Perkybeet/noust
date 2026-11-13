import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { HelpPopover } from "../ui/HelpPopover";
import { InlineMessage } from "../ui/InlineMessage";
import { Meter } from "../ui/Progress";

export interface UsageReading {
  id: string;
  /** What it counts ("What it has read"). */
  label: string;
  used: number;
  /** The limit, always a number: a limit is never "none". */
  cap: number;
  /** The figure in words, already formatted ("120 k of 400 k"). */
  valueText: string;
  /** Shown only when it is near its limit (the steps of a turn): it is noise until then. */
  onlyWhenNear?: boolean;
}

export const NEAR = 0.8;

export interface UsageLineProps {
  readings: readonly UsageReading[];
  /** What the "?" lists besides the nearest limit: every limit, with the figure. */
  details?: readonly string[];
  /** Notes about what is not counted (no price, an estimate). */
  notes?: readonly ReactNode[];
  /** The link that changes the limits, for who may; whoever may not is told who does. */
  change?: ReactNode;
}

function ratio(reading: UsageReading): number {
  return reading.cap > 0 ? reading.used / reading.cap : 0;
}

/** The reading that is nearest to its limit among those worth showing, or null when there is none. */
export function tightestReading(readings: readonly UsageReading[]): UsageReading | null {
  const shown = readings.filter((reading) => reading.onlyWhenNear !== true || ratio(reading) >= NEAR);
  return shown.reduce<UsageReading | null>((worst, reading) => (worst === null || ratio(reading) > ratio(worst) ? reading : worst), null);
}

/**
 * How much of its limits the conversation on screen has used, without crowding the box where a question is typed: one slim meter
 * for the limit that is nearest to being reached, its figure in words, a sentence only when it is near (80 %) and the others behind
 * the "?". The meter turns amber and red by itself and adds the glyph and the word, so it never says a thing by colour alone.
 */
export function UsageLine({ readings, details = [], notes = [], change }: UsageLineProps) {
  const t = useT();
  const worst = tightestReading(readings);
  if (worst === null) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-end gap-2">
        <Meter size="sm" className="min-w-0 flex-1" label={worst.label} value={Math.min(worst.used, worst.cap)} max={worst.cap} valueText={worst.valueText} thresholds={{ warn: NEAR, fail: 1 }} />
        <HelpPopover label={t("assistant.usage.help")} title={t("assistant.usage.helpTitle")}>
          <div className="flex flex-col gap-2 text-13 text-fg-muted">
            <p>{t("assistant.usage.helpBody")}</p>
            {details.length > 0 ? (
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {details.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : null}
            {notes.map((note, index) => (
              <p key={`note${String(index)}`}>{note}</p>
            ))}
            {change ?? <p>{t("assistant.usage.changeIsOthers")}</p>}
          </div>
        </HelpPopover>
      </div>
      {ratio(worst) >= NEAR && ratio(worst) < 1 ? <InlineMessage tone="warning">{t("assistant.usage.near", { value: worst.valueText })}</InlineMessage> : null}
    </div>
  );
}
