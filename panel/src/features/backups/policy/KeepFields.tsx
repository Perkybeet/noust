import type { ReactNode } from "react";

import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";

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

/** How many scheduled backups a policy keeps on this server, and for how long: two optional limits. */
export function KeepFields({ words, count, days, onCount, onDays, countError, daysError, children }: KeepFieldsProps) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-5">
      <legend className="mb-3 text-13 font-medium text-fg">{words.legend}</legend>
      <div className="flex flex-wrap gap-4">
        <Field label={words.count} optional description={words.countHelp} error={countError} className="w-44">
          <Input mono inputMode="numeric" value={count} onValueChange={onCount} placeholder="7" />
        </Field>
        <Field label={words.days} optional description={words.daysHelp} error={daysError} className="w-44">
          <Input mono inputMode="numeric" value={days} onValueChange={onDays} placeholder="30" />
        </Field>
      </div>
      {children}
      <p className="text-12 text-fg-faint">{words.note}</p>
    </fieldset>
  );
}
