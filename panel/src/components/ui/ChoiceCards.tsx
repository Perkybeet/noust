import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";
import { Check, ChevronDown } from "lucide-react";
import { useId, useState } from "react";
import type { ComponentType, ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { GroupError } from "./Field";

export interface ChoiceCard<V extends string> {
  value: V;
  /** The option's name: the radio's accessible name. Already translated. */
  label: ReactNode;
  /** What choosing it means, in one line: read as the option's description. */
  description: ReactNode;
  /**
   * The rest: what it asks for, what it changes, when not to choose it. Folded under the card
   * behind a "Details" button that is not part of the choice, so the card stays a title and a
   * line and whoever needs more asks for it. Not read as the option's description.
   */
  detail?: ReactNode;
  /** The icon of the option, drawn bare (a tile behind an icon is for a real logo). A component, so the kit sets its size. */
  icon?: ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  /** A short note after the label: "Recommended". */
  badge?: string;
  /** The label is a system value that is not translated: a template's own name. */
  untranslated?: boolean;
  /**
   * It cannot be chosen now, and its `description` says why (a server with no connection, a plan that lacks it): it
   * stays in the list, so that nobody wonders where it went, but it is not raised, because nothing says it can be
   * pressed, and the arrow keys go past it. An option that does not apply at all is left out, not turned off.
   */
  disabled?: boolean;
}

export interface ChoiceCardsProps<V extends string> {
  /** Names the choice, on screen and for assistive technology. */
  legend: string;
  options: readonly ChoiceCard<V>[];
  /**
   * The chosen one. `null` is nothing chosen yet, for a choice whose wrong answer is expensive (which server a
   * deployment goes to): no card is preselected, and a form that needs one says "Choose ..." through `error`.
   */
  value: V | null;
  onValueChange: (value: V) => void;
  /** One column (a list of roles) or two from 640px (a pair of choices). Defaults to two. */
  columns?: 1 | 2;
  /**
   * The choice is not valid ("Choose how deploys work"): the same glyph and sentence a `Field`
   * shows, tied to the group with `aria-describedby` and announced with it.
   */
  error?: string | null | undefined;
  className?: string;
}

/**
 * One of two to six exclusive choices, each a title and a line: a radio group of raised cards,
 * one tab stop and the arrow keys between them. The chosen one takes the accent as a ring and a
 * check, and every card rises under the pointer and sinks when pressed, like a button. Each card
 * is a radio named by its label and described by its line, so the choice is read with what it
 * means; what does not fit in a line is its `detail`, folded. For a choice that needs no
 * sentence, a `SegmentedControl` or a `Select`.
 */
export function ChoiceCards<V extends string>({ legend, options, value, onValueChange, columns = 2, error, className }: ChoiceCardsProps<V>) {
  const t = useT();
  const base = useId();
  const legendId = `${base}-legend`;
  const errorId = `${base}-error`;
  const invalid = error !== undefined && error !== null && error !== "";
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const toggle = (key: string): void => {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  return (
    <div className={cx("flex flex-col gap-2", className)}>
      <p id={legendId} className="text-13 font-semibold text-fg">
        {legend}
      </p>
      <RadioGroup<V | null>
        aria-labelledby={legendId}
        {...(invalid ? { "aria-invalid": true, "aria-describedby": errorId } : {})}
        // Nothing chosen is a value of the group, not an uncontrolled group: it never picks for the person.
        value={value}
        onValueChange={(next: V | null) => {
          if (next !== null) onValueChange(next);
        }}
        className={cx("grid gap-2", columns === 2 && "sm:grid-cols-2")}
      >
        {options.map((option) => {
          const labelId = `${base}-${option.value}-label`;
          const descriptionId = `${base}-${option.value}-description`;
          const detailId = `${base}-${option.value}-detail`;
          const chosen = option.value === value;
          const off = option.disabled === true;
          const hasDetail = option.detail !== undefined;
          const expanded = open.has(option.value);
          const Icon = option.icon;
          return (
            <div key={option.value} className="relative">
              <Radio.Root
                value={option.value}
                aria-labelledby={labelId}
                aria-describedby={descriptionId}
                disabled={off}
                className={cx(
                  "flex w-full items-start gap-3 rounded-card px-3.5 py-3 text-left outline-none",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
                  // Off is flat and carved-grey, not raised: the stone only rises where it can be pressed.
                  off ? "cursor-not-allowed border border-border bg-bg-sunken" : chosen ? "cursor-pointer elev-raised-chosen bg-accent-soft" : "cursor-pointer elev-raised bg-surface-raised",
                  hasDetail && "pb-10",
                )}
              >
                {Icon !== undefined ? <Icon aria-hidden className={cx("mt-0.5 size-icon-lg shrink-0", chosen ? "text-accent-fg" : "text-fg-muted")} /> : null}
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    <span id={labelId} {...(option.untranslated === true ? { translate: "no" as const } : {})} className={cx("text-14 font-semibold", off ? "text-fg-muted" : "text-fg")}>
                      {option.label}
                    </span>
                    {option.badge !== undefined ? <span className="text-12 text-fg-muted">{option.badge}</span> : null}
                  </span>
                  <span id={descriptionId} className="text-13 text-pretty text-fg-muted">
                    {option.description}
                  </span>
                  {hasDetail ? (
                    <span id={detailId} hidden={!expanded} className="mt-2 block text-13 text-pretty text-fg">
                      {option.detail}
                    </span>
                  ) : null}
                </span>
                <span
                  aria-hidden="true"
                  className={cx(
                    "flex size-5 shrink-0 items-center justify-center rounded-pill",
                    chosen ? "bg-accent text-on-accent" : "text-transparent",
                  )}
                >
                  <Radio.Indicator keepMounted className="flex">
                    <Check className="size-3.5" strokeWidth={3} />
                  </Radio.Indicator>
                </span>
              </Radio.Root>
              {hasDetail ? (
                // Beside the radio and not in it: asking for more is not choosing.
                <button
                  type="button"
                  aria-expanded={expanded}
                  aria-controls={detailId}
                  onClick={() => {
                    toggle(option.value);
                  }}
                  className={cx(
                    "absolute bottom-2 left-3 inline-flex h-7 cursor-pointer items-center gap-1 rounded-control px-2 text-12 font-medium text-accent-fg",
                    "transition-colors duration-(--duration-press) ease-out hover:bg-bg-sunken",
                    "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
                  )}
                >
                  {expanded ? t("common.choiceCards.hideDetails") : t("common.choiceCards.details")}
                  <ChevronDown aria-hidden="true" className={cx("size-icon-sm transition-transform duration-(--duration-fast) ease-(--ease-standard)", expanded && "rotate-180")} />
                </button>
              ) : null}
            </div>
          );
        })}
      </RadioGroup>
      {invalid ? <GroupError id={errorId}>{error}</GroupError> : null}
    </div>
  );
}
