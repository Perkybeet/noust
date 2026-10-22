import { useRef, useState } from "react";

import { Checkbox } from "../../../components/ui/Checkbox";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";

export interface LimitFieldProps {
  /** What the limit does, as its box says it: "Keep only the newest dumps". */
  label: string;
  /** What it does, and what there is without it: no limit is a state the box shows, never an empty field. */
  help: string;
  /** What the number counts; the name of its box once the limit is on. */
  valueLabel: string;
  value: string;
  onChange: (value: string) => void;
  error?: string | undefined;
  /** The number it starts with when the box is ticked and there is none to go back to. */
  suggestion: string;
  suffix?: string | undefined;
  inputMode?: "numeric" | "decimal";
}

/**
 * One optional limit of a schedule's retention: a box that is ticked when there is a limit, and the
 * number it reveals. The schedule stores the limit as a number or as nothing; the box is how the page
 * says which, so an empty field never has to mean "no limit" (SW-7). Switching it off keeps what was
 * typed, and switching it on again brings it back.
 */
export function LimitField({ label, help, valueLabel, value, onChange, error, suggestion, suffix, inputMode = "numeric" }: LimitFieldProps) {
  const [asked, setAsked] = useState(false);
  const kept = useRef("");
  // On while there is a number, and while its box is being filled in: a number wiped out is not a limit switched off.
  const on = asked || value.trim() !== "";
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Checkbox
        label={label}
        description={help}
        checked={on}
        onCheckedChange={(next) => {
          if (next) {
            setAsked(true);
            onChange(kept.current !== "" ? kept.current : suggestion);
          } else {
            kept.current = value;
            setAsked(false);
            onChange("");
          }
        }}
      />
      {on ? (
        <div className="pl-6">
          <Field label={valueLabel} error={error} className="w-44">
            <Input
              mono
              inputMode={inputMode}
              value={value}
              onValueChange={(next: string) => {
                // Wiping the number out to type another one leaves the limit on.
                setAsked(true);
                onChange(next);
              }}
              {...(suffix !== undefined ? { suffix } : {})}
            />
          </Field>
        </div>
      ) : null}
    </div>
  );
}
