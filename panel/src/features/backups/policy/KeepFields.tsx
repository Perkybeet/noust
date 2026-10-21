import type { ReactNode } from "react";

import { useT } from "../../../i18n";
import { LimitField } from "./LimitField";

export interface KeepWords {
  legend: string;
  count: string;
  countHelp: string;
  days: string;
  daysHelp: string;
  note: string;
}

export interface KeepFieldsProps {
  words: KeepWords;
  count: string;
  days: string;
  onCount: (value: string) => void;
  onDays: (value: string) => void;
  countError?: string | undefined;
  daysError?: string | undefined;
  /** Extra content under the fields, before the note. */
  children?: ReactNode;
}

/** How many scheduled backups a schedule keeps on this server, and for how long: two optional limits, each a box that is ticked or not. */
export function KeepFields({ words, count, days, onCount, onDays, countError, daysError, children }: KeepFieldsProps) {
  const t = useT();
  return (
    <fieldset className="flex min-w-0 flex-col gap-4">
      <legend className="mb-3 text-13 font-medium text-fg">{words.legend}</legend>
      <LimitField label={words.count} help={words.countHelp} valueLabel={t("backups.limit.count")} value={count} onChange={onCount} error={countError} suggestion="7" />
      <LimitField label={words.days} help={words.daysHelp} valueLabel={t("backups.limit.days")} value={days} onChange={onDays} error={daysError} suggestion="30" />
      {children}
      <p className="text-12 text-fg-faint">{words.note}</p>
    </fieldset>
  );
}
