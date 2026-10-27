import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";

import { cx } from "../../lib/cx";
import { SlidingIndicator } from "../ui/SlidingIndicator";

export interface SegmentedOption<V extends string> {
  value: V;
  label: string;
}

export interface SegmentedControlProps<V extends string> {
  /** Names the choice for assistive technology: "Time range". */
  label: string;
  options: readonly SegmentedOption<V>[];
  value: V;
  onValueChange: (value: V) => void;
  className?: string;
}

/**
 * One of a few mutually exclusive views of the same thing (a chart's time range). A radio
 * group: one tab stop, arrow keys move the choice. It keeps its width (`shrink-0`) but never asks
 * for more than its container has: past that its options wrap, because a pill wider than a phone
 * widens the whole page.
 */
export function SegmentedControl<V extends string>({ label, options, value, onValueChange, className }: SegmentedControlProps<V>) {
  return (
    <RadioGroup<V>
      aria-label={label}
      value={value}
      onValueChange={(next: V) => {
        onValueChange(next);
      }}
      className={cx(
        "elev-carved relative isolate inline-flex max-w-full shrink-0 flex-wrap items-center gap-0.5 rounded-card bg-bg-sunken p-0.5",
        className,
      )}
    >
      <SlidingIndicator className="rounded-control bg-surface" />
      {options.map((option) => (
        <Radio.Root
          key={option.value}
          value={option.value}
          className={cx(
            "relative inline-flex h-7 min-w-9 cursor-pointer items-center justify-center rounded-control px-2.5 text-12 font-medium text-fg-muted select-none",
            // The chosen one is the indicator under it; the others rise when the pointer is over them.
            option.value === value ? "text-fg" : "elev-lift hover:text-fg hover:bg-surface-raised",
            "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
          )}
        >
          {option.label}
        </Radio.Root>
      ))}
    </RadioGroup>
  );
}
