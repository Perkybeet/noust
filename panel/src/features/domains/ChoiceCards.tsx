import { useId } from "react";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

export interface ChoiceCard<V extends string> {
  value: V;
  label: ReactNode;
  description: ReactNode;
  /** Sets the label as a system value that is not translated: a template's own name. */
  untranslated?: boolean;
}

export interface ChoiceCardsProps<V extends string> {
  /** The group's name, said by its legend. */
  legend: string;
  choices: readonly ChoiceCard<V>[];
  value: V;
  onChange: (value: V) => void;
}

/**
 * @deprecated Use the kit's `ChoiceCards` (`components/ui/ChoiceCards`), which names each
 * radio by its label and describes it by its sentence. Kept only for the token and account
 * dialogs, being rebuilt, whose tests still read the sentence in the radio's name; delete it
 * once they move.
 */
export function ChoiceCards<V extends string>({ legend, choices, value, onChange }: ChoiceCardsProps<V>) {
  const name = useId();
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1.5 text-13 font-medium text-fg">{legend}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {choices.map((choice) => (
          // eslint-disable-next-line no-restricted-syntax -- design-exception: raw-label a radio card: the whole card labels its own native radio, which Field cannot wrap
          <label
            key={choice.value}
            className={cx(
              "flex cursor-pointer items-start gap-2.5 rounded-control border px-3 py-2.5",
              "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-1 has-[:focus-visible]:outline-focus",
              value === choice.value ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-hover",
            )}
          >
            <input
              type="radio"
              name={name}
              value={choice.value}
              checked={value === choice.value}
              onChange={() => onChange(choice.value)}
              className="mt-0.5 size-4 shrink-0 accent-accent"
            />
            <span className="flex min-w-0 flex-col gap-0.5">
              <span {...(choice.untranslated === true ? { translate: "no" as const } : {})} className="text-13 font-medium text-fg">
                {choice.label}
              </span>
              <span className="text-12 text-pretty text-fg-muted">{choice.description}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
