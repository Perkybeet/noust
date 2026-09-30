import type { ReactNode } from "react";

import { PageHeader } from "../../app/PageHeader";
import type { PageHeaderProps } from "../../app/PageHeader";
import { cx } from "../../lib/cx";

export interface DetailPageProps {
  /**
   * The resource's header, the same on every tab: breadcrumbs, the name (mono for a system
   * identifier), its state, its facts, and its actions.
   */
  header: PageHeaderProps;
  /**
   * When the resource is broken: a Notice `variant="banner"` with the cause and the fix,
   * between the header and the tabs, so it is seen on every tab and not only on the first.
   */
  banner?: ReactNode;
  /** The job the operator just started on it (JobProgress), on every tab. */
  job?: ReactNode;
  /** The sections as URL tabs (LinkTabs). */
  tabs?: ReactNode;
  /** The active tab (an Outlet). It never repeats the page's or the tab's title in an h2. */
  children: ReactNode;
  className?: string;
}

/**
 * T2, one resource with tabs: an application, a database, a service, a site, a server. The
 * header and the tabs are the layout route's, so they keep their place and height while the
 * tabs change beneath them.
 */
export function DetailPage({ header, banner, job, tabs, children, className }: DetailPageProps) {
  const between = banner !== undefined || job !== undefined || tabs !== undefined;
  return (
    <div data-template="detail" className={cx("flex min-w-0 flex-col", className)}>
      <div data-slot="header">
        <PageHeader {...header} flush />
      </div>
      {between ? (
        <div className="mt-4 flex min-w-0 flex-col gap-4">
          {banner !== undefined ? <div data-slot="banner">{banner}</div> : null}
          {job !== undefined ? <div data-slot="job">{job}</div> : null}
          {tabs !== undefined ? <div data-slot="tabs">{tabs}</div> : null}
        </div>
      ) : null}
      <div data-slot="content" className="mt-8 min-w-0">
        {children}
      </div>
    </div>
  );
}
