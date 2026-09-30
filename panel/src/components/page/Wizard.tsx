import { useId, useState } from "react";
import type { ReactNode } from "react";

import { PageHeader } from "../../app/PageHeader";
import type { PageHeaderProps } from "../../app/PageHeader";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";
import { LG_UP, useMediaQuery } from "../ui/useMediaQuery";
import { Stepper } from "./Stepper";
import type { WizardStep } from "./Stepper";

export interface WizardActionsProps {
  back?: { label?: string; onClick: () => void };
  next: { label?: string; onClick: () => void; loading?: boolean };
  /**
   * What the step still needs, as a sentence ("Enter the domain the app answers on."). Next
   * stays enabled; pressing it says this, and calls `onMissing` (to focus the field), instead
   * of moving on. A disabled button would not say why.
   */
  missing?: ReactNode;
  onMissing?: () => void;
  className?: string;
}

/** Back on the left, the way forward on the right, and what is missing when it is. */
export function WizardActions({ back, next, missing, onMissing, className }: WizardActionsProps) {
  const t = useT();
  const [pointed, setPointed] = useState(false);
  const blocked = missing !== undefined && missing !== null && missing !== "";
  return (
    <div className={cx("flex flex-col gap-3", className)}>
      {blocked && pointed ? (
        <Notice tone="warning" live>
          {missing}
        </Notice>
      ) : null}
      <div className="flex items-center justify-between gap-3">
        {back !== undefined ? (
          <Button variant="ghost" onClick={back.onClick}>
            {back.label ?? t("common.wizard.back")}
          </Button>
        ) : (
          <span />
        )}
        <Button
          variant="primary"
          loading={next.loading ?? false}
          onClick={() => {
            if (blocked) {
              setPointed(true);
              onMissing?.();
              return;
            }
            setPointed(false);
            next.onClick();
          }}
        >
          {next.label ?? t("common.wizard.next")}
        </Button>
      </div>
    </div>
  );
}

export interface WizardProps {
  /** The page header ("New application"), when the wizard is a page of its own. */
  header?: PageHeaderProps;
  steps: readonly WizardStep[];
  current: string;
  onSelectStep?: (id: string) => void;
  /** The step's title, an h2, and one sentence under it. */
  title: string;
  description?: ReactNode;
  /** The step's fields: at most a screen and a fifth. */
  children: ReactNode;
  /** What has been chosen so far, beside the step on wide screens and under it on narrow ones. */
  summary?: ReactNode;
  /** Names the summary's landmark; "Chosen so far" by default. */
  summaryLabel?: string;
  actions: WizardActionsProps;
  className?: string;
}

/**
 * T5, a wizard as a page: the stepper, vertical beside the step on a wide screen and on top of
 * it otherwise, the step with its h2, and a bar at the foot with Back and Continue, where
 * Continue is never disabled. In a dialog, compose Stepper (horizontal) and WizardActions in
 * the Dialog's body and footer instead.
 */
export function Wizard({ header, steps, current, onSelectStep, title, description, children, summary, summaryLabel, actions, className }: WizardProps) {
  const t = useT();
  const wide = useMediaQuery(LG_UP);
  const titleId = useId();
  return (
    <div data-template="wizard" className={cx("flex min-w-0 flex-col", className)}>
      {header !== undefined ? (
        <div data-slot="header" className="mb-8">
          <PageHeader {...header} flush />
        </div>
      ) : null}
      <div
        className={cx(
          "grid min-w-0 gap-8",
          "lg:grid-cols-[var(--width-settings-nav)_minmax(0,var(--width-wizard))]",
          // The summary beside the step only where it has room to be read (16rem at least);
          // narrower, it follows the step instead of cutting every value it holds.
          summary !== undefined && "2xl:grid-cols-[var(--width-settings-nav)_minmax(0,var(--width-wizard))_minmax(16rem,1fr)]",
        )}
      >
        <div data-slot="stepper" className="min-w-0">
          <Stepper
            steps={steps}
            current={current}
            orientation={wide ? "vertical" : "horizontal"}
            {...(onSelectStep !== undefined ? { onSelect: onSelectStep } : {})}
            {...(wide ? { className: "sticky top-20" } : {})}
          />
        </div>
        <section aria-labelledby={titleId} data-slot="step" className="flex min-w-0 flex-col gap-6">
          <header>
            <h2 id={titleId} className="title text-16 text-fg">
              {title}
            </h2>
            {description !== undefined ? <p className="mt-1 max-w-measure text-14 text-pretty text-fg-muted">{description}</p> : null}
          </header>
          <div className="min-w-0">{children}</div>
          <WizardActions {...actions} className="sticky bottom-0 z-sticky border-t border-border bg-bg py-3" />
        </section>
        {summary !== undefined ? (
          <aside data-slot="summary" aria-label={summaryLabel ?? t("common.wizard.summary")} className="min-w-0 max-2xl:lg:col-start-2">
            {summary}
          </aside>
        ) : null}
      </div>
    </div>
  );
}
