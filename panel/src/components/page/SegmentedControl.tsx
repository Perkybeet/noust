import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import { SlidingIndicator } from "../ui/SlidingIndicator";

export interface SegmentedOption<V extends string> {
  value: V;
  label: string;
  /** A drawing of what the option is, before its name: decoration (`aria-hidden`), in the colour of the text. */
  icon?: ReactNode;
  /** How many things the option holds (the applications of a server), after its name, in the quiet colour; part of its accessible name. */
  count?: number;
}

export interface SegmentedControlProps<V extends string> {
  /** Names the choice for assistive technology: "Time range". */
  label: string;
  options: readonly SegmentedOption<V>[];
  value: V;
  onValueChange: (value: V) => void;
  /** The options share the width of the container equally, the drawing above the name (the navigation's choice of bar). */
  fill?: boolean;
  className?: string;
}

/**
 * One of a few mutually exclusive views of the same thing (a chart's time range). A radio
 * group: one tab stop, arrow keys move the choice. It keeps its width (`shrink-0`) but never asks
 * for more than its container has: past that its options wrap, because a pill wider than a phone
 * widens the whole page.
 */
export function SegmentedControl<V extends string>({ label, options, value, onValueChange, fill = false, className }: SegmentedControlProps<V>) {
  return (
    <RadioGroup<V>
      aria-label={label}
      value={value}
      onValueChange={(next: V) => {
        onValueChange(next);
      }}
      className={cx(
        "elev-carved relative isolate inline-flex max-w-full shrink-0 flex-wrap items-center gap-0.5 rounded-card bg-bg-sunken p-0.5",
        fill && "flex w-full flex-nowrap",
        className,
      )}
    >
      <SlidingIndicator className="rounded-control bg-surface" />
      {options.map((option) => (
        <Radio.Root
          key={option.value}
          value={option.value}
          className={cx(
            "relative inline-flex min-w-9 cursor-pointer items-center justify-center rounded-control text-12 font-medium text-fg-muted select-none",
            fill ? "min-h-11 min-w-0 flex-1 flex-col gap-0.5 px-1 py-1" : "h-7 px-2.5",
            // The chosen one is the indicator under it; the others rise when the pointer is over them.
            option.value === value ? "text-fg" : "elev-lift hover:text-fg hover:bg-surface-raised",
            "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
          )}
        >
          {option.icon !== undefined ? (
            <span aria-hidden="true" className={cx("flex shrink-0", option.value === value && "text-accent-fg")}>
              {option.icon}
            </span>
          ) : null}
          <span className={fill ? "max-w-full truncate" : undefined}>{option.label}</span>
          {option.count !== undefined ? (
            <>
              {" "}
              <span className="mono ml-1 text-fg-faint tabular-nums">{option.count}</span>
            </>
          ) : null}
        </Radio.Root>
      ))}
    </RadioGroup>
  );
}
