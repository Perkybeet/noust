import { Switch as BaseSwitch } from "@base-ui/react/switch";
import { useId } from "react";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface SwitchProps {
  /**
   * Being retired: a setting is a `Setting` row, which owns the label, the help and the risk, and
   * a switch on its own is a bare control with an `aria-label`. It stays, without a `@deprecated`
   * tag that would fail the lint of every page still using it, until those pages have moved; the
   * closing lane then removes it from the type.
   */
  label?: ReactNode;
  /** Being retired with `label`. */
  description?: ReactNode;
  /** Required when there is no visible label. */
  "aria-label"?: string;
  /** Named by something else on the page (the label of a `Setting` row). */
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
  checked?: boolean;
  defaultChecked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
  /**
   * The change is being applied. The switch keeps the value it had and draws its own arc on the
   * thumb, `aria-busy` says so to a screen reader (which is not told again), and a press does
   * nothing until it settles. Give it with `checked`: an uncontrolled switch cannot hold its value.
   */
  busy?: boolean;
  disabled?: boolean;
  name?: string;
  className?: string;
}

/** A setting that takes effect immediately. For a choice saved with a form, use Checkbox. */
export function Switch({
  label,
  description,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  "aria-describedby": ariaDescribedBy,
  checked,
  defaultChecked,
  onCheckedChange,
  busy = false,
  disabled = false,
  name,
  className,
}: SwitchProps) {
  const labelId = useId();
  const descriptionId = useId();
  const described = label !== undefined && description !== undefined;
  const control = (
    <BaseSwitch.Root
      {...(checked !== undefined ? { checked } : {})}
      {...(defaultChecked !== undefined ? { defaultChecked } : {})}
      {...(onCheckedChange
        ? {
            onCheckedChange: (next: boolean) => {
              // A change in flight is not asked twice.
              if (!busy) onCheckedChange(next);
            },
          }
        : {})}
      {...(ariaLabel !== undefined ? { "aria-label": ariaLabel } : {})}
      {...(described ? { "aria-labelledby": labelId, "aria-describedby": descriptionId } : {})}
      {...(!described && ariaLabelledBy !== undefined ? { "aria-labelledby": ariaLabelledBy } : {})}
      {...(!described && ariaDescribedBy !== undefined ? { "aria-describedby": ariaDescribedBy } : {})}
      {...(busy ? { "aria-busy": true } : {})}
      {...(name !== undefined ? { name } : {})}
      disabled={disabled}
      className={cx(
        "relative inline-flex h-5 w-8 shrink-0 cursor-pointer items-center rounded-pill border border-transparent bg-border-strong p-0.5",
        "transition-colors duration-(--duration-base) ease-out",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
        "data-checked:bg-accent data-disabled:cursor-not-allowed data-disabled:opacity-50 aria-busy:cursor-progress",
      )}
    >
      <BaseSwitch.Thumb
        className={cx(
          "flex size-3.5 items-center justify-center rounded-pill bg-on-accent shadow-raised",
          "transition-transform duration-(--duration-base) ease-out data-checked:translate-x-3",
        )}
      >
        {/* The arc is drawn on the thumb, so the switch does not change size or position while it
            works. It takes the accent, not the amber of other work in progress: the thumb is white
            in both themes, and amber on white is 1.9:1 in the dark one. */}
        {busy ? (
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" aria-hidden="true" data-slot="switch-arc" className="animate-spin text-accent">
            <path d="M8 2.25A5.75 5.75 0 1 1 2.25 8" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" />
          </svg>
        ) : null}
      </BaseSwitch.Thumb>
    </BaseSwitch.Root>
  );

  if (label === undefined) return control;
  return (
    <label
      className={cx(
        "flex cursor-pointer items-start justify-between gap-4 text-14 text-fg",
        disabled && "cursor-not-allowed text-fg-muted",
        className,
      )}
    >
      <span className="flex flex-col">
        <span id={labelId}>{label}</span>
        {/* Named apart from the label, as the Checkbox does: read as the description. */}
        {description !== undefined ? (
          <span id={descriptionId} className="text-13 text-fg-muted">
            {description}
          </span>
        ) : null}
      </span>
      <span className="mt-0.5 flex">{control}</span>
    </label>
  );
}
