import type { ReactNode } from "react";

import { PageHeader } from "../../app/PageHeader";
import type { PageHeaderProps } from "../../app/PageHeader";
import { cx } from "../../lib/cx";

export interface ListPageProps {
  /** The page header: title, one sentence, the primary action (create) and the overflow menu. */
  header: PageHeaderProps;
  /** Named subsets of the list as URL tabs (LinkTabs), when it has them: Backups, Schedules... */
  tabs?: ReactNode;
  /** At most one Notice about the whole list. */
  notice?: ReactNode;
  /** The FilterBar. */
  filters?: ReactNode;
  /**
   * The list: a DataTable with `mobile="cards"`, or, when there is nothing yet, a first-use
   * EmptyState in its place (no table, no column headers).
   */
  children: ReactNode;
  /** The CLI equivalent (CommandHint), once, at the foot. */
  footer?: ReactNode;
  className?: string;
}

/**
 * T1, a list of like resources: the header with the create action, optional subset tabs,
 * the filters and the table, and the CLI hint at the foot. Nothing else sits between the
 * header and the table: what configures the list goes to a tab or to settings, and a row's
 * light detail opens in a Drawer.
 */
export function ListPage({ header, tabs, notice, filters, children, footer, className }: ListPageProps) {
  return (
    <div data-template="list" className={cx("flex min-w-0 flex-col", className)}>
      <div data-slot="header">
        <PageHeader {...header} flush />
      </div>
      {tabs !== undefined ? (
        <div data-slot="tabs" className="mt-4">
          {tabs}
        </div>
      ) : null}
      <div className="mt-8 flex min-w-0 flex-col gap-4">
        {notice !== undefined ? <div data-slot="notice">{notice}</div> : null}
        {filters !== undefined ? <div data-slot="filters">{filters}</div> : null}
        <div data-slot="content" className="min-w-0">
          {children}
        </div>
      </div>
      {footer !== undefined ? (
        <div data-slot="footer" className="mt-8">
          {footer}
        </div>
      ) : null}
    </div>
  );
}
