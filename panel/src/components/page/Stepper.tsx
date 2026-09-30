import { Check } from "lucide-react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";

export interface WizardStep {
  id: string;
  /** Already translated: "Source", "Address". */
  label: string;
}

export interface StepperProps {
  steps: readonly WizardStep[];
  /** The id of the step on screen. Those before it are done, those after it not started. */
  current: string;
  /** Vertical beside a page's step; horizontal on top of a dialog's (and on a phone). */
  orientation?: "vertical" | "horizontal";
  /** Going back to a done step. Steps not reached yet are never links. */
  onSelect?: (id: string) => void;
  className?: string;
}

type StepState = "done" | "current" | "pending";

const SENTENCE = { done: "common.stepper.done", current: "common.stepper.current", pending: "common.stepper.pending" } as const;

function Marker({ state, number }: { state: StepState; number: number }) {
  return (
    <span
      aria-hidden="true"
      className={cx(
        "flex size-6 shrink-0 items-center justify-center rounded-pill border text-12 font-medium tabular-nums",
        state === "done" && "border-accent bg-accent text-on-accent",
        state === "current" && "border-accent bg-surface text-accent-fg ring-2 ring-accent-soft",
        state === "pending" && "border-border-strong bg-surface text-fg-muted",
      )}
    >
      {state === "done" ? <Check className="size-icon-sm" strokeWidth={3} /> : number}
    </span>
  );
}

/**
 * The one stepper: numbered, each step done, current or not started, said in words for screen
 * readers ("Source: done"). It never repeats "Step 2 of 5" in text: the list says it.
 */
export function Stepper({ steps, current, orientation = "vertical", onSelect, className }: StepperProps) {
  const t = useT();
  const at = Math.max(0, steps.findIndex((step) => step.id === current));
  const vertical = orientation === "vertical";
  return (
    <ol
      aria-label={t("common.stepper.label")}
      data-orientation={orientation}
      className={cx(vertical ? "flex flex-col" : "flex items-center gap-2", className)}
    >
      {steps.map((step, index) => {
        const state: StepState = index < at ? "done" : index === at ? "current" : "pending";
        const words = <span className="sr-only">{t(SENTENCE[state], { label: step.label })}</span>;
        const face = (
          <>
            <Marker state={state} number={index + 1} />
            <span
              aria-hidden="true"
              className={cx(
                "truncate text-13",
                state === "current" ? "font-medium text-fg" : "text-fg-muted",
                !vertical && state !== "current" && "max-sm:sr-only",
              )}
            >
              {step.label}
            </span>
            {words}
          </>
        );
        const row = "flex min-w-0 items-center gap-2.5";
        return (
          <li
            key={step.id}
            {...(state === "current" ? { "aria-current": "step" as const } : {})}
            className={cx(vertical ? "relative flex min-h-10 items-center" : "flex min-w-0 items-center gap-2", !vertical && index < steps.length - 1 && "flex-1")}
          >
            {state === "done" && onSelect !== undefined ? (
              <button
                type="button"
                onClick={() => onSelect(step.id)}
                className={cx(row, "-mx-1 cursor-pointer rounded-control px-1 hover:text-fg focus-visible:outline-2 focus-visible:outline-focus")}
              >
                {face}
              </button>
            ) : (
              <span className={row}>{face}</span>
            )}
            {!vertical && index < steps.length - 1 ? <span aria-hidden="true" className="h-px min-w-4 flex-1 bg-border" /> : null}
          </li>
        );
      })}
    </ol>
  );
}
