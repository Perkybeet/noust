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
 *
 * The step in hand is always read whole. Horizontally the others give way to their marker: on a phone every label is its marker, and
 * the step in hand is written under the row, as wide as the dialog; from 640px the labels sit beside their markers and the ones
 * that are not in hand are cut before the one that is. A label was once squeezed between two markers at 390px, over the next one.
 */
export function Stepper({ steps, current, orientation = "vertical", onSelect, className }: StepperProps) {
  const t = useT();
  const at = Math.max(0, steps.findIndex((step) => step.id === current));
  const vertical = orientation === "vertical";
  const list = (
    <ol
      aria-label={t("common.stepper.label")}
      data-orientation={orientation}
      className={cx(vertical ? "flex flex-col" : "flex items-center gap-2", vertical && className)}
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
                "text-13",
                // Beside a marker only where there is room: on a phone the label of every step is its marker, and the one in hand is drawn
                // under the row. From 640px the step in hand keeps all its text and the others are cut to what is left.
                vertical ? "truncate" : state === "current" ? "whitespace-nowrap max-sm:sr-only" : "truncate max-sm:sr-only",
                state === "current" ? "font-medium text-fg" : "text-fg-muted",
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
            className={cx(
              vertical ? "relative flex min-h-10 items-center" : "flex min-w-6 items-center gap-2",
              // A marker is never squeezed: the others share what the step in hand leaves, and the one in hand keeps its own width.
              !vertical && index < steps.length - 1 && "flex-1",
              !vertical && state === "current" && "shrink-0 sm:flex-none",
            )}
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
  if (vertical) return list;
  return (
    <div data-slot="stepper" className={className}>
      {list}
      {/* The step in hand, whole, where a phone has room for it: under the markers, over the width of the dialog, in as many lines as it
          needs. Drawn from an attribute so that the label is one text in the page and not two: the list already says it in words. */}
      <div
        data-slot="stepper-current"
        data-label={steps[at]?.label}
        aria-hidden="true"
        className="mt-2 text-13 font-medium text-balance text-fg after:content-[attr(data-label)] sm:hidden"
      />
    </div>
  );
}
