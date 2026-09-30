import type { ReactNode } from "react";

import { PageHeader } from "../../app/PageHeader";
import type { PageHeaderProps } from "../../app/PageHeader";
import { cx } from "../../lib/cx";

export interface DashboardPageProps {
  /** The title, the range control if the charts share one, and at most one primary action. */
  header: PageHeaderProps;
  /** Up to six key figures (StatTile), each linking to where it comes from. */
  figures?: ReactNode;
  /** What needs attention, worst first, five at most with a link to the rest. Absent when nothing does. */
  attention?: ReactNode;
  /** Recent activity as a timeline, beside the attention list. */
  activity?: ReactNode;
  /** The charts (Chart), after everything that asks for a decision. */
  charts?: ReactNode;
  /**
   * An empty server's first steps. When given it replaces everything below the header: four
   * empty charts are not a welcome.
   */
  firstSteps?: ReactNode;
  /** The CLI equivalent, once, at the foot. */
  footer?: ReactNode;
  className?: string;
}

/**
 * T4, a dashboard (Overview, Fleet, a server's summary): it summarises and links, never
 * repeats a whole list. Key figures first, then what needs attention beside what happened
 * recently, then the charts: at 1440px the first two rows are above the fold.
 */
export function DashboardPage({ header, figures, attention, activity, charts, firstSteps, footer, className }: DashboardPageProps) {
  const both = attention !== undefined && activity !== undefined;
  return (
    <div data-template="dashboard" className={cx("flex min-w-0 flex-col", className)}>
      <div data-slot="header">
        <PageHeader {...header} flush />
      </div>
      {firstSteps !== undefined ? (
        <div data-slot="firstSteps" className="mt-8 min-w-0">
          {firstSteps}
        </div>
      ) : (
        <div className="mt-8 flex min-w-0 flex-col gap-8">
          {figures !== undefined ? (
            <div data-slot="figures" className="grid min-w-0 grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
              {figures}
            </div>
          ) : null}
          {attention !== undefined || activity !== undefined ? (
            <div className={cx("grid min-w-0 gap-6", both && "lg:grid-cols-2")}>
              {attention !== undefined ? (
                <div data-slot="attention" className="min-w-0">
                  {attention}
                </div>
              ) : null}
              {activity !== undefined ? (
                <div data-slot="activity" className="min-w-0">
                  {activity}
                </div>
              ) : null}
            </div>
          ) : null}
          {charts !== undefined ? (
            <div data-slot="charts" className="grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {charts}
            </div>
          ) : null}
        </div>
      )}
      {footer !== undefined ? (
        <div data-slot="footer" className="mt-8">
          {footer}
        </div>
      ) : null}
    </div>
  );
}
