import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";
import { useId } from "react";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface ChoiceCard<V extends string> {
  value: V;
  /** The option's name: the radio's accessible name. Already translated. */
  label: ReactNode;
  /** What choosing it means, in a sentence: read as the option's description. */
  description: ReactNode;
  /** A short note after the label: "Recommended". */
  badge?: string;
  /** The label is a system value that is not translated: a template's own name. */
  untranslated?: boolean;
}

export interface ChoiceCardsProps<V extends string> {
  /** Names the choice, on screen and for assistive technology. */
  legend: string;
  options: readonly ChoiceCard<V>[];
  value: V;
  onValueChange: (value: V) => void;
  className?: string;
}

/**
 * One of two to four exclusive choices, each with its consequence spelled out: a radio group
 * of cards, two to a row from 640px, one tab stop and the arrow keys between them. Each card is
 * a radio named by its label and described by its sentence, so the choice is read with what it
 * means. For a choice that needs no sentence, a `SegmentedControl` or a `Select`.
 */
export function ChoiceCards<V extends string>({ legend, options, value, onValueChange, className }: ChoiceCardsProps<V>) {
  const base = useId();
  const legendId = `${base}-legend`;
  return (
    <div className={cx("flex flex-col gap-2", className)}>
      <p id={legendId} className="text-13 font-medium text-fg">
        {legend}
      </p>
      <RadioGroup<V>
        aria-labelledby={legendId}
        value={value}
        onValueChange={(next: V) => {
          onValueChange(next);
        }}
        className="grid gap-2 sm:grid-cols-2"
      >
        {options.map((option) => {
          const labelId = `${base}-${option.value}-label`;
          const descriptionId = `${base}-${option.value}-description`;
          const chosen = option.value === value;
          return (
            <Radio.Root
              key={option.value}
              value={option.value}
              aria-labelledby={labelId}
              aria-describedby={descriptionId}
              className={cx(
                "flex cursor-pointer items-start gap-2.5 rounded-control border px-3 py-2.5 text-left",
                "transition-[background-color,border-color] duration-(--duration-fast) ease-out",
                chosen ? "border-accent bg-accent-soft" : "border-border bg-surface hover:bg-surface-hover",
              )}
            >
              <span
                aria-hidden="true"
                className={cx(
                  "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-pill border",
                  chosen ? "border-accent bg-accent" : "border-border-strong bg-surface",
                )}
              >
                <Radio.Indicator className="size-1.5 rounded-pill bg-on-accent" />
              </span>
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <span id={labelId} {...(option.untranslated === true ? { translate: "no" as const } : {})} className="text-13 font-medium text-fg">
                    {option.label}
                  </span>
                  {option.badge !== undefined ? <span className="text-12 text-fg-muted">{option.badge}</span> : null}
                </span>
                <span id={descriptionId} className="text-12 text-pretty text-fg-muted">
                  {option.description}
                </span>
              </span>
            </Radio.Root>
          );
        })}
      </RadioGroup>
    </div>
  );
}
